"""
PortfolioLinker — the pipeline.

    pick topic -> generate post -> choose + download image -> fix alt text
    -> render HTML -> quality gate (up to 2 attempts) -> save to drafts/

Nothing here ever publishes. publish_blogger.py (step 5) is a separate,
explicitly-invoked script and it refuses to publish while PUBLISH_LIVE=false.

    python main.py                     # full pipeline, saves a draft
    python main.py --topic "..."       # use a topic you supply
    python main.py --image "..."       # force an image query
    python main.py --gate-rerun FILE   # re-run only the quality gate
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

import alt_text
import config
import fetch_image
import generate_post
import ledger
import quality_gate
import render
import textnorm
import topic_picker


def _banner(step: str, text: str) -> None:
    print(f"\n{'=' * 70}\n  {step}  {text}\n{'=' * 70}")


def choose_topic(explicit: str | None) -> dict[str, Any]:
    if explicit:
        return {"topic": explicit, "angle": "", "image_query": "",
                "kind": "personal"}

    pool_path = config.ROOT / "topic_pool.json"
    pool = json.loads(pool_path.read_text(encoding="utf-8")) if pool_path.exists() else []

    # Backfill kind on pool entries written before the field existed. Both
    # pooled topics were personal-flavoured, and personal is the safe default,
    # so a missing value never silently relaxes the tripwires.
    fresh = []
    for t in pool:
        t = dict(t)
        t["kind"] = (str(t.get("kind") or "").strip().lower() or "personal")
        if t["kind"] not in ("personal", "general"):
            t["kind"] = "personal"
        if not ledger.too_similar(t.get("topic", "")):
            fresh.append(t)
    if fresh:
        pick = random.choice(fresh)
        print(f"[info] using pooled topic ({pick['kind']}): {pick['topic']}")
        return pick

    print("[info] topic pool exhausted or clashing - picking a new one")
    topics = topic_picker.pick_topics(3)
    if not topics:
        raise SystemExit("[!] topic_picker produced nothing usable")
    return topics[0]


def get_image(post: dict[str, Any], forced_query: str = "") -> dict[str, Any] | None:
    query = forced_query or post.get("image_query") or ""

    warn = fetch_image.check_query_specificity(query)
    if warn:
        print(f"[warn] {warn}")

    hits = fetch_image.search(query) if query else []
    kept, reasons = fetch_image.filter_hits(hits)
    print(f"[info] {len(hits)} hits, {len(kept)} passed filters")
    for r in reasons[:5]:
        print(f"       rejected {r}")
    if not kept:
        return None

    best, why = fetch_image.pick_relevant(query, post.get("title", ""), kept)
    print(f"[info] pick: {why}")
    if best is None:
        return None

    meta = fetch_image.download(best, post.get("slug", "post"))
    meta["search_query"] = query
    meta["relevance_reason"] = why
    return meta


MAX_ROUNDS = 2


def run(topic: str | None = None, image_query: str = "") -> dict[str, Any]:
    _banner("1/5", "TOPIC")
    topic_obj = choose_topic(topic)
    print(f"  {topic_obj['topic']}")

    # A failed gate sends its fix_instructions back into the generator, so the
    # second round is a genuinely different post rather than a re-read of the
    # same text. Two rounds is the hard stop.
    feedback = ""
    history: list[dict[str, Any]] = []
    post = image = result = None

    for round_no in range(1, MAX_ROUNDS + 1):
        tag = f"{round_no}/{MAX_ROUNDS}" if MAX_ROUNDS > 1 else "1/1"
        _banner(f"2/5  ROUND {tag}", "GENERATE")
        if feedback:
            print("  [info] rewriting with review feedback from the last round\n")

        try:
            post = generate_post.generate_post(topic_obj, feedback=feedback)
        except config.DailyTokenLimitError as exc:
            print(f"\n[!] {exc}")
            _save_draft({"topic": topic_obj["topic"],
                         "title": topic_obj["topic"],
                         "slug": textnorm.slugify(topic_obj["topic"]),
                         "backlinks": [], "sections": [], "tags": [],
                         "lede": "", "meta_description": "",
                         "image_query": "", "alt_text": ""},
                        None, "ABORTED - daily token limit", str(exc))
            return {"post": None, "passed": False, "stage": "quota"}

        if post.get("errors"):
            print(f"\n[!] generator still failing: {post['errors']}")
            feedback = "The previous draft could not be produced validly. " + \
                "; ".join(post["errors"])
            history.append({"stage": "generate", "errors": post["errors"]})
            continue

        print(f"  {post['title']}")
        print(f"  {post['word_count']} words | {len(post['sections'])} sections "
              f"| 2 links")

        # image + alt text are stable across rounds unless the topic changed
        if image is None or round_no == 1:
            _banner("3/5", "IMAGE")
            image = get_image(post, image_query)
            if image:
                print(f"  {image['filename']}  {image['width']}x{image['height']}")

            _banner("4/5", "ALT TEXT")
            if image:
                img_path = config.IMAGES_DIR / image["filename"]
                alt = alt_text.set_alt_text(
                    post, {"tags": image.get("tags")},
                    image.get("search_query", ""),
                    image_path=str(img_path) if img_path.exists() else None,
                )
                print(f"  \"{alt}\"")
                print(f"  source: {post.get('alt_text_source', '?')}")
                alt_err = generate_post.validate_alt(post)
                if alt_err:
                    print(f"  [warn] {alt_err}")
            else:
                print("  [skip] no image chosen, alt text left as drafted")

        _banner("5/5", f"QUALITY GATE  (round {tag})")
        result = quality_gate.check(post, image, attempts=1)
        history.append(result)

        if result["passed"]:
            break

        last = result["history"][-1]
        trip = last.get("tripwires") or {}
        parts = [last.get("fix_instructions", "")]

        if not trip.get("clean", True):
            parts.append(
                "The draft below contains first-person claims that only Krish can "
                "confirm. Rewrite it so every claim is either true by "
                "construction or explicitly framed as hypothetical.\n"
                "Rewrite these passages - do not keep them:\n"
                + "\n".join(f"- {f['quote']}" for f in trip["findings"])
            )

        parts.append("Problems that must not appear:")
        parts += [f"- {p}" for p in last.get("problems", [])]
        feedback = "\n".join(p for p in parts if p)
        if round_no < MAX_ROUNDS:
            print("\n[info] regenerating the whole post with that feedback…")

    passed = bool(result and result.get("passed"))
    gate_text = ("" if passed
                 else quality_gate.summarise(result) if result
                 else "generator never produced a valid post")

    if passed:
        _save_draft(post, image, "PASSED GATE", gate_text)
        ledger.record({
            "topic": post["topic"],
            "kind": post.get("kind", "personal"),
            "title": post["title"],
            "slug": post["slug"],
            "meta_description": post["meta_description"],
            "tags": post["tags"],
            "backlinks": post["backlinks"],
            "image": {k: image[k] for k in ("id", "filename", "width", "height")}
                     if image else None,
            "alt_text": post.get("alt_text"),
            "alt_text_source": post.get("alt_text_source"),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "status": "draft",
            "gate_rounds": len(history),
        })
        print(f"\n[ok] PASSED on round {len(history)}. Draft saved.")
    else:
        _save_draft(post, image,
                    f"FAILED after {MAX_ROUNDS} rounds", gate_text)
        print(f"\n[!] failed after {MAX_ROUNDS} rounds - saved to drafts/ "
              f"for manual review. Nothing was published.")

    return {"post": post, "image": image, "result": result,
            "passed": passed, "history": history}


def _save_draft(post: dict[str, Any], image: dict[str, Any] | None,
                status: str, gate_text: str) -> Path:
    config.DRAFTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = post.get("slug") or "untitled"
    stem = f"{stamp}-{slug}"

    body = render.to_html_with_local_image(
        post, image["filename"] if image else "", image)
    (config.DRAFTS_DIR / f"{stem}.body.html").write_text(
        f'<meta name="description" content="{post.get("meta_description", "")}">\n'
        f'<meta name="keywords" content="{", ".join(post.get("tags", []))}">\n'
        f"{body}\n", encoding="utf-8")

    (config.DRAFTS_DIR / f"{stem}.preview.html").write_text(
        render.preview_page(post, image["filename"] if image else "",
                            image, gate_text), encoding="utf-8")

    payload = dict(post)
    payload["_image"] = image
    payload["_status"] = status
    payload["_audit"] = render.audit(body)
    (config.DRAFTS_DIR / f"{stem}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    latest = config.DRAFTS_DIR / "LATEST.txt"
    latest.write_text(stem, encoding="utf-8")

    print(f"\n[ok] draft -> drafts/{stem}.*")
    print(f"     open drafts/{stem}.preview.html in a browser to read it")
    return config.DRAFTS_DIR / f"{stem}.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the full PortfolioLinker pipeline.")
    ap.add_argument("--topic", help="use this topic instead of the pool")
    ap.add_argument("--image", help="force this Pixabay query")
    ap.add_argument("--gate-rerun", metavar="DRAFT_JSON",
                    help="re-run only the quality gate on a saved draft")
    args = ap.parse_args()

    try:
        if args.gate_rerun:
            post = json.loads(Path(args.gate_rerun).read_text(encoding="utf-8"))
            image = post.pop("_image", None)
            res = quality_gate.check(post, image)
            print(quality_gate.summarise(res) if not res["passed"] else "PASSED")
            return 0 if res["passed"] else 1

        result = run(topic=args.topic, image_query=args.image)
        # 0 = produced a passing draft, 1 = failed to a draft, 2 = quota abort
        if result.get("stage") == "quota":
            return 2
        return 0 if result.get("passed") else 1
    except config.DailyTokenLimitError as exc:
        print(f"\n[!] {exc}")
        return 2
    finally:
        if config.USAGE["calls"]:
            print(f"\n[i] LLM usage this run: {config.usage_report()}")


if __name__ == "__main__":
    raise SystemExit(main())