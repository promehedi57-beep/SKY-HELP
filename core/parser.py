"""
Message normalization.

Handles: lowercase, unicode NFKC, punctuation/symbol stripping, whitespace
collapse, common Banglish letter patterns kept INTACT (we do not transliterate
Banglish aggressively — fuzzy matching handles it), and command-prefix removal.
"""

import re
import unicodedata

# Characters to strip entirely
_SYMBOL_RE = re.compile(r"[^\w\s\u0980-\u09FF]", re.UNICODE)  # keep Bengali block
_WS_RE = re.compile(r"\s+")

# English filler words that add no matching signal
_FILLER = {
    "please", "pls", "plz", "the", "a", "an", "is", "are", "am", "how",
    "to", "for", "of", "in", "on", "my", "me", "i", "can", "you", "do",
    "does", "not", "working", "work",
}


def normalize(text: str) -> str:
    """Aggressive normalization for matching: lowercase, no symbols, single spaces."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _SYMBOL_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def tokens(text: str) -> list[str]:
    """Normalized token list (filler words removed)."""
    norm = normalize(text)
    return [t for t in norm.split() if t not in _FILLER and t]


def is_short_command(text: str) -> bool:
    """Short queries like 'bd', 'wa' match exactly — no tokens needed."""
    norm = normalize(text)
    return len(norm.split()) <= 2


def content_hash(text: str) -> str:
    """Stable hash for cache/duplicate detection (used again in Phase 16/24)."""
    import hashlib
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()
