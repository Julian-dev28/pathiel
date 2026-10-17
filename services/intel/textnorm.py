"""Title normalization, dedup, and stable id hashing. Pure functions, no I/O.

Dedup matters because the same wire story shows up verbatim-ish across
Google News, GDELT, and oilprice.com with different trailing " - Source"
suffixes, different quote characters, and sometimes different casing. Two
headlines dedup when their normalized forms are IDENTICAL or when one's
normalized token set is a near-superset of the other's (handles the
"Headline - Reuters" vs "Headline, Reuters says" framing some aggregators
add) — see `is_near_duplicate`.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable, List, Sequence, Tuple, TypeVar

# " - Source Name" / " | Source Name" suffixes that Google News and most RSS
# aggregators append. Matched case-sensitively on the dash/pipe so we don't
# eat a real hyphenated headline like "Six-month low".
_SOURCE_SUFFIX = re.compile(r"\s+[-|]\s+[A-Za-z0-9.&'’ ]{2,40}$")
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def strip_source_suffix(title: str) -> str:
    return _SOURCE_SUFFIX.sub("", title).strip()


def normalize_title(title: str) -> str:
    """Lowercase, strip the aggregator's ' - Source' suffix, collapse
    punctuation and whitespace. Used both for dedup keys and as the basis
    for keyword matching in scoring.py."""
    t = unicodedata.normalize("NFKC", title or "")
    t = strip_source_suffix(t)
    t = t.lower()
    t = _PUNCT.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    return t


def title_tokens(title: str) -> frozenset:
    return frozenset(normalize_title(title).split())


def stable_id(*parts: str) -> str:
    """Deterministic short id: same input -> same id, forever. Used so a
    headline/policy item/catalyst keeps the same id across runs (the thesis
    cites these ids, so they must not reshuffle every refresh)."""
    h = hashlib.sha256("|".join(parts).encode("utf-8", "ignore")).hexdigest()
    return h[:16]


T = TypeVar("T")


def is_near_duplicate(a: str, b: str, *, min_overlap: float = 0.72) -> bool:
    """True if the normalized token sets of `a` and `b` overlap enough to be
    the same story. Jaccard-ish but biased toward the SMALLER set (a short
    aggregator rewrite should still match the fuller original headline)."""
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return ta == tb
    if ta == tb:
        return True
    inter = len(ta & tb)
    smaller = min(len(ta), len(tb))
    return smaller > 0 and inter / smaller >= min_overlap


def dedup_by_title(items: Sequence[T], title_fn, *, keep: str = "first") -> List[T]:
    """Drop near-duplicate items by title, keeping the first (default) or
    the item chosen by `keep='best'` ranking left-to-right as better, i.e.
    the caller should pre-sort so the one worth keeping comes first."""
    out: List[T] = []
    kept_titles: List[str] = []
    for item in items:
        t = title_fn(item)
        if any(is_near_duplicate(t, kt) for kt in kept_titles):
            continue
        out.append(item)
        kept_titles.append(t)
    return out


def dedup_pairs(items: Iterable[Tuple[str, T]]) -> List[Tuple[str, T]]:
    """Dedup a (title, payload) iterable, preserving input order."""
    out: List[Tuple[str, T]] = []
    kept: List[str] = []
    for title, payload in items:
        if any(is_near_duplicate(title, kt) for kt in kept):
            continue
        out.append((title, payload))
        kept.append(title)
    return out
