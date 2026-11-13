"""PUBLIC_DEFAULT_PROFILE: what a fresh public install sizes with.

3x max leverage, 1% of equity at risk per trade (2% only when the venue
minimum forces a bump), one position at a time. See services/risk/README.md,
"Public install default". Pure, offline.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.risk.models import RiskProfile
from services.risk import presets
from services.risk.derive import BookSpec, VenueRules, derive_limits

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "config" / "public" / "agent-config.json"
VENUE = VenueRules(min_order_notional_usd=10.5)
EQUITIES = [5, 10, 25, 50, 100, 500, 1_000, 10_000, 50_000, 100_000, 1_000_000]


def test_public_profile_values():
    p = presets.PUBLIC_DEFAULT_PROFILE
    assert p == RiskProfile(preset="custom", risk_per_trade_pct=1.0, daily_loss_limit_pct=3.0,
                            max_leverage=3.0, max_open_positions=1, allow_min_order_bump=True,
                            max_bumped_risk_pct=2.0)
    assert None not in p.model_dump().values(), "every field set: nothing falls back to a tier"


def test_public_default_profile_is_a_copy():
    a = presets.public_default_profile()
    assert a == presets.PUBLIC_DEFAULT_PROFILE and a is not presets.PUBLIC_DEFAULT_PROFILE
    a.max_leverage = 50.0
    assert presets.PUBLIC_DEFAULT_PROFILE.max_leverage == 3.0
    assert presets.public_default_profile().max_leverage == 3.0


@pytest.mark.parametrize("equity", EQUITIES)
def test_profile_resolves_without_falling_back_to_a_preset(equity):
    """'custom' with every field set: resolve_profile carries every number
    through, at any equity, and never swaps in the pro-tier fallback."""
    name, resolved = presets.resolve_profile(presets.PUBLIC_DEFAULT_PROFILE, equity)
    assert name == "custom"
    assert resolved.model_dump() == presets.PUBLIC_DEFAULT_PROFILE.model_dump()


@pytest.mark.parametrize("equity", EQUITIES)
def test_one_position_at_every_equity(equity):
    limits = derive_limits(equity, presets.PUBLIC_DEFAULT_PROFILE,
                           [BookSpec(name="main_engine", stop_pct=2.0)], VENUE)
    assert limits.max_open_positions == 1
    assert limits.profile.max_leverage == 3.0
    assert limits.risk_per_trade_usd == pytest.approx(equity * 0.01, abs=0.01)
    for row in limits.books:
        if row.can_trade:
            assert row.leverage <= 3.0
            assert row.risk_pct <= (2.0 if row.bumped_to_min else 1.0) + 1e-6


def test_template_matches_the_python_profile():
    template = json.loads(TEMPLATE.read_text())
    assert template["risk_profile"] == presets.PUBLIC_DEFAULT_PROFILE.model_dump()
    assert RiskProfile.model_validate(template["risk_profile"]) == presets.PUBLIC_DEFAULT_PROFILE
    assert template == {
        "enable_hip3": True, "hip3_dex_allowlist": ["xyz"], "hip3_dex_blocklist": [],
        "active_desks": ["oil"], "risk_profile": presets.PUBLIC_DEFAULT_PROFILE.model_dump(),
    }
    # Nothing in the template turns trading loose or names the operator's desks.
    assert "desk_equity_share" not in template and "mode" not in template
