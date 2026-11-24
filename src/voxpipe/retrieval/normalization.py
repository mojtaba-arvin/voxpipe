"""Stable Persian-aware query normalization for lexical and semantic retrieval."""
from __future__ import annotations

import re
import unicodedata

_TRANSLATION = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه",
    "ؤ": "و", "أ": "ا", "إ": "ا", "ٱ": "ا",
    **{chr(0x0660 + i): str(i) for i in range(10)},
    **{chr(0x06F0 + i): str(i) for i in range(10)},
})
_DIACRITICS = re.compile(r"[\u064b-\u065f\u0670\u06d6-\u06ed]")
_WHITESPACE = re.compile(r"\s+")


def normalize_persian_query(query: str) -> str:
    """Normalize Arabic/Persian orthographic variants without changing FTS syntax.

    The policy is versioned as fa-query-v1. It folds Arabic Yeh/Kaf and
    common Alef/Hah/Waw variants, maps Arabic and Persian digits to ASCII,
    removes combining diacritics and tatweel, treats ZWNJ as a word boundary,
    collapses whitespace and case-folds mixed Latin/Persian text.

    Search punctuation, quotes and websearch operators are intentionally
    preserved so PostgreSQL websearch_to_tsquery keeps phrase/negation
    semantics. This is not a linguistic stemmer and does not rewrite words.
    """
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    value = unicodedata.normalize("NFC", query).translate(_TRANSLATION)
    value = value.replace("\u200c", " ").replace("\u0640", "")
    value = _DIACRITICS.sub("", value)
    return _WHITESPACE.sub(" ", value).strip().casefold()
