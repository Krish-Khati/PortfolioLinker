"""
Step 0 — one-time Google OAuth setup for the Blogger API.

Run this ONCE. It:
  1. Opens your browser to Google's consent screen (scope: blogger).
  2. Exchanges the code for a long-lived refresh_token.
  3. Prints that refresh_token for you to paste into .env.
  4. Lists every Blogger blog on your account with its blog ID.

Usage
-----
    python get_refresh_token.py              # full flow, prints token
    python get_refresh_token.py --save       # ...and write it into .env for you
    python get_refresh_token.py --check      # skip login; verify an existing token
    python get_refresh_token.py --port 9000  # if 8765 is taken on your machine
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv, set_key
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"

# Only the Blogger scope. Least privilege — do not add drive/analytics scopes.
SCOPES = ["https://www.googleapis.com/auth/blogger"]

BLOGGER_API = "https://blogger.googleapis.com/v3"


# --------------------------------------------------------------------------
# credential loading
# --------------------------------------------------------------------------
def load_env() -> None:
    if not ENV_PATH.exists():
        sys.exit(
            f"[!] No .env file at {ENV_PATH}\n"
            f"    Copy .env.example to .env and fill in BLOGGER_CLIENT_ID and\n"
            f"    BLOGGER_CLIENT_SECRET, then run this script again."
        )
    load_dotenv(ENV_PATH)

    import os

    for key in ("BLOGGER_CLIENT_ID", "BLOGGER_CLIENT_SECRET"):
        val = os.environ.get(key, "").strip()
        if not val:
            sys.exit(
                f"[!] {key} is missing or empty in .env\n"
                f"    Get it from https://console.cloud.google.com/apis/credentials"
            )


# --------------------------------------------------------------------------
# step 1 — interactive login
# --------------------------------------------------------------------------
def do_login(port: int, timeout_seconds: int):
    """Open the browser, block until the user approves, return credentials."""
    import os

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": os.environ["BLOGGER_CLIENT_ID"],
                "client_secret": os.environ["BLOGGER_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=SCOPES,
    )

    print("=" * 68)
    print("  A browser tab should open now (Google sign-in).")
    print("  Sign in, then click 'Allow' to grant Blogger access.")
    print("  If no tab opened, copy the URL printed below into your browser.")
    print("=" * 68)

    creds = flow.run_local_server(
        host="localhost",
        port=port,
        authorization_prompt_message="\n>> Open this URL if no tab opened:\n{url}\n",
        success_message="\n[ok] Google approved access.",
        timeout_seconds=timeout_seconds,
        open_browser=True,
    )
    return creds


# --------------------------------------------------------------------------
# step 2 — list blogs
# --------------------------------------------------------------------------
def list_blogs(creds):
    """Return [(blog_id, title, url, status)] using the authenticated user's own view."""
    import requests

    resp = requests.get(
        f"{BLOGGER_API}/users/self/blogs",
        headers={"Authorization": f"Bearer {creds.token}"},
        params={"maxResults": 100},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def refresh_if_needed(creds):
    if not creds.valid:
        creds.refresh(Request())


def print_blogs(blogs) -> None:
    if not blogs:
        print("[!] No blogs found on this Google account.")
        return

    print()
    print("-" * 68)
    print(f"  Found {len(blogs)} blog(s)")
    print("-" * 68)
    for b in blogs:
        print(f"  BLOGGER_BLOG_ID = {b.get('id')}")
        print(f"  title   : {b.get('name')}")
        print(f"  url     : {b.get('url')}")
        print(f"  status  : {b.get('status', 'unknown')}  (LIVE / NOT_YET_PUBLISHED)")
        print()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Get a Blogger refresh token (one-time).")
    parser.add_argument("--check", action="store_true", help="verify the .env token, no browser")
    parser.add_argument("--save", action="store_true", help="write the refresh token into .env")
    parser.add_argument("--port", type=int, default=8765, help="local redirect port")
    parser.add_argument("--timeout", type=int, default=300, help="seconds to wait for consent")
    args = parser.parse_args()

    load_env()
    import os

    # ---- --check path: token already in .env ----
    if args.check:
        token = os.environ.get("BLOGGER_REFRESH_TOKEN", "").strip()
        if not token:
            sys.exit("[!] BLOGGER_REFRESH_TOKEN is empty in .env — run without --check first.")

        creds = Credentials(
            token=None,
            refresh_token=token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["BLOGGER_CLIENT_ID"],
            client_secret=os.environ["BLOGGER_CLIENT_SECRET"],
            scopes=SCOPES,
        )
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001
            sys.exit(f"[!] Stored refresh token is invalid or revoked:\n    {exc}")
        print("[ok] Existing BLOGGER_REFRESH_TOKEN works.")
        print_blogs(list_blogs(creds))
        return 0

    # ---- full login flow ----
    creds = do_login(port=args.port, timeout_seconds=args.timeout)

    if not creds.refresh_token:
        # Happens if you previously authorised this app: Google returns a token
        # response without a refresh_token unless you add access_type=offline.
        print()
        print("[!] No refresh_token returned by Google.")
        print("    This normally means this app already has a refresh token for you.")
        print("    Either:")
        print("      a) run:  python get_refresh_token.py --check   (if .env already has one), or")
        print("      b) revoke access at https://myaccount.google.com/permissions")
        print("         and run this script again.")
        return 1

    refresh_if_needed(creds)

    print()
    print("=" * 68)
    print("  YOUR REFRESH TOKEN (paste the whole line into .env)")
    print("=" * 68)
    print(creds.refresh_token)
    print("=" * 68)

    if args.save:
        set_key(str(ENV_PATH), "BLOGGER_REFRESH_TOKEN", creds.refresh_token, quote_mode="never")
        print("[ok] Written to .env as BLOGGER_REFRESH_TOKEN")
    else:
        print("Paste it into .env yourself, or re-run with --save.")
    print()

    print_blogs(list_blogs(creds))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[!] Cancelled.")
        raise SystemExit(130)