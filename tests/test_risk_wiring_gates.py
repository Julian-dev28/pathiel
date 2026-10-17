"""Unit tests for the two risk_gates.py additions this wave made (both
optional parameters, both None-default, both backward compatible for every
caller that predates them — see docs/overhaul/SWARM_RULES.md section 10 and
each function's own docstring for the exact precedence):

  eval_all_gates(..., max_open_positions_override=None)      -- services/risk
      DerivedLimits.max_open_positions replaces config["max_concurrent"]
      as the max_concurrent_positions_gate's ceiling, when given.
  effective_daily_loss_limit(..., profile_daily_loss_limit_pct=None)
      the resolved RiskProfile's daily_loss_limit_pct is the SECOND choice
      (after an explicit config max_daily_loss_pct, before the static
      max_daily_loss_usd fallback) for the kill-switch's percent.
"""
from __future__ import annotations

from pathiel.agents.risk_gates import GateContext, eval_all_gates, effective_daily_loss_limit


def _ctx(n_positions: int = 0, equity: float = 100.0, daily_pnl: float = 0.0) -> GateContext:
    return GateContext(
        confidence=0.99,
        current_positions=[{"coin": f"C{i}", "side": "long", "size_usd": 10.0} for i in range(n_positions)],
        trade_notional_usd=10.0, daily_pnl=daily_pnl, market_volume_24h_usd=1e8,
        coin="BTC", trade_side="long", has_binary_news_risk=False,
        equity=equity, total_open_notional=0.0,
    )


_PERMISSIVE_CFG = {
    "min_ai_confidence": 0.0, "max_trade_notional_usd": 0, "max_daily_loss_usd": -1e9,
    "daily_giveback_halt_pct": 0.0, "min_market_volume_usd": 0, "min_short_volume_usd": 0,
    "coin_allowlist": [], "coin_blocklist": [], "cooldown_min": 0,
    "max_crypto_long_correlated": 999, "max_xyz_short_names": 999,
    "max_xyz_short_notional_pct": 999, "max_total_notional_pct": 999,
}


# ───────────────────────────────────────────────────── max_open_positions

def test_override_replaces_the_static_max_concurrent_default():
    cfg = dict(_PERMISSIVE_CFG, max_concurrent=10)   # config alone would ALLOW a 4th slot
    ctx = _ctx(n_positions=3)
    out = eval_all_gates(ctx, cfg, max_open_positions_override=3)
    assert out["results"]["max_concurrent"]["pass"] is False
    assert "3/3" in out["results"]["max_concurrent"]["reason"]


def test_no_override_keeps_byte_identical_old_behavior():
    cfg = dict(_PERMISSIVE_CFG, max_concurrent=10)
    ctx = _ctx(n_positions=3)
    out = eval_all_gates(ctx, cfg)   # no max_open_positions_override at all
    assert out["results"]["max_concurrent"]["pass"] is True


def test_override_of_none_is_identical_to_omitting_it():
    cfg = dict(_PERMISSIVE_CFG, max_concurrent=10)
    ctx = _ctx(n_positions=3)
    with_none = eval_all_gates(ctx, cfg, max_open_positions_override=None)
    without = eval_all_gates(ctx, cfg)
    assert with_none["results"]["max_concurrent"] == without["results"]["max_concurrent"]


def test_book_carveout_still_exempts_max_concurrent_even_with_an_override():
    """The 2026-07-10 funnel-audit capital carveout (risk_gates.py's own
    docstring) is untouched by this wave: a strategy-book entry with
    book_capital_carveout on (the default) skips the aggregate concurrency
    gate regardless of what max_open_positions_override says."""
    cfg = dict(_PERMISSIVE_CFG, max_concurrent=10)
    ctx = _ctx(n_positions=10)   # already "full" by any sane ceiling
    out = eval_all_gates(ctx, cfg, is_book=True, max_open_positions_override=1)
    assert out["results"]["max_concurrent"] == {"pass": True, "reason": "book_carveout"}


def test_override_at_the_boundary_is_blocked_not_fencepost_off():
    cfg = dict(_PERMISSIVE_CFG, max_concurrent=10)
    out_at = eval_all_gates(_ctx(n_positions=3), cfg, max_open_positions_override=3)
    out_under = eval_all_gates(_ctx(n_positions=2), cfg, max_open_positions_override=3)
    assert out_at["results"]["max_concurrent"]["pass"] is False
    assert out_under["results"]["max_concurrent"]["pass"] is True


# ─────────────────────────────────────────────────── daily loss limit pct

def test_effective_daily_loss_limit_prefers_explicit_config_pct_over_profile():
    limit = effective_daily_loss_limit({"max_daily_loss_pct": 0.05}, equity=100.0,
                                       daily_pnl=0.0, profile_daily_loss_limit_pct=8.0)
    assert limit == -5.0   # 5%, not the profile's 8%


def test_effective_daily_loss_limit_uses_profile_pct_when_config_pct_absent():
    limit = effective_daily_loss_limit({}, equity=100.0, daily_pnl=0.0,
                                       profile_daily_loss_limit_pct=8.0)
    assert limit == -8.0


def test_effective_daily_loss_limit_falls_back_to_static_usd_when_both_absent():
    limit = effective_daily_loss_limit({"max_daily_loss_usd": -42.0}, equity=100.0,
                                       daily_pnl=0.0, profile_daily_loss_limit_pct=None)
    assert limit == -42.0


def test_effective_daily_loss_limit_profile_pct_none_is_byte_identical_to_before():
    with_none = effective_daily_loss_limit({"max_daily_loss_usd": -42.0}, 100.0, 0.0, None)
    without = effective_daily_loss_limit({"max_daily_loss_usd": -42.0}, 100.0, 0.0)
    assert with_none == without == -42.0


def test_profile_pct_wired_through_eval_all_gates_can_trip_the_kill_switch():
    """End-to-end within risk_gates.py (no executor involved): a $100
    account, profile daily_loss_limit_pct=8%, already down $10 today ->
    the kill switch must trip even though no config key says anything
    about daily loss at all."""
    cfg = dict(_PERMISSIVE_CFG)
    cfg.pop("max_daily_loss_usd")
    out = eval_all_gates(_ctx(equity=100.0, daily_pnl=-10.0), cfg,
                         profile_daily_loss_limit_pct=8.0)
    assert out["results"]["daily_loss"]["pass"] is False
    assert out["blocked"] is True


def test_profile_pct_non_positive_is_a_no_op():
    limit_zero = effective_daily_loss_limit({}, 100.0, 0.0, 0.0)
    limit_default = effective_daily_loss_limit({}, 100.0, 0.0)
    assert limit_zero == limit_default
