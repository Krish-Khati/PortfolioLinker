"""
Step 4 — quality_gate.py

The second opinion. A fresh Groq pass reads the finished prose and answers the
four questions the mechanical validator in generate_post.py cannot:

  1. Does it read AI-generated?
  2. Is there any fabricated personal claim?
  3. Are there exactly 2 natural backlinks?
  4. Is the image relevant?

Everything it flags is a hard fail. The caller runs at most two attempts; if
both fail the post goes to drafts/ for human review and is never published.
"""

from __future__ import annotations

import json
import re
from typing import Any

import config
import profile as P
import textnorm

PASS = "pass"
FAIL = "fail"
MAX_ATTEMPTS = 2

SYSTEM = """You are the last checkpoint before a blog post goes live on a real
Blogger account. You are adversarial. Your job is to find the reason to reject.

You return STRICT JSON only. No fences, no prose, no encouragement.

Default to failing. A post only passes if nothing you can name is wrong with
it. Do not reward effort. Do not soften. Do not invent problems either - if a
check genuinely passes, say it passes."""


# A model problem string that alleges a link target is not on the approved
# list. Kept deliberately narrow: it must actually be about approval, and must
# NOT also be an anchor-quality complaint, which stays a valid judgement.
_APPROVAL_COMPLAINT = re.compile(
    r"(?:not\s+(?:in|on)\s+(?:the\s+)?(?:explicit\s+)?(?:list\s+of\s+)?"
    r"(?:approved|allow(?:ed)?|verified)"
    r"|(?:approved|allow(?:ed)?|verified)\s+(?:url|link|page|list)"
    r"|unapproved|unverif(?:ied|iable)\s+(?:url|link))",
    re.I,
)

_ANCHOR_QUALITY = re.compile(
    r"(?:anchor\s+text|filler|seo|reads?\s+(?:like|slightly)|forced|"
    r"promote|awkward|natural)",
    re.I,
)


def _is_url_approval_complaint(problem: str) -> bool:
    return bool(_APPROVAL_COMPLAINT.search(problem)) and not \
        _ANCHOR_QUALITY.search(problem)


def _facts_block() -> str:
    """Everything the writer is actually allowed to have done."""
    projects = "\n".join(f"  - {n}: {p['is']}" for n, p in P.PROJECTS.items())
    return f"""
WHAT IS TRUE ABOUT THE WRITER:
  - Krish Khati, Class 12 student from Pundri, Kaithal, Haryana. Self-taught,
    no formal training, no degree, no job.
  - Skills he actually has: Python, C, C++, web development, AI tools,
    Claude Code, Supabase, Git/GitHub, video editing, content creation.
  - Real projects:
{projects}
  - Also built a Python chatbot with Supabase auth and vector search.
  - His main public pages (listed for reference when writing prose. This is
    NOT an allow-list and is deliberately incomplete - judging a link against
    this list is a mistake. See the IMPORTANT note under check 3):
      https://krish-portfolio-dev.vercel.app/
      https://krish-portfolio-dev.vercel.app/blog
      https://krish-portfolio-dev.vercel.app/projects
      https://krish-portfolio-dev.vercel.app/faq
      https://github.com/krish-khati
""".strip()


def _build_prompt(post: dict[str, Any], image: dict[str, Any] | None) -> str:
    body: list[str] = [post.get("lede", "")]
    for s in post.get("sections", []) or []:
        body.append(f"\n## {s.get('heading', '')}")
        body.extend(s.get("paragraphs", []) or [])
    prose = "\n".join(body)

    links = "\n".join(
        f"  {l.get('id')} ({l.get('placement')}): \"{l.get('anchor')}\" -> "
        f"{P.LINK_TARGETS.get(l.get('target'), l.get('target'))}"
        for l in post.get("backlinks", [])
    )

    if image:
        img_block = (
            f"IMAGE CHOSEN:\n"
            f"  pixabay id   : {image.get('id')}\n"
            f"  pixabay tags : {image.get('tags')}\n"
            f"  search query : {image.get('search_query')}\n"
            f"  alt text     : {post.get('alt_text')}\n"
            f"  dimensions   : {image.get('width')}x{image.get('height')}"
        )
    else:
        img_block = "IMAGE CHOSEN: none yet"

    return f"""{_facts_block()}

==============================================================
POST UNDER REVIEW
  title : {post.get('title')}
  meta  : {post.get('meta_description')}
  tags  : {', '.join(post.get('tags', []))}
  words : {post.get('word_count')}  (target 600-900)
  links :
{links}

{img_block}

PROSE:
{prose}
==============================================================

Check all four things and be specific.

1. AI_SLOP
   Does it read as machine-written? Look for: throat-clearing openers, ideas
   restated in different words, paragraphs that exist only to be transitional,
   tricolour or alliterative padding, every sentence the same length, a closing
   paragraph that just summarises, uniformly "balanced" sentence rhythm.
   Quote the single worst sentence if you find one.

2. FABRICATION - this is the most important check
   Does the post assert something happened to the writer that did not happen,
   or claim a tool/stack/detail not listed as true above? Watch specifically for
   invented debugging stories, invented incidents, invented numbers or metrics,
   invented clients, and stack claims that were never made.
   Quote EVERY offending sentence verbatim and say which claim is unsupported.
   If there are none, say so plainly - do not invent one to look thorough.

3. BACKLINKS
   Exactly two? One genuinely mid-article and natural, one a soft close?
   Do the anchor texts read like something a person typed, or like SEO filler?
   Is either one contextually wrong - e.g. a link whose anchor promises
   something the page does not have?

   IMPORTANT - do not second-guess the URL allow-list. Every href printed
   below came from a verified table in the code. All of them are approved
   pages. Do NOT report a URL as "not in the approved list"; only judge
   whether the ANCHOR TEXT honestly describes that page. A project subpage
   such as /projects/fitness-temple is always legitimate.

4. IMAGE
   Given the Pixabay tags and search query, is the image a sensible companion
   for this article? Also: does the alt text describe the TAGS, or does it
   describe something else entirely?

Return exactly:
{{
  "verdict": "pass" or "fail",
  "urls_all_approved": true,
  "scores": {{
    "ai_slop": {{"score": 0-10, "note": "..."}},
    "fabrication": {{"score": 0-10, "note": "...", "quotes": ["..."]}},
    "backlinks": {{"score": 0-10, "note": "..."}},
    "image": {{"score": 0-10, "note": "..."}}
  }},
  "problems": ["short list of concrete, fixable problems"],
  "fix_instructions": "one paragraph telling the writer exactly what to change"
}}

Scoring: 0 = flawless, 10 = unacceptable.
Set verdict to "fail" if ANY score is 6 or higher, or if problems is non-empty.
Always set "urls_all_approved" to true unless a href is genuinely unrecognised.
"""


def _parse(raw: str) -> dict[str, Any]:
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned[cleaned.find("{"): cleaned.rfind("}") + 1])


def review(post: dict[str, Any], image: dict[str, Any] | None) -> dict[str, Any]:
    """Run the gate. Returns the parsed verdict dict, plus a normalised summary.

    Temperature is pinned near-greedy. At 0.25 this gate was measured 7 times
    on identical text: 6 runs returned byte-identical scores, and 1 run
    returned BACKLINKS=8 on anchor text the other 6 scored 0. A reviewer that
    fails a draft one time in eight turns a publish attempt into a coin flip,
    and the failure mode is always the expensive one - a good draft thrown
    away. There is no creativity to buy here, only a repeatable verdict.
    """
    raw = config.chat(_build_prompt(post, image), SYSTEM,
                      temperature=0.1, max_tokens=2600, as_json=True)
    try:
        data = _parse(raw)
    except (json.JSONDecodeError, ValueError):
        return {
            "verdict": FAIL,
            "scores": {},
            "problems": ["quality gate returned unparseable JSON"],
            "fix_instructions": "",
            "gate_error": True,
        }

    scores = data.get("scores", {}) or {}
    verdicts = {}
    for key, label in (("ai_slop", "AI_SLOP"), ("fabrication", "FABRICATION"),
                       ("backlinks", "BACKLINKS"), ("image", "IMAGE")):
        s = (scores.get(key) or {})
        try:
            verdicts[label] = int(s.get("score", 10))
        except (TypeError, ValueError):
            verdicts[label] = 10

    problems = [str(p) for p in (data.get("problems") or [])]

    # A complaint that a URL is not approved is mechanically checkable, and it
    # is the single most common way this gate fails a good draft. Measured on
    # one draft: 3 of 6 runs returned BACKLINKS=8, and the stated reason was
    # "/projects/calculator is not in the explicit list of approved URLs" -
    # which is false. The list in the facts block is deliberately incomplete,
    # and /projects/calculator comes from the verified LINK_TARGETS table just
    # like the five URLs the block does list.
    #
    # So decide it deterministically rather than by vote. If every backlink
    # target is in the verified table, any approval complaint is false and is
    # dropped. Anchor-quality complaints are left alone: those are the gate's
    # actual job and the model is entitled to make them.
    unapproved = [l.get("target") for l in post.get("backlinks", []) or []
                  if l.get("target") not in P.LINK_TARGETS]
    discarded = 0
    if not unapproved and problems:
        before = len(problems)
        problems = [p for p in problems if not _is_url_approval_complaint(p)]
        discarded = before - len(problems)
        if discarded:
            print(f"[gate] discarded {discarded} URL-approval complaint(s): "
                  "all link targets are in the verified table")

    # Dropping the complaint is not enough on its own: the numeric score still
    # fails the post on its own. We cannot know what the "true" backlink score
    # would have been, but we do know the premise behind it was false, so that
    # one dimension stops being decisive. It is capped below the fail line
    # rather than zeroed, so a genuinely bad anchor still shows up in the
    # report. Nothing else is touched - AI_SLOP, FABRICATION, IMAGE and the
    # deterministic tripwires below all still fail the post on their own.
    if discarded and verdicts.get("BACKLINKS", 0) >= 6:
        verdicts["BACKLINKS_APPROVAL_DISCOUNTED"] = verdicts["BACKLINKS"]
        verdicts["BACKLINKS"] = 5

    import fabrication

    scan = fabrication.scan_all(post, post.get("kind"))
    verdicts["FABRICATION_TRIPWIRES"] = 0 if scan["clean"] else 9
    if not scan["clean"]:
        for f in scan["findings"]:
            problems.append(
                f"UNVERIFIABLE CLAIM ({f['kind']}, in '{f['section']}'): "
                f"\"{f['quote'][:150]}\""
            )

    # enforce the rule locally instead of trusting the model's own verdict.
    # If we just disproved the premise behind its complaints, its bare verdict
    # is ignored too - otherwise the override above would change nothing.
    worst = max(verdicts.values()) if verdicts else 10
    failed = (worst >= 6 or bool(problems)
              or (data.get("verdict") == FAIL and not discarded))

    return {
        "verdict": FAIL if failed else PASS,
        "scores": verdicts,
        "problems": problems,
        "fix_instructions": data.get("fix_instructions", ""),
        "details": scores,
        "tripwires": scan,
        "gate_error": False,
    }


def check(post: dict[str, Any], image: dict[str, Any] | None,
          attempts: int = MAX_ATTEMPTS) -> dict[str, Any]:
    """Run the gate up to `attempts` times. Returns the last verdict either way.

    A pass on any attempt is a pass. Two fails mean the post goes to drafts/.
    """
    history: list[dict[str, Any]] = []

    for attempt in range(1, attempts + 1):
        try:
            result = review(post, image)
        except Exception as exc:  # noqa: BLE001 - LLM/network failures
            print(f"[warn] quality gate attempt {attempt} failed: "
                  f"{exc.__class__.__name__}: {exc}")
            result = {
                "verdict": FAIL,
                "scores": {},
                "problems": [f"gate could not run: {exc.__class__.__name__}"],
                "fix_instructions": "",
                "gate_error": True,
            }

        history.append(result)
        sc = result["scores"]
        summary = "  ".join(f"{k}={v}" for k, v in sc.items()) or "no scores"
        print(f"[gate {attempt}/{attempts}] {result['verdict'].upper()}  {summary}")

        if result["verdict"] == PASS:
            return {"passed": True, "attempts": attempt, "history": history}

        for p in result["problems"][:6]:
            print(f"    - {p}")

    return {"passed": False, "attempts": attempts, "history": history}


def summarise(result: dict[str, Any]) -> str:
    """One-paragraph summary of the failing attempt, for the drafts/ note."""
    last = result["history"][-1]
    if last.get("gate_error"):
        return "Quality gate could not complete (model unavailable)."
    lines = [f"verdict: {last['verdict']}"]
    for k, v in last["scores"].items():
        lines.append(f"  {k}: {v}/10")
    if last["problems"]:
        lines.append("problems:")
        lines += [f"  - {p}" for p in last["problems"]]
    trip = last.get("tripwires")
    if trip and not trip.get("clean"):
        import fabrication

        lines += ["", fabrication.verdict_text(trip)]
    if last.get("fix_instructions"):
        lines += ["suggested fixes:", textnorm.normalize(last["fix_instructions"])]
    return "\n".join(lines)