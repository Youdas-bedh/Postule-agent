"""Conservative company-name normalization (exact normalized match only)."""
from __future__ import annotations
import re
import unicodedata

_SUFFIXES = {"inc", "incorporated", "ltd", "limited", "llc", "plc", "corp", "corporation", "groupe", "group", "sas", "sasu", "sa", "sarl", "srl", "bv", "ag", "nv", "se"}


def normalize_company_name(name: str | None) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    parts = text.split()
    # Legal suffix punctuation is often spelled with separators (S.A.S., S.A.R.L.).
    suffix_phrases = {"s a s", "s a r l", "s a", "s a s u", "s r l", "l l c", "p l c"}
    while len(parts) > 1:
        count = next((n for n in range(min(4, len(parts)), 0, -1) if " ".join(parts[-n:]) in suffix_phrases), 0)
        if not count:
            break
        parts = parts[:-count]
    while len(parts) > 1 and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)
