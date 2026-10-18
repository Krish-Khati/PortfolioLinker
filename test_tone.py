"""
Tests for the tone rules added after manual review of a passing draft:
hedges in link sentences, filler verbs in anchors, and lede length.

    python test_tone.py
"""

from __future__ import annotations

import copy
import sys

import generate_post as G

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


BASE = {
    "title": "A reasonable title",
    "slug": "s",
    "image_query": "programming code",
    "alt_text": "source code on a dark editor",
    "meta_description": "m" * 155,
    "tags": ["t1", "t2", "t3"],
    "lede": "A tutorial shows you one path through a problem. "
            "Building it yourself is where the real decisions live.",
    "sections": [
        {"heading": "Scope", "paragraphs": ["word " * 70]},
        {"heading": "Decisions", "paragraphs": ["Some plain prose with [[LINK1]] inside it."]},
        {"heading": "Practice", "paragraphs": ["word " * 70]},
        {"heading": "Close", "paragraphs": ["A closing note carrying [[LINK2]] at the end."]},
    ],
    "backlinks": [
        {"id": "LINK1", "anchor": "the fitness temple layout",
         "target": "home", "placement": "body"},
        {"id": "LINK2", "anchor": "where I write things up",
         "target": "blog", "placement": "close"},
    ],
}


def errs(post):
    p = copy.deepcopy(post)
    G._normalize_post(p)
    p["word_count"] = G.word_count(p)
    return G.validate(p)


print("\n0. baseline post is clean apart from length")
# This fixture is deliberately short on total words; length is covered by
# test_hardening. Only assert that no TONE rule fires on it.
_tone_only = lambda es: [e for e in es if "words" not in e.split("(")[0]
                         and "need 600" not in e]
check("no tone errors", _tone_only(errs(BASE)), [])

print("\n1. lede length 25-65 words")


def lede_words(n):
    """Exactly n words, each distinct enough to avoid de-duplication."""
    base = ("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu "
            "nu xi omicron pi rho sigma tau upsilon phi chi psi omega "
            "one two three four five six seven eight nine ten").split()
    return " ".join(base[:n]) if n <= len(base) else \
        " ".join(base + [f"w{i}" for i in range(n - len(base))])


for n, want in ((15, True), (30, False), (55, False), (70, False), (96, True)):
    p = copy.deepcopy(BASE)
    p["lede"] = lede_words(n)
    got = any("lede is" in e for e in errs(p))
    check(f"lede of {n} words flagged={want}", got, want)

print("\n2. hedges inside the link sentence")
for hedge, want in (("feel free to", True),
                    ("do not hesitate to", True),
                    ("don't hesitate to", True),
                    ("you are welcome to", True),
                    ("hop on over", True),
                    ("why not", True),
                    ("head over to", True),
                    ("plainly", False),
                    ("you might notice", False)):
    p = copy.deepcopy(BASE)
    p["sections"][1]["paragraphs"][0] = f"See it {hedge} [[LINK1]] here."
    got = any("hedge" in e for e in errs(p))
    check(f"hedge {hedge!r} flagged={want}", got, want)

print("\n3. hedge outside a link sentence is not flagged")
p = copy.deepcopy(BASE)
p["sections"][0]["paragraphs"][0] = "Feel free to disagree with this. " + "word " * 60
got = any("hedge" in e for e in errs(p))
check("hedge in non-link prose is allowed", got, False)

print("\n4. filler verbs in anchor text")
for anchor, want in (("browse through my work", True),
                     ("my repository collection", True),
                     ("all my projects", True),
                     ("my archive of code", True),
                     ("have a look at my code", True),
                     ("the fitness temple layout", False),
                     ("how I picked a shuffle algorithm", False)):
    p = copy.deepcopy(BASE)
    p["backlinks"][0]["anchor"] = anchor
    got = any("banned phrase" in e or "generic SEO filler" in e for e in errs(p))
    check(f"anchor {anchor!r} flagged={want}", got, want)

print("\n5. the exact phrases from the reviewed draft")
# These are verbatim from a draft that passed review but read badly.
p = copy.deepcopy(BASE)
p["backlinks"][1]["anchor"] = "my repository collection"
p["sections"][3]["paragraphs"][0] = (
    "If you want to see more of what I build, feel free to browse through "
    "[[LINK2]] to see how these come together in practice.")
es = errs(p)
check_true("hedge caught on the real sentence",
           any("feel free to" in e for e in es))
check_true("filler anchor caught on the real anchor",
           any("banned phrase" in e or "generic SEO filler" in e for e in es))

print("\n6. AI tells and tidy transitions are rejected")
for phrase, want in (("This improves the user experience overall.", True),
                     ("The solution is seamless and robust.", True),
                     ("It's worth noting that this matters.", True),
                     ("In this article we will explore error handling.", True),
                     ("Let's explore how this works in practice.", True),
                     ("Now that we've covered the basics, let's move on.", True),
                     ("It navigates the complexities of validation.", True)):
    p = copy.deepcopy(BASE)
    p["lede"] = phrase + " " + lede_words(30)
    p["sections"][0]["paragraphs"][0] = phrase
    got = any("AI filler" in e or "AI transition" in e for e in errs(p))
    check(f"{phrase[:34]!r} flagged={want}", got, want)

print("\n7. a lede cut mid-sentence is rejected")
p = copy.deepcopy(BASE)
p["lede"] = "Console apps break unless you plan for failure from the very first"
check_true("dangling lede caught",
           any("mid-sentence" in e for e in errs(p)))
p["lede"] = "Console apps break unless you plan for failure from line one."
check("complete lede passes that check",
      any("mid-sentence" in e for e in errs(p)), False)

print("\n8. the length repair must not leave a fragment")
_flat = dict(BASE)
_flat["lede"] = ("Console applications break the moment an unexpected input "
                 "hits them unless you plan for failure from the very first "
                 "line of code when building software for the terminal.")
_repaired = copy.deepcopy(_flat)
G._repair(_repaired)
check_true("over-long lede left whole rather than truncated mid-clause",
           _repaired["lede"].rstrip().endswith((".", "!", "?")))
print(f"      lede now: {_repaired['lede'][:70]}...")

print("\n9. monotonous rhythm is rejected")
# Every sentence long and same-shaped: the loudest "this was generated" tell.
_mono = " ".join(
    f"The system provides a comprehensive and robust mechanism for handling "
    f"the various conditions which may arise during operation of the tool."
    for _ in range(14))
p = copy.deepcopy(BASE)
p["lede"] = lede_words(30)
p["sections"][0]["paragraphs"][0] = _mono
check_true("uniform long rhythm caught",
           any("rhythm" in e for e in errs(p)))
# Varied prose from the real reviewed draft must NOT trip it.
_varied = ("Completion is a skill on its own. Almost nothing else trains it. "
           "Most tutorials end when the video ends, so the exercise never "
           "reaches the state where it has to work without you watching. "
           "Shipping is the only reliable way I know to find the gap between "
           "having seen something work and understanding why it worked. "
           "That gap is the whole point. It is uncomfortable and it is also "
           "where most of the actual learning happens, which is why it is "
           "worth sitting with rather than skipping to the next tutorial.")
p = copy.deepcopy(BASE)
p["lede"] = lede_words(30)
p["sections"][0]["paragraphs"][0] = _varied
check("varied human rhythm allowed",
      any("rhythm" in e for e in errs(p)), False)

print("\n" + "=" * 52)
print(f"  {PASSES} passed, {FAILS} failed")
print("=" * 52)
sys.exit(1 if FAILS else 0)