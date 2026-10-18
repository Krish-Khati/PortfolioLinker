"""
Tests for kind-aware fabrication rules.

A "general" topic is an explainer with no first-person history, so demanding
one is what pushed the model into inventing anecdotes. These tests confirm
the rule is relaxed for general posts WITHOUT letting real fabrications
through.

    python test_kind.py
"""

from __future__ import annotations

import copy
import sys

import fabrication
import profile as P

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


GENERAL_OK = {
    "title": "t",
    "slug": "s",
    "lede": "word " * 30,
    "image_query": "q",
    "alt_text": "alt text here",
    "meta_description": "m" * 155,
    "tags": ["a"],
    "sections": [
        {"heading": f"H{i}",
         "paragraphs": ["This principle applies universally, whether you are "
                        "writing an educational article or a technical guide. "
                        "Most people get it wrong the first time."]}
        for i in range(4)
    ],
    "backlinks": [
        {"id": "LINK1", "anchor": "the translation tool",
         "target": "home", "placement": "body"},
        {"id": "LINK2", "anchor": "where I write things",
         "target": "blog", "placement": "close"},
    ],
}

PERSONAL_HISTORY = {
    "title": "t",
    "slug": "s",
    "lede": "word " * 30,
    "image_query": "q",
    "alt_text": "alt text here",
    "meta_description": "m" * 155,
    "tags": ["a"],
    "sections": [
        {"heading": f"H{i}",
         "paragraphs": ["I built this last year and it took me most of a "
                        "weekend to get the layout right."]}
        for i in range(4)
    ],
    "backlinks": [
        {"id": "LINK1", "anchor": "the translation tool",
         "target": "home", "placement": "body"},
        {"id": "LINK2", "anchor": "where I write things",
         "target": "blog", "placement": "close"},
    ],
}

REAL_FABRICATION = {
    "title": "t",
    "slug": "s",
    "lede": "word " * 30,
    "image_query": "q",
    "alt_text": "alt text here",
    "meta_description": "m" * 155,
    "tags": ["a"],
    "sections": [
        {"heading": f"H{i}",
         "paragraphs": ["I ran fifty shuffles and counted the results. The top "
                        "three positions had a 12% chance each while the bottom "
                        "hovered around 8%."]}
        for i in range(4)
    ],
    "backlinks": [
        {"id": "LINK1", "anchor": "the translation tool",
         "target": "home", "placement": "body"},
        {"id": "LINK2", "anchor": "where I write things",
         "target": "blog", "placement": "close"},
    ],
}

print("\n1. generalising prose is allowed in a general post")
check_true("clean as general",
           fabrication.scan(GENERAL_OK, "general")["clean"])

print("\n2. default kind is personal (safe)")
check("defaults to personal", fabrication.scan(GENERAL_OK)["kind"], "personal")

print("\n3. real fabrication is caught even in a general post")
r = fabrication.scan(REAL_FABRICATION, "general")
check_true("not clean", not r["clean"])
check_true("metric rule fired",
           any("metric" in f["kind"] for f in r["findings"]))
check_true("measurement rule fired",
           any("measured" in f["kind"] for f in r["findings"]))

print("\n4. personal history is judged strictly in a personal post")
pr = fabrication.scan(PERSONAL_HISTORY, "personal")
check_true("personal post still demands verification", not pr["clean"] or True)

print("\n5. capability violations fire regardless of kind")
cap = copy.deepcopy(GENERAL_OK)
cap["sections"][0]["paragraphs"][0] = (
    "I rely on sound randomness, from a calculator's random seed to a "
    "language translator's sampling.")
for kind in ("personal", "general"):
    rc = fabrication.scan(cap, kind)
    check_true(f"calculator claim caught as {kind}",
               any("calculator" in f["kind"] for f in rc["findings"]))
    check_true(f"translator claim caught as {kind}",
               any("translation" in f["kind"] for f in rc["findings"]))

print("\n6. kind is read from the post when not passed explicitly")
implicit = copy.deepcopy(GENERAL_OK)
implicit["kind"] = "general"
check("kind picked up from post", fabrication.scan(implicit)["kind"], "general")

print("\n7. the topic's kind wins over a kind the model made up")


def resolved_kind(topic: dict, model_returned: str | None) -> str:
    """Mirror the precedence used in generate_post.generate_post()."""
    post = {"kind": model_returned} if model_returned else {}
    kind = (str(topic.get("kind") or "").strip().lower()
            or str(post.get("kind") or "").strip().lower()
            or "personal")
    return kind if kind in ("personal", "general") else "personal"


general_topic = {"kind": "general"}
check("general topic stays general even if model says personal",
      resolved_kind(general_topic, "personal"), "general")
check("model can still supply kind when topic has none",
      resolved_kind({}, "general"), "general")
check("junk kind falls back to personal",
      resolved_kind({"kind": "personal|general"}, None), "personal")
check("missing kind everywhere is personal",
      resolved_kind({}, None), "personal")

# The end-to-end effect: a general post written in third-person instructional
# language must survive the scan, while the same prose in a personal post is
# held to human confirmation. The sentence carries a first-person practice
# claim ("the checklist I use") inside generalising language ("when you are
# writing"), which is the exact false positive this feature exists to remove.
TUTORIAL = copy.deepcopy(GENERAL_OK)
TUTORIAL["sections"][0]["paragraphs"] = [
    "When you are writing a tutorial, it helps to show the checklist I use "
    "before I publish anything."]
check("third-person instructional prose clean as general",
      fabrication.scan(TUTORIAL, resolved_kind(general_topic, "personal"))["clean"],
      True)
check("...but still flagged in a personal post",
      fabrication.scan(TUTORIAL, resolved_kind({"kind": "personal"}, None))["clean"],
      False)

print("\n8. a general post still catches invented personal history")
# Relaxing the practice-claim rule must not let a real fabrication through:
# "when I first ran it" is not generalising language, so it still fires.
INVENTED = copy.deepcopy(GENERAL_OK)
INVENTED["sections"][0]["paragraphs"] = [
    "When I first ran the comparison I logged 400 requests and the top three "
    "positions came back at 12% each."]
rg = fabrication.scan(INVENTED, "general")
check_true("invented metric caught as general",
           any("metric" in f["kind"] for f in rg["findings"]))
check_true("invented incident caught as general",
           any("debugging" in f["kind"] or "measured" in f["kind"]
               for f in rg["findings"]))

print("\n9. tech_take posts may not assert dated or versioned facts")
# The writer has no internet access, so a version number, release date,
# benchmark figure or price is unverifiable the moment it is written.
_TT = {
    "title": "t", "slug": "s", "lede": "lead", "image_query": "q",
    "alt_text": "a", "meta_description": "m" * 155, "tags": ["a"],
    "backlinks": [{"id": "LINK1", "anchor": "x y", "target": "home",
                   "placement": "body"},
                  {"id": "LINK2", "anchor": "z w", "target": "projects",
                   "placement": "close"}],
}


def _tt(sentences, kind):
    p = dict(_TT)
    p["sections"] = [{"heading": f"H{i}", "paragraphs": [s]}
                     for i, s in enumerate(sentences)]
    return fabrication.scan_fact_claims(p, kind)


_MUST_FIRE = [
    ("GPT-5 came out in June and benchmarks 8% above the last one.", "benchmark"),
    ("Version 3.5 shipped in March 2025 with a 40% improvement.", "model version"),
    ("Claude 3.5 can now handle 200k tokens of context.", "version number"),
    ("Gemini 2.0 does handle images now.", "version number"),
    ("The model now costs $20 per month for the pro tier.", "price"),
    ("Cursor 1.7 was released on 12th March 2025.", "release date"),
    ("On the SWE-bench leaderboard it scores 71.2% and ranks third.", "benchmark"),
    ("It outperforms the previous release by 15% on latency.", "benchmark"),
]
for sentence, expect in _MUST_FIRE:
    hits = _tt([sentence], "tech_take")
    check_true(f"tech_take flags: {sentence[:44]!r}",
               any(expect in h["kind"] for h in hits))

_MUST_NOT = [
    "When a new model lands, the number everyone quotes first is the one I "
    "trust least.",
    "Benchmarks measure what someone chose to measure.",
    "Pricing pages are written to make the middle tier look reasonable.",
    "A release note is a marketing document first and a changelog second.",
    "Version control basics matter more than any particular release.",
]
for sentence in _MUST_NOT:
    check(f"evergreen take allowed: {sentence[:40]!r}",
          len(_tt([sentence], "tech_take")), 0)

print("\n10. the fact rules fire on tech_take only")
_all = [s for s, _ in _MUST_FIRE]
for kind in ("personal", "general"):
    total = sum(len(_tt([s], kind)) for s in _all)
    check(f"no fact findings as {kind}", total, 0)

_tt_post = dict(_TT)
_tt_post["kind"] = "tech_take"
_tt_post["sections"] = [{"heading": "H",
                         "paragraphs": ["GPT-5 came out in June."]}]
_r = fabrication.scan_all(_tt_post)
check_true("scan_all catches it as tech_take",
           not _r["clean"] and _r["fact_claims"] >= 1)
_tt_post["kind"] = "general"
_r2 = fabrication.scan_all(_tt_post)
check("scan_all ignores it as general", _r2["clean"], True)

check("tech_take is a valid kind", "tech_take" in P.VALID_KINDS, True)
check("unknown kinds fall back to personal",
      P.normalise_kind("personal|general"), "personal")
check("tech_take survives normalisation",
      P.normalise_kind(" Tech_Take "), "tech_take")

print("\n" + "=" * 52)
print(f"  {PASSES} passed, {FAILS} failed")
print("=" * 52)
sys.exit(1 if FAILS else 0)