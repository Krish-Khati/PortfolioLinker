"""
Text normalisation shared by the generator, the validator and the ledger.

Models emit typographic characters that look fine but break slugs, meta
descriptions and anchor matching: U+2011 non-breaking hyphen, U+2013 en dash,
U+2014 em dash, U+00A0 no-break space, zero-width joiners. This module folds
those down to ASCII so downstream comparison is exact.
"""

from __future__ import annotations

import re
import unicodedata

# Every dash-like codepoint -> ASCII hyphen-minus
_DASHES = {
    0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE58, 0xFE63, 0xFF0D,
}
_SPACE = {
    0x00A0, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006,
    0x2007, 0x2008, 0x2009, 0x200A, 0x200B, 0x200C, 0x200D, 0x3000,
}
_TRANS = {c: "-" for c in _DASHES}
_TRANS.update({c: " " for c in _SPACE})


def normalize(text: str) -> str:
    """Fold dashes/spaces to ASCII. Curly quotes are kept - they encode fine."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_TRANS)
    # any remaining exotic dashes (e.g. U+2E3A, U+2E3B) -> hyphen
    text = re.sub(r"[\u2e3a\u2e3b\u058a\u05be\u1806\u301c\u3030\u30a0\ufe31\ufe32\ufe58]",
                  "-", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def slugify(text: str) -> str:
    """URL-friendly, lowercase, ASCII-only, max 70 chars, no trailing dash."""
    s = normalize(text).lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # Drop intra-word marks instead of turning them into separators, so
    # "What's" -> "whats" rather than "what-s".
    s = re.sub(r"['’ʼ`]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:70].strip("-")


def meta_safe(text: str) -> str:
    """Normalised, single-spaced, no trailing period. Used for meta descriptions."""
    s = normalize(text).strip().rstrip(".")
    return re.sub(r"\s+", " ", s)


def anchor_key(anchor: str) -> str:
    """Comparable form of anchor text: normalised, lowercase, unpunctuated.

    Hyphens are PRESERVED so 'step-by-step' does not collide with a
    different anchor that happens to read 'stepbystep'.
    """
    s = normalize(anchor).lower()
    s = re.sub(r"['’ʼ`]", "", s)
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return re.sub(r"\s+", " ", s).strip()