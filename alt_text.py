"""
Alt text generation - runs AFTER the image has been chosen.

The original post draft guesses at alt text before any image exists, which is
guaranteed to be wrong ("a cluttered desk with a laptop" for a photo of code).
This module replaces it with a description grounded in what Pixabay actually
returned for the chosen image.

Groq vision is unavailable for the text models we use, so this is grounded in
the Pixabay tag set plus the search query that found the hit. If the LLM is
unavailable the deterministic builder produces the alt text on its own, so
this module never hard-fails the pipeline.
"""

from __future__ import annotations

import re
from typing import Any

# Tags Pixabay returns constantly and which say nothing about the subject.
STOP_TAGS = {
    "technology", "computer", "digital", "work", "business", "background",
    "developing", "development", "developer", "professional", "online", "web",
    "data", "software", "science", "research", "abstract", "industry",
    "programmer", "programming", "coding", "code", "screen", "monitor",
}

# Tag -> the noun phrase we are willing to say is visible.
PHRASES = {
    "laptop": "a laptop",
    "desk": "a desk",
    "keyboard": "a keyboard",
    "source": "source code",
    "terminal": "a terminal window",
    "editor": "a code editor",
    "html": "an HTML document",
    "javascript": "a JavaScript file",
    "python": "a Python script",
    "gym": "a gym",
    "weights": "dumbbells and weights",
    "workout": "someone working out",
    "fitness": "a fitness scene",
    "cards": "playing cards",
    "puzzle": "puzzle pieces",
    "cards game": "playing cards",
    "mathematics": "handwritten maths",
    "calculator": "a calculator",
    "language": "a language lesson",
    "translate": "a translation interface",
    "notebook": "an open notebook",
    "pencil": "a pencil and notebook",
    "writing": "someone writing by hand",
    "coffee": "a coffee cup",
    "meeting": "a team meeting",
    "workshop": "a workshop bench",
    "tools": "hand tools",
    "wood": "a wooden workbench",
    "circuit": "a circuit board",
    "wires": "wiring and cables",
}

SYSTEM = """You write alt text for images on a blog.

Return STRICT JSON only. No fences.

Rules:
- Describe ONLY what you can actually see in the image. Do not guess.
- Purely descriptive. Never mention the article topic, the article title, or
  what the image is "illustrating".
- Never start with "image of", "picture of", "photo of" or "a photo".
- 8 to 18 words. One sentence, no trailing period.
- Lowercase start, no keyword stuffing.

Good: "source code on a dark-themed editor, highlighted in several colours"
Bad:  "image of technology representing software development" """

# Used when the model cannot see the image and must work from tags alone.
SYSTEM_TAGS_ONLY = """You write alt text for an image you CANNOT see.

You are given only the search tags and the search query that found it. Infer
the most likely literal content from those, and keep the description vague
enough that it cannot be wrong in a specific way.

Return STRICT JSON only. No fences.
Rules:
- Purely descriptive. Never mention the article topic or title.
- Never start with "image of", "picture of", "photo of" or "a photo".
- 8 to 18 words. One sentence, no trailing period.
Good: "source code displayed in a dark-themed code editor"
Bad:  "image of technology representing software development" """


def dedupe_tags(raw: str | list[str]) -> list[str]:
    """Pixabay repeats tags heavily; keep first-seen order, drop the useless."""
    if isinstance(raw, str):
        parts = re.split(r"[,\s]+", raw)
    else:
        parts = list(raw)
    out: list[str] = []
    for p in parts:
        t = p.strip().lower()
        if t and t not in out:
            out.append(t)
    return out


# Curated subject rules. The first rule whose trigger tags are present wins.
# This exists so the fallback reads like English instead of keyword salad -
# you never want alt text to be "ethernet programming code computer".
SUBJECT_RULES: list[tuple[list[str], str, list[str]]] = [
    (["source", "code editor", "editor"], "source code on a computer screen",
     ["html", "javascript", "python"]),
    (["javascript", "html", "script"], "a code editor window showing source code",
     []),
    (["terminal", "console", "shell", "command line"],
     "a terminal window running commands", []),
    (["gym", "fitness"], "an indoor gym space",
     ["weights", "dumbbell", "workout", "training"]),
    (["workout", "training", "exercise", "athlete"],
     "someone exercising with free weights", []),
    (["cards", "puzzle", "card game", "playing cards"],
     "playing cards spread across a surface", []),
    (["calculator"], "a handheld calculator showing numbers", []),
    (["mathematics", "math", "algebra"],
     "handwritten mathematical working", []),
    (["notebook", "pencil", "writing", "pen"],
     "an open notebook with handwritten notes", []),
    (["tools", "workbench", "workshop", "carpenter", "wood"],
     "hand tools laid out on a workbench", []),
    (["circuit", "wires", "electronics", "soldering"],
     "a circuit board with wiring attached", []),
    (["translate", "language"], "a translation interface on a screen", []),
    (["laptop"], "an open laptop on a desk", ["code", "screen", "notebook"]),
    (["keyboard"], "a keyboard on a desk", []),
    (["phone", "mobile"], "a smartphone held in one hand", []),
    (["coffee", "cafe"], "a cup of coffee on a desk", []),
    (["meeting", "team", "office"], "people gathered around a table", []),
]


def build_deterministic(tags: list[str], query: str) -> str:
    """LLM-free fallback. A real English noun phrase, never a tag dump."""
    have = set(dedupe_tags(tags))

    for triggers, subject, extras in SUBJECT_RULES:
        if have & set(triggers):
            extra = [e for e in extras if e in have]
            if extra:
                return f"{subject} and {extra[0]}".rstrip()
            return subject

    # no rule matched: fall back to the query's noun words, de-duplicated,
    # and never more than three of them.
    filler = {"on", "in", "of", "a", "an", "the", "and", "with", "at",
              "for", "to", "some", "photo", "image", "picture"}
    words = [w for w in re.split(r"[^\w+]+", query.lower()) if w and w not in filler]
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
        if len(seen) == 3:
            break
    return " ".join(seen) if seen else (next(iter(have), "photograph"))


ALT_FILLERS = ("image of", "picture of", "photo of", "a photo", "illustration of",
               "drawing of", "render of", "graphic of", "shot of", "view of",
               "screenshot of", "close-up of", "close up of")


def _clean_alt(alt: str) -> str | None:
    """Accept an alt string only if it is plausibly a description."""
    alt = (alt or "").strip().rstrip(".")
    if not 4 <= len(alt.split()) <= 22:
        return None
    if alt.lower().startswith(ALT_FILLERS):
        return None
    return alt


def _from_json(raw: str) -> str | None:
    import json as _json

    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        data = _json.loads(cleaned[cleaned.find("{"): cleaned.rfind("}") + 1])
    except (ValueError, _json.JSONDecodeError):
        return None
    return _clean_alt(str(data.get("alt_text", "")))


def set_alt_text(post: dict[str, Any], hit: dict[str, Any], query: str,
                 image_path: str | None = None) -> str:
    """Replace post['alt_text'] with something true of the chosen image.

    Preference order:
      1. Vision  - actually look at the file (needs image_path + working key)
      2. Tags    - infer from Pixabay tags
      3. Hardcoded subject rules, which never fail
    """
    import config

    tags = dedupe_tags(hit.get("tags", ""))

    # ---- 1. vision ----
    if image_path:
        try:
            raw = config.chat_vision(
                "Describe this image for use as alt text.", [image_path],
                SYSTEM, temperature=0.3, max_tokens=400, as_json=True,
            )
            alt = _from_json(raw)
            if alt:
                post["alt_text"] = alt
                post["alt_text_source"] = "vision"
                print("  [alt] generated from the actual image (vision)")
                return alt
            print("[warn] vision alt text failed validation; falling back to tags")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] vision unavailable ({exc.__class__.__name__}); "
                  f"falling back to tags")

    # ---- 2. tags ----
    try:
        raw = config.chat(
            f"Pixabay tags for the chosen image: {', '.join(tags)}\n"
            f"Search query that found it: \"{query}\"\n\n"
            f"Write the alt text.",
            SYSTEM_TAGS_ONLY, temperature=0.4, max_tokens=300, as_json=True,
        )
        alt = _from_json(raw)
        if alt:
            post["alt_text"] = alt
            post["alt_text_source"] = "tags"
            print("  [alt] generated from Pixabay tags")
            return alt
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] tag-based alt LLM unavailable "
              f"({exc.__class__.__name__}); using deterministic")

    # ---- 3. deterministic ----
    fallback = build_deterministic(tags, query)
    post["alt_text"] = fallback
    post["alt_text_source"] = "deterministic"
    print(f"  [alt] deterministic fallback")
    return fallback