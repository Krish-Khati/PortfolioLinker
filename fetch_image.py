"""
Step 3 - fetch_image.py

Searches Pixabay for an image that actually matches the post's subject,
picks the best hit, downloads it and verifies it is a real, usable JPEG.

Two-stage selection, because relevance is the thing stock-photo APIs get
wrong most often:
  1. Hard filters drop anything too small, non-landscape or low-engagement.
  2. Groq reads the surviving candidates against the post topic and picks the
     one that genuinely depicts the subject, rather than the one with the
     prettiest thumbnail.

    python fetch_image.py --query "workbench small project"
    python fetch_image.py --query "..." --topic "..." --show-all
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import config

PIXABAY_API = "https://pixabay.com/api/"

# ---------------------------------------------------------------- sizing
# Pixabay's free-tier webformatURL is capped at 640px, which is far too small
# for a post hero. So: prefer webformatURL when it is already big enough,
# otherwise pull largeImageURL and downscale locally to exactly HERO_WIDTH.
HERO_WIDTH = 1280
MIN_WIDTH, MIN_HEIGHT = 1280, 675
MIN_ASPECT, MAX_ASPECT = 1.2, 2.6
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_OUT_BYTES = 400 * 1024
JPEG_QUALITY = 82
MIN_LIKES = 3

# Words that mean "generic filler" and should never be the whole query.
FILLER_WORDS = {
    "business", "technology", "success", "digital", "future", "abstract",
    "background", "workspace", "computer", "internet", "marketing", "growth",
}

SYSTEM = """You choose the header image for a blog article.

You return STRICT JSON only. No fences, no prose.

The test is NOT "does this photo literally depict the article's argument".
Most articles are conceptual - nobody can photograph "the pedagogical value of
finishing small projects". The test is:

  "Would a reasonable person accept this photo as the header image for this
   article without finding it odd?"

REJECT a photo only when it is genuinely off-domain:
  - a different subject entirely (carpentry, food, wildlife, a wedding)
  - generic filler that says nothing about the topic at all (a smiling stock
    model, a handshake, empty office desks, generic "business success")

ACCEPT a photo when it is a sensible visual companion, including:
  - a literal depiction of the article's subject
  - the tool, medium or environment the work happens in
  - a photo of the actual craft a technical article is about

Examples:
  article about writing code        -> code on a screen: ACCEPT
  article about finishing projects -> code on a screen: ACCEPT
  article about finishing projects -> a carpenter's workbench: REJECT
  article about learning to code   -> students at laptops: ACCEPT
  article about any dev topic      -> a handshake, or "business success": REJECT"""


# ------------------------------------------------------------------ search
# Pixabay allows 100 requests/minute on a free key. A scheduled runner making
# search + download calls back to back can trip it, and search used to raise
# SystemExit on 429 - which is not caught by `except Exception`, so a rate
# limit killed the whole cron run and produced no draft at all. Retry with
# backoff instead, and degrade to "no image" rather than aborting.
SEARCH_RETRIES = 4
SEARCH_BACKOFF = (3, 8, 20)
DOWNLOAD_RETRIES = 3
_last_request_at: float = 0.0
MIN_SECONDS_BETWEEN_CALLS = 1.0


def _throttle() -> None:
    """Keep at least MIN_SECONDS_BETWEEN_CALLS between Pixabay calls."""
    global _last_request_at
    import time

    wait = _last_request_at + MIN_SECONDS_BETWEEN_CALLS - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def search(query: str, per_page: int = 20, page: int = 1) -> list[dict[str, Any]]:
    """Search Pixabay, retrying with backoff on a 429.

    Returns [] on a persistent rate limit rather than aborting the run: a post
    with no hero image is recoverable by hand, a cron run that dies having
    written nothing is not.
    """
    import time

    import requests

    key = config.env("PIXABAY_API_KEY")
    if not key:
        raise SystemExit("[!] PIXABAY_API_KEY is missing from .env")

    for attempt in range(1, SEARCH_RETRIES + 1):
        _throttle()
        resp = requests.get(
            PIXABAY_API,
            params={
                "key": key,
                "q": query,
                "image_type": "photo",       # no illustrations/vectors
                "safesearch": "true",
                "orientation": "horizontal",  # matches the post hero slot
                "per_page": min(per_page, 200),
                "page": page,
                "order": "popular",
            },
            timeout=30,
        )

        if resp.status_code == 429:
            if attempt < SEARCH_RETRIES:
                delay = SEARCH_BACKOFF[min(attempt - 1, len(SEARCH_BACKOFF) - 1)]
                print(f"[pixabay] rate limited, retrying in {delay}s "
                      f"(attempt {attempt}/{SEARCH_RETRIES})")
                time.sleep(delay)
                continue
            print("[warn] Pixabay rate limit persisted; continuing without an "
                  "image")
            return []

        if resp.status_code >= 500 and attempt < SEARCH_RETRIES:
            delay = SEARCH_BACKOFF[min(attempt - 1, len(SEARCH_BACKOFF) - 1)]
            print(f"[pixabay] server {resp.status_code}, retrying in {delay}s")
            time.sleep(delay)
            continue

        if resp.status_code != 200:
            print(f"[warn] Pixabay search failed ({resp.status_code}); "
                  "continuing without an image")
            return []

        return resp.json().get("hits", [])

    return []


def best_source(hit: dict[str, Any]) -> tuple[str | None, str]:
    """Return (url, note) for the largest URL that still yields >=HERO_WIDTH.

    webformatURL on the free tier is only 640px wide, so anything below
    HERO_WIDTH has to come from largeImageURL and be downscaled locally.
    """
    web_w = hit.get("webformatWidth") or 0
    orig_w = hit.get("imageWidth") or 0

    if web_w >= HERO_WIDTH and hit.get("webformatURL"):
        return hit["webformatURL"], f"webformatURL at {web_w}px (already hero-sized)"
    if orig_w >= MIN_WIDTH and hit.get("largeImageURL"):
        return hit["largeImageURL"], f"largeImageURL at {orig_w}px, will downscale"
    if hit.get("webformatURL"):
        return hit["webformatURL"], f"webformatURL at {web_w}px (smaller than ideal)"
    return None, "no usable URL"


def filter_hits(hits: list[dict[str, Any]], reject_ai: bool = True
                ) -> tuple[list[dict], list[str]]:
    """Hard quality gates. Returns (kept, rejection_reasons)."""
    kept: list[dict] = []
    reasons: list[str] = []
    for h in hits:
        w, ht = h.get("imageWidth", 0), h.get("imageHeight", 0)
        hid = h.get("id")

        if reject_ai and h.get("isAiGenerated"):
            reasons.append(f"#{hid} flagged AI-generated")
            continue
        if h.get("isLowQuality"):
            reasons.append(f"#{hid} flagged low quality")
            continue
        if w < MIN_WIDTH:
            reasons.append(f"#{hid} original too small ({w}x{ht})")
            continue

        aspect = w / max(ht, 1)
        if not (MIN_ASPECT <= aspect <= MAX_ASPECT):
            reasons.append(f"#{hid} aspect {aspect:.2f} outside "
                           f"{MIN_ASPECT}-{MAX_ASPECT}")
            continue

        if h.get("likes", 0) < MIN_LIKES:
            reasons.append(f"#{hid} low engagement ({h.get('likes')} likes)")
            continue

        url, note = best_source(h)
        if not url:
            reasons.append(f"#{hid} no usable image URL")
            continue
        # The free tier only serves 640px from webformatURL, so nearly every
        # hit has to come from largeImageURL and be downscaled. That is fine,
        # but if the ORIGINAL is too small to reach hero width we must drop it.
        if note.startswith("webformatURL") and h.get("webformatWidth", 0) < MIN_WIDTH \
                and h.get("imageWidth", 0) < MIN_WIDTH:
            reasons.append(f"#{hid} no source can reach {MIN_WIDTH}px wide ({note})")
            continue

        h["_source_url"], h["_source_note"] = url, note
        kept.append(h)
    return kept, reasons


# ------------------------------------------------------------------ choose
def pick_relevant(query: str, topic: str, hits: list[dict]) -> tuple[dict | None, str]:
    """Ask Groq which candidate genuinely depicts the topic."""
    if not hits:
        return None, "no candidates survived filtering"
    if len(hits) == 1:
        return hits[0], "only candidate left"

    listing = "\n".join(
        f"  [{i}] id={h['id']}  tags={h.get('tags', '')}  "
        f"{h.get('imageWidth')}x{h.get('imageHeight')}  likes={h.get('likes')}"
        for i, h in enumerate(hits[:12])
    )
    prompt = f"""
ARTICLE TOPIC: {topic}
IMAGE SEARCH QUERY USED: "{query}"

CANDIDATE PHOTOS:
{listing}

Pick the single best candidate as the header image for this article.
Judge by the tags, since you cannot see the pixels.

Return exactly:
{{"index": <int>, "reason": "<one short sentence>", "fit": "good|ok|reject"}}

If every candidate is off-domain or generic filler, return fit "reject" and
index -1.
"""

    raw = config.chat(prompt, SYSTEM, temperature=0.2, max_tokens=600, as_json=True)
    try:
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned[cleaned.find("{"): cleaned.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError):
        # judging failed - fall back to the highest-engagement candidate
        best = max(hits[:12], key=lambda h: h.get("likes", 0))
        return best, "relevance judging failed; used highest-engagement hit"

    idx, fit = data.get("index", -1), data.get("fit", "reject")
    reason = data.get("reason", "")
    if fit == "reject" or idx is None or not (0 <= int(idx) < len(hits)):
        return None, f"rejected: {reason}"
    return hits[int(idx)], f"[{fit}] {reason}"


# ---------------------------------------------------------------- download
def download(hit: dict[str, Any], slug: str) -> dict[str, Any]:
    """Fetch the image, downscale to HERO_WIDTH, verify, and cache it."""
    import requests
    from PIL import Image

    dest_dir = config.IMAGES_DIR
    dest_dir.mkdir(exist_ok=True)

    url = hit.get("_source_url") or best_source(hit)[0]
    if not url:
        raise SystemExit("[!] hit has no usable URL")

    dest = dest_dir / f"{slug or 'image'}-{hit['id']}.jpg"

    if dest.exists() and dest.stat().st_size > 10_000:
        print(f"[info] cached: {dest.name}")
    else:
        print(f"[info] fetching {hit.get('_source_note', url)}")
        raw = None
        for attempt in range(1, DOWNLOAD_RETRIES + 1):
            _throttle()
            try:
                with requests.get(url, stream=True, timeout=120) as r:
                    if r.status_code == 429 or r.status_code >= 500:
                        raise requests.RequestException(
                            f"HTTP {r.status_code}")
                    r.raise_for_status()
                    declared = int(r.headers.get("content-length") or 0)
                    if declared and declared > MAX_DOWNLOAD_BYTES:
                        raise SystemExit(
                            f"[!] source is {declared / 1e6:.1f} MB, over the "
                            f"{MAX_DOWNLOAD_BYTES / 1e6:.0f} MB cap"
                        )
                    buf, total = bytearray(), 0
                    for chunk in r.iter_content(65536):
                        buf.extend(chunk)
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_BYTES:
                            raise SystemExit("[!] download exceeded the size cap")
                raw = bytes(buf)
                break
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001
                if attempt < DOWNLOAD_RETRIES:
                    delay = SEARCH_BACKOFF[min(attempt - 1,
                                                len(SEARCH_BACKOFF) - 1)]
                    print(f"[pixabay] download failed ({exc.__class__.__name__}"
                          f"), retrying in {delay}s")
                    import time as _t
                    _t.sleep(delay)
                    continue
                raise SystemExit(
                    f"[!] could not download the image after "
                    f"{DOWNLOAD_RETRIES} attempts: {exc}")

        # open, downscale, re-encode
        with Image.open(__import__("io").BytesIO(raw)) as im:
            im = im.convert("RGB")
            if im.width > HERO_WIDTH:
                new_h = round(im.height * HERO_WIDTH / im.width)
                im = im.resize((HERO_WIDTH, new_h), Image.LANCZOS)
            if im.width < MIN_WIDTH:
                raise SystemExit(
                    f"[!] final width {im.width} is under {MIN_WIDTH}; pick another hit"
                )
            q = JPEG_QUALITY
            for _ in range(4):  # walk quality down until the file fits
                dest.write_bytes(_encode(im, q))
                if dest.stat().st_size <= MAX_OUT_BYTES:
                    break
                q -= 8
            final_w, final_h = im.size
        print(f"[info] downscaled to {final_w}x{final_h} "
              f"at q={q}, {dest.stat().st_size / 1024:.0f} KB")

    # verify what is actually on disk
    with Image.open(dest) as im:
        im.verify()
    with Image.open(dest) as im:
        w, ht, fmt = im.width, im.height, im.format
    if fmt != "JPEG":
        raise SystemExit(f"[!] expected JPEG, got {fmt}")
    if w < MIN_WIDTH:
        raise SystemExit(f"[!] on-disk width {w} is under {MIN_WIDTH}")

    meta = {
        "id": hit["id"],
        "file": str(dest),
        "filename": dest.name,
        "width": w,
        "height": ht,
        "format": fmt,
        "bytes": dest.stat().st_size,
        "sha1": hashlib.sha1(dest.read_bytes()).hexdigest(),
        "source_url": url,
        "pixabay_page": hit.get("pageURL"),
        "tags": hit.get("tags"),
        "user": hit.get("user"),
        "search_query": None,   # filled by caller
    }
    dest.with_suffix(".json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return meta


def _encode(im, quality: int) -> bytes:
    import io

    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def check_query_specificity(query: str) -> str | None:
    """Complain if the query is generic filler."""
    words = [w for w in query.lower().split() if w]
    if not words:
        return "empty query"
    if set(words) <= FILLER_WORDS:
        return f"query {query!r} is entirely generic filler"
    if len(words) == 1 and words[0] in FILLER_WORDS:
        return f"query {query!r} is a single generic word"
    return None


# -------------------------------------------------------------------- main
def show_hits(hits: list[dict], limit: int = 12) -> None:
    for i, h in enumerate(hits[:limit]):
        print(f"  [{i}] id={h['id']:<9} {h['imageWidth']}x{h['imageHeight']}  "
              f"likes={h.get('likes'):<5} user={h.get('user', '')[:16]:<16} "
              f"tags={h.get('tags', '')[:70]}")
    if len(hits) > limit:
        print(f"  ... and {len(hits) - limit} more")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch a relevant Pixabay image.")
    ap.add_argument("--query", required=True, help="Pixabay search phrase")
    ap.add_argument("--topic", default="", help="article topic, for relevance judging")
    ap.add_argument("--slug", default="post", help="filename prefix")
    ap.add_argument("--show-all", action="store_true", help="list candidates too")
    ap.add_argument("--allow-ai", action="store_true",
                    help="allow Pixabay AI-generated images")
    ap.add_argument("--no-download", action="store_true", help="search only")
    args = ap.parse_args()

    warn = check_query_specificity(args.query)
    if warn:
        print(f"[warn] {warn}")
        print("       A generic query is the main cause of filler images.\n")

    print(f"[info] searching Pixabay: {args.query!r}")
    raw_hits = search(args.query)
    print(f"[info] {len(raw_hits)} raw hits")

    kept, reasons = filter_hits(raw_hits, reject_ai=not args.allow_ai)
    print(f"[info] {len(kept)} passed hard filters")
    for r in reasons[:8]:
        print(f"       rejected {r}")
    if not kept:
        print("[!] nothing survived. Try a more literal, more concrete query.")
        return 1

    if args.show_all:
        print("\n  candidates:")
        show_hits(kept)

    topic = args.topic or args.query
    best, why = pick_relevant(args.query, topic, kept)
    print(f"\n[info] pick: {why}")

    if best is None:
        print("[!] relevance judge rejected every candidate. Rewrite the query.")
        return 1

    meta = {"id": best["id"], "tags": best.get("tags"),
            "user": best.get("user"), "webformatURL": best["webformatURL"],
            "pageURL": best.get("pageURL"),
            "imageWidth": best.get("imageWidth"),
            "imageHeight": best.get("imageHeight")}
    print("\n" + "=" * 70)
    print(f"  CHOSEN  id={best['id']}")
    print(f"  tags    {best.get('tags')}")
    print(f"  user    {best.get('user')}")
    print(f"  size    {best.get('imageWidth')}x{best.get('imageHeight')}")
    print(f"  source  {best.get('_source_note', '?')}")
    print(f"  flags   ai_generated={best.get('isAiGenerated')} "
          f"low_quality={best.get('isLowQuality')}")
    print(f"  page    {best.get('pageURL')}")
    print("=" * 70)

    if args.no_download:
        return 0

    meta2 = download(best, args.slug)
    meta2["search_query"] = args.query
    meta2["relevance_reason"] = why
    print(f"\n[ok] saved {meta2['filename']} "
          f"({meta2['width']}x{meta2['height']}, {meta2['bytes'] / 1024:.0f} KB)")
    print(f"[ok] metadata -> {meta2['filename'].replace('.jpg', '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())