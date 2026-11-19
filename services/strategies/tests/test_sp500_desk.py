"""The sp500 desk (built 2026-11-19): its spec, its rule's numbers, its 50x
cap, and the equity shares the operator applies. Gate tests: pure, no
network. The operator-position guard has its own file
(tests/test_sp500_operator_guard.py); the place_bracket handler's leverage
and target offset are pinned in tests/test_sp500_place_bracket.py.
"""
from __future__ import annotations

import pytest

from contracts.risk.models import RiskProfile
from pathiel.agents import bracket
from services.strategies import desk_leverage, desk_share, desks
from services.strategies import next_trade as NT

SP500, BRENT, GOLD, SILVER = "xyz:SP500", "xyz:BRENTOIL", "xyz:GOLD", "xyz:SILVER"

# The config the operator applies to switch the desk on
# (docs/strategy/SP500_STRATEGY.md): oil stays 90%, the S&P gets 5%, and the
# inactive metals desk goes from 10% to 5% so the three add up to all of it.
OPERATOR_CONFIG = {"active_desks": ["oil", "sp500"],
                   "desk_equity_share": {"oil": 0.90, "metals": 0.05, "sp500": 0.05}}


# ── the desk ─────────────────────────────────────────────────────────────

def test_the_desk_is_registered_and_trades_one_coin():
    spec = desks.get_spec("sp500")
    assert spec.id == "sp500" and spec.name == "S&P 500"
    assert [(i.coin, i.role) for i in spec.instruments] == [(SP500, "primary")]
    assert desk_share.desk_coins("sp500") == [SP500]
    assert max(spec.leverage_ladder) == 50
    assert len(spec.news_queries) == 8                 # every phrase is searched (GNEWS_MAX_QUERIES)
    text = " ".join(spec.news_queries + spec.policy_topics + spec.prediction_market_queries).lower()
    for driver in ("federal reserve", "yields", "nvidia", "earnings", "jobs", "inflation", "tariff"):
        assert driver in text, driver


def test_the_indices_desk_is_untouched_and_never_shadows_sp500():
    assert [i.coin for i in desks.get_spec("indices").instruments][:2] == ["xyz:XYZ100", SP500]
    assert desk_share.desk_for_coin(SP500, OPERATOR_CONFIG) == "sp500"
    assert desk_share.desk_for_coin(SP500, {"active_desks": ["oil"]}) is None


# ── equity shares ────────────────────────────────────────────────────────

def test_the_shares_add_up_to_one_and_sp500_gets_five_percent():
    saved = OPERATOR_CONFIG["desk_equity_share"]
    assert sum(saved.values()) == pytest.approx(1.0)
    assert saved == {"oil": 0.90, "metals": 0.05, "sp500": 0.05}
    active = desk_share.desk_shares(OPERATOR_CONFIG)           # validates; raises on a bad sum
    assert active == {"oil": 0.90, "sp500": 0.05}
    assert desk_share.share_for_coin(SP500, OPERATOR_CONFIG) == ("sp500", 0.05)
    assert desk_share.share_for_coin(BRENT, OPERATOR_CONFIG) == ("oil", 0.90)
    assert desk_share.slots(OPERATOR_CONFIG, 1) == 2


def test_metals_coming_back_still_fits():
    cfg = {**OPERATOR_CONFIG, "active_desks": ["oil", "sp500", "metals"]}
    assert sum(desk_share.desk_shares(cfg).values()) == pytest.approx(1.0)
    with pytest.raises(desk_share.DeskShareError):             # the old 10% would not
        desk_share.desk_shares({**cfg, "desk_equity_share": {"oil": 0.90, "metals": 0.10, "sp500": 0.05}})


def test_sp500_active_without_a_share_fails_closed():
    with pytest.raises(desk_share.DeskShareError):
        desk_share.desk_shares({"active_desks": ["oil", "sp500"], "desk_equity_share": {"oil": 0.90}})


# ── the leverage cap, per desk ───────────────────────────────────────────

PROFILE = RiskProfile(preset="custom", risk_per_trade_pct=6, max_leverage=20, max_open_positions=1)


def test_sp500_caps_at_50x():
    assert desk_leverage.DESK_MAX_LEVERAGE == {"sp500": 50.0}
    assert desk_leverage.cap_for_coin(SP500, {}) == 50.0
    assert desk_leverage.entry_leverage(SP500, 20.0, {}) == 50.0
    sized = desk_leverage.profile_for_coin(PROFILE, SP500, {})
    assert sized.max_leverage == 50.0 and PROFILE.max_leverage == 20      # a copy; the saved profile is not edited
    assert sized.model_dump(exclude={"max_leverage"}) == PROFILE.model_dump(exclude={"max_leverage"})


@pytest.mark.parametrize("coin", [BRENT, "xyz:CL", GOLD, SILVER, "xyz:NATGAS", "BTC", "xyz:XYZ100", "xyz:NVDA"])
def test_oil_and_every_other_coin_keep_the_profile_cap(coin):
    """The 20x cap is unchanged everywhere but the S&P desk."""
    for cfg in ({}, OPERATOR_CONFIG, {"desk_max_leverage": {"sp500": 50}}, {"desk_max_leverage": "junk"}):
        assert desk_leverage.cap_for_coin(coin, cfg) is None
        assert desk_leverage.entry_leverage(coin, 20.0, cfg) == 20.0
        assert desk_leverage.entry_leverage(coin, None, cfg) is None
        assert desk_leverage.profile_for_coin(PROFILE, coin, cfg) is PROFILE     # the very same object


def test_the_operator_can_lower_the_sp500_cap_in_config_and_junk_is_ignored():
    assert desk_leverage.cap_for_coin(SP500, {"desk_max_leverage": {"sp500": 25}}) == 25.0
    for junk in ({"sp500": 500}, {"sp500": 0}, {"sp500": "fast"}, {"sp500": None}, "junk", ["sp500"]):
        assert desk_leverage.cap_for_coin(SP500, {"desk_max_leverage": junk}) == 50.0
    # only an explicit operator entry can change oil
    assert desk_leverage.cap_for_coin(BRENT, {"desk_max_leverage": {"oil": 10}}) == 10.0


def test_a_public_install_gets_no_code_default(monkeypatch):
    from contracts import runtime
    monkeypatch.setattr(runtime, "is_public", lambda: True)
    assert desk_leverage.cap_for_coin(SP500, {}) is None
    assert desk_leverage.entry_leverage(SP500, 3.0, {}) == 3.0
    assert desk_leverage.cap_for_coin(SP500, {"desk_max_leverage": {"sp500": 10}}) == 10.0


# ── the rule ─────────────────────────────────────────────────────────────

def test_the_rules_numbers():
    assert NT.SP500_RULE == {"k_atr": 1.0, "floor_frac": 0.004, "target_offset_frac": 0.0, "backtest_sides": "both"}
    assert NT.leverage_fit_for(SP500) == (50.0,) and NT.SP500_LEVERAGE == desk_leverage.DESK_MAX_LEVERAGE["sp500"]
    assert NT.target_offset_frac_for(SP500) == 0.0
    assert NT.stop_buffer_for(SP500, 12.0, 7180.0) == pytest.approx(0.004 * 7180.0)    # the floor binds
    assert NT.stop_buffer_for(SP500, 40.0, 7180.0) == 40.0                              # a wild hour: 1.0 x ATR
    assert NT.stop_buffer_for(SP500, None, 7180.0) is None


def test_brent_gold_and_silver_rules_are_unchanged():
    assert NT.stop_buffer_for(BRENT, 0.5, 90.0) == 0.75
    assert NT.stop_buffer_for(GOLD, 10.0, 4000.0) == 32.0            # 0.8% floor over 1.3 x ATR
    assert NT.stop_buffer_for(SILVER, 0.5, 50.0) == 0.65             # 1.3 x ATR over the 0.8% floor
    for coin in (BRENT, "xyz:CL", GOLD, SILVER):
        assert NT.target_offset_frac_for(coin) == bracket.TARGET_OFFSET_FRAC == 0.005
    assert NT.leverage_fit_for(BRENT) == NT.leverage_fit_for(GOLD) == NT.leverage_fit_for(SILVER) == (20.0, 15.0)
    assert NT.leverage_fit_for("xyz:CL") == ()


def _sp(side, mid, low=7100.0, high=7180.0, thesis_entry=None, atr=12.0):
    return NT.next_trade(side, mid, low, high, thesis_entry=thesis_entry, leverage=NT.SP500_LEVERAGE,
                         coin_max_leverage=50.0, stop_buffer=NT.stop_buffer_for(SP500, atr, mid),
                         leverage_fit=NT.leverage_fit_for(SP500), offset_frac=NT.target_offset_frac_for(SP500))


def test_a_long_at_the_24h_low_trades_at_50x_with_the_stop_inside_half_the_liquidation_distance():
    nt = _sp("long", 7104.0)
    assert nt["ok"] is True and nt["leverage"] == 50.0
    assert nt["stop_px"] == pytest.approx(7100 - 0.004 * 7104)
    assert nt["tp_px"] == nt["structure_tp_px"] == 7180.0             # no offset: the far 24h edge itself
    assert nt["stop_frac_of_liq"] <= 0.5 and nt["r_now"] >= 2.0
    assert (7104 - nt["stop_px"]) / 7104 * 100 <= 0.505
    assert nt["liq_px"] == pytest.approx(7104 * (1 - 0.0101), abs=0.5)


def test_a_short_at_the_24h_high_mirrors_it():
    nt = _sp("short", 7176.0)
    assert nt["ok"] is True and nt["leverage"] == 50.0
    assert nt["stop_px"] == pytest.approx(7180 + 0.004 * 7176) and nt["tp_px"] == 7100.0


def test_mid_range_is_refused_because_the_stop_does_not_fit_50x_and_is_never_refitted_lower():
    nt = _sp("long", 7120.0)                          # 20 points off the low: a 0.68% stop
    assert nt["ok"] is False
    assert any("stop too wide for 50x" in b for b in nt["blockers"])
    assert nt["leverage"] == 50.0                     # no 40x, no 20x
    assert nt["trigger_px"] < 7120.0                  # it says where it would trade instead


def test_never_chase_and_no_entry_after_a_break_apply():
    chase = _sp("long", 7104.0, thesis_entry=7102.0)
    assert chase["ok"] is False and any("never chase" in b for b in chase["blockers"])
    broke = _sp("long", 7098.0)
    assert broke["ok"] is False and any("no entry after a break" in b for b in broke["blockers"])
    assert _sp("flat", 7104.0)["ok"] is False


def test_a_narrow_range_pays_under_2r_and_is_refused():
    nt = _sp("long", 7104.0, low=7100.0, high=7150.0)        # 46 points up, 32 at risk
    assert nt["ok"] is False and any("needs 2R" in b for b in nt["blockers"])


def test_place_brackets_own_gates_agree_at_50x_with_no_offset():
    """The order next_trade answers is one bracket.place_bracket accepts."""
    nt = _sp("long", 7104.0)
    assert bracket.check_levels(7104.0, nt["stop_px"], bracket.offset_target(nt["structure_tp_px"], "long", 0.0))["ok"]
    assert bracket.check_leverage(7104.0, nt["stop_px"], 50.0, 50.0)["ok"]
    # oil's 0.5% offset would turn the same trade down: 44.5 points left, 2R needs 64.8
    assert not bracket.check_levels(7104.0, nt["stop_px"], bracket.offset_target(7180.0, "long", 0.005))["ok"]
