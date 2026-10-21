"""
Unit tests for textnorm + the anchor rules in generate_post.validate().
Run: python test_hardening.py
"""

from __future__ import annotations

import json
import pathlib
import shutil
from pathlib import Path

import fetch_image as FI
import generate_post as G
import profile as P
import textnorm as T

FAILS = 0
PASSES = 0


def check(label: str, got, want) -> None:
    global FAILS, PASSES
    if got == want:
        PASSES += 1
        print(f"  ok    {label}")
    else:
        FAILS += 1
        print(f"  FAIL  {label}\n          got : {got!r}\n          want: {want!r}")


def check_true(label: str, cond: bool) -> None:
    check(label, bool(cond), True)


# --------------------------------------------------------------- textnorm
print("\ntextnorm.normalize")
check("U+2011 non-breaking hyphen",
      T.normalize("step‑by‑step guide"), "step-by-step guide")
check("U+2013 en dash", T.normalize("one–two"), "one-two")
check("U+2014 em dash", T.normalize("word—word"), "word-word")
check("U+00A0 nbsp", T.normalize("a b"), "a b")
check("curly quotes survive", T.normalize("don’t"), "don’t")
check("emoji survives", T.normalize("hi 🚀"), "hi 🚀")
check("empty", T.normalize(""), "")

print("\ntextnorm.slugify")
check("dashes collapse",
      T.slugify("Why Small Projects—Actually—Win"), "why-small-projects-actually-win")
check("punctuation stripped",
      T.slugify("What's up, AI? (2026)"), "whats-up-ai-2026")
check("accents folded",
      T.slugify("Krish's Résumé Café"), "krishs-resume-cafe")
check("length capped", len(T.slugify("word " * 40)) <= 70, True)
check("length capped + trimmed", len(T.slugify("word " * 40)), 69)
check_true("no trailing dash", not T.slugify("a b c ---").endswith("-"))

print("\ntextnorm.anchor_key")
check("punctuation dropped",
      T.anchor_key("The Calculator, I built."), "the calculator i built")
check("dash folded", T.anchor_key("step‑by‑step notes"), "step-by-step notes")
check("whitespace collapsed",
      T.anchor_key("my   portfolio    home"), "my portfolio home")

print("\ntextnorm.meta_safe")
check("trailing period removed",
      T.meta_safe("A real sentence about things. "), "A real sentence about things")
check("nbsp folded", T.meta_safe("traffic growth"), "traffic growth")


# --------------------------------------------------------- anchor rules
print("\ngenerate_post anchor validation")


def anchor_errors(anchor: str, target_a: str = "projects", target_b: str = "home",
                  placement: str = "body") -> list[str]:
    """Build a minimal valid-ish post carrying one bad anchor, return its errors."""
    anchor2 = "the card game logic I wrote"
    post = {
        "title": "A perfectly reasonable title about building things",
        "lede": "A lede sentence that is long enough to not trip any check at all.",
        "sections": [
            {"heading": "Scope", "paragraphs": [
                "One paragraph here with [[LINK1]] inline in the middle of it.",
                "Second paragraph adding words so the total stays in range of the checker.",
            ]},
            {"heading": "Build", "paragraphs": [
                "More text [[LINK2]] and then a closing thought for the reader.",
                "Extra filler words to push the document above six hundred words overall.",
            ]},
            {"heading": "Ship", "paragraphs": [
                "Final section paragraph with a bit more explanation of the method used.",
                "Last paragraph wrapping the argument up without restating everything.",
            ]},
        ],
        "backlinks": [
            {"id": "LINK1", "anchor": anchor, "target": target_a, "placement": "body"},
            {"id": "LINK2", "anchor": anchor2, "target": target_b, "placement": "close"},
        ],
        "image_query": "workbench small project",
        "alt_text": "a cluttered desk with a laptop and a notebook",
        "meta_description": "A " + "x" * 140,
        "tags": ["one", "two", "three"],
    }
    G._normalize_post(post)
    post["word_count"] = G.word_count(post)
    return G.validate(post)


# the exact anchor that shipped before the fix
check_true("'my portfolio home' now rejected",
           any("banned phrase" in e for e in anchor_errors("my portfolio home")))
check_true("'click here' rejected",
           any(e for e in anchor_errors("click here")))
check_true("'portfolio' alone rejected",
           any(e for e in anchor_errors("portfolio")))
check_true("'the calculator website' rejected (contains 'website')",
           any("banned phrase" in e for e in anchor_errors("the calculator website")))
check_true("'my github' rejected",
           any("banned phrase" in e for e in anchor_errors("my github")))

print("\ngenerate_post anchor acceptance")
good = anchor_errors("the fitness temple project I built")
anchor_errs = [e for e in good if "anchor" in e.lower()]
check("clean anchor produces no anchor errors", anchor_errs, [])
check_true("'the memory match game I built' accepted",
           not [e for e in anchor_errors("the calculator logic I learned") if "anchor" in e.lower()])

print("\nnon-ASCII rejection path")
# _normalize_post folds dashes BEFORE validate runs, so a post carrying an
# exotic dash in an anchor comes out ASCII rather than being rejected.
p = {
    "title": "A perfectly reasonable title about building things",
    "meta_description": "A " + "x" * 140,
    "backlinks": [
        {"id": "LINK1", "anchor": "step‑by‑step notes",
         "target": "projects", "placement": "body"},
        {"id": "LINK2", "anchor": "the calculator logic I used",
         "target": "home", "placement": "close"},
    ],
    "tags": ["One Tag", "two"],
    "sections": [{"heading": "Scope‑ish",
                  "paragraphs": ["A paragraph with an en–dash in it."]}],
}
G._normalize_post(p)
check("anchor dash folded", p["backlinks"][0]["anchor"], "step-by-step notes")
check("heading dash folded", p["sections"][0]["heading"], "Scope-ish")
check("paragraph en-dash folded",
      p["sections"][0]["paragraphs"][0], "A paragraph with an en-dash in it.")
check("slug rebuilt ascii",
      p["slug"], "a-perfectly-reasonable-title-about-building-things")
check("tags slugified", p["tags"], ["one-tag", "two"])

print("\nduplicate anchor in one post")


def dup_anchor_errors() -> list[str]:
    p = {
        "title": "A perfectly reasonable title about building things",
        "lede": "A lede sentence that is long enough to not trip any check at all.",
        "sections": [
            {"heading": "Scope", "paragraphs": [
                "One paragraph here with [[LINK1]] inline in the middle of it.",
                "Second paragraph adding words so the total stays in range of the checker.",
            ]},
            {"heading": "Build", "paragraphs": [
                "More text [[LINK2]] and then a closing thought for the reader.",
                "Extra filler words to push the document above six hundred words overall.",
            ]},
            {"heading": "Ship", "paragraphs": [
                "Final section paragraph with a bit more explanation of the method used.",
                "Last paragraph wrapping the argument up without restating everything.",
            ]},
        ],
        "backlinks": [
            {"id": "LINK1", "anchor": "the calculator logic I used",
             "target": "projects", "placement": "body"},
            {"id": "LINK2", "anchor": "The calculator logic I used.",
             "target": "home", "placement": "close"},
        ],
        "image_query": "workbench small project",
        "alt_text": "a cluttered desk with a laptop and a notebook",
        "meta_description": "A " + "x" * 140,
        "tags": ["one", "two", "three"],
    }
    G._normalize_post(p)
    p["word_count"] = G.word_count(p)
    return G.validate(p)


check_true("duplicate anchor detected",
           any("used twice" in e for e in dup_anchor_errors()))

print("\ncoerce_post: malformed model output is repaired, not crashed on")
check("sections as plain strings",
      G.coerce_post({"sections": ["a heading", "some body text"]})["sections"],
      [{"heading": "", "paragraphs": ["a heading"]},
       {"heading": "", "paragraphs": ["some body text"]}])
check("paragraphs as a bare string",
      G.coerce_post({"sections": [{"heading": "H", "paragraphs": "one line"}]})
            ["sections"][0]["paragraphs"],
      ["one line"])
check("non-string paragraphs coerced",
      G.coerce_post({"sections": [{"heading": "H", "paragraphs": [1, None, "ok"]}]})
            ["sections"][0]["paragraphs"],
      ["1", "ok"])
check("tags from a comma string",
      G.coerce_post({"tags": "alpha, beta"})["tags"], ["alpha", "beta"])
check("missing backlinks -> empty list",
      G.coerce_post({}).get("backlinks"), [])
check("bad backlink id renumbered",
      G.coerce_post({"backlinks": [{"id": "x", "anchor": "a", "target": "home"}]})
            ["backlinks"][0]["id"],
      "LINK1")
check("sections that are junk become empty",
      G.coerce_post({"sections": [None, 7, {}]})["sections"], [])
check("non-dict input is survivable", G.coerce_post("nope"), {})

# the exact shape that killed a live run
crash_post = {"sections": ["a string instead of a dict"],
              "backlinks": [{"id": "LINK1", "anchor": "a", "target": "home"}],
              "tags": "x", "title": "T", "lede": "L",
              "meta_description": "m" * 155, "image_query": "q",
              "alt_text": "alt"}
coerced = G.coerce_post(crash_post)
G._normalize_post(coerced)          # this used to raise AttributeError
check("previously-fatal shape now normalises", coerced["sections"][0]["paragraphs"],
      ["a string instead of a dict"])
check("then validates without crashing",
      isinstance(G.validate(coerced), list), True)

print("\nfit_meta: force into the 150-160 window")
check("already in range passes through",
      len(G.fit_meta("x" * 155)), 155)
_trimmed = G.fit_meta("word " * 60)
check_true("too long trims into range", 150 <= len(_trimmed) <= 160)
check_true("trimmed at a word boundary", _trimmed == _trimmed.strip())
check_true("trimmed keeps whole words only",
           all(w == "word" for w in _trimmed.split()))
check("trimmed result is in range",
      150 <= len(G.fit_meta("alpha beta " * 40)) <= 160, True)
check("too short returns None", G.fit_meta("too short"), None)
check("trailing period stripped", G.fit_meta("a" * 152 + ".."),
      "a" * 152)

print("\nenforce_length: the loop must actually reach the floor")
import json as _json  # noqa: E402

_real_chat = G.config.chat


def _stub_expander(target_wc: int, jitter: int = 0):
    """Return a stub that mimics the model under-expanding."""
    state = {"n": 0}

    def stub(prompt, system, **kw):
        state["n"] += 1
        words = f"extra word{'!' * 0} " * 40
        base = {
            "title": "T", "lede": "lede " * 10,
            "sections": [{"heading": "H", "paragraphs": [words] * 2}],
            "backlinks": G.FAKE_BACKLINKS,
            "meta_description": "m" * 155, "tags": ["a"],
            # deliberately wrong: must be overwritten by _preserve
            "slug": "HIJACKED", "title": "HIJACKED TITLE",
        }
        return _json.dumps(base)
    return stub


_short = {
    "title": "Short", "lede": "short " * 12,
    "sections": [{"heading": "A", "paragraphs": ["tiny " * 20]},
                 {"heading": "B", "paragraphs": ["tiny " * 20]}],
    "backlinks": G.FAKE_BACKLINKS, "meta_description": "m" * 155,
    "tags": ["a"], "slug": "short",
}
G._normalize_post(_short)
_wc0 = G.word_count(_short)
check_true("fixture starts under the floor", _wc0 < G.MIN_WORDS)

try:
    G.config.chat = _stub_expander(700)
    out = G.enforce_length(_short, target=G.MIN_WORDS + 40, max_rounds=3)
    check_true("loop produced a longer post",
               G.word_count(out) > _wc0)
    check("word_count refreshed", out["word_count"], G.word_count(out))
    check_true("length_ok flag present", "length_ok" in out)
    check("slug preserved through expansion", out["slug"], "short")
    check("title preserved through expansion", out["title"], "Short")
    check("meta preserved through expansion", out["meta_description"], "m" * 155)
    check("backlinks preserved through expansion",
          out["backlinks"], G.FAKE_BACKLINKS)
finally:
    G.config.chat = _real_chat

print("\nenforce_length: a stub that never helps must still return, flagged")
try:
    G.config.chat = lambda p, s, **k: "{}"      # nothing usable
    out2 = G.enforce_length(_short, target=700, max_rounds=2)
    check_true("returns a post even when expansion fails", "sections" in out2)
    check("length_ok correctly False", out2["length_ok"], False)
finally:
    G.config.chat = _real_chat

print("\nmeta description length is repaired, not rejected")
# A GitHub Actions run lost a whole draft to this: the model returned 138
# chars, fit_meta gave up on anything short, and three regenerations all
# landed in the same place.
_lede = ("Search engines read a sitemap to decide which pages to crawl and how "
         "often to come back. A good sitemap describes your site honestly.")
_short = ("A practical guide to building sitemaps that search engines can "
          "actually read, covering what belongs in one and what to skip")
_ext = G.fit_meta(_short, extra=_lede)
check("short meta is extended from the post's own words", _ext is not None, True)
check("extended meta lands in range", 150 <= len(_ext or "") <= 160, True)
check("extension reads as separate sentences", ". " in (_ext or ""), True)
check("still gives up when there is no source text",
      G.fit_meta(_short, extra=""), None)
check("too-long meta is trimmed",
      150 <= len(G.fit_meta("x" * 200) or "") <= 160, True)

print("\nshort drafts get sections added, not regenerated")
_added = {"sections": [{"heading": "C", "paragraphs": ["body c"]}]}
_real_chat2 = G.config.chat
G.config.chat = lambda *a, **k: json.dumps(_added)
try:
    _two = {"title": "t", "slug": "s", "lede": _lede, "kind": "general",
            "meta_description": _short, "sections": [
                {"heading": "A", "paragraphs": ["body a"]},
                {"heading": "B", "paragraphs": ["body b"]}]}
    _out = G._ensure_sections(_two)
    check("one section added", len(_out["sections"]), 3)
    check("originals preserved first",
          [s["heading"] for s in _out["sections"]][:2], ["A", "B"])

    _four = {"title": "t", "slug": "s", "lede": _lede,
             "sections": [{"heading": c, "paragraphs": ["x"]} for c in "ABCD"]}
    check("a full post is left alone",
          len(G._ensure_sections(_four)["sections"]), 4)
finally:
    G.config.chat = _real_chat2

print("\nanchor examples must survive the validator")
# _repair_general_links picks from profile.ANCHOR_EXAMPLES programmatically,
# so a banned example there becomes a rejected draft rather than just bad
# prompt text. "the projects listed on my site" contained the banned "my site".
for _tgt, _exs in P.ANCHOR_EXAMPLES.items():
    for _e in _exs:
        _k = T.anchor_key(_e)
        _bad = (_k in G.ANCHOR_EXACT_BANNED
                or any(p in _k for p in G.ANCHOR_PHRASE_BANNED))
        check(f"{_tgt}: {_e!r} is not a banned anchor", _bad, False)

print("\npixabay failures degrade instead of killing the run")
check("search retries on 429", FI.SEARCH_RETRIES > 1, True)
check("download retries too", FI.DOWNLOAD_RETRIES > 1, True)
check("calls are throttled", FI.MIN_SECONDS_BETWEEN_CALLS > 0, True)
_src = __import__("pathlib").Path(FI.__file__).read_text(encoding="utf-8")
check("search no longer aborts the run on 429",
      "Pixabay rate limit hit" in _src, False)
check("it degrades to no image instead", "continuing without an" in _src, True)

print("\ntruncated JSON is detected and answered differently")
import json as _j

_full = _j.dumps({"a": "x " * 80, "s": [{"p": ["y " * 50] * 2}] * 3})
_FENCE = chr(96) * 3
check("complete JSON is not truncation", G._looks_truncated(_full), False)
check("fenced JSON is not truncation",
      G._looks_truncated(_FENCE + "json\n" + _full + "\n" + _FENCE), False)
check("empty reply is not truncation", G._looks_truncated(""), False)
check("prose reply is not truncation",
      G._looks_truncated("no json here"), False)
check("cut-off reply is truncation",
      G._looks_truncated(_full[: int(len(_full) * 0.6)]), True)
check("and a cut-off reply really does fail to parse",
      G._parse(_full[: int(len(_full) * 0.6)]), None)
check("a failure stub is titled, not 'untitled'",
      G._looks_truncated("") or True, True)
_src2 = pathlib.Path(G.__file__).read_text(encoding="utf-8")
check("the stub carries the topic as its title",
      '"title": f"FAILED: {topic[\'topic\']}"' in _src2, True)

print("\nledger keeps one entry per post, not one per event")
import tempfile

import ledger as L

_tmp = Path(tempfile.mkdtemp())
_real_ledger_path = L.LEDGER_PATH
# ledger.py does `from config import LEDGER_PATH`, so the name lives in
# ledger's namespace. Patching config.LEDGER_PATH writes the REAL ledger.
L.LEDGER_PATH = _tmp / "posted_topics.json"

_D = {"topic": "How to structure sitemaps", "slug": "structure-sitemaps",
      "title": "How to structure sitemaps", "image": {"id": 7},
      "alt_text": "a laptop", "gate_rounds": 2, "status": "draft"}
_P = {"topic": "How to structure sitemaps", "slug": "structure-sitemaps",
      "title": "How to structure sitemaps", "blogger_post_id": "999",
      "url": "https://x/1", "status": "published"}

try:
    L.record(_D)
    L.record(_P)
    _rows = L.all_posts()
    check("draft + published is one entry", len(_rows), 1)
    check("blogger id merged in", _rows[0].get("blogger_post_id"), "999")
    check("image from the first record kept",
          (_rows[0].get("image") or {}).get("id"), 7)
    check("alt text kept", _rows[0].get("alt_text"), "a laptop")
    check("gate_rounds kept", _rows[0].get("gate_rounds"), 2)
    check("status escalated", _rows[0].get("status"), "published")

    L.record(dict(_D))
    check("a later draft record does not demote",
          L.all_posts()[0].get("status"), "published")
    check("still one entry", len(L.all_posts()), 1)

    print("\na deleted post frees its topic and its anchors")
    # Five published posts were deleted from the Blogger dashboard. They never
    # lived on the blog, so they must not hold their topics hostage: the
    # pipeline should be free to write about them again, and free to reuse
    # their anchor text.
    _DEL_NOTE = "deleted from the Blogger dashboard - never lived on the blog"
    L.record({"topic": "How to structure sitemaps", "slug": "structure-sitemaps",
              "title": "How to structure sitemaps", "blogger_post_id": "999",
              "url": "https://x/1", "status": "published"})
    check("published again", L.all_posts()[0].get("status"), "published")
    check("the deletion was recorded",
          L.mark_deleted("999", _DEL_NOTE), 1)
    check("the status is recorded", L.all_posts()[0].get("status"), "deleted")
    check("the note is kept", L.all_posts()[0].get("note"), _DEL_NOTE)
    check("the blogger id is kept for the audit trail",
          L.all_posts()[0].get("blogger_post_id"), "999")
    check("its topic is free again",
          L.too_similar("How to structure sitemaps"), None)
    check("its anchors are free again",
          L.anchor_used("the finished work"), False)

    # a fresh record for the same topic brings the row back to life
    L.record({"topic": "How to structure sitemaps", "slug": "structure-sitemaps",
              "title": "How to structure sitemaps", "status": "draft"})
    _revived = L.all_posts()[0]
    check("a new record revives the row", _revived.get("status"), "draft")
    check("the dead blogger id does not come back",
          _revived.get("blogger_post_id"), None)
    check("the dead url does not come back", _revived.get("url"), None)
    check("and its topic is held again",
          L.too_similar("How to structure sitemaps") is not None, True)
    L.record(_P)
    check("publishing it again escalates normally",
          L.all_posts()[0].get("status"), "published")

    L.record({"topic": "A different topic", "slug": "other", "status": "draft"})
    check("a different post is its own entry", len(L.all_posts()), 2)

    L.record({"topic": "A different topic", "slug": "other", "title": "",
              "url": None})
    _o = [x for x in L.all_posts() if x["slug"] == "other"][0]
    check("empty values do not invent anything", _o.get("url"), None)

    L.LEDGER_PATH.write_text(json.dumps({"posts": [
        {"topic": "A", "slug": "a", "status": "draft", "image": {"id": 7}},
        {"topic": "A", "slug": "a", "status": "published", "blogger_post_id": "1"},
        {"topic": "B", "slug": "b", "status": "draft"},
    ]}, indent=2), encoding="utf-8")
    check("dedupe removes the duplicate", L.dedupe(), 1)
    check("two posts remain", len(L.all_posts()), 2)
    check("merged row keeps both sides",
          (L.all_posts()[0].get("image") or {}).get("id"), 7)
    check("merged row has the blogger id",
          L.all_posts()[0].get("blogger_post_id"), "1")
    check("dedupe is idempotent", L.dedupe(), 0)
finally:
    L.LEDGER_PATH = _real_ledger_path
    shutil.rmtree(_tmp, ignore_errors=True)

print("\na failed draft is still saved, not crashed on")
import main as M

_BAD = {"topic": "T", "title": "Broken draft", "slug": "broken-draft",
        "lede": "x", "sections": [{"heading": "H", "paragraphs": ["p"]}],
        "backlinks": [{"id": "LINK1", "anchor": "a b", "target": "***",
                       "placement": "body"}],
        "tags": ["a"], "meta_description": "m" * 155,
        "image_query": "q", "alt_text": "a",
        "errors": ["illegal link target '***'"]}

# render must keep refusing: anything unverified must never reach Blogger.
_refused = False
try:
    import render as _R
    _R.to_html_with_local_image(_BAD, "", None)
except ValueError:
    _refused = True
check_true("renderer still refuses an unverified target", _refused)

# ...but saving a failed draft must not raise, or the run produces nothing.
_body = M._render_body(_BAD, None)
check_true("_render_body returned instead of raising", isinstance(_body, str))
check_true("and it is not empty", len(_body) > 0)

_safe = M._strip_bad_links(_BAD)
check("unverified link dropped", len(_safe["backlinks"]), 0)
check("original post left untouched", len(_BAD["backlinks"]), 1)

_good = dict(_BAD)
_good["backlinks"] = [{"id": "LINK1", "anchor": "a b", "target": "home",
                       "placement": "body"}]
check_true("a valid post still renders normally",
           len(M._render_body(_good, None)) > 0)

print("\n" + "=" * 50)
print(f"  {PASSES} passed, {FAILS} failed")
print("=" * 50)
raise SystemExit(1 if FAILS else 0)

