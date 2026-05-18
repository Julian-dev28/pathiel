"""Offline tests for the hermes-trader codebase.

Covers the pure/refactored logic — no network or Hyperliquid credentials
required. Run from the repo root: ``pytest`` (or ``python3 -m pytest``).

Network-dependent paths (live order placement, account-state fetches, the
OpenRouter research call, the full market scan) are not covered here — they
can only be exercised against the real exchange.
"""
import importlib.util
import json
import math
import pathlib
import subprocess
import sys

from hermes_agent.models.types import Candle

ROOT = pathlib.Path(__file__).resolve().parents[1]
MCP_SCRIPT = str(ROOT / "scripts" / "hermes-mcp-server.py")


def _candles(n=150):
    return [
        Candle(t=i, o=100 + i * 0.1, h=101 + i * 0.1, l=99 + i * 0.1,
               c=100 + i * 0.1 + math.sin(i) * 0.5, v=1000.0 + i)
        for i in range(n)
    ]


# ── models ──────────────────────────────────────────────────────────────
def test_candle_model_and_getitem():
    c = Candle(t=1, o=2.0, h=3.0, l=1.0, c=2.5, v=100.0)
    assert c.c == 2.5
    assert c["c"] == 2.5 and c["t"] == 1


# ── indicators ──────────────────────────────────────────────────────────
def test_candle_val_dict_and_obj():
    from hermes_agent.indicators.math import candle_val
    assert candle_val(Candle(t=1, o=1, h=2, l=0.5, c=1.5, v=9), "c") == 1.5
    assert candle_val({"c": 7.0}, "c") == 7.0
    assert candle_val({}, "c") == 0


def test_ema_sma():
    from hermes_agent.indicators.math import ema, sma
    vals = [float(i) for i in range(50)]
    assert len(ema(vals, 8)) == 50
    assert len(sma(vals, 8)) == 50
    assert ema([], 8) == []


def test_atr_rsi_adx_produce_finite_output():
    from hermes_agent.indicators.math import atr, rsi, adx
    cs = _candles(150)
    for fn in (atr, rsi, adx):
        out = fn(cs, 14)
        assert len(out) == 150
        assert any(math.isfinite(x) for x in out)


# ── triggers ────────────────────────────────────────────────────────────
def test_triggers_return_shape():
    from hermes_agent.indicators.triggers import (
        pct_move_spike, volume_spike, breakout, range_compression, trend_strength,
    )
    cs = _candles(150)
    for fn in (pct_move_spike, volume_spike, breakout, range_compression, trend_strength):
        h = fn(cs)
        assert set(h) == {"name", "score", "reason", "fired"}
        assert isinstance(h["fired"], bool)


def test_composite_score_in_range():
    from hermes_agent.indicators.triggers import pct_move_spike, volume_spike, composite_score
    cs = _candles(150)
    weights = {"pctMoveSpike": 0.35, "volumeSpike": 0.25}
    s = composite_score([pct_move_spike(cs), volume_spike(cs)], weights)
    assert 0 <= s <= 100
    assert composite_score([], weights) == 0


# ── exchange order-result parsing (DRY-5 helper) ────────────────────────
def test_parse_order_result():
    from hermes_agent.client.exchange import _parse_order_result
    filled = {"status": "ok", "response": {"data": {"statuses": [{"filled": {"oid": 123}}]}}}
    assert _parse_order_result(filled) == {"ok": True, "order_id": "123"}
    err = {"status": "ok", "response": {"data": {"statuses": [{"error": "bad px"}]}}}
    assert _parse_order_result(err) == {"ok": False, "error": "bad px"}
    resting = {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 7}}]}}}
    assert _parse_order_result(resting, accept_resting=True) == {"ok": True, "order_id": "7"}
    assert _parse_order_result("boom")["ok"] is False


# ── research verdict parsing (camelCase fallback kept intentionally) ─────
def test_parse_verdict_json_camelcase():
    from hermes_agent.agents.research import parse_verdict
    txt = ('reasoning\n{"verdict":"LONG","confidence":0.8,"side":"long",'
           '"entryPx":100,"stopPx":95,"tpPx":110,"reasoning":"x"}')
    v = parse_verdict(txt, "BTC", {"mid": 50})
    assert v["verdict"] == "LONG" and v["side"] == "long"
    assert v["entry_px"] == 100 and v["stop_px"] == 95 and v["tp_px"] == 110


def test_parse_verdict_empty_defaults_to_pass():
    from hermes_agent.agents.research import parse_verdict
    v = parse_verdict("", "BTC", {"mid": 42})
    assert v["verdict"] == "PASS" and v["entry_px"] == 42


# ── kelly sizing ────────────────────────────────────────────────────────
def test_kelly_size():
    from hermes_agent.agents.executor import kelly_size
    assert kelly_size(0.9, 1000, 2.0, 500) > 0
    assert kelly_size(0.3, 1000, 2.0, 500) == 0          # negative edge
    assert kelly_size(0.99, 1_000_000, 5.0, 100) == 100  # capped


# ── risk gates ──────────────────────────────────────────────────────────
def _ctx(**kw):
    from hermes_agent.agents.risk_gates import GateContext
    base = dict(confidence=0.9, current_positions=[], trade_notional_usd=50,
                daily_pnl=0, market_volume_24h_usd=1e8, coin="BTC",
                trade_side="long", has_binary_news_risk=False, equity=1000,
                total_open_notional=0)
    base.update(kw)
    return GateContext(**base)


def test_risk_gates_pass_and_block():
    from hermes_agent.agents.risk_gates import eval_all_gates
    cfg = {"min_ai_confidence": 0.8, "max_concurrent": 3, "max_trade_notional_usd": 200,
           "max_daily_loss_usd": -100, "min_market_volume_usd": 5e6,
           "max_total_notional_pct": 1.0, "cooldown_min": 60}
    assert eval_all_gates(_ctx(), cfg)["blocked"] is False
    blocked = eval_all_gates(_ctx(confidence=0.1), cfg)
    assert blocked["blocked"] is True
    assert any("confidence" in r for r in blocked["block_reasons"])


# ── DSL exit engine (incl. the ExitVerdict.coin field added by cleanup) ──
def test_dsl_max_loss_exit_populates_coin():
    from hermes_agent.agents import dsl_exit
    from hermes_agent.agents.executor import monitor_exits
    dsl_exit._active_positions.clear()
    dsl_exit.register_position("ETH", "long", 100.0)
    verdicts = dsl_exit.check_all_positions({"ETH": 96.0})  # 4% loss > 2.5% cap
    assert len(verdicts) == 1 and verdicts[0].exit is True
    assert verdicts[0].coin == "ETH"          # field the cleanup added
    exits = monitor_exits({"ETH": 96.0})
    assert exits and exits[0]["coin"] == "ETH"
    dsl_exit._active_positions.clear()


def test_dsl_no_exit_when_flat():
    from hermes_agent.agents import dsl_exit
    dsl_exit._active_positions.clear()
    dsl_exit.register_position("SOL", "long", 100.0)
    assert dsl_exit.check_all_positions({"SOL": 100.5}) == []
    dsl_exit._active_positions.clear()


# ── resolve_user_address (DRY-2 helper) ─────────────────────────────────
def test_resolve_user_address(monkeypatch):
    from hermes_agent.client.hl_client import resolve_user_address
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", "0xMASTER")
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", "0xWALLET")
    assert resolve_user_address() == "0xMASTER"
    monkeypatch.delenv("HYPERLIQUID_MASTER_ADDRESS")
    assert resolve_user_address() == "0xWALLET"


# ── memory round-trip ───────────────────────────────────────────────────
def test_memory_record_and_read():
    from hermes_agent.agents.memory import AgentMemory
    m = AgentMemory()
    m.record_trade({"id": "t1", "coin": "BTC", "size_usd": 10})
    m.record_analysis({"id": "a1", "coin": "BTC"})
    assert m.get_recent_trades()[-1]["id"] == "t1"
    assert m.get_analysis_by_id("a1")["coin"] == "BTC"


# ── MCP server: stub table + end-to-end stdio handshake ─────────────────
def _load_mcp():
    spec = importlib.util.spec_from_file_location("mcpsrv", MCP_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mcpsrv"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_mcp_stub_table_and_tool_coverage():
    mod = _load_mcp()
    assert len(mod._STUB_RESPONSES) == 48
    assert len({t["name"] for t in mod.TOOLS}) == 100
    handler = mod._make_stub_handler({"rewards": []})
    assert json.loads(handler({})) == {"rewards": [], "note": "SDK method pending"}


def test_mcp_server_stdio_end_to_end():
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "get_rewards", "arguments": {}}},
    ]
    inp = "\n".join(json.dumps(r) for r in reqs) + "\n"
    proc = subprocess.run([sys.executable, MCP_SCRIPT], input=inp,
                          capture_output=True, text=True, timeout=90, cwd=str(ROOT))
    resps = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert len(resps) == 3, proc.stderr
    assert resps[0]["result"]["serverInfo"]["name"] == "hermes-trader"
    assert len(resps[1]["result"]["tools"]) == 100
    call = json.loads(resps[2]["result"]["content"][0]["text"])
    assert call == {"rewards": [], "note": "SDK method pending"}
