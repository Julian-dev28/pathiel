"""size_trade: one specific trade sized by the same rules as the books."""
from __future__ import annotations

import pytest

from contracts.risk.models import RiskProfile
from services.risk.derive import BookSpec, VenueRules, derive_limits
from services.risk.size import size_trade

VENUE = VenueRules(min_order_notional_usd=10.5)
STD = RiskProfile(preset="standard")


def test_side_is_inferred_from_the_stop():
    assert size_trade(20_000, STD, [], entry_px=100, stop_px=95, venue=VENUE).side == "long"
    assert size_trade(20_000, STD, [], entry_px=100, stop_px=105, venue=VENUE).side == "short"


@pytest.mark.parametrize("entry,stop", [(0, 95), (100, 0), (100, 100), (-1, 5)])
def test_bad_prices_never_size(entry, stop):
    t = size_trade(20_000, STD, [], entry_px=entry, stop_px=stop, venue=VENUE)
    assert t.can_trade is False and t.notional_usd is None and t.reason


def test_zero_equity_never_sizes():
    t = size_trade(0, STD, [], entry_px=100, stop_px=95, venue=VENUE)
    assert t.can_trade is False and "no equity" in t.reason


def test_risk_matches_the_profile_budget_on_a_normal_account():
    t = size_trade(20_000, STD, [], entry_px=100, stop_px=95, leverage=5, venue=VENUE)
    # standard: 1.5% of $20k = $300 at a 5% stop -> $6,000 notional
    assert t.can_trade and t.risk_usd == pytest.approx(300.0, abs=0.01)
    assert t.notional_usd == pytest.approx(6_000.0, abs=0.01)
    assert t.margin_usd == pytest.approx(t.notional_usd / t.leverage, abs=0.01)


def test_same_answer_as_a_book_with_that_stop():
    """A manual trade is sized exactly like the bot sizes a book."""
    books = [BookSpec(name="b", stop_pct=5.0, max_leverage=5)]
    lim = derive_limits(20_000, STD, books, VENUE)
    t = size_trade(20_000, STD, books, entry_px=100, stop_px=95, leverage=5, venue=VENUE)
    assert t.notional_usd == pytest.approx(lim.books[0].notional_usd, abs=0.01)
    assert t.risk_usd == pytest.approx(lim.books[0].risk_usd, abs=0.01)


@pytest.mark.parametrize("asked,coin_max,profile_max,applied,capped", [
    (20, 10, 5, 5, "profile"),
    (20, 3, 10, 3, "coin"),
    (2, 20, 10, 2, None),
])
def test_leverage_is_the_smallest_cap_and_says_which(asked, coin_max, profile_max, applied, capped):
    prof = RiskProfile(preset="standard", max_leverage=profile_max)
    t = size_trade(20_000, prof, [], entry_px=100, stop_px=95, leverage=asked,
                   coin_max_leverage=coin_max, venue=VENUE)
    assert t.leverage == applied and t.leverage_capped_by == capped


def test_rounding_never_lands_under_the_venue_minimum():
    t = size_trade(15.21, RiskProfile(preset="starter"), [], entry_px=98.87, stop_px=88.22,
                   leverage=5, venue=VENUE, sz_decimals=2)
    assert t.can_trade
    assert t.size == round(t.size, 2)
    assert t.notional_usd >= VENUE.min_order_notional_usd


def test_targets_pay_in_the_calls_direction():
    long_t = size_trade(20_000, STD, [], entry_px=100, stop_px=95, targets=[110], venue=VENUE)
    short_t = size_trade(20_000, STD, [], entry_px=100, stop_px=105, targets=[90], venue=VENUE)
    for t in (long_t, short_t):
        assert t.targets[0].gain_usd > 0
        assert t.targets[0].r_multiple == pytest.approx(2.0, abs=0.01)   # 10% move / 5% stop


def test_too_small_without_a_bump_cannot_trade():
    prof = RiskProfile(preset="standard", allow_min_order_bump=False)
    t = size_trade(15, prof, [], entry_px=100, stop_px=90, venue=VENUE)
    assert t.can_trade is False and "minimum" in t.reason


def test_a_tiny_account_is_bumped_to_the_minimum_when_allowed():
    t = size_trade(15.21, RiskProfile(preset="starter"), [], entry_px=98.87, stop_px=96.0,
                   leverage=5, venue=VENUE)
    assert t.can_trade and t.notional_usd >= VENUE.min_order_notional_usd


def test_targets_that_pay_less_than_the_stop_are_warned_about():
    t = size_trade(20_000, STD, [], entry_px=100, stop_px=90, targets=[102, 105], venue=VENUE)
    assert t.warnings and "0.50R" in t.warnings[0]


def test_a_target_worth_the_risk_is_not_warned_about():
    t = size_trade(20_000, STD, [], entry_px=100, stop_px=95, targets=[102, 110], venue=VENUE)
    assert t.warnings == []
