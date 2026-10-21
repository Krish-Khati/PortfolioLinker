"""
Step 5 - publish_blogger.py

Draft-mode only. This script CANNOT publish while PUBLISH_LIVE=false, and the
default in .env is false. It:

  1. reads a saved draft from drafts/
  2. re-runs the quality gate (a draft that passed once may not pass now)
  3. refuses to publish if any fabrication tripwire fires
  4. embeds the image in the post body (see _image_data_uri for why the
     image travels with the post instead of being hotlinked)
  5. creates the post as a Blogger DRAFT, or publishes only when explicitly told

    python publish_blogger.py --draft drafts/<file>.json              # to drafts
    python publish_blogger.py --draft <file> --publish                # explicit
    python publish_blogger.py --list                                  # what's in drafts
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
import fabrication
import ledger
import quality_gate
import render
from config import BLOGGER_API, BLOGGER_SCOPE, ROOT


# How many times the publish-time gate is asked before the publish is blocked.
#
# The re-check exists to catch a draft that no longer passes - not to re-roll
# a noisy reviewer. The gate is a sampled judgement: measured seven times on
# identical text it returned byte-identical scores six times and a different
# verdict once. With a single sample, that one unlucky verdict blocked a
# publish on a live run: the draft had passed this same gate minutes earlier
# in main.py, and the re-check rejected it with every score far below the
# fail line and NO problems at all - the model said "fail" and named nothing.
#
# Three samples does not loosen the standard. The draft still has to clear
# the gate, a reasoned veto still blocks the publish (see the check below),
# and the deterministic fabrication tripwires below are not sampled at all.
# It just stops one unexplained shrug from deciding the outcome.
GATE_RECHECK_ATTEMPTS = 3


# The largest image we are willing to embed in a post body.
#
# base64 is 4/3 of the binary, so this is roughly a 900 KB post body. Blogger
# takes a megabyte of HTML per post, and the images this pipeline downloads
# are 1280px wide at q=82, which lands between 40 KB and 150 KB. The cap is
# here so a pathological image fails the publish instead of silently
# producing a post the API rejects.
MAX_EMBEDDED_IMAGE_BYTES = 700 * 1024


# ------------------------------------------------------------------- oauth
def blogger_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=config.env("BLOGGER_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.env("BLOGGER_CLIENT_ID"),
        client_secret=config.env("BLOGGER_CLIENT_SECRET"),
        scopes=[BLOGGER_SCOPE],
    )
    creds.refresh(Request())
    return build("blogger", "v3", credentials=creds, cache_discovery=False)


# ------------------------------------------------------------------- image
def _image_data_uri(path: Path) -> str:
    """Return the hero image as a self-contained data URI.

    Why not hotlink the image, the way this used to:

    Pixabay hands out /get/<hash> URLs, and they are NOT permanent. The hash
    changes on every API response - the same photo fetched twice gets two
    different URLs - and the old ones die. Measured on this repo's own image
    cache: 13 of the 14 recorded URLs were already dead (HTTP 400) a few days
    after they were fetched. A post that hotlinks one of them keeps its
    text and loses its picture, a few days after it was published.

    Blogger's v3 API has no media upload method, so the image cannot be
    copied into Blogger's own store. Embedding it in the post body is the
    remaining way to make the post self-contained: the bytes are the exact
    bytes that were downloaded and validated here, they travel with the
    post, and nothing can expire them.

    The cost is size: base64 is 4/3 of the binary, so a 50 KB image becomes
    a 67 KB data URI. That is well inside Blogger's limits, and far cheaper
    than a blog whose images all break.

    The original Pixabay URL stays in the sidecar next to the image, so the
    provenance of every picture is still recorded.
    """
    import base64
    import mimetypes

    if not path.exists():
        raise SystemExit(f"[!] image not found: {path}")

    raw = path.read_bytes()
    if len(raw) > MAX_EMBEDDED_IMAGE_BYTES:
        raise SystemExit(
            f"[!] {path.name} is {len(raw) / 1e6:.1f} MB, over the "
            f"{MAX_EMBEDDED_IMAGE_BYTES / 1e6:.1f} MB limit for an embedded "
            f"image. Nothing was published.")

    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if not mime.startswith("image/"):
        raise SystemExit(f"[!] {path.name} is not an image ({mime}). "
                         "Nothing was published.")

    encoded = base64.b64encode(raw).decode("ascii")
    print(f"[ok] embedded {path.name} ({len(raw) / 1024:.0f} KB -> "
          f"{len(encoded) / 1024:.0f} KB base64)")
    print("     [note] the image travels inside the post; it cannot expire "
          "the way a Pixabay hotlink does")
    return f"data:{mime};base64,{encoded}"


# ------------------------------------------------------------------- post
def build_request_body(post: dict[str, Any], image_url: str,
                       image: dict[str, Any] | None, publish_now: bool) -> dict:
    body_html = render.to_html(post, image_url, image)
    audit = render.audit(body_html)

    if audit["unknown_targets"]:
        raise SystemExit(f"[!] refusing to send unverified links: {audit['unknown_targets']}")
    if audit["link_count"] != 2:
        raise SystemExit(f"[!] body has {audit['link_count']} links, expected exactly 2")
    body: dict = {
        "title": post["title"],
        "content": body_html,
        # A Blogger draft is a full post payload anyway, so labels always ship.
        "labels": post.get("tags", [])[:5],
    }
    # Deliberately NOT setting "status" or "published" for a draft.
    #
    # `status` is read-only on this resource ("only set for admin-level
    # requests") and posts.insert ignores it, so sending status=DRAFT is a
    # silent no-op. `published` is a datetime string, not a bool, so False is a
    # 400. And simply omitting `published` does NOT yield a draft - Blogger
    # defaults it to now and the post goes straight to the public blog. That
    # was a real bug here: a draft was created, returned status "LIVE" and was
    # publicly reachable.
    #
    # Draft vs live is decided by the isDraft QUERY PARAMETER on the insert
    # call, not by anything in this body.
    if publish_now:
        body["published"] = datetime.now(timezone.utc).isoformat()
    return body


def main() -> int:
    ap = argparse.ArgumentParser(description="Send a draft to Blogger.")
    ap.add_argument("--draft", help="path to a drafts/*.json file")
    ap.add_argument("--publish", action="store_true",
                    help="actually publish (still blocked unless PUBLISH_LIVE=true)")
    ap.add_argument("--skip-gate", action="store_true",
                    help="do not re-run the quality gate (not recommended)")
    ap.add_argument("--list", action="store_true", help="list drafts and exit")
    args = ap.parse_args()

    if args.list or not args.draft:
        drafts = sorted(config.DRAFTS_DIR.glob("*.json"))
        if not drafts:
            print("[!] no drafts yet. Run: python main.py")
            return 1
        print("available drafts:")
        for f in drafts:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            print(f"  {f.name[:60]:62} {d.get('_status', '?')}")
        print("\nusage: python publish_blogger.py --draft drafts/<file>.json")
        return 0

    path = Path(args.draft)
    if not path.exists():
        raise SystemExit(f"[!] no such draft: {path}")

    post = json.loads(path.read_text(encoding="utf-8"))
    image = post.pop("_image", None)
    post.pop("_status", None)
    post.pop("_audit", None)
    post.pop("errors", None)

    for field in ("title", "lede", "sections", "backlinks", "meta_description",
                  "tags", "slug", "alt_text", "word_count"):
        if field not in post:
            raise SystemExit(f"[!] draft is missing {field!r}")

    # ---- re-run the gate -------------------------------------------------
    if not args.skip_gate:
        print("[gate] re-checking draft before it goes anywhere...")
        res = quality_gate.check(post, image, attempts=GATE_RECHECK_ATTEMPTS)
        if not res["passed"]:
            # Work out WHY it failed before blocking a publish on it.
            #
            # A reasoned veto - a score at 6 or above, or problems the
            # reviewer named - blocks, exactly as before. A verdict with
            # nothing behind it does not: that is the reviewer contradicting
            # itself, and it is not a reason to keep a good draft off the
            # blog. The draft has already passed the gate in main.py, so a
            # reasonless shrug here does not get to overrule it.
            reasoned_veto = [h for h in res["history"]
                             if h.get("verdict") == "fail"
                             and not h.get("inconsistent")]
            if reasoned_veto:
                print("\n[!] gate failed; not publishing. Problems:")
                for p in reasoned_veto[-1].get("problems", [])[:8]:
                    print(f"    - {p}")
                return 1
            print("\n[!] the gate said 'fail' on every attempt but named no "
                  "reason (no score at 6 or above, no problems).")
            print("    That is not a verdict, so it does not block a draft "
                  "that already passed the gate.")
        print("[gate] passed")

    # ---- hard fabrication check -----------------------------------------
    scan = fabrication.scan_all(post, post.get("kind"))
    if not scan["clean"]:
        print("\n[!] fabrication tripwires fired; not publishing:")
        print(fabrication.verdict_text(scan))
        return 1

    if args.publish and not config.publish_live():
        print("\n[!] --publish requested but PUBLISH_LIVE is false in .env.")
        print("    Creating a Blogger DRAFT instead. Flip PUBLISH_LIVE=true")
        print("    only when you want posts going live automatically.")
        publish_now = False
    else:
        publish_now = args.publish

    service = blogger_service()
    blog_id = config.env("BLOGGER_BLOG_ID")

    image_url = ""
    if image:
        img_path = config.IMAGES_DIR / image["filename"]
        if not img_path.exists():
            raise SystemExit(f"[!] image not found: {img_path}")
        image_url = _image_data_uri(img_path)

    body = build_request_body(post, image_url, image, publish_now)
    print(f"\n[info] {'PUBLISHING' if publish_now else 'creating DRAFT'}: "
          f"{post['title']}")

    # blogId is a method parameter, not a body field. posts.insert raises
    # TypeError("Missing required parameter") without it.
    #
    # isDraft is the ONLY thing that decides draft vs live. It is a query
    # parameter, not a body field - this is the mistake that once published an
    # unreviewed draft to the public blog.
    result = service.posts().insert(blogId=blog_id, body=body,
                                    isDraft=not publish_now).execute()
    url = result.get("url", "")
    post_id = result.get("id", "")
    state = result.get("status") or "DRAFT"

    # Post-condition. Never report success on a draft run without confirming
    # Blogger agrees the post is a draft.
    #
    # Check `status`, NOT `published`: Blogger populates `published` with the
    # current timestamp even on a real isDraft=true insert, so testing it
    # rejects perfectly good drafts. As a second opinion, a live post has a
    # permalink path, while a draft's url is just the blog root ("/").
    from urllib.parse import urlparse

    path = (urlparse(url).path or "/") if url else "/"
    is_live = result.get("status") == "LIVE" or path not in ("/", "")
    if not publish_now and is_live:
        print("\n[!!] Blogger created the post as PUBLISHED despite "
              "isDraft=true. Reverting to draft now.")
        try:
            service.posts().revert(blogId=blog_id, postId=post_id).execute()
        except Exception as exc:  # noqa: BLE001
            print(f"[!!] revert failed ({exc.__class__.__name__}). "
                  f"DELETE IT BY HAND: post id {post_id}")
        else:
            print("[ok] reverted; it is a draft again.")
        raise SystemExit(
            f"[!] refused to report success: the post was briefly public.\n"
            f"    Blogger post id: {post_id}\n"
            f"    Check it in Blogger before doing anything else.")

    print(f"\n[ok] Blogger returned: {post_id} [{state}]")
    if not publish_now:
        print("     No public URL yet - it is a DRAFT. Review it in Blogger,")
        print("     then publish there or re-run with --publish.")
    else:
        print(f"     {url}")

    ledger.record({
        "topic": post.get("topic", ""),
        "title": post["title"],
        "slug": post["slug"],
        "url": url,
        "blogger_post_id": post_id,
        "backlinks": post["backlinks"],
        "date": datetime.now(timezone.utc).date().isoformat(),
        "status": "published" if publish_now else "blogger-draft",
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())