from __future__ import annotations

from contracts.risk.models import RiskProfile
from services.risk.migrate import migrate_legacy


def test_empty_config_degrades_to_safe_defaults_never_raises():
    profile, report = migrate_legacy({})
    RiskProfile.model_validate(profile.model_dump())   # round-trips the contract
    assert profile.preset == "auto"
    assert 0 < profile.risk_per_trade_pct <= 25
    assert report  # always explains itself, even on an empty config


def test_none_config_is_the_same_as_empty():
    a, _ = migrate_legacy(None)
    b, _ = migrate_legacy({})
    assert a.model_dump() == b.model_dump()


def test_bump_mult_zero_turns_off_bump_allowance():
    profile, report = migrate_legacy({"min_order_bump_max_mult": 0})
    assert profile.allow_min_order_bump is False
    assert profile.max_bumped_risk_pct is None
    assert any("False" in r for r in report)


def test_bump_mult_missing_defaults_to_legacy_default_of_2x_i_e_on():
    profile, _ = migrate_legacy({})
    assert profile.allow_min_order_bump is True
    assert profile.max_bumped_risk_pct is not None


def test_max_concurrent_carried_over_when_in_bounds():
    profile, report = migrate_legacy({"max_concurrent": 3})
    assert profile.max_open_positions == 3
    assert any("max_concurrent(3)" in r for r in report)


def test_max_concurrent_out_of_bounds_is_left_unset_not_clamped_silently():
    profile, report = migrate_legacy({"max_concurrent": 999})
    assert profile.max_open_positions is None
    assert any("left unset" in r for r in report)


def test_max_daily_loss_pct_present_is_preferred_and_converted_to_percent():
    profile, report = migrate_legacy({"max_daily_loss_pct": 0.121})
    assert profile.daily_loss_limit_pct == 12.1
    assert any("12.10%" in r for r in report)


def test_max_daily_loss_usd_alone_is_explained_not_silently_converted():
    profile, report = migrate_legacy({"max_daily_loss_usd": -100})
    assert profile.daily_loss_limit_pct is None
    assert any("FIXED dollar kill-switch" in r for r in report)


def test_leverage_clamped_to_contract_bound():
    profile, report = migrate_legacy({"leverage": 999})
    assert profile.max_leverage == 50.0
    assert any("clamped to 50" in r for r in report)


def test_risk_per_trade_pct_never_exceeds_contract_bound_even_on_extreme_config():
    profile, _ = migrate_legacy({"equity_fraction_per_trade": 5.0, "leverage": 50,
                                 "news_surge_short": {"enabled": True, "stop_pct": 50.0}})
    assert 0 < profile.risk_per_trade_pct <= 25


def test_min_tradable_equity_usd_is_explained_as_no_longer_a_global_block():
    _profile, report = migrate_legacy({"min_tradable_equity_usd": 26.0})
    assert any("no longer a global trading block" in r for r in report)


def test_precedence_statement_is_always_the_first_report_line():
    _profile, report = migrate_legacy({})
    assert report[0].startswith("Precedence:")


def test_representative_stop_prefers_enabled_books_over_disabled_ones():
    """news_surge_short enabled at 6% vs xs_reversal enabled with no override
    (floors to book_params.py's 15%) — median of an enabled-only set must not
    be dragged by a book that ISN'T actually enabled."""
    cfg = {
        "news_surge_short": {"enabled": False, "stop_pct": 6.0},
        "xs_reversal": {"enabled": True},   # resolves to FLOOR_STOP_PCT=15.0
    }
    profile, _ = migrate_legacy(cfg)
    # equity_frac default 0.2 x leverage default 12 x stop(15%) x 100 = 36%, clamped to 25
    assert profile.risk_per_trade_pct == 25.0


def test_real_legacy_shape_produces_a_valid_profile():
    """A shape matching what docs/overhaul/PROFIT_AUDIT.md describes as the
    live $15.19 account's config: an explicit operator min_tradable_equity_usd
    override well above current equity, a percent-based daily loss limit, and
    one actively-traded book with no stop override."""
    cfg = {
        "mode": "LIVE",
        "equity_fraction_per_trade": 0.35,
        "leverage": 3,
        "max_concurrent": 3,
        "max_daily_loss_pct": 0.121,
        "min_order_bump_max_mult": 2.0,
        "min_tradable_equity_usd": 26.0,
        "xs_reversal": {"enabled": True, "shadow_only": False},
    }
    profile, report = migrate_legacy(cfg)
    RiskProfile.model_validate(profile.model_dump())
    assert profile.max_open_positions == 3
    assert profile.daily_loss_limit_pct == 12.1
    assert profile.max_leverage == 3.0
    assert profile.allow_min_order_bump is True
    assert len(report) >= 7
