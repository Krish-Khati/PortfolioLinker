"""
Tests for the publish path: does a draft that clears the gate actually go
LIVE on Blogger, or does it quietly become a Blogger draft?

The bug this covers: a live run on 2026-10-21 produced a draft that passed
the quality gate in main.py, and the publish-time re-check then rejected it
with every score far below the fail line and NO problems at all - the model
said "fail" and named no reason. The publish was blocked and the topic was
burned for nothing.

The rule these tests pin down:
  - a reasoned veto (a score at 6 or above, or problems the reviewer named)
    blocks the publish, exactly as before
  - a verdict with nothing behind it does not block a draft that already
    passed the gate
  - and, above all: --publish with PUBLISH_LIVE=true means isDraft=False on
    the Blogger insert. isDraft is the only thing that decides live vs draft.

    python test_publish_live.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import config
import fabrication
import publish_blogger as PB
import quality_gate as Q

PASSES, FAILS = 0, 0


def check(label, got, want):
    global PASSES, FAILS
    if got == want:
        PASSES += 1
        print(f"  ok    {label}")
    else:
        FAILS += 1
        print(f"  FAIL  {label}\n          got : {got!r}\n          want: {want!r}")


def check_true(label, cond):
    check(label, bool(cond), True)


# --------------------------------------------------------------- fake Blogger
class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakePosts:
    def __init__(self, log):
        self.log = log

    def insert(self, **kw):
        self.log.append(kw)
        # Blogger answers a draft insert with status DRAFT and the blog root
        # as the url; a live insert answers LIVE with a permalink. Mirror
        # that, or the post-condition check in publish_blogger sees a draft
        # that "went public" and tries to revert it.
        if kw.get("isDraft"):
            return FakeRequest({"id": "999", "status": "DRAFT",
                                "url": "https://fake.blogspot.com/"})
        return FakeRequest({"id": "999", "status": "LIVE",
                            "url": "https://fake.blogspot.com/2026/10/x.html"})

    def revert(self, **kw):
        self.log.append({"reverted": kw})
        return FakeRequest({})


class FakeService:
    def __init__(self, log):
        self.log = log

    def posts(self):
        return FakePosts(self.log)


# ------------------------------------------------------------------- fixtures
POST = {
    "title": "A Post That Should Go Live",
    "slug": "a-post-that-should-go-live",
    "lede": "A lede that is long enough to count as a real opening sentence "
            "for the renderer and the validator to work with.",
    "sections": [
        {"heading": "First", "paragraphs": ["Body text one [[LINK1]] more."]},
        {"heading": "Second", "paragraphs": ["Body text two."]},
        {"heading": "Third", "paragraphs": ["Body three [[LINK2]] end."]},
    ],
    "backlinks": [
        {"id": "LINK1", "anchor": "the finished work", "target": "projects",
         "placement": "body"},
        {"id": "LINK2", "anchor": "where I write it up", "target": "blog",
         "placement": "close"},
    ],
    "image_query": "desk with notebook",
    "alt_text": "a desk with a notebook and a pen",
    "meta_description": "x" * 155,
    "tags": ["a", "b", "c"],
    "word_count": 700,
    "kind": "general",
}


def write_draft(tmp: Path, post=None) -> Path:
    payload = dict(post or POST)
    path = tmp / "draft.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def run_publish(draft: Path, *, publish: bool, live: bool,
                gate_result: dict) -> tuple[int, list]:
    """Run publish_blogger.main() with the network stubbed out."""
    log: list = []

    def fake_service():
        return FakeService(log)

    _svc = PB.blogger_service
    _url = PB._pixabay_public_url
    _gate = Q.check
    _scan = fabrication.scan_all
    _record = PB.ledger.record
    _argv = sys.argv
    _live = os.environ.get("PUBLISH_LIVE")

    PB.blogger_service = fake_service
    PB._pixabay_public_url = lambda path: "https://cdn.fake/image.jpg"
    Q.check = lambda post, image, attempts=1: gate_result
    fabrication.scan_all = lambda post, kind=None: {"clean": True,
                                                     "findings": [], "count": 0}
    PB.ledger.record = lambda *a, **k: None
    os.environ["PUBLISH_LIVE"] = "true" if live else "false"
    sys.argv = ["publish_blogger.py", "--draft", str(draft)]
    if publish:
        sys.argv.append("--publish")

    try:
        try:
            rc = PB.main()
        except SystemExit as exc:
            rc = f"SystemExit: {exc}"
    finally:
        PB.blogger_service = _svc
        PB._pixabay_public_url = _url
        Q.check = _gate
        fabrication.scan_all = _scan
        PB.ledger.record = _record
        sys.argv = _argv
        if _live is None:
            os.environ.pop("PUBLISH_LIVE", None)
        else:
            os.environ["PUBLISH_LIVE"] = _live

    return rc, log


def gate(passed: bool, history: list) -> dict:
    return {"passed": passed, "attempts": len(history), "history": history}


PASS_HIST = [{"verdict": "pass", "scores": {"AI_SLOP": 1}, "problems": [],
              "inconsistent": False, "gate_error": False}]

# what the live run actually produced: "fail" with every score below the
# line and no problems named
SHRUG_HIST = [{"verdict": "fail", "scores": {"AI_SLOP": 3, "BACKLINKS": 2},
               "problems": [], "inconsistent": True, "gate_error": False}]

REAL_VETO_HIST = [{"verdict": "fail", "scores": {"FABRICATION": 9},
                   "problems": ["claims a debugging incident it never had"],
                   "inconsistent": False, "gate_error": False}]

tmp = Path(tempfile.mkdtemp())

try:
    print("\n1. --publish with PUBLISH_LIVE=true inserts isDraft=False")
    rc, log = run_publish(write_draft(tmp), publish=True, live=True,
                          gate_result=gate(True, PASS_HIST))
    check("the run succeeded", rc, 0)
    check_true("Blogger was called", bool(log))
    check("isDraft is False - this is what makes it LIVE", log[0].get("isDraft"),
          False)
    check_true("the body carries a published timestamp",
               bool(log[0].get("body", {}).get("published")))
    check_true("no status field was sent (it is read-only on Blogger)",
               "status" not in log[0].get("body", {}))

    print("\n2. --publish with PUBLISH_LIVE=false creates a Blogger DRAFT")
    rc, log = run_publish(write_draft(tmp), publish=True, live=False,
                          gate_result=gate(True, PASS_HIST))
    check("the run succeeded", rc, 0)
    check("isDraft is True - it must NOT go live", log[0].get("isDraft"), True)
    check_true("no published timestamp was sent",
               not log[0].get("body", {}).get("published"))

    print("\n3. no --publish at all is always a draft")
    rc, log = run_publish(write_draft(tmp), publish=False, live=True,
                          gate_result=gate(True, PASS_HIST))
    check("the run succeeded", rc, 0)
    check("isDraft is True", log[0].get("isDraft"), True)

    print("\n4. a reasoned veto still blocks the publish")
    rc, log = run_publish(write_draft(tmp), publish=True, live=True,
                          gate_result=gate(False, REAL_VETO_HIST))
    check("the publish was refused", rc, 1)
    check("nothing was sent to Blogger", len(log), 0)

    print("\n5. a reasonless 'fail' does not block a draft that passed")
    rc, log = run_publish(write_draft(tmp), publish=True, live=True,
                          gate_result=gate(False, SHRUG_HIST))
    check("the publish went ahead", rc, 0)
    check_true("Blogger was called", bool(log))
    check("and it went out live", log[0].get("isDraft"), False)

    print("\n6. the gate marks a reasonless verdict as inconsistent")
    os.environ.setdefault("PUBLISH_LIVE", "false")
    real_chat = config.chat
    try:
        config.chat = lambda p, s, **k: json.dumps(
            {"verdict": "fail",
             "scores": {"ai_slop": {"score": 3}, "fabrication": {"score": 0},
                        "backlinks": {"score": 2}, "image": {"score": 0}},
             "problems": [], "fix_instructions": ""})
        r = Q.review(POST, None)
        check("it is flagged as inconsistent", r.get("inconsistent"), True)
        check("and it is not a plain pass", r["verdict"], "fail")

        config.chat = lambda p, s, **k: json.dumps(
            {"verdict": "fail",
             "scores": {"ai_slop": {"score": 9}, "fabrication": {"score": 0},
                        "backlinks": {"score": 2}, "image": {"score": 0}},
             "problems": ["reads like filler"], "fix_instructions": ""})
        r2 = Q.review(POST, None)
        check("a score at 9 is NOT inconsistent", r2.get("inconsistent"), False)
        check("and it still fails", r2["verdict"], "fail")
    finally:
        config.chat = real_chat

    print("\n7. the gate asks again when the reviewer contradicts itself")
    asked = {"n": 0}

    def shrug_then_pass(p, s, **k):
        asked["n"] += 1
        if asked["n"] == 1:
            return json.dumps(
                {"verdict": "fail",
                 "scores": {"ai_slop": {"score": 3}, "fabrication": {"score": 0},
                            "backlinks": {"score": 2}, "image": {"score": 0}},
                 "problems": [], "fix_instructions": ""})
        return json.dumps(
            {"verdict": "pass",
             "scores": {"ai_slop": {"score": 1}, "fabrication": {"score": 0},
                        "backlinks": {"score": 0}, "image": {"score": 0}},
             "problems": [], "fix_instructions": ""})

    try:
        config.chat = shrug_then_pass
        res = Q.check(POST, None, attempts=2)
        check("it passed on the second sample", res["passed"], True)
        check("the gate was asked twice", asked["n"], 2)
    finally:
        config.chat = real_chat
finally:
    pass

print("\n" + "=" * 52)
print(f"  {PASSES} passed, {FAILS} failed")
print("=" * 52)
sys.exit(1 if FAILS else 0)
