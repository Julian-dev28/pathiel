"""Unit + property tests for services/risk/derive.py.

The property tests are the ones Julien explicitly asked for: equities
[5, 8, 10, 12, 15, 20, 25, 50, 100, 250, 500, 1k, 5k, 10k, 100k] times every
preset (plus "auto", which is the real-world default and the one path where
the preset ITSELF changes mid-sweep at the $1k/$10k/$50k boundaries)."""
from __future__ import annotations

import pytest

from contracts.risk.models import RiskProfile
from services.risk.derive import (
    BookSpec,
    VenueRules,
    _derive_max_open_positions,
    _needs_equity_usd,
    book_feasibility,
    derive_limits,
)

EQUITIES = [5, 8, 10, 12, 15, 20, 25, 50, 100, 250, 500, 1000, 5000, 10000, 100000]
PRESET_NAMES = ["starter", "small", "standard", "pro", "auto"]

BOOKS = [
    BookSpec(name="tight_stop", stop_pct=6.0, max_leverage=10),
    BookSpec(name="mid_stop", stop_pct=15.0, max_leverage=3),
    BookSpec(name="wide_stop", stop_pct=21.0, max_leverage=1),
    BookSpec(name="disabled_book", enabled=False, stop_pct=15.0),
    BookSpec(name="ladder", fixed_min_equity_usd=525.0, fixed_description="five rungs"),
    BookSpec(name="unsized"),
]

VENUE = VenueRules(min_order_notional_usd=10.0, free_margin_floor_frac=0.10)


# ─────────────────────────────────────────────────────────────── unit tests

def test_needs_equity_usd_bump_is_never_larger_than_organic():
    organic = _needs_equity_usd(15.0, 3.0, False, 0.0, 10.0)
    bumped = _needs_equity_usd(15.0, 3.0, True, 6.0, 10.0)
    assert bumped <= organic


def test_needs_equity_usd_degenerate_inputs_return_inf():
    assert _needs_equity_usd(0.0, 3.0, True, 6.0, 10.0) == float("inf")
    assert _needs_equity_usd(15.0, 0.0, True, 6.0, 10.0) == float("inf")


def test_derive_max_open_positions_floors_at_one():
    n = _derive_max_open_positions(
        equity_usd=0.01, risk_per_trade_usd=0.0003, daily_loss_limit_usd=0.0008,
        max_leverage=10.0, positions_cap=1, venue=VENUE,
    )
    assert n == 1


def test_fixed_book_below_floor_reports_needs_equity_and_shortfall():
    limits = derive_limits(15.19, RiskProfile(preset="starter"),
                           [BookSpec(name="ladder", fixed_min_equity_usd=525.0,
                                    fixed_description="five rungs of $10.50 each at 1x")],
                           VenueRules(min_order_notional_usd=10.5))
    row = book_feasibility(limits, "ladder")
    assert row.can_trade is False
    assert row.needs_equity_usd == 525.0
    assert "$509.81" in row.reason  # 525 - 15.19
    assert "525" in row.reason


def test_fixed_book_above_floor_can_trade():
    limits = derive_limits(600.0, RiskProfile(preset="starter"),
                           [BookSpec(name="ladder", fixed_min_equity_usd=525.0,
                                    fixed_description="five rungs")],
                           VenueRules())
    row = book_feasibility(limits, "ladder")
    assert row.can_trade is True
    assert row.needs_equity_usd is None


def test_disabled_book_reports_disabled_not_a_dollar_amount():
    limits = derive_limits(1_000_000.0, RiskProfile(preset="pro"),
                           [BookSpec(name="x", enabled=False, stop_pct=15.0)], VenueRules())
    row = book_feasibility(limits, "x")
    assert row.can_trade is False
    assert row.reason == "Book is disabled."
    assert row.needs_equity_usd is None


def test_unsized_book_is_never_tradeable_but_always_has_a_reason():
    limits = derive_limits(1_000_000.0, RiskProfile(preset="pro"), [BookSpec(name="x")], VenueRules())
    row = book_feasibility(limits, "x")
    assert row.can_trade is False
    assert row.reason


def test_book_feasibility_lookup_returns_none_for_unknown_book():
    limits = derive_limits(100.0, RiskProfile(preset="starter"), BOOKS, VENUE)
    assert book_feasibility(limits, "does_not_exist") is None


def test_bumped_book_names_the_bump_in_its_reason():
    limits = derive_limits(15.0, RiskProfile(preset="starter"),
                           [BookSpec(name="tight_stop", stop_pct=6.0, max_leverage=10)], VENUE)
    row = book_feasibility(limits, "tight_stop")
    assert row.can_trade is True
    assert row.bumped_to_min is True
    assert "minimum" in row.reason.lower()


def test_negative_equity_raises():
    with pytest.raises(ValueError):
        derive_limits(-1.0, RiskProfile(), BOOKS, VENUE)


def test_empty_book_list_is_well_formed_and_cannot_trade():
    limits = derive_limits(1000.0, RiskProfile(preset="starter"), [], VENUE)
    assert limits.books == []
    assert limits.can_trade is False
    assert limits.min_equity_to_trade_usd == 0.0
    assert limits.headline


def test_profile_echoed_back_is_fully_resolved_every_field_set():
    limits = derive_limits(100.0, RiskProfile(preset="auto"), BOOKS, VENUE)
    p = limits.profile
    assert p.preset != "auto"
    for field in ("risk_per_trade_pct", "daily_loss_limit_pct", "max_leverage",
                 "max_open_positions", "allow_min_order_bump", "max_bumped_risk_pct"):
        assert getattr(p, field) is not None, f"{field} was left unresolved"


def test_explicit_max_open_positions_overrides_derivation():
    limits = derive_limits(100.0, RiskProfile(preset="starter", max_open_positions=7), BOOKS, VENUE)
    assert limits.max_open_positions == 7
    assert limits.profile.max_open_positions == 7


def test_real_dollar_padded_venue_minimum_is_respected():
    """The live wiring passes pathiel.client.exchange.MIN_ORDER_USD (10.5),
    not the contract's illustrative 10.0 default — a bumped book must show
    the REAL padded minimum, not a number Hyperliquid would still reject."""
    limits = derive_limits(15.19, RiskProfile(preset="starter"),
                           [BookSpec(name="b", stop_pct=15.0, max_leverage=3)],
                           VenueRules(min_order_notional_usd=10.5))
    row = book_feasibility(limits, "b")
    if row.bumped_to_min:
        assert row.notional_usd == 10.5


# ────────────────────────────────────────────────────────── property tests

@pytest.mark.parametrize("preset", PRESET_NAMES)
def test_every_book_row_has_a_reason(preset):
    for eq in EQUITIES:
        limits = derive_limits(eq, RiskProfile(preset=preset), BOOKS, VENUE)
        for row in limits.books:
            assert row.reason, f"preset={preset} eq={eq} book={row.book} has no reason"


@pytest.mark.parametrize("preset", PRESET_NAMES)
def test_risk_never_exceeds_its_cap_except_through_the_documented_bump(preset):
    for eq in EQUITIES:
        limits = derive_limits(eq, RiskProfile(preset=preset), BOOKS, VENUE)
        for row in limits.books:
            if not row.can_trade or row.risk_pct is None:
                continue
            cap = (limits.profile.max_bumped_risk_pct if row.bumped_to_min
                  else limits.profile.risk_per_trade_pct)
            assert cap is not None
            assert row.risk_pct <= cap + 1e-6, (
                f"preset={preset} eq={eq} book={row.book} risk_pct={row.risk_pct} "
                f"exceeds cap={cap} (bumped={row.bumped_to_min})")


@pytest.mark.parametrize("preset", PRESET_NAMES)
def test_headline_agrees_with_can_trade(preset):
    for eq in EQUITIES:
        limits = derive_limits(eq, RiskProfile(preset=preset), BOOKS, VENUE)
        starts_add = limits.headline.startswith("Add $")
        if limits.can_trade:
            assert not starts_add, f"preset={preset} eq={eq}: can_trade but {limits.headline!r}"
        else:
            assert starts_add or "No book is configured" in limits.headline, (
                f"preset={preset} eq={eq}: not can_trade but {limits.headline!r}")


@pytest.mark.parametrize("preset", PRESET_NAMES)
def test_more_equity_never_shrinks_the_feasible_book_set_or_drops_max_open(preset):
    prev_feasible: set = set()
    prev_max_open = 0
    for eq in EQUITIES:
        limits = derive_limits(eq, RiskProfile(preset=preset), BOOKS, VENUE)
        feasible = {b.book for b in limits.books if b.can_trade}
        assert feasible.issuperset(prev_feasible), (
            f"preset={preset} eq={eq}: feasible set shrank, "
            f"prev={sorted(prev_feasible)} now={sorted(feasible)}")
        assert limits.max_open_positions >= prev_max_open, (
            f"preset={preset} eq={eq}: max_open_positions dropped "
            f"{prev_max_open} -> {limits.max_open_positions}")
        prev_feasible, prev_max_open = feasible, limits.max_open_positions


@pytest.mark.parametrize("preset", PRESET_NAMES)
def test_max_open_positions_always_in_contract_bounds(preset):
    for eq in EQUITIES:
        limits = derive_limits(eq, RiskProfile(preset=preset), BOOKS, VENUE)
        assert 1 <= limits.max_open_positions <= 50


@pytest.mark.parametrize("preset", PRESET_NAMES)
def test_derive_limits_is_pure_same_inputs_same_outputs(preset):
    for eq in (5, 15.19, 1000, 100000):
        a = derive_limits(eq, RiskProfile(preset=preset), BOOKS, VENUE)
        b = derive_limits(eq, RiskProfile(preset=preset), BOOKS, VENUE)
        assert a.model_dump() == b.model_dump()
