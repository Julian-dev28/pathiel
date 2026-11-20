"""The hype desk (built 2026-11-20): its spec, its rule's numbers, its 10x
cap, and the equity shares the operator would apply to switch it on. Gate
tests: pure, no network. The operator-position guard has its own file
(tests/test_hype_operator_guard.py); the place_bracket handler's leverage
and target offset are pinned in tests/test_hype_place_bracket.py.
"""
from __future__ import annotations

import pytest

from contracts.risk.models import RiskProfile
from pathiel.agents import bracket
from services.strategies import desk_leverage, desk_share, desks
from services.strategies import next_trade as NT

HYPE, BRENT, GOLD, SILVER, SP500 = "HYPE", "xyz:BRENTOIL", "xyz:GOLD", "xyz:SILVER", "xyz:SP500"

# The config the operator WOULD apply to switch the desk on (proposed in
# docs/strategy/HYPE_STRATEGY.md, not saved -- the desk ships off): oil
# stays 90%, hype gets 5%, the inactive metals desk drops from 10% to 5% so
# the three (oil, metals, hype) add up to 1.0 if metals comes back too.
PROPOSED_CONFIG = {"active_desks": ["oil", "hype"],
                   "desk_equity_share": {"oil": 0.90, "metals": 0.05, "hype": 0.05}}


# ── the desk ─────────────────────────────────────────────────────────────

def test_the_desk_is_registered_and_trades_one_coin():
    spec = desks.get_spec("hype")
    assert spec.id == "hype" and spec.name == "HYPE"
    assert [(i.coin, i.role) for i in spec.instruments] == [(HYPE, "primary")]
    assert desk_share.desk_coins("hype") == [HYPE]
    assert max(spec.leverage_ladder) == 10
    assert len(spec.news_queries) == 8                 # every phrase is searched (GNEWS_MAX_QUERIES)
    text = " ".join(spec.news_queries + spec.policy_topics + spec.prediction_market_queries).lower()
    for driver in ("hyperliquid", "buyback", "exploit", "hack", "bitcoin", "unlock", "regulation"):
        assert driver in text, driver


def test_the_desk_is_crypto_tagged_and_gets_token_unlock_coverage():
    """desk_rules.is_crypto_desk reads the coin against unlock_map.json
    (Hyperliquid -> HYPE), which turns on the token_unlock catalyst
    automatically -- no code here lists it explicitly."""
    from services.intel import desk_rules
    from services.intel.sources import catalysts
    spec = desks.get_spec("hype")
    assert desk_rules.is_crypto_desk(spec)
    assert "token_unlock" in catalysts.active_kinds(spec)
    assert not desk_rules.is_oil_desk(spec) and not desk_rules.is_metals_desk(spec)
    assert not desk_rules.is_indices_desk(spec) and not desk_rules.is_equity_desk(spec)


def test_registered_alongside_every_other_desk_with_no_pair():
    spec = desks.get_spec("hype")
    assert spec.pairs == []
    assert spec.horizon_days == 7


# ── equity shares (proposed, not saved) ─────────────────────────────────

def test_the_proposed_shares_add_up_to_one_and_hype_gets_five_percent():
    saved = PROPOSED_CONFIG["desk_equity_share"]
    assert sum(saved.values()) == pytest.approx(1.0)
    assert saved == {"oil": 0.90, "metals": 0.05, "hype": 0.05}
    active = desk_share.desk_shares(PROPOSED_CONFIG)           # validates; raises on a bad sum
    assert active == {"oil": 0.90, "hype": 0.05}
    assert desk_share.share_for_coin(HYPE, PROPOSED_CONFIG) == ("hype", 0.05)
    assert desk_share.share_for_coin(BRENT, PROPOSED_CONFIG) == ("oil", 0.90)
    assert desk_share.slots(PROPOSED_CONFIG, 1) == 2


def test_hype_active_without_a_share_fails_closed():
    with pytest.raises(desk_share.DeskShareError):
        desk_share.desk_shares({"active_desks": ["oil", "hype"], "desk_equity_share": {"oil": 0.90}})


def test_sp500_and_hype_can_both_be_active_at_once():
    """Nothing about hype's wiring assumes it is the only non-oil desk."""
    cfg = {"active_desks": ["oil", "sp500", "hype"],
           "desk_equity_share": {"oil": 0.85, "sp500": 0.05, "hype": 0.10}}
    shares = desk_share.desk_shares(cfg)
    assert shares == {"oil": 0.85, "sp500": 0.05, "hype": 0.10}
    assert desk_share.share_for_coin(HYPE, cfg) == ("hype", 0.10)
    assert desk_share.share_for_coin(SP500, cfg) == ("sp500", 0.05)


# ── the leverage cap, per desk ───────────────────────────────────────────

PROFILE = RiskProfile(preset="custom", risk_per_trade_pct=6, max_leverage=20, max_open_positions=1)


def test_hype_caps_at_10x():
    assert desk_leverage.DESK_MAX_LEVERAGE == {"sp500": 50.0, "hype": 10.0}
    assert desk_leverage.cap_for_coin(HYPE, {}) == 10.0
    assert desk_leverage.entry_leverage(HYPE, 20.0, {}) == 10.0
    sized = desk_leverage.profile_for_coin(PROFILE, HYPE, {})
    assert sized.max_leverage == 10.0 and PROFILE.max_leverage == 20      # a copy; the saved profile is not edited
    assert sized.model_dump(exclude={"max_leverage"}) == PROFILE.model_dump(exclude={"max_leverage"})


@pytest.mark.parametrize("coin", [BRENT, "xyz:CL", GOLD, SILVER, "xyz:NATGAS", "BTC", "xyz:XYZ100"])
def test_every_other_coin_keeps_the_profile_cap_not_hypes(coin):
    """10x is HYPE's own cap; no other coin's cap moves because of it."""
    for cfg in ({}, PROPOSED_CONFIG, {"desk_max_leverage": {"hype": 10}}, {"desk_max_leverage": "junk"}):
        assert desk_leverage.cap_for_coin(coin, cfg) is None
        assert desk_leverage.entry_leverage(coin, 20.0, cfg) == 20.0


def test_sp500s_own_50x_cap_is_unaffected_by_hypes_10x():
    for cfg in ({}, PROPOSED_CONFIG, {"desk_max_leverage": {"hype": 10}}):
        assert desk_leverage.cap_for_coin(SP500, cfg) == 50.0


def test_the_operator_can_lower_the_hype_cap_in_config_and_junk_is_ignored():
    assert desk_leverage.cap_for_coin(HYPE, {"desk_max_leverage": {"hype": 5}}) == 5.0
    for junk in ({"hype": 500}, {"hype": 0}, {"hype": "fast"}, {"hype": None}, "junk", ["hype"]):
        assert desk_leverage.cap_for_coin(HYPE, {"desk_max_leverage": junk}) == 10.0
    # only an explicit operator entry can change oil
    assert desk_leverage.cap_for_coin(BRENT, {"desk_max_leverage": {"oil": 10}}) == 10.0


def test_a_public_install_gets_no_code_default(monkeypatch):
    from contracts import runtime
    monkeypatch.setattr(runtime, "is_public", lambda: True)
    assert desk_leverage.cap_for_coin(HYPE, {}) is None
    assert desk_leverage.entry_leverage(HYPE, 3.0, {}) == 3.0
    assert desk_leverage.cap_for_coin(HYPE, {"desk_max_leverage": {"hype": 8}}) == 8.0


# ── the rule ─────────────────────────────────────────────────────────────

def test_the_rules_numbers():
    assert NT.HYPE_RULE == {"k_atr": 1.0, "floor_frac": 0.0, "target_offset_frac": 0.0,
                            "backtest_sides": "trend120", "trend_gate_span": 120}
    assert NT.leverage_fit_for(HYPE) == (10.0,) and NT.HYPE_LEVERAGE == desk_leverage.DESK_MAX_LEVERAGE["hype"]
    assert NT.target_offset_frac_for(HYPE) == 0.0
    assert NT.trend_gate_span_for(HYPE) == 120
    assert NT.stop_buffer_for(HYPE, 0.73, 65.0) == 0.73                   # pure ATR: the floor is 0, never binds
    assert NT.stop_buffer_for(HYPE, None, 65.0) is None


def test_brent_sp500_gold_and_silver_rules_are_unchanged():
    assert NT.stop_buffer_for(BRENT, 0.5, 90.0) == 0.75
    assert NT.stop_buffer_for(SP500, 12.0, 7180.0) == pytest.approx(0.004 * 7180.0)
    assert NT.stop_buffer_for(GOLD, 10.0, 4000.0) == 32.0
    assert NT.stop_buffer_for(SILVER, 0.5, 50.0) == 0.65
    for coin in (BRENT, "xyz:CL", GOLD, SILVER):
        assert NT.target_offset_frac_for(coin) == bracket.TARGET_OFFSET_FRAC == 0.005
    assert NT.leverage_fit_for(BRENT) == NT.leverage_fit_for(GOLD) == NT.leverage_fit_for(SILVER) == (20.0, 15.0)
    assert NT.leverage_fit_for("xyz:CL") == ()
    assert NT.trend_gate_span_for(BRENT) is None and NT.trend_gate_span_for(SP500) is None


def _hy(side, mid, low=60.0, high=70.0, thesis_entry=None, atr=0.73, closes_1h=None):
    return NT.next_trade(side, mid, low, high, thesis_entry=thesis_entry, leverage=NT.HYPE_LEVERAGE,
                         coin_max_leverage=10.0, stop_buffer=NT.stop_buffer_for(HYPE, atr, mid),
                         leverage_fit=NT.leverage_fit_for(HYPE), offset_frac=NT.target_offset_frac_for(HYPE),
                         closes_1h=closes_1h, trend_gate_span=NT.trend_gate_span_for(HYPE))


def test_a_long_near_the_24h_low_trades_at_10x_with_room_to_spare_on_the_stop():
    nt = _hy("long", 60.5)
    assert nt["ok"] is True and nt["leverage"] == 10.0
    assert nt["stop_px"] == pytest.approx(60.0 - 0.73)
    assert nt["tp_px"] == nt["structure_tp_px"] == 70.0                  # no offset: the far 24h edge itself
    assert nt["stop_frac_of_liq"] <= 0.5 and nt["r_now"] >= 2.0
    # the stop sits well inside the 10x half-liquidation budget (2.63%), unlike the S&P's 50x line
    assert (60.5 - nt["stop_px"]) / 60.5 * 100 < 2.1


def test_a_short_near_the_24h_high_mirrors_it():
    nt = _hy("short", 69.5)
    assert nt["ok"] is True and nt["leverage"] == 10.0
    assert nt["stop_px"] == pytest.approx(70.0 + 0.73) and nt["tp_px"] == 60.0


def test_never_chase_and_no_entry_after_a_break_apply():
    chase = _hy("long", 60.5, thesis_entry=60.2)
    assert chase["ok"] is False and any("never chase" in b for b in chase["blockers"])
    broke = _hy("long", 59.0)
    assert broke["ok"] is False and any("no entry after a break" in b for b in broke["blockers"])
    assert _hy("flat", 60.5)["ok"] is False


def test_the_trend_gate_vetoes_a_new_entry_against_the_120h_return_and_nothing_else_carries_it():
    """closes_1h missing (no history yet, or a coin with no span): no veto.
    With history, a negative 120h return blocks a long and allows a short;
    the mirror for a positive return."""
    down = [100.0] + [90.0] * 120                      # a -10% 120h return
    up = [90.0] + [100.0] * 120                        # a +11.1% 120h return
    long_vs_downtrend = _hy("long", 60.5, closes_1h=down)
    assert long_vs_downtrend["ok"] is False
    assert any("no new long against a 120h downtrend" in b for b in long_vs_downtrend["blockers"])
    assert long_vs_downtrend["trend_gate"]["blocked"] is True
    short_vs_downtrend = _hy("short", 69.5, closes_1h=down)
    assert short_vs_downtrend["ok"] is True                              # a short agrees with the downtrend
    assert short_vs_downtrend["trend_gate"]["blocked"] is False
    short_vs_uptrend = _hy("short", 69.5, closes_1h=up)
    assert short_vs_uptrend["ok"] is False
    assert any("no new short against a 120h uptrend" in b for b in short_vs_uptrend["blockers"])
    assert _hy("long", 60.5, closes_1h=None)["ok"] is True               # no history: no veto, main rule decides
    assert "trend_gate" not in _hy("long", 60.5, closes_1h=None)


def test_the_trend_gate_never_applies_to_a_coin_with_no_span():
    """Passing closes_1h for Brent does nothing: trend_gate_span_for(BRENT)
    is None, so next_trade never even reads the closes."""
    down = [100.0] + [90.0] * 120
    nt = NT.next_trade("long", 90.0, 88.0, 93.0, thesis_entry=None, leverage=20.0, coin_max_leverage=20.0,
                       stop_buffer=0.75, closes_1h=down, trend_gate_span=NT.trend_gate_span_for(BRENT))
    assert "trend_gate" not in nt


def test_a_narrow_range_pays_under_2r_and_is_refused():
    nt = _hy("long", 60.5, low=60.0, high=62.0)
    assert nt["ok"] is False and any("needs 2R" in b for b in nt["blockers"])


def test_place_brackets_own_gates_agree_at_10x_with_no_offset():
    """The order next_trade answers is one bracket.place_bracket accepts."""
    nt = _hy("long", 60.5)
    assert bracket.check_levels(60.5, nt["stop_px"], bracket.offset_target(nt["structure_tp_px"], "long", 0.0))["ok"]
    assert bracket.check_leverage(60.5, nt["stop_px"], 10.0, 10.0)["ok"]


def test_spec_builders_are_pure_and_repeatable():
    from services.strategies.desks import hype as hype_mod
    a, b = hype_mod.build_spec(), hype_mod.build_spec()
    assert a == b
