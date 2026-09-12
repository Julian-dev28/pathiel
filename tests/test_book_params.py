"""Omitting a sizing key must never make a position BIGGER.

THE BUG THIS PREVENTS
Before 2026-09-06 each book read its own sizing with its own inline fallback,
and the fallbacks disagreed:

    book                leverage   notional   stop
    xs_reversal             1        $11      15%
    news_surge_short       10        $20      15%
    news_surge_multi       10        $20       6%
    unlock_short            1        $20      15%

Every book also carried an explicit value in .agent-config.json, so no fallback
ever fired and the disagreement was invisible. The config looked full of
redundant duplicates - six books repeating leverage 3, notional $11, stop 15% -
and deleting them, the obvious tidy-up, would have moved two books to 10x
leverage and $20 notional on a $34 account. The duplication WAS the safety.

These tests exist so that is never true again: the config can be tidied because
the fallback is correct, not because every value is spelled out.

WHY TWO GEOMETRIES ARE NOW LEGAL
`scripts/autonomous_cycle.py` promotes a book that clears its evidence bars, and
promotion writes a deliberate geometry: PROMOTE_NOTIONAL_USD / PROMOTE_LEVERAGE
/ PROMOTE_STOP_PCT. So "every book resolves identically" stopped being true the
first time the loop promoted one, and asserting it turned a working safety
system into four red tests that blocked every commit in the repo.

What replaced it is narrower and still catches the original landmine: books the
loop has NOT promoted must all resolve identically, and books it HAS promoted
must match the promotion geometry exactly. An unintended hand edit still fails;
the loop doing its job does not.

The size invariant is unchanged in intent and sharper in expression. Promotion
tightens the stop (15% -> 6%) while raising leverage (3 -> 10), so a per-field
comparison reads the wider fallback stop as "sizing up" when risk per trade
actually falls. The assertion is now on the thing that matters — notional,
leverage, and leverage x stop — rather than on each field in isolation.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from pathia.agents.book_params import (
    FLOOR_LEVERAGE, FLOOR_NOTIONAL_USD, FLOOR_STOP_PCT, book_params)

BOOKS = ("xs_reversal", "news_surge_short", "news_surge_multi",
         "unlock_short", "social_trending")
_ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((_ROOT / ".agent-config.json").read_text())

# Imported rather than copied: if the promotion geometry moves, these tests must
# move with it, and a second copy of the numbers is how the original bug got in.
_AC_SPEC = importlib.util.spec_from_file_location(
    "autonomous_cycle", _ROOT / "scripts" / "autonomous_cycle.py")
_AC = importlib.util.module_from_spec(_AC_SPEC)
_AC_SPEC.loader.exec_module(_AC)
PROMOTED = (_AC.PROMOTE_NOTIONAL_USD, _AC.PROMOTE_LEVERAGE, _AC.PROMOTE_STOP_PCT)


def _is_promoted(book: str) -> bool:
    """A book carrying the loop's exact promotion geometry.

    Read off the config rather than hardcoded, so this tracks whatever the
    evidence loop has promoted today instead of needing an edit each time.
    """
    p = book_params(CFG, book)
    return (p.notional_usd, p.leverage, p.stop_pct) == PROMOTED


BASELINE_BOOKS = tuple(b for b in BOOKS if not _is_promoted(b))
PROMOTED_BOOKS = tuple(b for b in BOOKS if _is_promoted(b))


# ------------------------------------------------------------- the invariant
@pytest.mark.parametrize("book", BOOKS)
def test_omitting_every_key_never_increases_size(book):
    """The whole point. Strip a book to nothing and it must not size up.

    Compared on risk rather than field by field. A promoted book runs a tighter
    stop at higher leverage, so its fallback stop is WIDER (15% against 6%) —
    which a per-field assertion reads as sizing up while the actual exposure per
    trade falls, because leverage drops 10 -> 3 at the same time. The product is
    what the account feels.
    """
    full = book_params(CFG, book)
    stripped = book_params({**CFG, book: {}}, book)
    assert stripped.leverage <= full.leverage, "fallback raised leverage"
    assert stripped.notional_usd <= full.notional_usd, "fallback raised notional"
    # Loss at the stop, as a fraction of margin: the number that decides how
    # much of the account one bad trade costs.
    assert stripped.leverage * stripped.stop_pct <= full.leverage * full.stop_pct, (
        "fallback raised risk per trade")


def test_an_empty_config_produces_the_smallest_placeable_position():
    """Not merely 'a' default - the SMALLEST one. A missing config is a broken
    config, and a broken config must not open a 10x position."""
    p = book_params({}, "anything")
    assert p.leverage == FLOOR_LEVERAGE == 1
    assert p.notional_usd == FLOOR_NOTIONAL_USD
    assert p.stop_pct == FLOOR_STOP_PCT


def test_floors_are_never_more_aggressive_than_the_live_config():
    """Guards the floors themselves: raising FLOOR_LEVERAGE to 10 would
    reintroduce the exact bug, and this fails if anyone does."""
    assert FLOOR_LEVERAGE <= int(CFG["leverage"])
    assert FLOOR_STOP_PCT <= float(CFG["default_stop_pct"])


# ------------------------------------------------------------- the precedence
def test_book_value_beats_the_top_level_default():
    cfg = {"leverage": 3, "default_notional_usd": 11.0, "default_stop_pct": 15.0,
           "b": {"leverage": 2, "notional_usd": 50.0, "stop_pct": 9.0}}
    p = book_params(cfg, "b")
    assert (p.leverage, p.notional_usd, p.stop_pct) == (2, 50.0, 9.0)


def test_top_level_default_fills_what_the_book_omits():
    cfg = {"leverage": 3, "default_notional_usd": 11.0, "default_stop_pct": 15.0,
           "b": {"enabled": True}}
    p = book_params(cfg, "b")
    assert (p.leverage, p.notional_usd, p.stop_pct) == (3, 11.0, 15.0)


def test_every_unpromoted_book_resolves_identically():
    """The tripwire for an unintended hand edit.

    Narrowed from "all five books" because promotion is a legitimate way for a
    book to diverge — see the note at the top of this file. Everything the loop
    has not promoted must still share one geometry.
    """
    if not BASELINE_BOOKS:
        pytest.skip("every book is promoted; nothing left to compare")
    resolved = {b: book_params(CFG, b) for b in BASELINE_BOOKS}
    assert len(set(resolved.values())) == 1, resolved


def test_promoted_books_carry_exactly_the_loops_geometry():
    """A promoted book must match `apply_action(..., "promote")` to the digit.

    Anything else means someone edited a promoted book by hand, which is the
    case the identical-resolution test used to catch and no longer can.
    """
    for book in PROMOTED_BOOKS:
        p = book_params(CFG, book)
        assert (p.notional_usd, p.leverage, p.stop_pct) == PROMOTED, book


def test_a_promoted_stop_stays_reachable_and_inside_liquidation():
    """The 2026-07-20 bug: executor.py clamps the backup stop to
    entry * 60/leverage percent, so a promoted book whose stop sits outside that
    trades a geometry it was never graded at."""
    for book in PROMOTED_BOOKS:
        p = book_params(CFG, book)
        assert p.stop_pct <= 60.0 / p.leverage, f"{book}: stop unreachable"
        assert p.stop_pct < 100.0 / p.leverage, f"{book}: stop past liquidation"


# ----------------------------------------------------------------- robustness
@pytest.mark.parametrize("junk", [None, "abc", [], {}])
def test_unparseable_values_fall_to_the_floor_rather_than_raising(junk):
    """A malformed config must not crash the trading loop mid-cycle, and must
    not be read as 'unlimited' either."""
    cfg = {"b": {"leverage": junk, "notional_usd": junk, "stop_pct": junk}}
    p = book_params(cfg, "b")
    assert p.leverage >= 1
    assert p.notional_usd > 0
    assert p.stop_pct > 0


def test_a_non_dict_book_entry_does_not_raise():
    assert book_params({"b": "not a dict"}, "b").leverage == FLOOR_LEVERAGE


def test_leverage_is_never_below_one():
    assert book_params({"b": {"leverage": 0}}, "b").leverage == 1
    assert book_params({"b": {"leverage": -5}}, "b").leverage == 1


# ------------------------------------------------- fraction-sizing convention
def test_zero_notional_means_equity_fraction_sizing():
    """0 is not 'no position'. It tells executor.py to size from
    strategy_book_equity_frac instead of a fixed dollar figure, which is how
    the live config deploys a percentage of the account."""
    assert float(CFG["default_notional_usd"]) == 0.0
    assert float(CFG["strategy_book_equity_frac"]) > 0
    for b in BASELINE_BOOKS:
        assert book_params(CFG, b).notional_usd == 0.0
    # A promoted book opts out on purpose: the loop grades a fixed dollar
    # position, so it must trade the size it was graded at, not a fraction that
    # drifts with equity.
    for b in PROMOTED_BOOKS:
        assert book_params(CFG, b).notional_usd == _AC.PROMOTE_NOTIONAL_USD
