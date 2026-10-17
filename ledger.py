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


def record(post: dict[str, Any]) -> None:
    """Append a finished post. Keeps the ledger append-only."""
    data = load()
    entry = dict(post)
    entry.setdefault("date", date.today().isoformat())
    entry.setdefault("status", "draft")
    data["posts"].append(entry)
    save(data)
    print(f"[ok] recorded -> {LEDGER_PATH.name} ({len(data['posts'])} total)")