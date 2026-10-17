"""
Offline integration test for the whole pipeline with the LLM stubbed out.

Everything except the actual Groq calls is exercised for real: topic choice,
validation, alt-text grounding, rendering, the render audit, the quality-gate
decision logic and the drafts/ output. Run:

    python test_pipeline.py
"""

from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

import config
from pathlib import Path as pathlib_Path

PASSES, FAILS = 0, 0


def check(label: str, got, want) -> None:
    global PASSES, FAILS
    if got == want:
        PASSES += 1
        print(f"  ok    {label}")
    else:
        FAILS += 1
        print(f"  FAIL  {label}\n          got : {got!r}\n          want: {want!r}")


def check_true(label: str, cond) -> None:
    check(label, bool(cond), True)


# ---------------------------------------------------------------- fixtures
FAKE_POST = {
    "title": "Why Finishing Small Projects Teaches More Than Endless Tutorials",
    "slug": "why-finishing-small-projects-teaches-more",
    "lede": "A tutorial tells you what worked for the person who made it. "
            "A project you finish forces you to decide what works for you, and "
            "that gap is where most of the actual learning happens.",
    "sections": [
        {"heading": "Deciding what to leave out", "paragraphs": [
            "The first useful decision in any small project is what not to "
            "build. A tutorial never asks you to make that call, because the "
            "author already made it for you two years ago, on hardware you do "
            "not have, for a version of the language that has since changed. "
            "You can follow every step and still end up with something whose "
            "shape you could not justify from scratch.",

            "I keep a short list before writing any code, then delete from it "
            "until only the essential remains. If I can explain the scope in "
            "one sentence without hedging, it is probably close enough to "
            "start. The point is not discipline for its own sake. The point "
            "is that a small finished thing has edges, and edges are where "
            "you find out whether your understanding was actually load bearing.",
        ]},
        {"heading": "Living with the consequences", "paragraphs": [
            "Once something is finished and other people can open it, the "
            "abstract questions turn concrete very fast. Naming becomes "
            "important, because a badly named function is impossible to ignore "
            "once you have to read it again. So does behaviour when input is "
            "wrong, because someone else will eventually supply the input you "
            "did not plan for.",

            "That is the part tutorials skip, and it is most of what separates "
            "someone who has shipped from someone who has only followed along. "
            "A tutorial can absorb a bad decision because the author is "
            "standing next to you explaining why it was fine. Nobody stands "
            "next to you in your own project. That silence is uncomfortable "
            "and it is the whole point, even though nothing about it feels "
            "productive at the time.",
        ]},
        {"heading": "What finishing actually gives you", "paragraphs": [
            "Completion is a skill on its own, and almost nothing else trains "
            "it. Most tutorials end when the video ends, which means the "
            "exercise never reaches the state where it has to work without "
            "you watching. Shipping is the only reliable way I know to find "
            "the gap between having seen something work and understanding why "
            "it worked.",

            "It also gives you something concrete to point at. Not a claim "
            "about what you understand, but an artifact somebody else can open "
            "and judge. That distinction matters more than it sounds, because "
            "the claim version is unfalsifiable and nobody, including you, can "
            "check it later. I keep the finished ones together in [[LINK1]] "
            "mostly as a record of how far the gap has closed over time, and "
            "occasionally as a reminder that the early ones were rougher than "
            "I remember them being, which is its own kind of useful signal.",
        ]},
        {"heading": "Where to start", "paragraphs": [
            "Pick something small enough that you are not tempted to postpone "
            "it. Boring scope is fine. Boring and finished beats ambitious and "
            "abandoned every time, and the ratio of effort to visible result "
            "is much better when there is not much to build.",

            "Then give it a real ending. Push it, write down what you would "
            "change, and move on. Do not reopen it to add one more feature "
            "unless something actually breaks, because the reopen is where "
            "most half-built things quietly go to die. If you want the habit "
            "to stay visible, [[LINK2]] is where I write these up as I go. "
            "The projects themselves are rarely the hard part. Finishing them "
            "is what turns watching into knowing, and there is no substitute "
            "for it that does not involve actually doing the finishing.",
        ]},
    ],
    "backlinks": [
        {"id": "LINK1", "anchor": "the finished projects I kept together",
         "target": "projects", "placement": "body"},
        {"id": "LINK2", "anchor": "where I write these up",
         "target": "blog", "placement": "close"},
    ],
    "image_query": "programming code on computer screen",
    "alt_text": "PLACEHOLDER",
    "meta_description": "Why finishing a small project teaches more than "
                        "another tutorial, and why deciding what to leave out "
                        "matters more than copying what to build in public",
    "tags": ["project based learning", "software craft", "self taught"],
}

FAKE_HIT = {
    "id": 1839406,
    "tags": "code, coding, computer, html, programming, screen, software, work",
    "user": "Pexels",
    "pageURL": "https://pixabay.com/photos/code-coding-computer-data-1839406/",
    "imageWidth": 3543, "imageHeight": 2365,
    "webformatWidth": 640, "webformatHeight": 427,
    "webformatURL": "https://cdn.fake/1839406_640.jpg",
    "largeImageURL": "https://cdn.fake/1839406.jpg",
    "isAiGenerated": False, "isLowQuality": False, "likes": 792,
}


def make_stub(gate_score: int = 2, gate_verdict: str = "pass"):
    """Replace config.chat with a stub that never touches the network."""

    def fake_chat(prompt: str, system: str, **kw):
        if "topics" in prompt.lower() and "Produce exactly" in prompt:
            return json.dumps({"topics": [{"topic": "stub topic", "angle": "a",
                                           "image_query": "x", "difficulty": "easy",
                                           "why_it_fits": "y"}]})
        if "POST UNDER REVIEW" in prompt:
            return json.dumps({
                "verdict": gate_verdict,
                "scores": {
                    "ai_slop": {"score": 2, "note": "fine"},
                    "fabrication": {"score": 1, "note": "no invented claims",
                                    "quotes": []},
                    "backlinks": {"score": 2, "note": "two natural links"},
                    "image": {"score": 3, "note": "code on screen fits"},
                },
                "problems": [] if gate_verdict == "pass"
                             else ["invented debugging story in section 3"],
                "fix_instructions": "cut the third section" if gate_verdict != "pass" else "",
            })
        if "alt text" in prompt.lower():
            return json.dumps({"alt_text": "source code on a dark-themed editor"})
        return json.dumps(FAKE_POST)

    return fake_chat


# ------------------------------------------------------------------- tests
print("\n1. validator accepts a well-formed post")
import generate_post as G  # noqa: E402

post = json.loads(json.dumps(FAKE_POST))
G._normalize_post(post)
post["word_count"] = G.word_count(post)
errs = G.validate(post)
check("no validation errors", errs, [])
print(f"       word_count={post['word_count']} "
      f"sections={len(post['sections'])} "
      f"meta={len(post['meta_description'])}")

print("\n2. render + audit")
import render as R  # noqa: E402

body = R.to_html(post, "https://cdn.example/hero.jpg", None)
audit = R.audit(body)
check("exactly 2 links rendered", audit["link_count"], 2)
check("2 distinct targets", audit["distinct_targets"], 2)
check("no unknown targets", audit["unknown_targets"], [])
check("no residual [[LINK]] tokens", audit["residual_tokens"], [])
check("image present", audit["has_img"], True)
check("image has non-empty alt", audit["img_has_alt"], True)
check("h2 count matches sections", audit["h2_count"], 4)

print("\n3. renderer refuses an unverified target")
bad = json.loads(json.dumps(post))
bad["backlinks"][0]["target"] = "https://evil.example.com/"
try:
    R.to_html(bad, "https://cdn.example/hero.jpg", None)
    check("raises on unverified target", "no raise", "ValueError")
except ValueError as e:
    check_true("raises on unverified target", "unverified link target" in str(e))

print("\n4. anchor appears exactly once, as a real <a>")
hrefs = R.link_inventory(body)
check("link 1 href", hrefs[0],
      "https://krish-portfolio-dev.vercel.app/projects")
check("link 2 href", hrefs[1],
      "https://krish-portfolio-dev.vercel.app/blog")
check_true("anchor text present in body",
           "the finished projects I kept together" in body)
check_true("no raw markdown leaked", "[[" not in body and "]]" not in body)

print("\n5. alt text is grounded in the image, not the draft guess")
import alt_text as A  # noqa: E402

# (a) vision path - model actually sees the file
cfg_a = config.chat
cfg_v = getattr(config, "chat_vision", None)
try:
    config.chat_vision = lambda p, imgs, s, **k: json.dumps(
        {"alt_text": "PHP source code on a dark-themed editor, syntax highlighted"})
    alt = A.set_alt_text(post, {"tags": FAKE_HIT["tags"]},
                         "programming code on computer screen",
                         image_path="images/fake.jpg")
    check("vision alt used", post["alt_text"],
          "PHP source code on a dark-themed editor, syntax highlighted")
    check("alt source recorded", post.get("alt_text_source"), "vision")

    # (b) vision unavailable -> tags
    def boom_vision(*a, **k):
        raise RuntimeError("no vision")
    config.chat_vision = boom_vision
    config.chat = lambda p, s, **k: json.dumps(
        {"alt_text": "source code in a dark-themed editor"})
    alt2 = A.set_alt_text(post, {"tags": FAKE_HIT["tags"]},
                          "programming code on computer screen",
                          image_path="images/fake.jpg")
    check("tags fallback used", post.get("alt_text_source"), "tags")

    # (c) everything LLM-side dead -> deterministic
    config.chat = boom_vision
    alt3 = A.set_alt_text(post, {"tags": FAKE_HIT["tags"]},
                          "programming code on computer screen",
                          image_path=None)
    check("deterministic fallback used", post.get("alt_text_source"),
          "deterministic")

    # (d) bad LLM output is rejected, not shipped
    config.chat_vision = lambda p, imgs, s, **k: json.dumps(
        {"alt_text": "image of technology"})
    alt4 = A.set_alt_text(post, {"tags": FAKE_HIT["tags"]}, "code screen")
    check("filler alt rejected", post["alt_text_source"] in {"tags", "deterministic"},
          True)
finally:
    config.chat = cfg_a
    if cfg_v is not None:
        config.chat_vision = cfg_v

check_true("deterministic alt is descriptive",
           4 <= len(alt3.split()) <= 18)
check_true("deterministic alt has no filler opener",
           not alt3.lower().startswith(("image of", "photo of", "picture of")))
print(f"       deterministic: {alt3!r}")

print("\n6a. fabrication tripwires on a clean post")
import fabrication as FAB  # noqa: E402

clean_scan = FAB.scan(post)
check_true("no tripwires on the honest fixture", clean_scan["clean"])
check("zero findings", clean_scan["count"], 0)

print("\n6b. tripwires catch the sentences the LLM gate scored 0 on")
# Regression fixture: verbatim sentences from the first live post, which the
# Groq gate passed with FABRICATION=0. Self-contained on purpose - a regression
# test must not depend on runtime artefacts in drafts/.
FABRICATED = {
    "title": "Shuffle",
    "lede": "When I first ran my memory match game, the shuffled cards "
            "sometimes lined up in a predictable pattern.",
    "sections": [
        {"heading": "when bias shows up", "paragraphs": [
            "In my memory match game, I saw the first row repeat the same "
            "two suits more than expected.",
            "I ran a quick simulation: fifty shuffles and counted how often "
            "each card landed in each position. The distribution was uneven. "
            "The top three positions had a 12% chance each, while the bottom "
            "positions hovered around 8%.",
        ]},
        {"heading": "fixing the shuffle", "paragraphs": [
            "I wrote a utility that shuffles an array in place. I seed the "
            "random generator with the current time and a simple multiplier "
            "to avoid repeating patterns.",
            "I created a simple test harness that ran the shuffle thousands "
            "of times. The counts were evenly distributed, confirming the "
            "algorithm worked.",
        ]},
        {"heading": "why it matters", "paragraphs": [
            "I've built a few more projects that rely on sound randomness, "
            "from a calculator's random seed to a language translator's "
            "sampling.",
        ]},
    ],
    "backlinks": [], "tags": ["a"], "meta_description": "x" * 155,
}
_fs = FAB.scan(FABRICATED)
check_true("LLM said FABRICATION=0; tripwires fire", not _fs["clean"])
_kinds = {f["kind"] for f in _fs["findings"]}
for _want in ("claims a debugging incident",
              "claims a specific bug or failure",
              "claims to have measured something",
              "states a specific metric or percentage",
              "claims authorship of a utility/harness",
              "claims a specific code/infra decision",
              "claims an outcome was verified",
              "claims multiple finished projects",
              "attributes randomness to the calculator",
              "attributes sampling to a translation tool"):
    check_true(f"catches: {_want}", _want in _kinds)
print(f"       {_fs['count']} tripwires fired")

import json as _j  # noqa: E402

print("\n6. quality gate verdict logic (scored locally, not trusted)")
import quality_gate as Q  # noqa: E402

passing = {"verdict": "pass", "scores": {"ai_slop": {"score": 2},
        "fabrication": {"score": 1}, "backlinks": {"score": 2},
        "image": {"score": 3}}, "problems": [], "fix_instructions": ""}
liar = {"verdict": "pass", "scores": {"ai_slop": {"score": 2},
        "fabrication": {"score": 9}, "backlinks": {"score": 1},
        "image": {"score": 1}}, "problems": [], "fix_instructions": ""}
problems_but_low = {"verdict": "pass", "scores": {"ai_slop": {"score": 1},
        "fabrication": {"score": 1}, "backlinks": {"score": 1},
        "image": {"score": 1}}, "problems": ["something is off"],
        "fix_instructions": ""}

cfg = config.chat
try:
    config.chat = lambda p, s, **k: json.dumps(passing)
    check("all-low scores pass", Q.review(post, None)["verdict"], "pass")
    config.chat = lambda p, s, **k: json.dumps(liar)
    r = Q.review(post, None)
    check("one score >=6 forces fail", r["verdict"], "fail")
    config.chat = lambda p, s, **k: json.dumps(problems_but_low)
    check("non-empty problems force fail", Q.review(post, None)["verdict"], "fail")
    config.chat = lambda p, s, **k: "not json at all"
    check("unparseable gate output fails closed",
          Q.review(post, None)["verdict"], "fail")

    # the decisive test: LLM says pass, tripwires say fail -> fail
    config.chat = lambda p, s, **k: _j.dumps(passing)
    r_real = Q.review(FABRICATED, None)
    check("tripwires override a passing LLM verdict",
          r_real["verdict"], "fail")
    check_true("tripwire score surfaced",
               r_real["scores"].get("FABRICATION_TRIPWIRES") == 9)

    print("\n7. two-strike rule")
    config.chat = lambda p, s, **k: json.dumps(liar)
    res = Q.check(post, None, attempts=2)
    check("did not pass", res["passed"], False)
    check("used both attempts", res["attempts"], 2)
    check("history has 2 entries", len(res["history"]), 2)

    config.chat = lambda p, s, **k: json.dumps(passing)
    res2 = Q.check(post, None, attempts=2)
    check("pass on first attempt", (res2["passed"], res2["attempts"]), (True, 1))
finally:
    config.chat = cfg

print("\n8. full pipeline with LLM stubbed")
# ISOLATION: redirect drafts/ to a temp dir so the test never clobbers real
# drafts, LATEST.txt, or the user's ledger.
import tempfile
_real_drafts = config.DRAFTS_DIR
config.DRAFTS_DIR = pathlib_Path(tempfile.mkdtemp(prefix="pl_test_"))
backup_ledger = config.LEDGER_PATH.with_suffix(".json.testbak")
if config.LEDGER_PATH.exists():
    shutil.copy(config.LEDGER_PATH, backup_ledger)
# start from an empty ledger so real recorded anchors cannot fail the fixture
config.LEDGER_PATH.write_text('{"posts": []}', encoding="utf-8")

cfg = config.chat
stub = make_stub(gate_verdict="pass")
import fetch_image as FI  # noqa: E402

real_download = FI.download
FI.download = lambda hit, slug: {
    "id": hit["id"], "filename": f"{slug}-{hit['id']}.jpg",
    "width": 1280, "height": 854, "format": "JPEG", "bytes": 160000,
    "tags": hit["tags"], "user": hit["user"], "search_query": None,
}
real_search = FI.search
FI.search = lambda q, **k: [dict(FAKE_HIT)]
try:
    config.chat = stub
    import main as M
    _before = len(json.loads(
        config.LEDGER_PATH.read_text(encoding="utf-8"))["posts"])
    out = M.run(topic="Why finishing small projects teaches more than tutorials")
    check("pipeline passed", out["passed"], True)

    check_true("real drafts dir untouched", _real_drafts.resolve() != config.DRAFTS_DIR.resolve())
    drafts = sorted(config.DRAFTS_DIR.glob("*.json"))
    check_true("draft json written", len(drafts) > 0)
    latest = (config.DRAFTS_DIR / "LATEST.txt").read_text(encoding="utf-8").strip()
    check_true("preview html written",
               (config.DRAFTS_DIR / f"{latest}.preview.html").exists())
    check_true("body html written",
               (config.DRAFTS_DIR / f"{latest}.body.html").exists())

    saved = json.loads((config.DRAFTS_DIR / f"{latest}.json").read_text(encoding="utf-8"))
    check("audit shows 2 links", saved["_audit"]["link_count"], 2)
    check("alt text is not the placeholder", saved["alt_text"] != "PLACEHOLDER", True)

    before_n = _before
    data = json.loads(config.LEDGER_PATH.read_text(encoding="utf-8"))
    check("exactly one post appended", len(data["posts"]) - before_n, 1)
    check("recorded anchors", len(data["posts"][-1]["backlinks"]), 2)
    check("status is draft", data["posts"][-1]["status"], "draft")

    preview = (config.DRAFTS_DIR / f"{latest}.preview.html").read_text(encoding="utf-8")
    check_true("preview has description meta",
               'name="description"' in preview)
    check_true("preview is noindex", 'noindex' in preview)

    print("\n9. anchor reuse is caught on the next run")
    import ledger as L
    check("first anchor now used", L.anchor_used(
        "the finished projects I kept together"), True)
    check("punctuation-variant also counts as used", L.anchor_used(
        "The finished projects I kept together."), True)
    check("similar-but-different anchor still free",
          L.anchor_used("the finished projects I kept together here"), False)
finally:
    config.chat = cfg
    FI.download = real_download
    FI.search = real_search
    if backup_ledger.exists():
        shutil.copy(backup_ledger, config.LEDGER_PATH)
        backup_ledger.unlink()
    shutil.rmtree(config.DRAFTS_DIR, ignore_errors=True)
    config.DRAFTS_DIR = _real_drafts

print("\n" + "=" * 52)
print(f"  {PASSES} passed, {FAILS} failed")
print("=" * 52)
sys.exit(1 if FAILS else 0)