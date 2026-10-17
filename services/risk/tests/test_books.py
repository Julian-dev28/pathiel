from __future__ import annotations

from services.risk import books


def test_book_names_mirror_the_live_claims_registry_exactly():
    """See books.py's module docstring: BOOK_NAMES is a deliberate
    duplicate of rebalancer_owned._ACTIVE_CLAIM_BOOKS (not an import, to
    avoid pulling a live-signing module into this package). This test is
    the tripwire that catches the two sets drifting apart."""
    from pathiel.agents.rebalancer_owned import _ACTIVE_CLAIM_BOOKS
    assert set(books.BOOK_NAMES) == set(_ACTIVE_CLAIM_BOOKS)


def test_drawdown_ladder_fixed_minimum_matches_the_live_formula():
    """Re-derived, not imported (see books.py's module docstring) — this
    test is the tripwire for THAT duplication: if drawdown_ladder_live.py's
    constants ever change, this fails instead of silently going stale."""
    from pathiel.agents.drawdown_ladder_live import (
        DEFAULT_SLEEVE_FRAC, LEVERAGE, RUNGS, UNIVERSE, min_equity_to_trade)
    live_value = min_equity_to_trade(DEFAULT_SLEEVE_FRAC)
    assert books._DRAWDOWN_LADDER_MIN_EQUITY_USD == live_value
    assert books._FIXED_BOOKS["drawdown_ladder"]["fixed_min_equity_usd"] == live_value
    assert live_value == 525.0
    # sanity on the formula itself, independent of either module
    from pathiel.client.exchange import MIN_ORDER_USD
    assert live_value == MIN_ORDER_USD * RUNGS * len(UNIVERSE) / LEVERAGE / DEFAULT_SLEEVE_FRAC


def test_config_key_for_the_one_naming_split():
    assert books.config_key_for("unlock_short_runin") == "unlock_short"
    for name in books.BOOK_NAMES:
        if name == "unlock_short_runin":
            continue
        assert books.config_key_for(name) == name


def test_risk_scaled_names_excludes_the_fixed_and_unsized_books():
    assert set(books.RISK_SCALED_BOOK_NAMES) == set(books.BOOK_NAMES) - {"drawdown_ladder", "copy_trade"}


def test_book_spec_from_config_disabled_when_block_absent():
    spec = books.book_spec_from_config({}, "xs_reversal")
    assert spec.enabled is False


def test_book_spec_from_config_disabled_when_shadow_only():
    cfg = {"xs_reversal": {"enabled": True, "shadow_only": True}}
    spec = books.book_spec_from_config(cfg, "xs_reversal")
    assert spec.enabled is False


def test_book_spec_from_config_enabled_reads_resolved_sizing():
    cfg = {"news_surge_short": {"enabled": True, "shadow_only": False,
                                "leverage": 10, "stop_pct": 6.0}}
    spec = books.book_spec_from_config(cfg, "news_surge_short")
    assert spec.enabled is True
    assert spec.stop_pct == 6.0
    assert spec.max_leverage == 10.0


def test_book_spec_from_config_applies_the_naming_split():
    cfg = {"unlock_short": {"enabled": True, "shadow_only": False, "stop_pct": 12.0}}
    spec = books.book_spec_from_config(cfg, "unlock_short_runin")
    assert spec.enabled is True
    assert spec.stop_pct == 12.0


def test_book_spec_from_config_coin_max_leverage_tightens_the_cap():
    cfg = {"xs_reversal": {"enabled": True, "shadow_only": False, "leverage": 20}}
    spec = books.book_spec_from_config(cfg, "xs_reversal", coin_max_leverage=5)
    assert spec.max_leverage == 5.0   # the tighter of 20 (config) and 5 (coin)


def test_book_spec_from_config_drawdown_ladder_is_fixed_not_risk_scaled():
    cfg = {"drawdown_ladder": {"enabled": True, "shadow_only": False}}
    spec = books.book_spec_from_config(cfg, "drawdown_ladder")
    assert spec.enabled is True
    assert spec.fixed_min_equity_usd == 525.0
    assert spec.stop_pct is None


def test_all_book_specs_returns_exactly_the_seven_books_in_order():
    specs = books.all_book_specs({})
    assert [s.name for s in specs] == list(books.BOOK_NAMES)
    assert len(specs) == 7


def test_copy_trade_is_neither_risk_scaled_nor_fixed():
    """A third shape (see module docstring): no stop_pct, no
    fixed_min_equity_usd, so derive_limits reports it honestly as unsized
    rather than guessing a stop for a leader-mirroring book."""
    cfg = {"copy_trade": {"enabled": True, "shadow_only": False, "leverage_mult": 1.0}}
    spec = books.book_spec_from_config(cfg, "copy_trade")
    assert spec.enabled is True
    assert spec.stop_pct is None
    assert spec.fixed_min_equity_usd is None
    assert "copy_trade" not in books.RISK_SCALED_BOOK_NAMES
    from services.risk.derive import VenueRules, book_feasibility, derive_limits
    from contracts.risk.models import RiskProfile
    limits = derive_limits(1_000_000.0, RiskProfile(preset="pro"), [spec], VenueRules())
    row = book_feasibility(limits, "copy_trade")
    assert row.can_trade is False
    assert "no sizing rule" in row.reason
