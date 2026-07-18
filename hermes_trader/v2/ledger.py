"""v2 shadow-ledger adapter — reuse agents/shadow_ledger, never fork it.

The unified shadow ledger (append-only jsonl per book + scripts/shadow_status.py
forward grading) is the organ that killed news_catalyst and premium_fade honestly.
v2 reuses it VERBATIM by import; the only v2-specific rule is the book-name
namespace: every v2 book records under a `v2_` prefix so Phase-2 shadow-parity
records never mix with v1's live books in the same file, while the 5,700+
historical PIT records stay untouched in place.

Grading needs nothing new: `python scripts/shadow_status.py` already inventories
every `<state_dir>/shadow_ledger/*.jsonl`, so `v2_extreme_fade.jsonl` et al show
up in the same survey with the same VALIDATED/REFUTED/PENDING verdicts.
"""
from __future__ import annotations

from typing import Any, Dict, List

from hermes_trader.agents import shadow_ledger

V2_PREFIX = "v2_"


def book_name(book: str) -> str:
    """`extreme_fade` -> `v2_extreme_fade`; already-prefixed names pass through."""
    book = str(book)
    return book if book.startswith(V2_PREFIX) else V2_PREFIX + book


def record(book: str, **kwargs: Any) -> Dict[str, Any]:
    """Append one signal record under the v2 namespace (best-effort, never raises)."""
    return shadow_ledger.record(book_name(book), **kwargs)


def record_many(book: str, rows: List[Dict[str, Any]]) -> int:
    """Append a list of candidate kwargs-dicts. Returns count written."""
    return shadow_ledger.record_many(book_name(book), rows)


def load(book: str) -> List[Dict[str, Any]]:
    """Read back this v2 book's records (test/verification helper)."""
    return shadow_ledger.load(book_name(book))
