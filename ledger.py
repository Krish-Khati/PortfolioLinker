"""
posted_topics.json — the memory of the whole system.

Holds one record per post so no future run ever repeats a topic or reuses
anchor text from the last 5 posts.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from config import LEDGER_PATH
import textnorm

_WORD = re.compile(r"[a-z0-9]+")


def _norm(text: str) -> set[str]:
    """Lowercase word set, minus filler — used for similarity checks."""
    text = textnorm.normalize(text)
    stop = {
        "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
        "my", "me", "i", "is", "it", "that", "how", "why", "what", "do",
        "you", "your", "at", "by", "from", "as", "are", "be", "this", "about",
    }
    return {w for w in _WORD.findall(text.lower()) if w not in stop and len(w) > 2}


def load() -> dict[str, Any]:
    if not LEDGER_PATH.exists():
        return {"posts": []}
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"[warn] {LEDGER_PATH.name} is corrupt; starting a fresh ledger.")
        return {"posts": []}
    data.setdefault("posts", [])
    return data


def save(data: dict[str, Any]) -> None:
    LEDGER_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def all_posts() -> list[dict[str, Any]]:
    return load()["posts"]


def used_topic_keys() -> list[set[str]]:
    return [_norm(p.get("topic", "")) for p in all_posts() if p.get("topic")]


def too_similar(new_topic: str, threshold: float = 0.55) -> str | None:
    """Return the conflicting existing topic if new_topic is too close to it."""
    cand = _norm(new_topic)
    if not cand:
        return "empty topic"
    for keys in used_topic_keys():
        if not keys:
            continue
        overlap = len(cand & keys) / len(cand | keys)
        if overlap >= threshold:
            # find the human-readable original for the message
            for p in all_posts():
                if _norm(p.get("topic", "")) == keys:
                    return p.get("topic", "")
            return "(similar to a previous post)"
    return None


def recent_anchors(n: int = 5) -> list[str]:
    """Anchor text used in the last n posts — the generator must avoid these."""
    out: list[str] = []
    for post in all_posts()[-n:]:
        for link in post.get("backlinks", []) or []:
            anchor = (link.get("anchor") or "").strip()
            if anchor:
                out.append(anchor)
    return out


def anchor_used(anchor: str, lookback: int = 5) -> bool:
    """Compare anchors on normalised form so a curly dash never sneaks past."""
    target = textnorm.anchor_key(anchor)
    if not target:
        return False
    return any(textnorm.anchor_key(a) == target for a in recent_anchors(lookback))


# Lifecycle order. A second record for the same post must never demote it
# back to "draft" just because it was written earlier.
_STATUS_RANK = {"draft": 0, "blogger-draft": 1, "published": 2}

_EMPTY = (None, "", [], {})


def _same_post(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True if two records describe the same post.

    Slug first: it is exact and both callers pass the same value. Topic is the
    fallback for older rows written before a slug was set.
    """
    sa = (a.get("slug") or "").strip().lower()
    sb = (b.get("slug") or "").strip().lower()
    if sa and sb:
        return sa == sb
    ta = (a.get("topic") or "").strip().lower()
    tb = (b.get("topic") or "").strip().lower()
    return bool(ta) and ta == tb


def _merge(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Fold a later record into an earlier one without losing either."""
    out = dict(old)
    for k, v in new.items():
        if k == "date":
            continue                      # keep when the post was first made
        if k == "status":
            if (_STATUS_RANK.get(str(v), 0)
                    >= _STATUS_RANK.get(str(old.get(k, "draft")), 0)):
                out[k] = v
            continue
        if v in _EMPTY:
            continue                      # never blank out a real value
        out[k] = v
    return out


def record(post: dict[str, Any]) -> None:
    """Record a post. One entry per post, not one per event.

    main.py records a post once it clears the quality gate, and
    publish_blogger.py records it again after the Blogger post is created.
    Appending both gave every published post two rows, which double-counted it
    in recent_anchors() and in the topic-similarity check. A second record for
    the same post now merges into the first.
    """
    data = load()
    entry = dict(post)
    entry.setdefault("date", date.today().isoformat())
    entry.setdefault("status", "draft")

    posts = data["posts"]
    for i, existing in enumerate(posts):
        if _same_post(existing, entry):
            posts[i] = _merge(existing, entry)
            save(data)
            print(f"[ok] updated -> {LEDGER_PATH.name} "
                  f"({len(posts)} total) [{posts[i].get('status')}]")
            return

    posts.append(entry)
    save(data)
    print(f"[ok] recorded -> {LEDGER_PATH.name} ({len(posts)} total)")


def dedupe() -> int:
    """Collapse duplicate rows left by older versions. Returns rows removed.

    Safe to run repeatedly. Keeps the first occurrence of each post, folded
    with the rest, so no Blogger id or image metadata is lost.
    """
    data = load()
    posts = data["posts"]
    out: list[dict[str, Any]] = []
    for entry in posts:
        for i, kept in enumerate(out):
            if _same_post(kept, entry):
                out[i] = _merge(kept, entry)
                break
        else:
            out.append(dict(entry))
    removed = len(posts) - len(out)
    if removed:
        data["posts"] = out
        save(data)
    return removed