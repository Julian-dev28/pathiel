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
    BACKUP_SL_MAX_FRAC_OF_LIQ, FLOOR_LEVERAGE, FLOOR_NOTIONAL_USD,
    FLOOR_STOP_PCT, LIQ_SAFETY_FRAC, book_params, max_stop_pct_at_leverage)

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


# ------------------------------------------------- the clamp, in one place
class TestClampIsOneSourceOfTruth:
    """`max_stop_pct_at_leverage` and `executor.stop_honoring_leverage` answer
    the same question from opposite ends. PROMOTE_STOP_PCT is derived from the
    first, and the executor enforces the second. If they ever disagree, a
    promoted book trades a stop the executor silently narrows — which is the
    2026-07-20 bug, a different strategy rather than a smaller one.
    """

    @pytest.mark.parametrize("leverage", [1, 2, 3, 4, 5, 6, 8, 10, 12, 20])
    def test_the_widest_stop_round_trips_through_the_executor(self, leverage):
        """The property that makes "derived" mean something.

        Take the widest stop this leverage allows, hand it to the executor's
        cap, and the executor must leave the leverage alone. Off by a hair in
        either direction and this fails: too wide and the cap walks leverage
        down, too narrow and the derivation is leaving size on the table.
        """
        from pathia.agents.executor import stop_honoring_leverage
        widest = max_stop_pct_at_leverage(leverage)
        assert stop_honoring_leverage(leverage, widest) == leverage

    @pytest.mark.parametrize("leverage", [2, 3, 4, 6, 10, 20])
    def test_a_hair_wider_is_rejected(self, leverage):
        """The other half: the boundary is a boundary, not a suggestion."""
        from pathia.agents.executor import stop_honoring_leverage
        too_wide = max_stop_pct_at_leverage(leverage) * 1.02
        assert stop_honoring_leverage(leverage, too_wide) < leverage

    def test_the_promotion_constant_is_derived_not_written(self):
        """PROMOTE_STOP_PCT must equal the clamp at PROMOTE_LEVERAGE.

        It was the literal 6.0 with a comment reading "== 60/10, exactly at the
        clamp boundary" — true until someone edited either number.
        """
        assert _AC.PROMOTE_STOP_PCT == max_stop_pct_at_leverage(_AC.PROMOTE_LEVERAGE)

    def test_the_derivation_tracks_a_change_to_either_input(self):
        """Change the leverage or the clamp and the stop follows.

        The whole point: two numbers that have to agree by hand eventually do
        not, so this asserts the dependency rather than the current values.
        """
        assert max_stop_pct_at_leverage(20) == max_stop_pct_at_leverage(10) / 2
        looser = max_stop_pct_at_leverage(10, max_frac_of_liq=0.80)
        assert looser > max_stop_pct_at_leverage(10, max_frac_of_liq=0.60)

    def test_the_liq_bound_binds_on_a_low_max_leverage_coin(self):
        """`1/lev` overstates the liquidation distance because maintenance
        margin eats into it. On a 3x-max coin the naive width bound would
        authorize a stop the position dies before reaching."""
        naive = max_stop_pct_at_leverage(3)
        with_maint = max_stop_pct_at_leverage(3, coin_max_leverage=3)
        assert with_maint < naive

    def test_no_bound_is_not_read_as_a_safe_stop(self):
        """Disabling both bounds means nothing constrains the stop here, which
        is not the same as any stop being fine. `inf` cannot be mistaken for a
        usable default the way a large float could."""
        import math
        assert math.isinf(
            max_stop_pct_at_leverage(10, max_frac_of_liq=0, liq_safety_frac=0))

    def test_the_shared_defaults_are_what_the_executor_uses(self):
        """The constants moved to book_params so the evidence loop could read
        them without importing a signing stack. The executor must still be
        reading the same ones."""
        import inspect

        from pathia.agents import executor
        sig = inspect.signature(executor.stop_honoring_leverage)
        assert sig.parameters["max_frac_of_liq"].default == BACKUP_SL_MAX_FRAC_OF_LIQ
        assert sig.parameters["liq_safety_frac"].default == LIQ_SAFETY_FRAC


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
