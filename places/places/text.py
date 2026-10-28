"""Text normalisation shared by retrieval and the copy check."""
from __future__ import annotations

import re
import unicodedata

PT_PARTICLES = {"de", "da", "do", "dos", "das"}
STOP = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "its", "of", "on",
        "or", "the", "to", "with", "this", "that", "was", "were", "has", "have", "i", "we", "you", "near",
        "want", "see", "visit"} | PT_PARTICLES


def fold(s: str) -> str:
    """Lower-case and strip accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def words(s: str) -> list[str]:
    """Folded words with punctuation stripped (used by the copy check)."""
    return re.findall(r"[a-z0-9]+", fold(s))


def stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(s: str) -> list[str]:
    return [stem(w) for w in words(s) if w not in STOP]
