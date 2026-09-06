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
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pathia.agents.book_params import (
    FLOOR_LEVERAGE, FLOOR_NOTIONAL_USD, FLOOR_STOP_PCT, book_params)

BOOKS = ("xs_reversal", "news_surge_short", "news_surge_multi",
         "unlock_short", "social_trending")
CFG = json.loads((Path(__file__).resolve().parents[1] / ".agent-config.json").read_text())


# ------------------------------------------------------------- the invariant
@pytest.mark.parametrize("book", BOOKS)
def test_omitting_every_key_never_increases_size(book):
    """The whole point. Strip a book to nothing and it must not size up."""
    full = book_params(CFG, book)
    stripped = book_params({**CFG, book: {}}, book)
    assert stripped.leverage <= full.leverage
    assert stripped.notional_usd <= full.notional_usd
    assert stripped.stop_pct <= full.stop_pct


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


def test_every_live_book_resolves_identically():
    """All five books took the same numbers before the top-level defaults
    existed. If one silently diverges, the config was edited in a way the
    operator did not intend."""
    resolved = {b: book_params(CFG, b) for b in BOOKS}
    assert len(set(resolved.values())) == 1, resolved


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
    for b in BOOKS:
        assert book_params(CFG, b).notional_usd == 0.0
