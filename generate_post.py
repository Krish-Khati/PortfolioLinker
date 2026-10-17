"""
Step 2 — generate_post.py

Turns one topic into a full post: title, lede, 3-5 sections, exactly two
backlinks with fresh anchor text, meta description, tags, slug, image query
and alt text.

Inline link placement is done with [[LINK1]] / [[LINK2]] markers inside the
paragraph text, which the renderer swaps for real anchors. That makes "exactly
two links, one mid-article, one soft close" mechanically checkable instead of
something we have to trust the model about.

    python generate_post.py                 # generate from a random pooled topic
    python generate_post.py --topic "..."   # generate from a topic you type
"""

from __future__ import annotations

import argparse
import json
import random
import re
from typing import Any

import config
import ledger
import textnorm
import profile as P

MIN_WORDS, MAX_WORDS = 600, 900

# Token ceilings. gpt-oss models emit reasoning that counts against
# max_tokens, so a large ceiling mostly buys wasted reasoning. These are sized
# for ~900 words of prose plus room to think.
MAX_GEN_TOKENS = 3200
MAX_EXPAND_TOKENS = 2600
MIN_SECTIONS, MAX_SECTIONS = 3, 5

# ---------------------------------------------------------------- anchors
# Tier 1: reject the anchor if the whole thing IS one of these.
ANCHOR_EXACT_BANNED = {
    "click here", "here", "read more", "link", "this", "that", "portfolio",
    "my portfolio", "check it out", "source code", "learn more", "website",
    "blog", "my blog", "visit", "visit site", "click", "more", "post",
    "website here", "my website", "my site", "homepage", "home page",
}

# Tier 2: reject if the anchor CONTAINS any of these as a phrase. This is the
# substring check that tier 1 misses - "my portfolio home" is not literally
# "my portfolio", but it is the same lazy default and must not ship.
ANCHOR_PHRASE_BANNED = [
    "click here", "click this", "read more", "read this", "check it out",
    "check out", "learn more", "find out more", "my portfolio", "the portfolio",
    "my website", "my site", "my homepage", "my home page", "source code",
    "the code", "this link", "this page", "this post", "this article",
    "this video", "this website", "this blog", "here you", "portfolio",
    "website", "webpage", "homepage", "home page", "github profile",
    "my github", "visit here", "see here", "link below", "below",
    # filler verbs and padding that make an anchor read like filler
    "browse through", "browse", "have a look", "take a look", "check out",
    "go through", "look at", "look over", "dive into", "have a look at",
    "repository collection", "my repositories", "repositories",
    "all my code", "all my projects", "complete project",
    "project archive", "my archive", "collection", "showcase",
]

# Hedges and politeness padding that make a link sentence read as marketing.
# Checked in the sentence carrying the link, not just the anchor.
HEDGE_PHRASES = [
    "feel free to", "do not hesitate to", "don't hesitate to", "you are welcome to",
    "don't miss", "do not miss", "make sure to check out", "be sure to check",
    "why not", "if you want to know more", "if you'd like to know more",
    "if you would like to know more", "hop on over", "head over to",
    "without further ado", "as always",
]

# Possessive + generic noun ("my repository", "my collection", "my stuff").
# These slip past both lists above because the generic noun is not itself
# banned, so the whole phrase needs a pattern rather than a word list.
GENERIC_NOUNS = (
    "repository|repositories|repo|repos|collection|collections|stuff|things|"
    "work|works|projects|project|content|posts|post|blog|blogs|site|sites|"
    "page|pages|writing|writings|code|codes|portfolio|stuff|folder|files|"
    "archive|archives|list|lists|stuff"
)
GENERIC_OWNERSHIP = re.compile(
    r"\b(?:my|mine|our)\s+(?:\w+\s+){0,1}(?:" + GENERIC_NOUNS + r")\b",
    re.I,
)

MIN_ANCHOR_WORDS, MAX_ANCHOR_WORDS = 2, 7

# The lede is the first thing a reader sees. Long ledes drift into essay voice
# and bury the point, which is the failure mode flagged in review.
MIN_LEDE_WORDS, MAX_LEDE_WORDS = 25, 80

SYSTEM = """You write blog posts for an independent developer who publishes on
his own Blogger blog. The posts must read like a real person wrote them, not
like a marketing team.

You return STRICT JSON and nothing else. No markdown fences, no commentary.

Voice rules:
- First person, plain and conversational. Short sentences. Contractions.
- Never open with throat-clearing. Get to the point in the first sentence.
- No emoji unless it is genuinely how the writer would type.
- No "In today's world", no "Let's dive in", no tricolour lists.
- Do not summarise the article again at the end.

Honesty rules, and these matter more than anything else in this prompt:
- You may explain how something works, why a practice exists, what a
  technique is good for. All of that is safe.
- You may NOT invent something that happened to the writer: no debugging
  stories, no client work, no metrics, no numbers, no quotes from anyone.
- When you want a concrete example, make it a hypothetical the writer is
  reasoning about, not a memory they supposedly have."""


# ------------------------------------------------------------------ helpers
def _wc(text: str) -> int:
    """Word count used by BOTH validation and repair, so they never disagree."""
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def word_count(post: dict[str, Any]) -> int:
    chunks: list[str] = [post.get("lede", "") or ""]
    for s in post.get("sections", []) or []:
        chunks.append(s.get("heading", "") or "")
        chunks.extend(s.get("paragraphs", []) or [])
    text = " ".join(chunks)
    return len(re.findall(r"\b[\w'-]+\b", text))


def _slugify(title: str) -> str:
    return textnorm.slugify(title)


PARAGRAPH_KEYS = ("paragraphs", "body", "content", "text", "paras")


def _paragraph_list(section: dict[str, Any]) -> list[str]:
    """Pull paragraphs out of whatever key the provider used.

    Gemini returns section content under "body"; Groq uses "paragraphs".
    Both may arrive as a single string, in which case blank-line-separated
    blocks are the paragraphs.
    """
    for key in PARAGRAPH_KEYS:
        if key in section:
            val = section[key]
            break
    else:
        return []

    if isinstance(val, str):
        chunks = [c.strip() for c in re.split(r"\n\s*\n", val) if c.strip()]
        # a single blob with no blank lines is one paragraph
        return chunks or ([val.strip()] if val.strip() else [])

    if isinstance(val, list):
        out = []
        for p in val:
            if isinstance(p, str):
                # an element may itself be a multi-paragraph blob
                out += [c.strip() for c in re.split(r"\n\s*\n", p) if c.strip()]
            elif isinstance(p, dict):
                for k in PARAGRAPH_KEYS:
                    if isinstance(p.get(k), str) and p[k].strip():
                        out += [c.strip() for c in
                                re.split(r"\n\s*\n", p[k]) if c.strip()]
                        break
            elif isinstance(p, (int, float)):
                out.append(str(p))
        return out
    return []


def coerce_post(raw: Any) -> dict[str, Any]:
    """Repair whatever shape the model returned into the schema we expect.

    Providers disagree on key names (Groq: "paragraphs", Gemini: "body") and
    models intermittently return "sections": ["a heading", "some text"]. Without
    this the whole run dies with an AttributeError, or worse, silently publishes
    a post whose body is empty.
    """
    if not isinstance(raw, dict):
        return {}

    def as_text(v) -> str:
        if isinstance(v, str):
            return v
        if isinstance(v, (int, float)):
            return str(v)
        return ""

    out = dict(raw)

    # ---- backlinks: models sometimes return a raw URL instead of the key ----
    url_to_key = {v: k for k, v in P.LINK_TARGETS.items()}
    if isinstance(out.get("backlinks"), list):
        for l in out["backlinks"]:
            if isinstance(l, dict):
                tgt = l.get("target")
                if isinstance(tgt, str) and tgt.startswith("http"):
                    l["target"] = url_to_key.get(tgt.rstrip("/"), tgt)

    # ---- sections ----
    sections = out.get("sections")
    fixed_sections = []
    if isinstance(sections, list):
        for s in sections:
            if isinstance(s, dict):
                paras = _paragraph_list(s)
                heading = as_text(s.get("heading") or s.get("title"))
                if paras or heading:
                    fixed_sections.append({"heading": heading,
                                           "paragraphs": paras})
            elif isinstance(s, str) and s.strip():
                # a bare string is either a heading or a paragraph
                fixed_sections.append({"heading": "", "paragraphs": [s]})
    out["sections"] = fixed_sections

    # ---- backlinks ----
    links = out.get("backlinks")
    fixed_links = []
    if isinstance(links, list):
        for i, l in enumerate(links):
            if not isinstance(l, dict):
                continue
            lid = as_text(l.get("id")).upper()
            if lid not in {"LINK1", "LINK2"}:
                lid = f"LINK{i + 1}"
            fixed_links.append({
                "id": lid,
                "anchor": as_text(l.get("anchor")),
                "target": as_text(l.get("target")),
                "placement": as_text(l.get("placement")) or
                             ("body" if i == 0 else "close"),
            })
    out["backlinks"] = fixed_links

    # ---- scalars and tags ----
    for field in ("title", "lede", "meta_description", "image_query",
                  "alt_text", "slug"):
        out[field] = as_text(out.get(field))

    tags = out.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    out["tags"] = [as_text(t) for t in (tags or []) if as_text(t).strip()]

    return out


def _normalize_post(post: dict[str, Any]) -> None:
    """Fold exotic dashes/spaces across the whole post, in place.

    Done before validation so word counts and anchor comparisons are exact.
    """
    def fix(s: str) -> str:
        return textnorm.normalize(s) if isinstance(s, str) else s

    for field in ("title", "lede", "meta_description", "image_query", "alt_text"):
        if field in post:
            post[field] = fix(post[field])

    for s in post.get("sections", []) or []:
        if not isinstance(s, dict):
            continue
        if isinstance(s.get("heading"), str):
            s["heading"] = fix(s["heading"])
        if isinstance(s.get("paragraphs"), list):
            s["paragraphs"] = [fix(p) if isinstance(p, str) else "" for p in s["paragraphs"]]

    for l in post.get("backlinks", []) or []:
        if isinstance(l, dict) and isinstance(l.get("anchor"), str):
            l["anchor"] = fix(l["anchor"])

    if isinstance(post.get("tags"), list):
        post["tags"] = [textnorm.slugify(str(t)) for t in post["tags"]]

    post["meta_description"] = textnorm.meta_safe(post.get("meta_description", ""))
    post["slug"] = textnorm.slugify(post.get("slug") or post.get("title", ""))


def _targets_block() -> str:
    lines = []
    for k, v in P.LINK_TARGETS.items():
        ex = P.ANCHOR_EXAMPLES.get(k, [])
        lines.append(f"  {k} = {v}")
        if ex:
            lines.append(f"      good anchors: {'; '.join(ex)}")
    return "\n".join(lines)


def _build_prompt(topic: dict[str, Any], prior_anchors: list[str],
                  feedback: str = "") -> str:
    avoid = (
        "\n".join(f'  - "{a}"' for a in prior_anchors)
        if prior_anchors
        else "  (no previous posts yet - but still avoid generic anchors below)"
    )
    body_hints = ", ".join(P.BODY_TARGET_HINTS)
    close_hints = ", ".join(P.CLOSE_TARGET_HINTS)

    return f"""{P.ground_truth()}

{P.scope_block()}

{P.published_block()}

{P.bans_block()}

==============================================================
TOPIC TO WRITE ABOUT
  {topic['topic']}
  Angle: {topic['angle']}
  image_query suggestion: {topic.get('image_query', '')}
==============================================================

LENGTH: {MIN_WORDS}-{MAX_WORDS} words in total, counting the lede, every heading
and every paragraph. Hard limit. Aim for ~700.

This is the single most common failure, so budget explicitly:
  lede            45-60 words
  each section    120-160 words, across 2-3 paragraphs
  4 sections      roughly 550-640 words of body
  total           650-750 words
Short sections are a failure, not a stylistic choice. Do not summarise.

STRUCTURE:
- "title": a real, specific title. Not clickbait. No colon-heavy SEO titles.
- "lede": ONE or TWO sentences, {MIN_LEDE_WORDS}-{MAX_LEDE_WORDS} words total.
  This is a hard limit and it is the single most common failure. A 40-word
  lede is ideal. Do NOT write a paragraph here and do NOT summarise the whole
  article. If you are unsure, write 40 words.
- "sections": {MIN_SECTIONS}-{MAX_SECTIONS} sections. Each has a short "heading"
  (2-5 words, sentence case) and "paragraphs" (1-3 short paragraphs).
  Do not number the headings. Do not use the word "Conclusion".

BACKLINKS - exactly two, no more, no fewer:
- Link 1 goes mid-article, inside the section that actually discusses the
  relevant project or tool. It must feel like a natural aside, not an ad.
- Link 2 is the soft close, in the final paragraph. Something like pointing
  at where to see more of the work. Never "click here", never "check out my
  blog!".

HOW TO PLACE A LINK:
Put the literal token [[LINK1]] or [[LINK2]] inside the paragraph text, exactly
where the anchor should sit. Example:
  "I keep [[LINK1]] in a single file so I can re-read the whole flow."
The renderer replaces the token with a real <a> tag. Never write raw HTML.

LEGAL LINK TARGETS - pick a key, never invent a URL:
{_targets_block()}

  LINK1 should usually point somewhere in this group: {body_hints}
  LINK2 can come from this group: {close_hints}
  The two must point at DIFFERENT pages.

ANCHOR TEXT - must vary every post:
Banned outright because they are the generic SEO default:
  "click here", "here", "read more", "link", "this", "portfolio", "my portfolio",
  "check it out", "source code", "learn more"
Used in recent posts, do not reuse these wordings:
{avoid}

Instead write anchor text that a human would type: "the calculator project I
built", "how I set up LangChain", "my GitHub profile", "the Fitness Temple page".
Two to six words. Lowercase, no capitalised slogan words.

IMAGE:
- "image_query": a specific 2-5 word Pixabay search phrase matching THIS post's
  actual subject. Generic words like "business", "technology", "success" are banned.
- "alt_text": describes what is literally visible in the photo, 8-18 words.
  Never starts with "image of" or "picture of". Never keyword-stuffed.

SEO:
- "meta_description": {150}-{160} characters exactly. Must be a real sentence
  that describes this specific post, ending without a trailing period.
- "tags": {3}-{5} short lowercase tags.

Return exactly this JSON shape:
{{
  "title": "...",
  "slug": "url-friendly-kebab-case",
  "lede": "...",
  "sections": [
    {{"heading": "...", "paragraphs": ["...", "..."]}}
  ],
  "backlinks": [
    {{"id": "LINK1", "anchor": "...", "target": "projects_index_key", "placement": "body"}},
    {{"id": "LINK2", "anchor": "...", "target": "close_index_key", "placement": "close"}}
  ],
  "image_query": "...",
  "alt_text": "...",
  "meta_description": "...",
  "tags": ["...", "..."]
}}"""


# --------------------------------------------------------------- validation
ALT_FILLERS = ("image of", "picture of", "photo of", "a photo", "illustration of",
               "drawing of", "render of", "graphic of", "shot of", "view of",
               "screenshot of", "close-up of", "close up of")


def _alt_ok(alt: str) -> bool:
    if not alt or not (4 <= len(alt.split()) <= 24):
        return False
    return not alt.lower().startswith(ALT_FILLERS)


def _reinsert_link_tokens(post: dict[str, Any]) -> int:
    """Re-attach [[LINKn]] markers that an expansion or rewrite dropped.

    Providers routinely rewrite the prose and quietly omit the inline markers
    while we still hold valid anchor metadata. Rejecting the whole draft for
    that wastes a round; the anchor text and target are already validated, so
    the safe repair is to place the marker back.

    LINK1 goes into the first body paragraph, LINK2 into the last.
    """
    sections = [s for s in post.get("sections", []) or [] if s.get("paragraphs")]
    if not sections:
        return 0

    body = json.dumps(sections)
    inserted = 0

    for link_id, target_section in (("LINK1", 0), ("LINK2", -1)):
        if f"[[{link_id}]]" in body:
            continue
        try:
            idx = target_section % len(sections)
            sec = sections[idx]
            paras = sec["paragraphs"]
            # never split a sentence: attach to the end of a whole paragraph
            pos = len(paras) - 1
            text = paras[pos].rstrip()
            paras[pos] = f"{text} [[{link_id}]]."
            inserted += 1
        except (IndexError, TypeError):
            continue

    return inserted


def _repair(post: dict[str, Any]) -> dict[str, Any]:
    """Best-effort structural fixes before we spend a whole retry."""
    if not post.get("sections"):
        return post

    n = _reinsert_link_tokens(post)
    if n:
        print(f"[repair] re-inserted {n} lost link marker(s)")

    md = post.get("meta_description")
    if md:
        fixed = fit_meta(md)
        if fixed:
            post["meta_description"] = fixed

    lede = post.get("lede") or ""
    if lede and _wc(lede) > MAX_LEDE_WORDS:
        words = lede.split()
        while words and _wc(" ".join(words)) > MAX_LEDE_WORDS:
            words.pop()
        post["lede"] = " ".join(words).rstrip(",;:")
        print(f"[repair] trimmed lede to {len(words)} words")

    return post


def validate(post: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    for field in ("title", "lede", "meta_description", "image_query", "alt_text"):
        if not str(post.get(field, "")).strip():
            errs.append(f"missing '{field}'")

    sections = post.get("sections") or []
    if not (MIN_SECTIONS <= len(sections) <= MAX_SECTIONS):
        errs.append(f"{len(sections)} sections (need {MIN_SECTIONS}-{MAX_SECTIONS})")

    wc = word_count(post)
    if not (MIN_WORDS <= wc <= MAX_WORDS):
        errs.append(f"{wc} words (need {MIN_WORDS}-{MAX_WORDS})")

    # ---- exactly two links, each used once ----
    body_text = json.dumps(sections)
    n1, n2 = body_text.count("[[LINK1]]"), body_text.count("[[LINK2]]")
    if n1 != 1:
        errs.append(f"[[LINK1]] appears {n1} times (need exactly 1)")
    if n2 != 1:
        errs.append(f"[[LINK2]] appears {n2} times (need exactly 1)")
    if "[[LINK" in re.sub(r"\[\[LINK[12]\]\]", "", body_text):
        errs.append("stray [[LINK...]] token")

    links = post.get("backlinks") or []
    if len(links) != 2:
        errs.append(f"{len(links)} backlink entries (need 2)")
    else:
        ids = {l.get("id") for l in links}
        if ids != {"LINK1", "LINK2"}:
            errs.append(f"backlink ids are {ids}, need LINK1+LINK2")
        for l in links:
            if l.get("target") not in P.LINK_TARGETS:
                errs.append(f"illegal link target {l.get('target')!r}")
            if l.get("placement") not in {"body", "close"}:
                errs.append(f"bad placement {l.get('placement')!r}")
        if len({l.get("target") for l in links}) != 2:
            errs.append("both links point at the same page")

    # ---- anchor text ----
    seen: set[str] = set()
    for l in links:
        raw_anchor = (l.get("anchor") or "").strip()
        if not raw_anchor:
            errs.append("empty anchor text")
            continue

        if raw_anchor != textnorm.normalize(raw_anchor):
            errs.append(
                f"anchor {raw_anchor!r} contains a non-ASCII dash or space"
            )

        key = textnorm.anchor_key(raw_anchor)
        if key in ANCHOR_EXACT_BANNED:
            errs.append(f"anchor {key!r} is generic SEO filler")
        else:
            for phrase in ANCHOR_PHRASE_BANNED:
                if phrase in key:
                    errs.append(
                        f"anchor {key!r} contains banned phrase {phrase!r}"
                    )
                    break

        words = key.split()
        if words and not (MIN_ANCHOR_WORDS <= len(words) <= MAX_ANCHOR_WORDS):
            errs.append(
                f"anchor {key!r} is {len(words)} words "
                f"(need {MIN_ANCHOR_WORDS}-{MAX_ANCHOR_WORDS})"
            )

        if key in seen:
            errs.append(f"anchor {key!r} used twice in the same post")
        seen.add(key)

        if ledger.anchor_used(raw_anchor):
            errs.append(f"anchor {raw_anchor!r} reused from a recent post")

    # ---- meta description ----
    md = post.get("meta_description", "")
    if md and not (145 <= len(md) <= 165):
        errs.append(f"meta_description is {len(md)} chars (need 150-160)")

    tags = post.get("tags") or []
    if not (3 <= len(tags) <= 5):
        errs.append(f"{len(tags)} tags (need 3-5)")

    # ---- tone ----
    opener = (post.get("lede") or "").strip().lower()
    for bad in P.BANNED_OPENERS:
        if opener.startswith(bad.lower()):
            errs.append(f"banned opener: {bad!r}")
    if "conclusion" in (post.get("sections") and
                        json.dumps(sections).lower() or ""):
        errs.append("a section is named 'Conclusion'")

    # NOTE: alt_text is deliberately NOT validated here. At generation time no
    # image has been chosen, so the model's alt text is a placeholder by design.
    # validate_alt() runs in main.py once the real image is selected.

    # ---- lede length: keep it a hook, not an essay opener ----
    lede_wc = len(re.findall(r"\b[\w'-]+\b", post.get("lede", "") or ""))
    if post.get("lede") and not (MIN_LEDE_WORDS <= lede_wc <= MAX_LEDE_WORDS):
        errs.append(
            f"lede is {lede_wc} words (need {MIN_LEDE_WORDS}-{MAX_LEDE_WORDS})"
        )

    # ---- hedges inside the sentence carrying a link ----
    for s in sections:
        for para in s.get("paragraphs", []) or []:
            if "[[LINK" not in para:
                continue
            low = para.lower()
            hit = next((h for h in HEDGE_PHRASES if h in low), None)
            if hit:
                errs.append(
                    f"link sentence contains hedge {hit!r}: {para[:70]!r}"
                )

    # the lede must not be repeated as the opening paragraph
    lede_key = textnorm.anchor_key(post.get("lede", ""))
    if lede_key:
        first_para = ""
        for s in sections:
            paras = s.get("paragraphs") or []
            if paras:
                first_para = paras[0]
                break
        para_key = textnorm.anchor_key(first_para)
        if para_key and (para_key == lede_key
                         or para_key.startswith(lede_key)
                         or lede_key.startswith(para_key)):
            errs.append("the lede is duplicated as the first body paragraph")

    # headings must not repeat
    heads = [textnorm.anchor_key(s.get("heading", ""))
             for s in sections if s.get("heading")]
    dupes = {h for h in heads if h and heads.count(h) > 1}
    if dupes:
        errs.append(f"duplicate section heading(s): {sorted(dupes)}")

    return errs


def fit_meta(md: str) -> str | None:
    """Force a meta description into 150-160 chars by trimming at a word boundary.

    The model reliably writes the right idea and the wrong length, and
    regenerating the whole post over a character count is wasteful. Trimming
    to the last whole word inside the window preserves meaning.
    """
    md = textnorm.meta_safe(md or "")
    if 150 <= len(md) <= 160:
        return md

    if len(md) > 160:
        window = md[:160]
        if " " in window:
            window = window[: window.rfind(" ")]
        return window if 150 <= len(window) <= 160 else None

    # too short: the model did not write enough. Only commas give us room to
    # extend without inventing claims, so give up and let a retry handle it.
    return None


EXPAND_SYSTEM = """You lengthen a blog post without changing its argument.

You return STRICT JSON only. No fences.

Copy EVERY field other than "sections" and "lede" over EXACTLY as supplied.
You only add depth to the prose.

How to add depth. Padding is a failure - a reader can feel it.
- Extend the reasoning in each section: say WHY, not just what.
- Add concrete technical detail that is true by construction (how an algorithm
  works, what a practice is for, what a trade-off costs).
- Add the objection a real reader would raise, and answer it.
- Prefer 2-3 paragraphs per section over very long ones.

NEVER invent: an incident, a bug the writer hit, a number, a metric, a client,
a debugging story, or a stack/tool they did not use. You are expanding
explanation, not writing biography. If a claim cannot be made without
inventing something, write it generically instead."""

EXPAND_FEEDBACK = """
The previous expansion was not enough. Go further:
- Every section must now have at least 2 substantial paragraphs.
- Add a full extra paragraph to EVERY section.
- Deepen the reasoning; do not restate what is already there."""

# Per-section budgets. Sections below these are expanded individually, which
# is far more reliable than asking the model to bulk up the whole post.
SECTION_MIN_WORDS = 95


def _section_words(section: dict[str, Any]) -> int:
    text = " ".join([section.get("heading", "") or ""]
                    + list(section.get("paragraphs", []) or []))
    return len(re.findall(r"\b[\w'-]+\b", text))


def _preserve(new: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    """The expand pass may never alter the validated, non-prose fields."""
    for field in ("backlinks", "meta_description", "tags", "slug", "title",
                  "image_query", "alt_text", "topic", "kind"):
        if field in old and old.get(field) is not None:
            new[field] = old[field]
    return new


def expand_post(post: dict[str, Any], target: int = 700) -> dict[str, Any] | None:
    """Rewrite only the prose, preserving links, meta, tags and slug.

    Much more reliable than regenerating the whole post to fix a word count,
    because the parts that already validated cannot drift.
    """
    current = word_count(post)
    if current >= target:
        return post

    body: list[str] = [post.get("lede", "")]
    for s in post.get("sections", []) or []:
        body.append(f"\n## {s.get('heading', '')}")
        body.extend(s.get("paragraphs", []) or [])
    links = "\n".join(f"  {l.get('id')} ({l.get('placement')}): "
                      f"\"{l.get('anchor')}\" -> {l.get('target')}"
                      for l in post.get("backlinks", []) or [])

    prompt = f"""{P.ground_truth()}

The post below is {current} words and must reach {target}. Add roughly
{target - current} words of genuine explanation, spread across the lede and
every section. Aim for 2-3 paragraphs per section.

EXISTING POST
  title : {post.get('title')}
  meta  : {post.get('meta_description')}
  tags  : {', '.join(post.get('tags', []))}
  links :
{links}

PROSE:
{chr(10).join(body)}

Return the full JSON object with "lede" and "sections" rewritten longer."""

    try:
        raw = config.chat(prompt, EXPAND_SYSTEM, temperature=0.75,
                          max_tokens=MAX_EXPAND_TOKENS, as_json=True)
    except config.DailyTokenLimitError:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] expand pass failed: {exc.__class__.__name__}")
        return None

    new = _parse(raw)
    if not new:
        return None
    new = coerce_post(new)
    if not new.get("sections"):
        return None

    new = _preserve(new, post)
    _normalize_post(new)
    new["word_count"] = word_count(new)
    return new


def enforce_length(post: dict[str, Any], target: int = 700,
                   max_rounds: int = 3) -> dict[str, Any]:
    """Loop expansion until MIN_WORDS is reached or rounds run out.

    Deterministic in the sense that matters: the function always returns a
    post that either clears the floor or is the best attempt seen, and it
    reports which. Each round asks for more than the last, because a timid
    expansion does not converge.
    """
    best = post
    best_wc = word_count(post)

    for rnd in range(1, max_rounds + 1):
        if best_wc >= target:
            break

        # escalate the ask each round
        ask = max(target, best_wc + max(120, (target - best_wc) + 80))
        short = [s for s in best.get("sections", [])
                 if _section_words(s) < SECTION_MIN_WORDS]
        note = ""
        if short:
            names = ", ".join(f"'{s.get('heading', '?')}'" for s in short[:4])
            note = (f"\nThese sections are still too thin: {names}. Each needs "
                    f"at least {SECTION_MIN_WORDS} words.")

        print(f"[length] round {rnd}/{max_rounds}: {best_wc} words, "
              f"asking for {ask}")

        bigger = expand_post(best, target=ask)
        if not bigger:
            print("[length] expansion returned nothing usable")
            continue

        new_wc = word_count(bigger)
        if new_wc > best_wc:
            best, best_wc = bigger, new_wc
            print(f"[length] now {best_wc} words")
        else:
            print(f"[length] expansion did not help ({new_wc} words)")
            # escalate harder on the next round
            best = _preserve(bigger, best)
            best_wc = max(best_wc, word_count(best))

    best["word_count"] = word_count(best)
    best["length_ok"] = MIN_WORDS <= best["word_count"] <= MAX_WORDS
    best["length_rounds"] = max_rounds
    if not best["length_ok"]:
        print(f"[length] still {best['word_count']} words after "
              f"{max_rounds} rounds (need {MIN_WORDS}-{MAX_WORDS})")
    return best


def prompt_payload(post: dict[str, Any]) -> str:
    body = [post.get("lede", "")]
    for s in post.get("sections", []) or []:
        body.append(f"\n## {s.get('heading', '')}")
        body.extend(s.get("paragraphs", []) or [])
    links = "\n".join(f"  {l.get('id')} ({l.get('placement')}): "
                      f"\"{l.get('anchor')}\" -> {l.get('target')}"
                      for l in post.get("backlinks", []) or [])
    return f"""EXISTING POST
  title : {post.get('title')}
  meta  : {post.get('meta_description')}
  tags  : {', '.join(post.get('tags', []))}
  links :
{links}

PROSE:
{chr(10).join(body)}"""


def validate_alt(post: dict[str, Any]) -> str | None:
    """Check alt text after image selection. Returns an error string or None."""
    alt = (post.get("alt_text") or "").strip()
    if not alt:
        return "alt_text is empty"
    if not _alt_ok(alt):
        return f"alt_text is filler or the wrong length: {alt[:70]!r}"
    return None


def _parse(raw: str) -> dict[str, Any] | None:
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None


# ------------------------------------------------------------------- public
def generate_post(topic: dict[str, Any], max_attempts: int = 3,
                  feedback: str = "") -> dict[str, Any]:
    """Generate, validate and retry with the validator's complaints as feedback.

    `feedback` is the quality gate's fix_instructions from a previous round.
    Passing it in makes the next attempt a genuinely different post rather than
    a re-check of the same text.
    """
    prior = ledger.recent_anchors(5)
    base = _build_prompt(topic, prior)

    if feedback.strip():
        base += (
            "\n\n==============================================================\n"
            "A PREVIOUS DRAFT OF THIS POST WAS REJECTED BY REVIEW.\n"
            "Rewrite the entire post from scratch, fixing every issue below.\n"
            "Do not reuse sentences from the rejected draft.\n"
            "==============================================================\n"
            + feedback.strip()
        )

    prompt = base
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            print(f"[retry {attempt}] asking for a fresh draft…")
        try:
            raw = config.chat(prompt, SYSTEM, temperature=0.9,
                              max_tokens=MAX_GEN_TOKENS, as_json=True)
        except config.DailyTokenLimitError:
            raise          # org-wide quota: retrying is pointless, let it bubble
        except Exception as exc:  # noqa: BLE001 - all models failed
            print(f"[warn] generation call failed: {exc}")
            errors = [f"model unavailable: {exc.__class__.__name__}"]
            post = {"topic": topic["topic"], "errors": errors}
            return post

        post = _parse(raw)
        if post is None:
            errors = ["model returned unparseable JSON"]
            prompt = base + (
                "\n\nIMPORTANT: your last reply was not valid JSON. Return only "
                "the JSON object, nothing else."
            )
            continue

        post = coerce_post(post)
        if not post.get("sections"):
            # Gemini occasionally omits "sections" entirely. Ask once with a
            # stricter shape reminder rather than burning the whole round.
            print("[warn] no sections returned; retrying with a shape reminder")
            retry_prompt = (
                prompt + "\n\nCRITICAL: your reply must include a \"sections\" "
                "array. Each element is an object with \"heading\" (string) and "
                "\"paragraphs\" (array of strings). Do not use \"body\"."
            )
            try:
                raw2 = config.chat(retry_prompt, SYSTEM, temperature=0.9,
                                   max_tokens=MAX_GEN_TOKENS, as_json=True)
            except config.DailyTokenLimitError:
                raise
            except Exception:  # noqa: BLE001
                raw2 = ""
            post = coerce_post(_parse(raw2) or {})

        if not post.get("sections"):
            errors = ["model returned no usable sections"]
            continue

        post["slug"] = post.get("slug") or _slugify(post.get("title", ""))
        post["topic"] = topic["topic"]
        # The topic is where kind was decided; the model may echo a `kind` field
        # of its own, but it must not be able to upgrade a general explainer to
        # "personal" (which re-arms the strict first-person tripwires) or the
        # reverse. Topic wins, and anything unrecognised falls back to personal.
        post["kind"] = (str(topic.get("kind") or "").strip().lower()
                        or str(post.get("kind") or "").strip().lower()
                        or "personal")
        if post["kind"] not in ("personal", "general"):
            post["kind"] = "personal"
        _normalize_post(post)

        # --- cheap deterministic repairs before any retry ---
        post = _repair(post)

        # --- surgical expand: prose only, keeps validated links/meta intact ---
        if word_count(post) < MIN_WORDS:
            print(f"[length] only {word_count(post)} words - lengthening")
            post = enforce_length(post, target=700, max_rounds=3)
            post = _repair(post)

        post["word_count"] = word_count(post)

        errors = validate(post)
        if not errors:
            return post

        print(f"[retry {attempt}] draft rejected:")
        for e in errors:
            print(f"    - {e}")
        prompt = base + (
            "\n\nYour previous attempt was REJECTED for these reasons:\n"
            + "\n".join(f"  * {e}" for e in errors)
            + "\n\nRewrite the whole post fixing every listed problem."
            + (f"\n\nAlso address this review feedback:\n{feedback.strip()}"
               if feedback.strip() else "")
        )

    # If the last attempt failed to parse, `post` is still None. Hand back a
    # well-formed stub so callers never hit a NoneType.
    if post is None:
        post = {
            "topic": topic["topic"], "title": "", "slug": "", "lede": "",
            "sections": [], "backlinks": [], "tags": [],
            "meta_description": "", "image_query": "", "alt_text": "",
        }
    post["errors"] = errors or ["generation did not produce a valid post"]
    return post


# --------------------------------------------------------------------- show
def render_text(post: dict[str, Any]) -> str:
    """Plain-text view for eyeballing in the terminal."""
    lines = [post.get("title", "").upper(), "=" * len(post.get("title", "")),
             f"slug: {post.get('slug', '')}   words: {post.get('word_count', '?')}",
             f"tags: {', '.join(post.get('tags', []))}",
             f"meta ({len(post.get('meta_description', ''))} chars): "
             f"{post.get('meta_description', '')}",
             f"image query: {post.get('image_query', '')}",
             f"alt text   : {post.get('alt_text', '')}",
             "", post.get("lede", "")]

    for s in post.get("sections", []):
        lines += ["", f"## {s.get('heading', '')}"]
        for para in s.get("paragraphs", []):
            lines += [para, ""]

    lines += ["", "--- LINKS ---"]
    for l in post.get("backlinks", []):
        lines.append(f"{l['id']} ({l['placement']}): [{l['anchor']}] -> "
                     f"{P.LINK_TARGETS.get(l['target'], l['target'])}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate one full blog post.")
    ap.add_argument("--topic", help="a topic string (skips the topic pool)")
    ap.add_argument("--angle", default="", help="optional angle hint")
    ap.add_argument("--image", default="", help="optional image query hint")
    ap.add_argument("--json", action="store_true", help="dump raw JSON too")
    args = ap.parse_args()

    if args.topic:
        topic = {"topic": args.topic, "angle": args.angle,
                 "image_query": args.image or "laptop code screen"}
    else:
        pool_path = config.ROOT / "topic_pool.json"
        pool = json.loads(pool_path.read_text(encoding="utf-8")) if pool_path.exists() else []
        if not pool:
            print("[!] topic_pool.json is empty. Run: python topic_picker.py")
            return 1
        topic = random.choice(pool)
        print(f"[info] using pooled topic: {topic['topic']}\n")

    post = generate_post(topic)

    if post.get("errors"):
        print(f"\n[!] Still failing after retries: {post['errors']}")
        if args.json:
            print(json.dumps(post, indent=2, ensure_ascii=False))
        return 1

    print()
    print("=" * 70)
    print(render_text(post))
    print("=" * 70)
    if args.json:
        print(json.dumps(post, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Test fixtures re-use the shape from test_hardening without importing it.
FAKE_BACKLINKS = [
    {"id": "LINK1", "anchor": "the card game logic I wrote",
     "target": "projects", "placement": "body"},
    {"id": "LINK2", "anchor": "where I write these up",
     "target": "blog", "placement": "close"},
]
