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

STILL LOAD-BEARING AFTER THE BOOKS AND THE EXECUTOR ARE GONE
`book_params()` is not book-only infrastructure: `services.risk.books.
main_engine_spec` calls it (keyed on `"main_engine"`) to build the one
BookSpec the `risk_limits`/`risk_size_trade`/`place_bracket` feasibility
check prices against — the one entry path left since the strategy books
(wave 1) and the autonomous executor (wave 2) were both retired. The
floor-safety invariant below matters more with everything else gone, not
less: there is only one sizing fallback left, and this is it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pathiel.agents.book_params import (
    FLOOR_LEVERAGE, FLOOR_NOTIONAL_USD, FLOOR_STOP_PCT, book_params)

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


def test_floors_are_never_more_aggressive_than_the_live_risk_profile():
    """Guards the floors themselves: raising FLOOR_LEVERAGE to 10 would
    reintroduce the exact bug, and this fails if anyone does. 2026-10-23
    (wave 2): the live config no longer carries a top-level `leverage` /
    `default_stop_pct` (both retired with the autonomous executor, the one
    thing that read them as a global default) — the floor's job now is only
    to never exceed what the operator's saved risk profile allows."""
    assert FLOOR_LEVERAGE <= int(CFG["risk_profile"]["max_leverage"])


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
    """services.risk.books.main_engine_spec calls book_params(config,
    "main_engine") — the one entry path left since the strategy books (wave
    1) and the autonomous executor (wave 2) were both retired. No
    "main_engine" block, and no top-level `leverage`/`default_stop_pct`
    default, exists in the live config any more, so this always lands on
    the conservative floor."""
    p = book_params(CFG, "main_engine")
    assert (p.leverage, p.stop_pct) == (FLOOR_LEVERAGE, FLOOR_STOP_PCT)


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
