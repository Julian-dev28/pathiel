"""Wiring-integrity checks that run on every commit, not just at review time
(operator instruction 2026-07-20: check every integration for collisions and
leaks, before/during/after — and prefer integration tests over mocked units
for anything touching shared state).

Two kinds of check live here:
1. Static scans across hermes_trader/agents/*.py: no two live books may reuse
   a _BOOK_NAME or a state_file(...) path. A collision here is silent —
   nothing raises, two books just corrupt each other's persisted state or
   fight over the same ledger rows.
2. Real-ClaimsRegistry integration tests (not mocks) proving that two books
   which can plausibly target the SAME coin from the SAME underlying trigger
   (an inverse pair, or a shadow/live pair) cannot both hold it at once.
"""
from __future__ import annotations

import glob
import re
from pathlib import Path

import pytest

_AGENTS_DIR = Path(__file__).resolve().parents[1] / "hermes_trader" / "agents"


def _scan(pattern: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path in sorted(_AGENTS_DIR.glob("*.py")):
        src = path.read_text()
        for m in re.finditer(pattern, src):
            hits.setdefault(m.group(1), []).append(path.name)
    return hits


def _all_book_name_sites() -> dict[str, list[str]]:
    """Union of both ways a module names its ledger book: a `_BOOK_NAME`
    constant (most live-wired modules) or an inline shadow_ledger.record(...)
    / record_many(...) string literal (mover_recorders.py uses this — it has
    no _BOOK_NAME constant at all, so scanning only that pattern silently
    missed 4 real book names until this test was widened 2026-07-20)."""
    sites = _scan(r'_BOOK_NAME\s*=\s*"([^"]+)"')
    for k, v in _scan(r'shadow_ledger\.record(?:_many)?\(\s*"([^"]+)"').items():
        sites.setdefault(k, []).extend(v)
    return sites


def test_no_two_live_modules_share_a_book_name():
    dupes = {k: v for k, v in _all_book_name_sites().items() if len(set(v)) > 1}
    assert dupes == {}, f"duplicate ledger book name across modules: {dupes}"


def test_no_two_modules_share_a_state_file_path():
    dupes = {k: v for k, v in _scan(r'state_file\(\s*"([^"]+)"').items() if len(v) > 1}
    assert dupes == {}, f"duplicate state_file(...) path across modules: {dupes}"


def test_news_surge_short_registered_everywhere_it_needs_to_be():
    """A book that opens live orders but is missing from claim rights or
    priority attribution is a silent leak: it trades, but claims/PnL don't
    know it exists (the exact 2026-07-18 demolition trap, in reverse)."""
    import scripts.pnl_by_book as pbb
    from hermes_trader.agents.rebalancer_owned import active_claim_books
    from hermes_trader.agents import news_surge_short_live as nssl

    assert nssl._BOOK_NAME in active_claim_books()
    assert nssl._BOOK_NAME in pbb.BOOK_PRIORITY


# --------------------------------------------------------------- real-registry collisions
def test_news_surge_short_and_engulf_short_cannot_both_claim_the_same_coin(tmp_path):
    """Two unrelated live books racing the same claims file: whichever
    claims first wins, the second must see it and back off. Real
    ClaimsRegistry, not a mock — proves the file-backed contract holds."""
    import hermes_trader.agents.rebalancer_owned as ro

    claims_path = str(tmp_path / ".rebalancer_claims.json")
    registry = ro.ClaimsRegistry(claims_path).load()

    assert registry.claim("XPL", "engulf_short") is True
    assert "XPL" in registry.claimed_by_others("news_surge_short")
    assert registry.claim("XPL", "news_surge_short") is False
    registry.release("XPL", "engulf_short")
    assert registry.claim("XPL", "news_surge_short") is True
