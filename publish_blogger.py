"""
Step 5 - publish_blogger.py

Draft-mode only. This script CANNOT publish while PUBLISH_LIVE=false, and the
default in .env is false. It:

  1. reads a saved draft from drafts/
  2. re-runs the quality gate (a draft that passed once may not pass now)
  3. refuses to publish if any fabrication tripwire fires
  4. resolves the image to a usable URL (see _pixabay_public_url for why this
     is a hotlink and not a Blogger upload)
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
def _pixabay_public_url(path: Path) -> str:
    """Return the image's own public CDN URL, verified reachable.

    Why this exists instead of an upload: Blogger's v3 API has no media upload
    method, and the endpoint the web UI used to use for it is gone. The old
    code scraped a form token out of
    https://www.blogger.com/blogs.g?blogId=... and POSTed to
    blogger.google.com/api/upload/<token>. That page now returns a hard 404,
    so the upload path could never run. The replacement upload service
    (content-push.googleapis.com/upload/blogger[/resumable]) answers every
    request with "Request without ClientId (Feed name)", because the feed name
    it wants is an ephemeral per-session value only the SPA bootstrap knows.

    So we point the post at the image's canonical public URL instead. The file
    was downloaded from that same URL, so the bytes in the post are the exact
    bytes we validated. This is hotlinking, not a copy into Blogger's media
    store: if that host ever stops serving it, the post keeps its alt text but
    loses the picture.
    """
    import urllib.request

    meta_path = path.with_suffix(".json")
    url = ""
    if meta_path.exists():
        try:
            url = json.loads(meta_path.read_text(encoding="utf-8")).get(
                "source_url", "") or ""
        except (json.JSONDecodeError, OSError):
            url = ""
    if not url:
        raise SystemExit(
            f"[!] no public image URL available for {path.name}.\n"
            "    Blogger has no upload API and this image has no recorded\n"
            "    source_url. Re-fetch it with fetch_image.py. "
            "Nothing was published.")

    # Pixabay's CDN rejects a bare HEAD (403) but serves fine to a normal
    # client, so check with a browser-ish User-Agent before we commit to it.
    req = urllib.request.Request(url, method="HEAD", headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ctype = resp.headers.get("Content-Type", "")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"[!] image URL for {path.name} is not reachable "
            f"({exc.__class__.__name__}). Nothing was published.")

    if not ctype.startswith("image/"):
        raise SystemExit(
            f"[!] image URL for {path.name} returned {ctype!r}, not an "
            "image. Nothing was published.")

    print(f"[ok] {path.name} -> {url}")
    print("     [note] hotlinked from its public CDN URL; Blogger's own "
          "upload endpoint no longer exists")
    return url


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
        res = quality_gate.check(post, image, attempts=1)
        if not res["passed"]:
            print("\n[!] gate failed; not publishing. Problems:")
            for p in res["history"][-1].get("problems", [])[:8]:
                print(f"    - {p}")
            return 1
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
        image_url = _pixabay_public_url(img_path)

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