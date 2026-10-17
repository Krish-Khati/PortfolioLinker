"""
Step 5 - publish_blogger.py

Draft-mode only. This script CANNOT publish while PUBLISH_LIVE=false, and the
default in .env is false. It:

  1. reads a saved draft from drafts/
  2. re-runs the quality gate (a draft that passed once may not pass now)
  3. refuses to publish if any fabrication tripwire fires
  4. uploads the image to Blogger and swaps in the hosted URL
  5. creates the post as a Blogger DRAFT, or publishes only when explicitly told

    python publish_blogger.py --draft drafts/<file>.json              # to drafts
    python publish_blogger.py --draft <file> --publish                # explicit
    python publish_blogger.py --list                                  # what's in drafts
"""

from __future__ import annotations

import argparse
import json
import sys
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
def upload_image(blog_id: str, path: Path) -> str:
    """Upload to Blogger and return the hosted URL.

    Blogger's v3 posts.insert does not accept inline media, so this uses the
    legacy upload endpoint that the Blogger web UI itself calls.
    """
    import re

    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.credentials import Credentials

    if not path.exists():
        raise SystemExit(f"[!] image not found: {path}")

    creds = Credentials(
        token=None,
        refresh_token=config.env("BLOGGER_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.env("BLOGGER_CLIENT_ID"),
        client_secret=config.env("BLOGGER_CLIENT_SECRET"),
        scopes=[BLOGGER_SCOPE],
    )
    sess = AuthorizedSession(creds)

    page = sess.get(f"https://www.blogger.com/blogs.g?blogId={blog_id}").text
    m = re.search(r'name="(blogger\.google\.com/api/upload/[^"]+)"', page)
    if not m:
        raise SystemExit(
            "[!] Could not find Blogger's upload form in the page HTML.\n"
            "    The web UI may have changed, or the blog is not reachable with\n"
            "    this token. Nothing was published.")
    token = m.group(1)

    suffix = path.suffix.lstrip(".") or "jpeg"
    data = path.read_bytes()
    resp = sess.post(f"https://{token}", data=data,
                     headers={"Content-Type": f"image/{suffix}"})
    if resp.status_code != 200:
        raise SystemExit(f"[!] upload failed ({resp.status_code}): {resp.text[:200]}")

    rm = re.search(r'"url"\s*:\s*"(.*?)"', resp.text)
    if not rm:
        raise SystemExit(f"[!] no url in upload response: {resp.text[:200]}")
    url = rm.group(1).replace("\\/", "/")
    print(f"[ok] uploaded {path.name} -> {url}")
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
    if not publish_now:
        state = "DRAFT"
    else:
        state = "LIVE" if config.publish_live() else "DRAFT"

    return {
        "blog": {"id": config.env("BLOGGER_BLOG_ID")},
        "title": post["title"],
        "content": body_html,
        # Only set these on a real publish; a Blogger draft is a full post
        # payload anyway, so labels are always sent.
        "labels": post.get("tags", [])[:5],
        "published": state == "LIVE",
        "status": state,
    }


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
    scan = fabrication.scan(post)
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
        image_url = upload_image(blog_id, img_path)

    body = build_request_body(post, image_url, image, publish_now)
    print(f"\n[info] {'PUBLISHING' if publish_now else 'creating DRAFT'}: "
          f"{post['title']}")

    result = service.posts().insert(body=body).execute()
    url = result.get("url", "")
    state = result.get("status", "?")

    print(f"[ok] Blogger returned: {result.get('id')} [{state}]")
    print(f"     {url}")
    if not publish_now:
        print("\n     It is a DRAFT. Review it in Blogger, then publish there "
              "or re-run with --publish.")

    ledger.record({
        "topic": post.get("topic", ""),
        "title": post["title"],
        "slug": post["slug"],
        "url": url,
        "blogger_post_id": result.get("id"),
        "backlinks": post["backlinks"],
        "date": __import__("datetime").date.today().isoformat(),
        "status": "published" if publish_now else "blogger-draft",
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())