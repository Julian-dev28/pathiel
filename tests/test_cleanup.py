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

from hermes_trader.models.types import Candle

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
    from hermes_trader.indicators.math import candle_val
    assert candle_val(Candle(t=1, o=1, h=2, l=0.5, c=1.5, v=9), "c") == 1.5
    assert candle_val({"c": 7.0}, "c") == 7.0
    assert candle_val({}, "c") == 0


def test_ema_sma():
    from hermes_trader.indicators.math import ema, sma
    vals = [float(i) for i in range(50)]
    assert len(ema(vals, 8)) == 50
    assert len(sma(vals, 8)) == 50
    assert ema([], 8) == []


def test_atr_rsi_adx_produce_finite_output():
    from hermes_trader.indicators.math import atr, rsi, adx
    cs = _candles(150)
    for fn in (atr, rsi, adx):
        out = fn(cs, 14)
        assert len(out) == 150
        assert any(math.isfinite(x) for x in out)


def test_rsi_and_adx_stay_in_0_100_bound():
    """RSI and ADX are mathematically bounded 0-100 — every finite output
    value must respect that. A negative RSI means the loss/gain accumulator
    math is broken (regression guard for the avg_l sign bug)."""
    from hermes_trader.indicators.math import rsi, adx
    # exercise rising, falling and choppy series so the smoothing loop runs
    rising = [Candle(t=i, o=100 + i, h=101 + i, l=99 + i, c=100 + i, v=10) for i in range(150)]
    falling = [Candle(t=i, o=250 - i, h=251 - i, l=249 - i, c=250 - i, v=10) for i in range(150)]
    choppy = _candles(150)
    for series in (rising, falling, choppy):
        for fn in (rsi, adx):
            for v in fn(series, 14):
                if math.isfinite(v):
                    assert 0.0 <= v <= 100.0, f"{fn.__name__} out of bound: {v}"


# ── triggers ────────────────────────────────────────────────────────────
def test_triggers_return_shape():
    from hermes_trader.indicators.triggers import (
        pct_move_spike, volume_spike, breakout, range_compression, trend_strength,
    )
    cs = _candles(150)
    for fn in (pct_move_spike, volume_spike, breakout, range_compression, trend_strength):
        h = fn(cs)
        assert set(h) == {"name", "score", "reason", "fired"}
        assert isinstance(h["fired"], bool)


def test_composite_score_in_range():
    from hermes_trader.indicators.triggers import pct_move_spike, volume_spike, composite_score
    cs = _candles(150)
    weights = {"pctMoveSpike": 0.35, "volumeSpike": 0.25}
    s = composite_score([pct_move_spike(cs), volume_spike(cs)], weights)
    assert 0 <= s <= 100
    assert composite_score([], weights) == 0


def test_momentum_burst_fires_on_large_move():
    from hermes_trader.indicators.triggers import momentum_burst
    flat = [Candle(t=i, o=100, h=100, l=100, c=100.0, v=10) for i in range(10)]
    h = momentum_burst(flat, lookback=2, pct_threshold=4.0)
    assert h["name"] == "momentumBurst" and h["fired"] is False

    # +6% over the last 2 bars — well past a 4% threshold
    surge = flat[:-2] + [
        Candle(t=8, o=103, h=103, l=103, c=103.0, v=10),
        Candle(t=9, o=106, h=106, l=106, c=106.0, v=10),
    ]
    h = momentum_burst(surge, lookback=2, pct_threshold=4.0)
    assert h["fired"] is True
    assert h["score"] > 0
    assert "up" in h["reason"]

    # a downward burst fires too
    crash = flat[:-2] + [
        Candle(t=8, o=97, h=97, l=97, c=97.0, v=10),
        Candle(t=9, o=94, h=94, l=94, c=94.0, v=10),
    ]
    assert momentum_burst(crash, lookback=2, pct_threshold=4.0)["fired"] is True


# ── exchange order-result parsing (DRY-5 helper) ────────────────────────
def test_min_order_size_meets_10_dollar_floor():
    """_min_order_size must yield >= $10 notional at the coin's size precision.
    Regression: MEGA ($0.084, integer sizes) — 100 coins is only ~$8.4."""
    from hermes_trader.client.exchange import _min_order_size
    cases = [(0.084334, 0), (1.56, 0), (76000.0, 5), (3.2, 2), (0.0001, 0)]
    for price, sz_dec in cases:
        ms = _min_order_size(price, sz_dec)
        assert ms * price >= 10.0, f"price={price} sz_dec={sz_dec}: ${ms * price:.2f}"
        tick = 10.0 ** (-sz_dec)
        assert abs(round(ms / tick) - ms / tick) < 1e-9  # exact tick multiple
    # the specific regression: MEGA needs more than the old 100-coin cap
    assert _min_order_size(0.084334, 0) > 100


def test_parse_order_result():
    from hermes_trader.client.exchange import _parse_order_result
    filled = {"status": "ok", "response": {"data": {"statuses": [{"filled": {"oid": 123}}]}}}
    assert _parse_order_result(filled) == {"ok": True, "order_id": "123"}
    err = {"status": "ok", "response": {"data": {"statuses": [{"error": "bad px"}]}}}
    assert _parse_order_result(err) == {"ok": False, "error": "bad px"}
    resting = {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 7}}]}}}
    assert _parse_order_result(resting, accept_resting=True) == {"ok": True, "order_id": "7"}
    assert _parse_order_result("boom")["ok"] is False


def test_parse_order_result_extracts_avg_px_and_total_sz():
    """Realized-PnL computation depends on these fields being threaded through
    from the SDK response — regression guard against the parser dropping them."""
    from hermes_trader.client.exchange import _parse_order_result
    filled = {"status": "ok", "response": {"data": {"statuses": [
        {"filled": {"oid": 99, "avgPx": "0.5435", "totalSz": "100.0"}}
    ]}}}
    out = _parse_order_result(filled)
    assert out["ok"] is True and out["order_id"] == "99"
    assert out["avg_px"] == 0.5435 and out["total_sz"] == 100.0

    # Garbage avgPx should be tolerated, not raise — order still parses ok.
    garbage = {"status": "ok", "response": {"data": {"statuses": [
        {"filled": {"oid": 1, "avgPx": "nope"}}
    ]}}}
    g = _parse_order_result(garbage)
    assert g["ok"] is True and "avg_px" not in g


# ── research verdict parsing (camelCase fallback kept intentionally) ─────
def test_parse_verdict_json_camelcase():
    from hermes_trader.agents.research import parse_verdict
    txt = ('reasoning\n{"verdict":"LONG","confidence":0.8,"side":"long",'
           '"entryPx":100,"stopPx":95,"tpPx":110,"reasoning":"x"}')
    v = parse_verdict(txt, "BTC", {"mid": 50})
    assert v["verdict"] == "LONG" and v["side"] == "long"
    assert v["entry_px"] == 100 and v["stop_px"] == 95 and v["tp_px"] == 110


def test_parse_verdict_empty_defaults_to_pass():
    from hermes_trader.agents.research import parse_verdict
    v = parse_verdict("", "BTC", {"mid": 42})
    assert v["verdict"] == "PASS" and v["entry_px"] == 42


def test_fetch_news_no_key_returns_no_news(monkeypatch):
    """Without BRAVE_API_KEY, news fetch degrades to 'no news' — never raises."""
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    from hermes_trader.agents.research import _fetch_news
    assert _fetch_news("BTC") == "no news"


# ── kelly sizing ────────────────────────────────────────────────────────
def test_kelly_size():
    from hermes_trader.agents.executor import kelly_size
    assert kelly_size(0.9, 1000, 2.0, 500) > 0
    assert kelly_size(0.3, 1000, 2.0, 500) == 0          # negative edge
    assert kelly_size(0.99, 1_000_000, 5.0, 100) == 100  # capped


# ── risk gates ──────────────────────────────────────────────────────────
def _ctx(**kw):
    from hermes_trader.agents.risk_gates import GateContext
    base = dict(confidence=0.9, current_positions=[], trade_notional_usd=50,
                daily_pnl=0, market_volume_24h_usd=1e8, coin="BTC",
                trade_side="long", has_binary_news_risk=False, equity=1000,
                total_open_notional=0)
    base.update(kw)
    return GateContext(**base)


def test_risk_gates_pass_and_block():
    from hermes_trader.agents.risk_gates import eval_all_gates
    cfg = {"min_ai_confidence": 0.8, "max_concurrent": 3, "max_trade_notional_usd": 200,
           "max_daily_loss_usd": -100, "min_market_volume_usd": 5e6,
           "max_total_notional_pct": 1.0, "cooldown_min": 60}
    assert eval_all_gates(_ctx(), cfg)["blocked"] is False
    blocked = eval_all_gates(_ctx(confidence=0.1), cfg)
    assert blocked["blocked"] is True
    assert any("confidence" in r for r in blocked["block_reasons"])


def test_cfg_camelcase_tolerance():
    """Gate config keys resolve whether written snake_case or camelCase."""
    from hermes_trader.agents.risk_gates import _cfg
    assert _cfg({"max_trade_notional_usd": 30}, "max_trade_notional_usd", 200) == 30
    assert _cfg({"maxTradeNotionalUsd": 20}, "max_trade_notional_usd", 200) == 20  # camelCase
    assert _cfg({"minAiConfidence": 0.5}, "min_ai_confidence", 0.8) == 0.5
    assert _cfg({}, "max_trade_notional_usd", 200) == 200  # default


# ── DSL exit engine (incl. the ExitVerdict.coin field added by cleanup) ──
def test_dsl_max_loss_exit_populates_coin(monkeypatch, tmp_path):
    from hermes_trader.agents.executor import monitor_exits
    dsl_exit, _ = _isolate_dsl_state(monkeypatch, tmp_path)
    dsl_exit.register_position("ETH", "long", 100.0)
    verdicts = dsl_exit.check_all_positions({"ETH": 96.0})  # 4% loss > 2.5% cap
    assert len(verdicts) == 1 and verdicts[0].exit is True
    assert verdicts[0].coin == "ETH"          # field the cleanup added
    exits = monitor_exits({"ETH": 96.0})
    assert exits and exits[0]["coin"] == "ETH"


def test_dsl_no_exit_when_flat(monkeypatch, tmp_path):
    dsl_exit, _ = _isolate_dsl_state(monkeypatch, tmp_path)
    dsl_exit.register_position("SOL", "long", 100.0)
    assert dsl_exit.check_all_positions({"SOL": 100.5}) == []


def _isolate_dsl_state(monkeypatch, tmp_path):
    """Point DSL persistence at a tmp file and clear the in-memory + load latches."""
    from hermes_trader.agents import dsl_exit
    state_file = tmp_path / "dsl.json"
    monkeypatch.setattr(dsl_exit, "DSL_STATE_FILE", str(state_file))
    dsl_exit._active_positions.clear()
    dsl_exit._loaded_from_disk = False
    return dsl_exit, state_file


def test_dsl_persistence_roundtrip(monkeypatch, tmp_path):
    """register_position writes state; load_state on a fresh registry restores it."""
    dsl_exit, state_file = _isolate_dsl_state(monkeypatch, tmp_path)

    t = dsl_exit.register_position("ETH", "long", 100.0)
    t.peak_px = 105.0
    t._last_floor = 102.5
    dsl_exit._save_state()
    assert state_file.exists()

    # Simulate a process restart.
    dsl_exit._active_positions.clear()
    dsl_exit._loaded_from_disk = False
    dsl_exit.load_state()

    assert "ETH_long" in dsl_exit._active_positions
    restored = dsl_exit._active_positions["ETH_long"]
    assert restored.entry_px == 100.0
    assert restored.peak_px == 105.0
    assert restored._last_floor == 102.5
    dsl_exit._active_positions.clear()


def test_dsl_deregister_position(monkeypatch, tmp_path):
    dsl_exit, _ = _isolate_dsl_state(monkeypatch, tmp_path)
    dsl_exit.register_position("BTC", "long", 50_000.0)
    assert dsl_exit.deregister_position("BTC", "long") is True
    assert "BTC_long" not in dsl_exit._active_positions
    assert dsl_exit.deregister_position("BTC", "long") is False  # idempotent


def test_dsl_rehydrate_from_exchange(monkeypatch, tmp_path):
    """rehydrate synthesizes a tracker for an existing exchange position and drops
    trackers whose coin is no longer open."""
    dsl_exit, _ = _isolate_dsl_state(monkeypatch, tmp_path)

    # Pre-existing tracker for a coin that is NOT in the exchange position list
    # should be dropped.
    dsl_exit.register_position("OLD", "long", 1.0)

    asset_positions = [
        {"position": {"coin": "ETH", "szi": "0.5", "entryPx": "3000"}},
        {"position": {"coin": "SOL", "szi": "-10", "entryPx": "150"}},
        {"position": {"coin": "ZERO", "szi": "0", "entryPx": "1"}},  # ignored
    ]
    dsl_exit.rehydrate_from_exchange(asset_positions)

    keys = set(dsl_exit._active_positions)
    assert "ETH_long" in keys
    assert "SOL_short" in keys
    assert "OLD_long" not in keys
    assert "ZERO_long" not in keys and "ZERO_short" not in keys
    assert dsl_exit._active_positions["ETH_long"].entry_px == 3000.0
    assert dsl_exit._active_positions["SOL_short"].entry_px == 150.0
    dsl_exit._active_positions.clear()


def test_dsl_close_helper_deregisters(monkeypatch, tmp_path):
    """close_position_market deregisters the tracker on a successful close."""
    from hermes_trader.agents import dsl_exit, executor
    dsl_exit, _ = _isolate_dsl_state(monkeypatch, tmp_path)
    dsl_exit.register_position("ETH", "long", 100.0)

    monkeypatch.setattr(executor, "resolve_user_address", lambda: "0xUSER")
    monkeypatch.setattr(executor, "fetch_account_state", lambda u: {
        "asset_positions": [{"position": {"coin": "ETH", "szi": "0.5", "entryPx": "100"}}],
    })
    monkeypatch.setattr(executor, "get_hl_price", lambda c: 99.0)
    monkeypatch.setattr(executor, "place_hl_order",
                        lambda is_buy, size, mid_price, coin: {"ok": True, "order_id": "x1"})

    res = executor.close_position_market("ETH")
    assert res["ok"] is True
    assert res["side"] == "long"
    assert "ETH_long" not in dsl_exit._active_positions


def test_close_position_market_computes_realized_pnl_from_fill(monkeypatch, tmp_path):
    """When place_hl_order returns avg_px, the close result carries an exact
    realized PnL (leveraged × spot move from fill, minus taker fees) — this is
    what the dashboard surfaces to match HL's display."""
    from hermes_trader.agents import dsl_exit, executor
    dsl_exit, _ = _isolate_dsl_state(monkeypatch, tmp_path)
    # Long ARB 10x, entry 0.11684; close fills at 0.10522 → +9.945% spot,
    # +99.45% gross, − (2 × 0.025 × 10 = 0.5%) fees = +98.95% net realized.
    # We register as SHORT here since the screenshot showed ARB SHORT 10x.
    dsl_exit.register_position("ARB", "short", 0.11684, leverage=10)

    monkeypatch.setattr(executor, "resolve_user_address", lambda: "0xUSER")
    monkeypatch.setattr(executor, "fetch_account_state", lambda u: {
        "asset_positions": [{"position": {"coin": "ARB", "szi": "-1000", "entryPx": "0.11684"}}],
    })
    monkeypatch.setattr(executor, "get_hl_price", lambda c: 0.10522)
    monkeypatch.setattr(executor, "place_hl_order",
                        lambda is_buy, size, mid_price, coin: {
                            "ok": True, "order_id": "999",
                            "avg_px": 0.10522, "total_sz": 1000.0,
                        })

    res = executor.close_position_market("ARB")
    assert res["ok"] is True
    assert res["side"] == "short"
    assert res["fill_px"] == 0.10522
    assert res["entry_px"] == 0.11684
    assert res["leverage"] == 10
    # Short profits when fill < entry: (0.11684 - 0.10522) / 0.11684 ≈ 9.9452%
    assert abs(res["spot_pct"] - 9.9452) < 0.01
    # Realized = spot × 10 − (0.025 × 2 × 10) = 99.45 − 0.5 = 98.95
    assert abs(res["realized_pnl_pct"] - 98.95) < 0.05
    assert "ARB_short" not in dsl_exit._active_positions


# ── market regime + gate ─────────────────────────────────────────────────
def test_classify_asset():
    from hermes_trader.agents.market_regime import classify_asset
    # crypto default
    assert classify_asset("BTC") == "crypto"
    assert classify_asset("PEPE") == "crypto"
    assert classify_asset("randomcoin42") == "crypto"
    # equity perps
    assert classify_asset("TSLA") == "equity"
    assert classify_asset("nvda") == "equity"   # case-insensitive
    assert classify_asset("MSTR") == "equity"
    # commodity perps
    assert classify_asset("NATGAS") == "commodity"
    assert classify_asset("SILVER") == "commodity"


def test_trend_from_closes_up_down_neutral():
    """EMA20>EMA50 + positive fast-slope → up; opposite → down; flat → neutral."""
    from hermes_trader.agents.market_regime import _trend_from_closes
    # Pure uptrend: prices rising linearly
    assert _trend_from_closes([100 + i for i in range(60)]) == "up"
    # Pure downtrend
    assert _trend_from_closes([200 - i for i in range(60)]) == "down"
    # Pure flat
    assert _trend_from_closes([100.0] * 60) == "neutral"
    # Too few candles
    assert _trend_from_closes([100.0] * 20) == "neutral"


def test_detect_regime_caches_and_uses_proxy(monkeypatch):
    """detect_regime should call the proxy (BTC/NVDA/own) and cache the result."""
    from hermes_trader.agents import market_regime
    market_regime._regime_cache.clear()
    calls: list[str] = []
    monkeypatch.setattr(market_regime, "_detect_for_proxy",
                        lambda proxy: calls.append(proxy) or "up")
    # First call for an alt coin → fetches BTC proxy
    assert market_regime.detect_regime("PEPE") == "up"
    assert calls == ["BTC"]
    # Second call for another alt → cache hit, no new fetch
    assert market_regime.detect_regime("WIF") == "up"
    assert calls == ["BTC"]
    # Equity coin uses NVDA proxy
    assert market_regime.detect_regime("TSLA") == "up"
    assert calls == ["BTC", "NVDA"]
    # Commodity uses its own ticker
    assert market_regime.detect_regime("NATGAS") == "up"
    assert calls == ["BTC", "NVDA", "NATGAS"]


def test_market_regime_gate_aligned_passes(monkeypatch):
    from hermes_trader.agents import market_regime
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "up")
    # Long when up → pass, regardless of confidence
    r = market_regime_gate(_ctx(confidence=0.1, trade_side="long"))
    assert r["pass"] is True


def test_market_regime_gate_neutral_passes(monkeypatch):
    from hermes_trader.agents import market_regime
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    r = market_regime_gate(_ctx(confidence=0.1, trade_side="short"))
    assert r["pass"] is True


def test_market_regime_gate_counter_low_conf_blocks(monkeypatch):
    from hermes_trader.agents import market_regime
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "up")
    r = market_regime_gate(_ctx(confidence=0.5, trade_side="short"))
    assert r["pass"] is False
    assert "counter-regime" in r["reason"]


def test_market_regime_gate_counter_high_conf_passes(monkeypatch):
    """A 0.85-confidence counter-trend trade should sneak through the gate —
    high-conviction contrarian trades are the whole point of the bypass."""
    from hermes_trader.agents import market_regime
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "up")
    r = market_regime_gate(_ctx(confidence=0.85, trade_side="short"))
    assert r["pass"] is True


def test_market_regime_gate_wired_into_eval_all(monkeypatch):
    """The new gate is part of the 12-gate evaluation now and blocks at the
    right time — regression guard against forgetting to wire it in."""
    from hermes_trader.agents import market_regime
    from hermes_trader.agents.risk_gates import eval_all_gates
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "up")
    cfg = {"min_ai_confidence": 0.3, "max_concurrent": 10,
           "max_trade_notional_usd": 1000, "max_daily_loss_usd": -100,
           "min_market_volume_usd": 5e6, "max_total_notional_pct": 10.0,
           "cooldown_min": 0, "counter_regime_min_conf": 0.7}
    # Low-conf short in an up regime → blocked, with the new reason surfaced
    out = eval_all_gates(_ctx(confidence=0.4, trade_side="short"), cfg)
    assert out["blocked"] is True
    assert any("counter-regime" in r for r in out["block_reasons"])
    # Aligned long → not blocked by the regime gate
    out_ok = eval_all_gates(_ctx(confidence=0.4, trade_side="long"), cfg)
    assert out_ok["results"]["market_regime"]["pass"] is True


# ── resolve_user_address (DRY-2 helper) ─────────────────────────────────
def test_resolve_user_address(monkeypatch):
    from hermes_trader.client.hl_client import resolve_user_address
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", "0xMASTER")
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", "0xWALLET")
    assert resolve_user_address() == "0xMASTER"
    monkeypatch.delenv("HYPERLIQUID_MASTER_ADDRESS")
    assert resolve_user_address() == "0xWALLET"


# ── memory round-trip ───────────────────────────────────────────────────
def test_memory_record_and_read():
    from hermes_trader.agents.memory import AgentMemory
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
