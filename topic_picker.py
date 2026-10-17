"""
Step 1 — topic_picker.py

Asks Groq for post ideas drawn ONLY from Krish's real work, filters out anything
that collides with posted_topics.json, and stashes the survivors in topic_pool.json.

    python topic_picker.py          # show 3 topics
    python topic_picker.py --n 5    # ask for more
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import config
import ledger
import profile as P

THEMES = P.INTERESTS

BANNED_OPENERS = P.BANNED_OPENERS

SYSTEM = """You are a content strategist for an independent developer.

You return STRICT JSON and nothing else. No markdown fences, no prose.

Your ideas must be things a real person could genuinely write about after
actually doing the work. A topic is bad if it would require the writer to
have an experience they do not have.

General technology, SEO, SEM and content-marketing explainers ARE in scope and
do NOT need a personal story. Do not force every topic into a personal
narrative just because the writer is a person with a portfolio.

Every topic must be:
- specific (a real angle, not a vague category)
- one clear idea, not a listicle of five
- honestly writable from the background provided
- distinct from the topics already used

Add a "kind" field to each topic: "personal" or "general"."""


def _bullets(items: list[str], indent: str = "  - ") -> str:
    return "\n".join(indent + str(i) for i in items)


def attribution_conflict(text: str) -> str | None:
    """Catch a project being credited with a capability it does not have."""
    low = text.lower()

    if "linguabridge" in low:
        if any(w in low for w in ("blog", "seo", "meta description", "keyword")):
            return "LinguaBridge is a translation tool with no blog/SEO layer"
        if any(w in low for w in ("language learning", "vocabulary", "lesson",
                                  "course", "flashcard")):
            return "LinguaBridge is a translation tool, not a language-learning app"

    for name in P.PROJECTS:
        if name.lower() not in low:
            continue
        if name == "LinguaBridge":
            continue
        if any(w in low for w in ("blog page", "/blog", "meta description")):
            return f"the /blog page belongs to the portfolio site, not {name}"
    return None


def repeats_published(text: str) -> str | None:
    """Catch overlap with posts already live on the portfolio blog."""
    cand = ledger._norm(text)
    for prior in P.ALREADY_PUBLISHED_TOPICS:
        keys = ledger._norm(prior)
        if not keys:
            continue
        overlap = len(cand & keys) / len(cand | keys)
        if overlap >= 0.35:
            return f"overlaps an already-published post: {prior!r}"
    return None


def _build_prompt(n: int) -> str:
    used = [p.get("topic", "") for p in ledger.all_posts()]
    used_block = _bullets(used) if used else "  (none yet - first run)"
    theme_block = _bullets(THEMES)
    return f"""{P.ground_truth()}

{P.scope_block()}

THEMES TO DRAW FROM (do not invent a theme outside this list):
{theme_block}

{P.published_block()}

ALSO ALREADY POSTED BY THIS TOOL (posted_topics.json):
{used_block}

Produce exactly {n} topic ideas.

ANGLE SHAPE - this is the part that goes wrong most often:
An angle must be about a METHOD, PRINCIPLE or LESSON, never about a specific
event that happened to the writer. Concretely, all of these are FORBIDDEN
because they invent an incident that never occurred:

  BAD: "the specific bug where the shuffle function produced non-random order"
  BAD: "a moment when AI suggested a state update that broke the layout"
  BAD: "the hour I spent debugging and finally found the answer"
  BAD: anything shaped like "when I was building X, Y went wrong and I learned Z"

  GOOD: "why sort-based randomisation is the wrong tool for shuffling cards,
         and how to tell in advance"
  GOOD: "how to review an AI suggestion before you paste it"
  GOOD: "what small finished projects teach that tutorials never do"
  GOOD: "how to structure a portfolio when your only proof is personal projects"
  GOOD (general): "why a meta description is a promise, not a keyword field"
  GOOD (general): "what Core Web Vitals actually measure and why LCP dominates"
  GOOD (general): "how to pick a canonical URL when a page has many variants"

You may state things that are TRUE BY CONSTRUCTION - how a technique works,
what a tool does, why a practice exists, what a general learning curve looks
like. You may NOT state things that only the writer could know about what
actually happened to them.

image_query must be a specific, concrete Pixabay search phrase (2-5 words)
that visually matches the specific idea. Ban generic filler like
"business", "technology", "success", "work".

Return exactly this JSON shape:
{{
  "topics": [
    {{
      "topic": "short working title of the angle",
      "angle": "2-3 sentences on what the post covers",
      "image_query": "specific pixabay search phrase",
      "difficulty": "easy|medium|hard",
      "kind": "personal|general",
      "why_it_fits": "one short line"
    }}
  ]
}}"""


def _validate(t: dict[str, Any]) -> str | None:
    """Return an error string, or None if the topic is usable."""
    for field in ("topic", "angle", "image_query"):
        if not str(t.get(field, "")).strip():
            return f"missing field '{field}'"
    if len(t["topic"]) < 12:
        return "topic too short"

    # `kind` decides which fabrication tripwires arm later, so it has to be a
    # real value before this topic reaches the pool. The model sometimes echoes
    # the literal "personal|general" from the schema. Unknown values become
    # "personal" - the strict, safe default.
    t["kind"] = (str(t.get("kind") or "").strip().lower() or "personal")
    if t["kind"] not in ("personal", "general"):
        print(f"[warn] topic {t['topic'][:40]!r} had kind "
              f"{t.get('kind')!r}; defaulting to personal")
        t["kind"] = "personal"

    generic_images = {"business", "technology", "success", "work", "digital",
                      "future", "abstract", "background", "office"}
    q = set(t["image_query"].lower().split())
    if q and q <= generic_images:
        return "image_query is generic filler"

    clash = ledger.too_similar(t["topic"])
    if clash:
        return f"too similar to a posted topic: {clash!r}"

    blob = f"{t['topic']} {t['angle']}"

    blame = attribution_conflict(blob)
    if blame:
        return blame

    dupe = repeats_published(blob)
    if dupe:
        return dupe
    return None


def pick_topics(n: int = 3, max_rounds: int = 3) -> list[dict[str, Any]]:
    """Generate, validate and filter topics. Retries on collisions."""
    accepted: list[dict[str, Any]] = []

    for round_no in range(1, max_rounds + 1):
        need = n - len(accepted)
        if need <= 0:
            break
        if round_no > 1:
            print(f"[retry] asking for {need} more (round {round_no})…")

        raw = config.chat(
            _build_prompt(need),
            SYSTEM,
            temperature=0.9 if round_no == 1 else 1.0,
            max_tokens=2000,
            as_json=True,
        )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # tolerate a fenced block
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                print("[warn] model returned unparseable JSON; skipping round")
                continue

        for t in parsed.get("topics", []):
            if len(accepted) >= n:
                break
            err = _validate(t)
            if err:
                print(f"[drop] {t.get('topic', '?')[:48]!r} — {err}")
                continue
            accepted.append(t)

    return accepted


def main() -> int:
    ap = argparse.ArgumentParser(description="Pick blog topics from Krish's real work.")
    ap.add_argument("--n", type=int, default=3, help="how many topics (default 3)")
    ap.add_argument("--no-save", action="store_true", help="print only, don't save pool")
    args = ap.parse_args()

    topics = pick_topics(args.n)

    if not topics:
        print("[!] Could not produce any valid topics. Try again.")
        return 1

    pool_path = config.ROOT / "topic_pool.json"
    existing = json.loads(pool_path.read_text(encoding="utf-8")) if pool_path.exists() else []
    if not args.no_save:
        pool_path.write_text(
            json.dumps(existing + topics, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    print()
    print("=" * 70)
    print(f"  {len(topics)} TOPIC(S)")
    print("=" * 70)
    for i, t in enumerate(topics, 1):
        print(f"\n{i}. {t['topic']}")
        print(f"   angle : {t['angle']}")
        print(f"   image : {t['image_query']}")
        print(f"   level : {t.get('difficulty', '?')}")
        print(f"   fit   : {t.get('why_it_fits', '')}")

    anchors = ledger.recent_anchors(5)
    if anchors:
        print("\n" + "-" * 70)
        print("  anchor text used in the last 5 posts (do not reuse):")
        for a in anchors:
            print(f"    - {a}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())