"""size_trade: one specific trade sized by the same rules as the books."""
from __future__ import annotations

import pytest

from contracts.risk.models import RiskProfile
from services.risk.derive import BookSpec, VenueRules, derive_limits
from services.risk.size import equity_excluding_position_pnl, size_trade, topup_room

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


# ── equity_excluding_position_pnl / topup_room (2026-11-17 fix) ─────────────
#
# Live, 2026-11-16/17: the oil desk brief's "full size" for xyz:BRENTOIL (what
# a same-side top-up is measured against) read 13.59, then 19.62, then 7.71
# across ticks with an unchanged thesis. Root cause: sizing used the
# account's TOTAL equity, which includes the open position's own unrealized
# P&L, so the position's own mark-to-market swings fed back into its own
# sizing target. These back that P&L out first.

def test_equity_excluding_position_pnl_backs_out_a_gain():
    assert equity_excluding_position_pnl(94.0, 24.0) == pytest.approx(70.0)


def test_equity_excluding_position_pnl_backs_out_a_loss():
    # a position down $10 ADDS $10 back: the account would be $10 richer
    # without that loss.
    assert equity_excluding_position_pnl(60.0, -10.0) == pytest.approx(70.0)


def test_equity_excluding_position_pnl_is_a_noop_when_flat():
    assert equity_excluding_position_pnl(70.0, 0.0) == pytest.approx(70.0)
    assert equity_excluding_position_pnl(70.0) == pytest.approx(70.0)   # default


def test_equity_excluding_position_pnl_floors_at_zero():
    assert equity_excluding_position_pnl(5.0, 50.0) == 0.0


def test_topup_room_is_full_size_minus_held():
    assert topup_room(10.4, 1.62) == pytest.approx(8.78)


def test_topup_room_never_goes_negative():
    assert topup_room(9.5, 10.4) == 0.0


def test_topup_room_ignores_the_helds_sign():
    """A short position's held size is negative (signed szi); the room to
    add more is the same either way."""
    assert topup_room(10.4, -1.62) == pytest.approx(8.78)


def test_full_size_does_not_change_when_a_position_opens():
    """The regression the live bug violated: sizing a FRESH entry for this
    desk must answer the same whether the desk is flat or already holds the
    position being measured against -- the position's own uPnL must never
    feed back into its own sizing target.

    A desk with $70 of capital, flat, sizes to some full size S. The SAME
    desk, now holding a position that has made +$24 (so account equity
    reads $94, not $70): WITHOUT the fix, size_trade(94 * share, ...) sizes
    a bigger "full size" purely because of the position's own paper gain.
    WITH the fix (equity_excluding_position_pnl backs the $24 back out
    first), it sizes the exact same S again.
    """
    profile = RiskProfile(preset="custom", risk_per_trade_pct=100.0, daily_loss_limit_pct=100.0,
                          max_leverage=20.0, allow_min_order_bump=True, max_bumped_risk_pct=100.0)
    kwargs = dict(entry_px=89.4062, stop_px=88.64, leverage=20.0, coin_max_leverage=20.0,
                 venue=VENUE, sz_decimals=2)

    flat_equity = 70.0
    flat_size = size_trade(flat_equity, profile, [], **kwargs)

    held_upnl = 24.0
    total_equity_with_position_open = flat_equity + held_upnl   # what the venue reports: $94
    buggy_size = size_trade(total_equity_with_position_open, profile, [], **kwargs)
    fixed_equity = equity_excluding_position_pnl(total_equity_with_position_open, held_upnl)
    fixed_size = size_trade(fixed_equity, profile, [], **kwargs)

    assert fixed_equity == pytest.approx(flat_equity)
    assert fixed_size.size == pytest.approx(flat_size.size)
    # the bug this guards against: sizing straight off the inflated total
    # really did move full size, which is exactly what made it unstable.
    assert buggy_size.size != pytest.approx(flat_size.size)


@pytest.mark.parametrize("held_upnl,label", [(24.0, "a gain like the live +$24"),
                                             (-15.0, "a loss"), (0.0, "flat/no pnl yet")])
def test_full_size_is_stable_across_a_range_of_the_positions_own_pnl(held_upnl, label):
    """Same claim as above, swept over gain/loss/flat: full size is a
    function of the desk's underlying capital alone, never of how the held
    position happens to be doing right now."""
    profile = RiskProfile(preset="custom", risk_per_trade_pct=100.0, daily_loss_limit_pct=100.0,
                          max_leverage=20.0, allow_min_order_bump=True, max_bumped_risk_pct=100.0)
    kwargs = dict(entry_px=89.4062, stop_px=88.64, leverage=20.0, coin_max_leverage=20.0,
                 venue=VENUE, sz_decimals=2)
    base_equity = 70.0
    expected = size_trade(base_equity, profile, [], **kwargs).size
    observed_total_equity = base_equity + held_upnl
    stable_equity = equity_excluding_position_pnl(observed_total_equity, held_upnl)
    got = size_trade(stable_equity, profile, [], **kwargs).size
    assert got == pytest.approx(expected), label
