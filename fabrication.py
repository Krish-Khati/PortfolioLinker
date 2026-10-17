"""
Deterministic fabrication tripwires.

The Groq quality gate scored a post FABRICATION=0 that contained eight invented
first-person claims, including invented percentages. An LLM reviewer cannot be
the only defence against this class of failure, so these regexes scan the prose
mechanically and force human review no matter what the gate said.

The rule is narrow on purpose: we are not deciding whether a sentence is true.
We are detecting the SHAPE of a claim only the writer could have made, so that
some human has to confirm it happened before it goes live.
"""

from __future__ import annotations

import re
from typing import Any

# First-person claims of having done, seen, measured or built something.
# Each pattern is (label, regex). Labels say what tripped, not what is wrong.
INCIDENT_PATTERNS: list[tuple[str, str]] = [
    ("claims a debugging incident",
     r"\b(?:when i (?:first )?(?:ran|tried|built|shipped|deployed)|"
     r"the (?:first|very first) (?:shuffle|version|build|attempt|time) i)"),
    ("claims a specific bug or failure",
     r"\b(?:i (?:noticed|saw|found|discovered|hit|hit upon|ran into|came across)"
     r"|there was a (?:bug|glitch|flaw|issue|mistake))"),
    ("claims to have measured something",
     r"\b(?:i (?:ran|measured|counted|logged|tracked|recorded|tested|benchmarked)"
     r"|test harness|simulation|i profiled|i benchmarked)"),
    ("states a specific metric or percentage",
     r"\b\d+(?:\.\d+)?\s?%|\b\d[\d,]{2,}\s*(?:users|requests|ms|times|rows|"
     r"cards|visits|downloads)\b"),
    ("claims a specific code/infra decision",
     r"\b(?:i (?:seed|used|configured|opted|switched|chose|picked) the|"
     r"i (?:run|deploy) (?:it|this) on|i set up)\b"),
    ("claims authorship of a utility/harness",
     r"\b(?:i (?:wrote|created|built|added|dropped in) a|"
     r"i (?:wrote|created|built) (?:a|an|the) (?:utility|helper|function|"
     r"script|tool|harness|module))"),
    ("claims an outcome was verified",
     r"\b(?:the counts? (?:were|came back)|confirming the|which confirmed|"
     r"verifying that|it (?:worked|passed) for me|which proved)\b"),
    ("claims multiple finished projects",
     r"\bi(?:'|’)?ve built|i have built|"
     r"i(?:'|’)?ve (?:also )?(?:shipped|finished|made)"),
    # Any first-person claim about a habitual practice, present or past. We have
    # no verified list of what Krish actually does, so "I run a chi-square test"
    # is exactly as unverified as "I ran a simulation".
    ("first-person practice claim",
     r"\b(?:i|we)(?:'|’)?ve\b|\b(?:i|we)\b[^.]{0,50}?"
     r"\b(?:apply|applied|use|used|run|ran|check|checked|checks|test|tested|"
     r"ship|shipped|swap|swapped|verify|verified|deploy|deployed|add|added|"
     r"write|wrote|created|build|built|maintain|maintained|rely|relied)\b"),
]

# Claims about what a named project does internally. profile.py knows the
# projects exist; it does not know what their source code does.
INTERNAL_CLAIM = re.compile(
    r"\b(?:the (?:game|app|site|page|project|calculator|translator|tool))"
    r"[^.]{0,50}?\b(?:checks|verifies|tracks|stores|persists|uses|seeds|"
    r"caches|logs|audits|validates)\b", re.I)

# Claims about a project having a capability it does not have.
CAPABILITY_PATTERNS: list[tuple[str, str]] = [
    ("attributes randomness to the calculator",
     r"calculator[^.]{0,60}\brandom"),
    ("attributes sampling to a translation tool",
     r"linguabridge[^.]{0,60}\bsampl|"
     r"translat\w+[^.]{0,60}\bsampl"),
]

# Hedge words that turn a definite claim into a hypothetical. A post full of
# these may be safe, but it is also probably vague.
HEDGES = re.compile(r"\b(?:if you were to|suppose|imagine you|in theory|"
                    r"let's say|hypothetically)\b", re.I)


# Language that marks a sentence as generalising rather than reporting what the
# writer did. "whether you are writing a guide" is not a personal anecdote even
# though it contains a first-person pronoun further along.
GENERALISING = re.compile(
    r"\b(?:you|your|we're|anyone|people|somebody|if you|when you|whether|"
    r"it applies|applies to|generally|typically|in general|most people)\b",
    re.I,
)

# Rules that only make sense for a PERSONAL post. A general explainer has no
# first-person history, so demanding one is what pushed the model into
# inventing anecdotes.
PERSONAL_ONLY_RULES = {"first-person practice claim"}


def scan(post: dict[str, Any], kind: str | None = None) -> dict[str, Any]:
    """Return every tripwire that fires, with the sentence that fired it.

    `kind` is "personal" or "general" (general topics are explainers with no
    first-person history). Personal is the safe default when unknown.
    """
    kind = (str(kind or post.get("kind") or "").strip().lower() or "personal")
    if kind not in ("personal", "general"):
        kind = "personal"
    chunks: list[tuple[str, str]] = [("lede", post.get("lede", "") or "")]
    for i, s in enumerate(post.get("sections", []) or []):
        label = s.get("heading") or f"section {i + 1}"
        for p in s.get("paragraphs", []) or []:
            chunks.append((label, p or ""))

    findings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    all_patterns = INCIDENT_PATTERNS + CAPABILITY_PATTERNS

    for label, text in chunks:
        if not text:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            s = sentence.strip()
            if not s:
                continue
            # Sentences carrying a link marker are about the portfolio, which is
            # verified to exist, and are already constrained by the anchor
            # rules in generate_post.validate(). Skip them so the broad
            # practice-claim pattern cannot fire on "is where I write these up".
            if "[[LINK" in s:
                continue
            for name, pattern in all_patterns:
                if name in PERSONAL_ONLY_RULES and kind == "general":
                    continue
                if re.search(pattern, s, re.I):
                    # In a general post, a first-person match inside
                    # generalising language is a false positive.
                    if (kind == "general" and name in PERSONAL_ONLY_RULES
                            and GENERALISING.search(s)):
                        continue
                    key = (name, s[:70])
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append({
                        "kind": name,
                        "section": label,
                        "quote": s[:240],
                    })
            if INTERNAL_CLAIM.search(s):
                key = ("describes unseen project internals", s[:70])
                if key not in seen:
                    seen.add(key)
                    findings.append({
                        "kind": "describes unseen project internals",
                        "section": label,
                        "quote": s[:240],
                    })

    return {
        "clean": not findings,
        "count": len(findings),
        "findings": findings,
        "kind": kind,
    }


def verdict_text(result: dict[str, Any]) -> str:
    if result["clean"]:
        return "No fabrication tripwires fired."
    lines = [f"{result['count']} claim(s) need confirmation before publishing:"]
    for f in result["findings"]:
        lines.append(f"  [{f['kind']}] ({f['section']})")
        lines.append(f"    \"{f['quote']}\"")
    lines += [
        "",
        "These are not proven false. They are claims only Krish can confirm.",
        "Edit the post to remove or genericise them, or confirm they are real.",
    ]
    return "\n".join(lines)