from __future__ import annotations

import pytest

from contracts.risk.models import RiskProfile
from services.risk import presets


def test_resolve_preset_name_bands():
    assert presets.resolve_preset_name(0) == "starter"
    assert presets.resolve_preset_name(999.99) == "starter"
    assert presets.resolve_preset_name(1000) == "small"
    assert presets.resolve_preset_name(9999.99) == "small"
    assert presets.resolve_preset_name(10000) == "standard"
    assert presets.resolve_preset_name(49999.99) == "standard"
    assert presets.resolve_preset_name(50000) == "pro"
    assert presets.resolve_preset_name(10_000_000) == "pro"


@pytest.mark.parametrize("name", ["starter", "small", "standard", "pro"])
def test_every_preset_field_is_contract_valid(name):
    """Every preset's numbers must themselves satisfy RiskProfile's bounds —
    a preset that violates its own contract would fail at resolution time
    for every account auto-routed into it."""
    p = presets.PRESETS[name]
    RiskProfile(
        preset=name, risk_per_trade_pct=p.risk_per_trade_pct,
        daily_loss_limit_pct=p.daily_loss_limit_pct, max_leverage=p.max_leverage,
        allow_min_order_bump=p.allow_min_order_bump, max_bumped_risk_pct=p.max_bumped_risk_pct,
    )


def test_presets_tighten_as_equity_rises():
    """The four presets move in ONE direction as equity grows: risk%, daily
    loss% and leverage down; the position-count ceiling up. See
    services/risk/presets.py's module docstring for why this direction is
    the only defensible one."""
    order = [presets.PRESETS[n] for n in ("starter", "small", "standard", "pro")]
    for a, b in zip(order, order[1:]):
        assert a.risk_per_trade_pct >= b.risk_per_trade_pct
        assert a.daily_loss_limit_pct >= b.daily_loss_limit_pct
        assert a.max_leverage >= b.max_leverage
        assert a.max_positions_cap <= b.max_positions_cap


def test_loss_budget_ratio_is_non_decreasing_across_presets():
    """daily_loss_limit_pct / risk_per_trade_pct, floored, is what
    derive._derive_max_open_positions calls loss_budget_slots. It must be
    non-decreasing across the four presets or max_open_positions could drop
    exactly at a preset boundary as equity rises — the property this test
    pins analytically; test_derive.py's property tests confirm it numerically."""
    import math
    ratios = [math.floor(p.daily_loss_limit_pct / p.risk_per_trade_pct + 1e-9)
             for p in (presets.PRESETS[n] for n in ("starter", "small", "standard", "pro"))]
    assert ratios == sorted(ratios)


def test_auto_resolves_to_a_concrete_preset_and_fills_every_field():
    name, resolved = presets.resolve_profile(RiskProfile(preset="auto"), 15.19)
    assert name == "starter"
    assert resolved.preset == "starter"
    assert resolved.risk_per_trade_pct == presets.PRESETS["starter"].risk_per_trade_pct
    assert resolved.daily_loss_limit_pct == presets.PRESETS["starter"].daily_loss_limit_pct
    assert resolved.max_leverage == presets.PRESETS["starter"].max_leverage
    assert resolved.allow_min_order_bump == presets.PRESETS["starter"].allow_min_order_bump
    assert resolved.max_bumped_risk_pct == presets.PRESETS["starter"].max_bumped_risk_pct


def test_explicit_fields_override_the_preset():
    profile = RiskProfile(preset="auto", risk_per_trade_pct=0.5, max_leverage=2)
    name, resolved = presets.resolve_profile(profile, 15.19)
    assert name == "starter"
    assert resolved.risk_per_trade_pct == 0.5          # explicit, not starter's 3.0
    assert resolved.max_leverage == 2                  # explicit, not starter's 10.0
    assert resolved.daily_loss_limit_pct == presets.PRESETS["starter"].daily_loss_limit_pct  # not overridden


def test_explicit_preset_name_is_honored_even_if_equity_disagrees():
    name, resolved = presets.resolve_profile(RiskProfile(preset="pro"), 15.19)
    assert name == "pro"
    assert resolved.max_leverage == presets.PRESETS["pro"].max_leverage


def test_custom_preset_falls_back_to_the_safest_numbers_for_unset_fields():
    name, resolved = presets.resolve_profile(RiskProfile(preset="custom"), 15.19)
    assert name == "custom"
    pro = presets.PRESETS["pro"]
    assert resolved.risk_per_trade_pct == pro.risk_per_trade_pct
    assert resolved.max_leverage == pro.max_leverage
    assert resolved.allow_min_order_bump == pro.allow_min_order_bump


def test_custom_preset_still_honors_explicit_fields():
    profile = RiskProfile(preset="custom", risk_per_trade_pct=7.0)
    _name, resolved = presets.resolve_profile(profile, 15.19)
    assert resolved.risk_per_trade_pct == 7.0
    assert resolved.max_leverage == presets.PRESETS["pro"].max_leverage   # still falls back


def test_positions_cap_for_every_name():
    for name in ("starter", "small", "standard", "pro"):
        assert presets.positions_cap_for(name) == presets.PRESETS[name].max_positions_cap
    assert presets.positions_cap_for("custom") == presets.PRESETS["pro"].max_positions_cap
