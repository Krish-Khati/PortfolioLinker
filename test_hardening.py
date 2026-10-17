"""
Unit tests for textnorm + the anchor rules in generate_post.validate().
Run: python test_hardening.py
"""

from __future__ import annotations

import generate_post as G
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

print("\n" + "=" * 50)
print(f"  {PASSES} passed, {FAILS} failed")
print("=" * 50)
raise SystemExit(1 if FAILS else 0)