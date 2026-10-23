"""Omitting a sizing key must never make a position BIGGER.

THE BUG THIS PREVENTS
Before 2026-09-06 each of the seven strategy books (all retired 2026-10-23 —
headless desk, Claude trades through the MCP server) read its own sizing
with its own inline fallback, and the fallbacks disagreed:

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

STILL LOAD-BEARING AFTER THE BOOKS ARE GONE
`book_params()` is not book-only infrastructure: `pathiel.agents.executor.
maybe_execute` calls it (via `services.risk.books.main_engine_spec`) for
EVERY live trade now — main-engine / MCP-submitted entries resolve their
stop/leverage/notional through this exact precedence (book value > top-level
default > floor), just keyed on `"main_engine"` instead of a book name. The
floor-safety invariant below matters more with the books gone, not less:
there is only one entry path left, and this is its only sizing fallback.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pathiel.agents.book_params import (
    BACKUP_SL_MAX_FRAC_OF_LIQ, FLOOR_LEVERAGE, FLOOR_NOTIONAL_USD,
    FLOOR_STOP_PCT, LIQ_SAFETY_FRAC, book_params, max_stop_pct_at_leverage)

# Synthetic config keys standing in for the retired books — the precedence
# `book_params()` implements does not care what the key is named, and using
# a name no live config block exists for since 2026-10-23 is deliberate: the
# tests below exercise the FALLBACK path, not any particular book's tuned
# numbers.
SYNTHETIC_KEYS = ("alpha", "beta", "gamma")
_ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((_ROOT / ".agent-config.json").read_text())


# ------------------------------------------------------------- the invariant
@pytest.mark.parametrize("key", SYNTHETIC_KEYS)
def test_omitting_every_key_never_increases_size(key):
    """The whole point. Strip a config block to nothing and it must not size up."""
    cfg = {
        "leverage": 3, "default_notional_usd": 11.0, "default_stop_pct": 15.0,
        key: {"enabled": True, "leverage": 10, "notional_usd": 20.0, "stop_pct": 6.0},
    }
    full = book_params(cfg, key)
    stripped = book_params({**cfg, key: {}}, key)
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


def test_main_engine_resolves_through_the_same_precedence():
    """services.risk.books.main_engine_spec and executor.maybe_execute both
    call book_params(config, "main_engine") — the one entry path left since
    the strategy books were retired. No "main_engine" block exists in the
    live config, so this always lands on the top-level default / floor,
    exactly like any other key with no block of its own."""
    p = book_params(CFG, "main_engine")
    assert (p.leverage, p.stop_pct) == (int(CFG["leverage"]), float(CFG["default_stop_pct"]))


# ------------------------------------------------- the clamp, in one place
class TestClampIsOneSourceOfTruth:
    """`max_stop_pct_at_leverage` and `executor.stop_honoring_leverage` answer
    the same question from opposite ends. If they ever disagree, a trade
    requests a stop the executor silently narrows — which is the 2026-07-20
    bug, a different strategy rather than a smaller one.
    """

    @pytest.mark.parametrize("leverage", [1, 2, 3, 4, 5, 6, 8, 10, 12, 20])
    def test_the_widest_stop_round_trips_through_the_executor(self, leverage):
        """The property that makes "derived" mean something.

        Take the widest stop this leverage allows, hand it to the executor's
        cap, and the executor must leave the leverage alone. Off by a hair in
        either direction and this fails: too wide and the cap walks leverage
        down, too narrow and the derivation is leaving size on the table.
        """
        from pathiel.agents.executor import stop_honoring_leverage
        widest = max_stop_pct_at_leverage(leverage)
        assert stop_honoring_leverage(leverage, widest) == leverage

    @pytest.mark.parametrize("leverage", [2, 3, 4, 6, 10, 20])
    def test_a_hair_wider_is_rejected(self, leverage):
        """The other half: the boundary is a boundary, not a suggestion."""
        from pathiel.agents.executor import stop_honoring_leverage
        too_wide = max_stop_pct_at_leverage(leverage) * 1.02
        assert stop_honoring_leverage(leverage, too_wide) < leverage

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
        """The constants live in book_params so a research/grading script
        could read them without importing a signing stack. The executor must
        still be reading the same ones."""
        import inspect

        from pathiel.agents import executor
        sig = inspect.signature(executor.stop_honoring_leverage)
        assert sig.parameters["max_frac_of_liq"].default == BACKUP_SL_MAX_FRAC_OF_LIQ
        assert sig.parameters["liq_safety_frac"].default == LIQ_SAFETY_FRAC


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
