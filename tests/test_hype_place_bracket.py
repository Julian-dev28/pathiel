"""place_bracket's leverage cap is per desk: HYPE sizes at 10x with no
target offset; Brent sizes exactly as before (the risk profile's 20x, the
20/15 fit, the 0.5% offset). Also the levels script's defaults for HYPE,
including the trend-gate veto reading closes_1h off the levels payload.
Gate tests: the venue, the account and the config are stubbed;
bracket.place_bracket is captured, never run.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load("pathiel_mcp_server_hype_bracket", "scripts/pathiel-mcp-server.py")
L = _load("oil_levels_hype", "scripts/oil_levels.py")

HYPE, BRENT = "HYPE", "xyz:BRENTOIL"
SPECS = {HYPE: (10.0, 2), BRENT: (20.0, 2)}
MIDS = {HYPE: 60.5, BRENT: 90.0}


def _call(args):
    resp = M.dispatch_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "place_bracket", "arguments": args}})
    return json.loads(resp["result"]["content"][0]["text"])


@pytest.fixture()
def desk(monkeypatch):
    """A $200 account, the live risk profile (6% a trade, 20x), no shares
    saved, nothing open. Returns the kwargs bracket.place_bracket received."""
    from contracts.risk.models import RiskProfile
    from pathiel.agents import bracket, ownership
    from pathiel.client import exchange as ex
    from services.risk.derive import VenueRules
    seen = {}
    profile = RiskProfile(preset="custom", risk_per_trade_pct=6, daily_loss_limit_pct=100, max_leverage=20,
                          max_open_positions=1, allow_min_order_bump=True, max_bumped_risk_pct=100)
    monkeypatch.setattr(M, "_risk_inputs", lambda: (profile, [], VenueRules(min_order_notional_usd=10.0)))
    monkeypatch.setattr(M, "_coin_specs", lambda coin: SPECS[coin])
    monkeypatch.setattr(M, "_live_equity", lambda: 200.0)
    monkeypatch.setattr(M, "_desk_slice", lambda coin, equity: {
        "desk": "hype" if coin == HYPE else "oil", "equity_share": 1.0, "equity_usd": equity,
        "slice_usd": equity, "shares_on": False, "desk_coins": [coin], "slots": 1})
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xdesk")
    monkeypatch.setattr(ex, "get_hl_price", lambda coin: MIDS[coin])
    monkeypatch.setattr(bracket, "slot_taken_by", lambda *a, **k: None)
    monkeypatch.setattr(ownership, "check_operator_presence", lambda coin, **k: {"operator": False})
    import pathiel.agents.config_store as cs
    monkeypatch.setattr(cs, "read_agent_config", lambda: {})

    def capture(coin, **kw):
        seen.update(kw, coin=coin)
        return {"ok": True, "placed": False, "dry_run": True, "coin": coin}
    monkeypatch.setattr(bracket, "place_bracket", capture)
    seen["profile"] = profile
    return seen


def test_hype_sizes_at_10x_with_no_target_offset(desk):
    stop = 60.0 - 0.73                                   # the rule's stop: pure ATR beyond the 24h low
    body = _call({"coin": HYPE, "stop_px": stop, "tp_px": 70.0, "dry_run": True})
    assert body["ok"] is True
    assert desk["leverage"] == 10 and desk["coin_max_leverage"] == 10.0
    assert desk["target_offset_frac"] == 0.0
    # 6% of $200 at risk over the stop distance
    assert desk["size"] == pytest.approx(0.06 * 200 / (60.5 - stop), abs=0.01)
    assert body["sizing"]["risk_usd"] == pytest.approx(12.0, abs=0.3)
    assert body["sizing"]["margin_usd"] == pytest.approx(desk["size"] * 60.5 / 10, abs=0.5)
    assert desk["profile"].max_leverage == 20            # the saved profile was not edited


def test_oil_still_sizes_at_20x_with_the_half_percent_offset(desk):
    body = _call({"coin": BRENT, "stop_px": 89.0, "tp_px": 93.5, "dry_run": True})
    assert body["ok"] is True
    assert desk["leverage"] == 20 and desk["coin_max_leverage"] == 20.0
    assert "target_offset_frac" not in desk             # bracket's own 0.5% applies, the call is unchanged
    assert desk["size"] == pytest.approx(0.06 * 200 / 1.0, abs=0.01)


def test_a_lower_hype_cap_in_config_is_honoured(desk, monkeypatch):
    import pathiel.agents.config_store as cs
    monkeypatch.setattr(cs, "read_agent_config", lambda: {"desk_max_leverage": {"hype": 5}})
    _call({"coin": HYPE, "stop_px": 60.0 - 0.73, "tp_px": 70.0, "dry_run": True})
    assert desk["leverage"] == 5


def test_size_trade_tool_uses_the_same_cap(desk):
    resp = M.dispatch_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "size_trade", "arguments": {"coin": HYPE, "entry_px": 60.5, "stop_px": 59.27, "equity_usd": 200}}})
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["leverage"] == 10.0 and body["stop_before_liq"] is True
    resp = M.dispatch_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "size_trade", "arguments": {"coin": BRENT, "entry_px": 90.0, "stop_px": 89.0, "equity_usd": 200}}})
    assert json.loads(resp["result"]["content"][0]["text"])["leverage"] == 20.0


# ── the levels script ────────────────────────────────────────────────────

LEVELS = {"mid": 60.5, "atr14_1h": 0.73, "range_24h": {"low": 60.0, "high": 70.0}, "breakout_24h": None,
          "closes_1h": [60.0] * 130}


def test_levels_next_for_hype_answers_the_bracket_at_10x():
    out = L.next_order(HYPE, "long", LEVELS, 60.7, 10.0, 10.0)
    assert out["ok"] is True and out["leverage"] == 10.0
    assert out["place_bracket"] == {"coin": HYPE, "stop_px": out["stop_px"], "tp_px": 70.0}
    assert out["tp_px"] == 70.0                                   # no offset on HYPE
    assert L.next_order(HYPE, "short", LEVELS, None, 10.0, 10.0)["ok"] is False


def test_levels_next_for_hype_reads_the_trend_gate_off_closes_1h():
    """A flat closes_1h history (ret 0) blocks nothing; a downtrend vetoes
    the long even though the price structure alone would take it.
    trend_gate compares closes_1h[-1] against closes_1h[-1 - 120]: with 130
    bars that is index 9, so the first ten bars set the base close."""
    down = {**LEVELS, "closes_1h": [100.0] * 10 + [90.0] * 120}
    out = L.next_order(HYPE, "long", down, None, 10.0, 10.0)
    assert out["ok"] is False
    assert any("no new long against a 120h downtrend" in b for b in out["blockers"])
    assert out["trend_gate"]["blocked"] is True


def test_levels_cli_defaults_are_10x_for_hype_and_20x_for_everything_else(monkeypatch, capsys):
    seen = {}

    def fake_next(coin, side, levels, thesis_entry, leverage, max_leverage, **kw):
        seen[coin] = (leverage, max_leverage)
        return {"ok": False, "coin": coin}
    import pathiel.agents.config_store as cs
    monkeypatch.setattr(cs, "read_agent_config", lambda: {})
    monkeypatch.setattr(L, "next_order", fake_next)
    monkeypatch.setattr(L, "fetch_candles", lambda *a, **k: [])
    monkeypatch.setattr(L, "fetch_mid", lambda coin: 1.0)
    monkeypatch.setattr(L, "summarize", lambda *a, **k: {})
    for coin in (HYPE, BRENT, "xyz:GOLD"):
        assert L.main(["--coin", coin, "--next", "long"]) == 0
    assert L.main(["--coin", "xyz:GOLD", "--next", "long", "--max-lev", "25"]) == 0
    capsys.readouterr()
    assert seen[HYPE] == (10.0, 10.0) and seen[BRENT] == (20.0, 20.0)
    assert seen["xyz:GOLD"] == (20.0, 25.0)
    assert L.main(["--coin", HYPE, "--check", "60.5", "59.27", "70", "--lev", "10"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "10x OK"
    assert L.main(["--check", "90", "88.5", "93.5", "--lev", "20"]) == 0        # Brent: the 20x line, as before
    assert json.loads(capsys.readouterr().out) == L.check(90.0, 88.5, 93.5, leverage=20.0, max_leverage=20.0)


def test_the_fetch_window_widens_for_the_trend_gate_coin_only(monkeypatch):
    seen = {}

    def fake_fetch(coin, interval, hours, now_ms=None):
        seen[(coin, interval)] = hours
        return []
    monkeypatch.setattr(L, "fetch_candles", fake_fetch)
    monkeypatch.setattr(L, "fetch_mid", lambda coin: 1.0)
    monkeypatch.setattr(L, "summarize", lambda *a, **k: {"coin": "x"})
    for coin in (HYPE, BRENT):
        L.main(["--coin", coin])
    assert seen[(HYPE, "1h")] == 130           # trend_gate_span 120 + the 10h margin
    assert seen[(BRENT, "1h")] == 50           # unchanged: no trend-gate span


# ── the config change the operator would apply ───────────────────────────

def test_update_config_takes_the_proposed_change_and_a_lower_cap():
    """docs/strategy/HYPE_STRATEGY.md, "Config to apply", against the test
    config file (never the live one)."""
    from pathiel.agents import config_store
    from services.strategies import desk_leverage, desk_share
    try:
        config_store.write_agent_config({"mode": "OFF", "active_desks": ["oil"],
                                         "desk_equity_share": {"oil": 0.90, "metals": 0.10}})
        out = json.loads(M.handle_config({
            "active_desks": ["oil", "hype"],
            "desk_equity_share": {"oil": 0.90, "metals": 0.05, "hype": 0.05}}))
        assert out["active_desks"] == ["oil", "hype"]
        assert out["desk_equity_share"] == {"oil": 0.90, "metals": 0.05, "hype": 0.05}
        assert sum(out["desk_equity_share"].values()) == pytest.approx(1.0)
        assert desk_share.share_for_coin(HYPE, out) == ("hype", 0.05)
        assert desk_share.share_for_coin(BRENT, out) == ("oil", 0.90)
        assert desk_leverage.cap_for_coin(HYPE, out) == 10.0 and desk_leverage.cap_for_coin(BRENT, out) is None

        out = json.loads(M.handle_config({"desk_max_leverage": {"hype": 5}}))
        assert out["desk_max_leverage"] == {"hype": 5}
        assert desk_leverage.cap_for_coin(HYPE, out) == 5.0 and desk_leverage.cap_for_coin(BRENT, out) is None
        bad = json.loads(M.handle_config({"desk_max_leverage": {"hype": 500}}))
        assert bad["status"] == "error" and "hype" in bad["error"]
        assert config_store.read_agent_config()["desk_max_leverage"] == {"hype": 5}
    finally:
        config_store.write_agent_config({"mode": "OFF"})
