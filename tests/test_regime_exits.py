"""Tests for regime-aware exit selection (pure fn)."""

from hermes_trader.agents.executor import select_exit_params

SCALP_BASE = {
    "protect_pct": 1.5, "retrace_threshold": 0.30,
    "phase2_tiers": [{"pct_above_entry": 1.5, "retrace_threshold": 0.30}],
    "regime_aware": {
        "enabled": True,
        "trend_ride": {"protect_pct": 3.0, "retrace_threshold": 0.55,
                       "phase2_tiers": [{"pct_above_entry": 3.0, "retrace_threshold": 0.55}]},
    },
}


def test_chop_uses_scalp():
    pp, rt, tiers, label = select_exit_params(SCALP_BASE, "neutral")
    assert pp == 1.5 and rt == 0.30 and label == "scalp"


def test_down_uses_scalp():
    pp, rt, tiers, label = select_exit_params(SCALP_BASE, "down")
    assert pp == 1.5 and rt == 0.30 and label == "scalp"


def test_up_uses_trend_ride():
    pp, rt, tiers, label = select_exit_params(SCALP_BASE, "up")
    assert pp == 3.0 and rt == 0.55 and "trend_ride" in label
    assert tiers[0]["retrace_threshold"] == 0.55


def test_disabled_stays_scalp_even_in_up():
    cfg = {**SCALP_BASE, "regime_aware": {"enabled": False,
           "trend_ride": {"protect_pct": 3.0, "retrace_threshold": 0.55}}}
    pp, rt, tiers, label = select_exit_params(cfg, "up")
    assert pp == 1.5 and rt == 0.30 and label == "scalp"


def test_missing_regime_aware_safe():
    pp, rt, tiers, label = select_exit_params({"protect_pct": 1.5, "retrace_threshold": 0.30}, "up")
    assert pp == 1.5 and label == "scalp"
