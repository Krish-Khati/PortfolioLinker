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

# --------------------------------------------------------------- tech takes
# A tech_take post must not assert a checkable fact about the present-day
# world. The writer has no live internet access, so a version number, a
# release date or a benchmark figure is unverifiable the moment it is written
# and wrong within weeks. These fire only on tech_take posts: elsewhere the
# same phrasing is ordinary prose ("prices range from $10 to $20").
#
# Each pattern is (label, regex). The point is to force a human to confirm,
# not to decide whether the claim happens to be right.
FACT_CLAIM_PATTERNS: list[tuple[str, str]] = [
    # Named release with a version number.
    ("cites a model version",
     r"\b(?:version|v\.?|release|build)\s*\.?\s*\d+(?:\.\d+){1,3}\b"
     r"|\bv\d+\.\d+\b"
     r"|\b\d+\.\d+(?:\.\d+)?\s*(?:preview|rc|beta|alpha|release)\b"),
    # A release/announcement date.
    ("cites a release date",
     r"\b(?:released|launch(?:ed)?|announced|shipped|rolled out|"
     r"debuted|introduced)\s+(?:on|in)\s+"
     r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
     r"|\b(?:january|february|march|april|may|june|july|august|"
     r"september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?"
     r"[,]?\s*\d{4}\b"
     r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:january|february|march|"
     r"april|may|june|july|august|september|october|november|december)"
     r"\s+\d{4}\b"),
    # A benchmark figure or leaderboard claim.
    ("cites a benchmark score",
     r"\b(?:benchmark|benchmarks|bench|elo|score|scores|leaderboard|"
     r"ranking|ranked)\w*\b[^.]{0,60}?\b\d+(?:\.\d+)?\s*%?"
     r"|\b\d+(?:\.\d+)?\s*%?\s*(?:higher|lower|better|worse|more|less)"
     r"\s+(?:than|on)\b"
     r"|\boutperform\w*\b[^.]{0,50}\b\d"
     r"|\b\d+(?:\.\d+)?\s*%\s*(?:better|worse|higher|lower)\b"),
    # A specific price or quota stated as current fact.
    ("cites a current price",
     r"[$€£]\s?\d+(?:[.,]\d{2})?\s*(?:/\s*(?:mo|month|yr|year|user|token|"
     r"1k|1m))?\b[^.]{0,40}\b(?:now|currently|today|as of)\b"
     r"|\b(?:costs?|priced at|charges)\s+[$€£]\s?\d+"),
    # Named-model-with-claims, e.g. attributing a capability to a specific
    # product by name and version.
    ("cites a named model release as fact",
     r"\b(?:gpt|claude|gemini|llama|qwen|mistral|grok|deepseek|"
     r"copilot|codex|cursor|devin)\b[^.]{0,40}\b(?:can|does|now|just|already)\b"),
    # A named product carrying a bare version number: "Claude 3.5", "GPT-4",
    # "Gemini 2.0". The keyword-prefixed pattern above misses these.
    ("names a model with a version number",
     r"\b(?:gpt|chatgpt|claude|gemini|llama|qwen|mistral|grok|deepseek|"
     r"copilot|codex|cursor|devin)\s*[-_]?\d+(?:\.\d+)+\b"
     r"|\b(?:gpt|chatgpt)\s*[-_]?\d+\b(?!\.)"),
    # A stated context window or capability figure.
    ("cites a capability figure",
     r"\b\d+\s?[kKmM]\s*(?:tokens?|context|parameters?)\b"
     r"|\bcontext window of\s+\d+"),
]

# Kinds that must not carry dated or versioned factual assertions.
UNVERIFIABLE_FACT_KINDS = {"tech_take"}


def scan_fact_claims(post: dict[str, Any], kind: str) -> list[dict[str, str]]:
    """Return dated/versioned factual claims. Empty unless kind == tech_take."""
    if kind not in UNVERIFIABLE_FACT_KINDS:
        return []

    chunks: list[tuple[str, str]] = [("lede", post.get("lede", "") or "")]
    for i, s in enumerate(post.get("sections", []) or []):
        label = s.get("heading") or f"section {i + 1}"
        for p in s.get("paragraphs", []) or []:
            chunks.append((label, p or ""))

    findings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, text in chunks:
        if not text:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            s = sentence.strip()
            if not s:
                continue
            for name, pattern in FACT_CLAIM_PATTERNS:
                if re.search(pattern, s, re.I):
                    key = (name, s[:70])
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append({
                        "kind": name,
                        "section": label,
                        "quote": s[:240],
                    })
    return findings

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

    `kind` is "personal", "general" (explainer, no first-person history) or
    "tech_take" (an evergreen framework post about AI tooling). Personal is the
    safe default when unknown.

    Uses profile.normalise_kind so the three kinds live in one place. This
    used to hardcode ("personal", "general") and silently rewrote a tech_take
    post to personal, which quietly disabled the fact-claim tripwires.
    """
    import profile as P

    kind = P.normalise_kind(kind or post.get("kind"))
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


def scan_all(post: dict[str, Any], kind: str | None = None) -> dict[str, Any]:
    """scan() plus the tech_take fact-claim tripwires, as one result.

    Kept separate from scan() so the existing callers and tests keep their
    current behaviour, and so it is obvious at the call site that a tech_take
    post is held to a stricter standard.
    """
    result = scan(post, kind)
    facts = scan_fact_claims(post, result["kind"])
    if facts:
        result["findings"] = result["findings"] + facts
        result["count"] = len(result["findings"])
        result["clean"] = False
    result["fact_claims"] = len(facts)
    return result


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