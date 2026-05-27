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
    monkeypatch.setattr(executor, "fetch_account_state", lambda u, **kw: {
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
    monkeypatch.setattr(executor, "fetch_account_state", lambda u, **kw: {
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
    # Equity coin uses the configured equity proxy
    assert market_regime.detect_regime("TSLA") == "up"
    assert calls == ["BTC", market_regime.EQUITY_PROXY]
    # Commodity uses its own ticker
    assert market_regime.detect_regime("NATGAS") == "up"
    assert calls == ["BTC", market_regime.EQUITY_PROXY, "NATGAS"]


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


def test_mcp_norm_coin_preserves_hip3_dex_prefix():
    """`_norm_coin` uppercases bare crypto tickers but never the lowercase
    HIP-3 dex prefix — a naive .upper() turns `xyz:MU` into `XYZ:MU` and
    breaks every HIP-3 position lookup the MCP server does."""
    mod = _load_mcp()
    assert mod._norm_coin("btc") == "BTC"
    assert mod._norm_coin("BTC") == "BTC"
    assert mod._norm_coin("xyz:mu") == "xyz:MU"
    assert mod._norm_coin("xyz:MU") == "xyz:MU"
    assert mod._norm_coin("vntl:nvda") == "vntl:NVDA"
    assert mod._norm_coin("") == ""


def test_mcp_stub_table_and_tool_coverage():
    mod = _load_mcp()
    # The stub list is now a list of tool names (not a dict of fake payloads).
    # Each stubbed tool returns an explicit `not_implemented` error so LLM
    # callers don't silently consume placeholder data.
    assert len(mod._STUB_TOOL_NAMES) == 48
    assert len({t["name"] for t in mod.TOOLS}) == 100
    handler = mod._make_stub_handler("get_rewards")
    res = json.loads(handler({}))
    assert res["error"] == "not_implemented"
    assert res["tool"] == "get_rewards"
    assert "stub" in res["reason"].lower()


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
    assert call["error"] == "not_implemented"
    assert call["tool"] == "get_rewards"


# ── HIP-3 aggregation in fetch_account_state ────────────────────────────
def test_fetch_account_state_aggregates_hip3_dexes(monkeypatch):
    """include_hip3=True sums equity across main + per-dex clearinghouses,
    concatenates positions, and prefixes bare HIP-3 coins with the dex name."""
    from hermes_trader.client import hl_client

    def _fake_http_post(path, payload):
        kind = payload.get("type")
        if kind == "clearinghouseState" and "dex" not in payload:
            return {
                "marginSummary": {"accountValue": "1000", "totalNtlPos": "500", "totalMarginUsed": "100"},
                "withdrawable": "800",
                "assetPositions": [
                    {"position": {"coin": "BTC", "szi": "0.1", "entryPx": "60000"}},
                ],
            }
        if kind == "clearinghouseState" and payload.get("dex") == "xyz":
            return {
                "marginSummary": {"accountValue": "250", "totalNtlPos": "300"},
                # Bare coin name — code should prefix to "xyz:MU"
                "assetPositions": [
                    {"position": {"coin": "MU", "szi": "5", "entryPx": "100"}},
                ],
            }
        if kind == "clearinghouseState" and payload.get("dex") == "vntl":
            return {
                "marginSummary": {"accountValue": "50", "totalNtlPos": "0"},
                "assetPositions": [],
            }
        if kind == "spotClearinghouseState":
            return {"balances": [{"coin": "USDC", "total": "10"}]}
        return None

    monkeypatch.setattr(hl_client, "_http_post", _fake_http_post)
    monkeypatch.setattr("hermes_trader.client.universe.list_hip3_dexes", lambda: ["xyz", "vntl"])

    state = hl_client.fetch_account_state("0xUSER", include_hip3=True)
    # Aggregated equity = main 1000 + xyz 250 + vntl 50 = 1300
    assert state["equity"] == 1300.0
    # Aggregated notional = main 500 + xyz 300 + vntl 0 = 800
    assert state["total_ntl"] == 800.0
    # `available` = main free initial margin (equity 1000 - margin used 100);
    # stays main-only so executor sizing doesn't bleed in cross-dex idle USDC
    assert state["available"] == 900.0
    # Per-dex breakdown exposed for the dashboard
    assert state["dex_equity"] == {"": 1000.0, "xyz": 250.0, "vntl": 50.0}
    # Positions: main BTC + HIP-3 MU, with bare MU prefixed to xyz:MU
    coins = [p["position"]["coin"] for p in state["asset_positions"]]
    assert coins == ["BTC", "xyz:MU"]


def test_fetch_account_state_main_only_default(monkeypatch):
    """Default include_hip3=False keeps the behavior the executor relies on
    for trade sizing — equity must reflect only the main clearinghouse so
    free-margin calculations don't bleed in idle HIP-3 USDC."""
    from hermes_trader.client import hl_client

    def _fake_http_post(path, payload):
        if payload.get("type") == "clearinghouseState":
            assert "dex" not in payload  # must NOT query HIP-3 dexes
            return {
                "marginSummary": {"accountValue": "1000", "totalNtlPos": "500", "totalMarginUsed": "0"},
                "withdrawable": "1000",
                "assetPositions": [],
            }
        if payload.get("type") == "spotClearinghouseState":
            return {"balances": []}
        return None

    monkeypatch.setattr(hl_client, "_http_post", _fake_http_post)
    state = hl_client.fetch_account_state("0xUSER")
    assert state["equity"] == 1000.0
    assert state["available"] == 1000.0


# ── Scan bucket split ─────────────────────────────────────────────────────
def test_scan_bucket_split_keeps_hip3_slice(monkeypatch):
    """With include_hip3=True the scanner reserves HERMES_MAX_MARKETS_HIP3
    slots for HIP-3 markets so high-volume crypto doesn't crowd them out."""
    from hermes_trader.agents import perception

    # 100 fake crypto markets (all higher volume than the HIP-3 ones) +
    # 10 HIP-3 markets. With max=10 / hip3=3, expect 7 crypto + 3 HIP-3 = 10.
    universe = [
        {"coin": f"C{i}", "type": "perp", "dex": None, "dayNtlVlm": 1_000_000_000 - i}
        for i in range(100)
    ] + [
        {"coin": f"xyz:H{i}", "type": "perp", "dex": "xyz", "dayNtlVlm": 50_000_000 - i}
        for i in range(10)
    ]
    mids = {m["coin"]: "100" for m in universe}

    monkeypatch.setenv("HERMES_MAX_MARKETS", "10")
    monkeypatch.setenv("HERMES_MAX_MARKETS_HIP3", "3")
    monkeypatch.setenv("HERMES_MAX_MARKETS_MOVERS", "0")  # tested separately
    monkeypatch.setattr(perception, "fetch_all_mids", lambda include_hip3=False: mids)
    monkeypatch.setattr(perception, "get_universe", lambda include_hip3=False: universe)
    monkeypatch.setattr(perception, "_scan_single_market", lambda m, mid, cfg, ms: (True, None))
    # Force include_hip3=True via the runtime config
    monkeypatch.setattr("hermes_trader.agents.config_store.read_agent_config",
                        lambda: {"enable_hip3": True})

    seen = []
    real_scan = perception._scan_single_market
    def _capture(m, mid, cfg, ms):
        seen.append(m["coin"])
        return (True, None)
    monkeypatch.setattr(perception, "_scan_single_market", _capture)

    perception.scan_once(min_score=0)
    crypto_seen = [c for c in seen if not c.startswith("xyz:")]
    hip3_seen = [c for c in seen if c.startswith("xyz:")]
    # Crypto budget = 10 - 3 = 7; HIP-3 budget = 3
    assert len(crypto_seen) == 7, f"crypto picked: {crypto_seen}"
    assert len(hip3_seen) == 3, f"hip3 picked: {hip3_seen}"


# ── Contribution-aware daily PnL ────────────────────────────────────────
def test_fetch_aggregate_contributions_classifies_send_events(monkeypatch):
    """`fetch_aggregate_contributions_since` distinguishes pool-boundary
    transfers (spot↔perp, spot↔HIP-3) from intra-pool transfers (main↔xyz),
    treating only the former as contributions to the aggregated equity."""
    from hermes_trader.client import hl_client

    USER = "0xUSER"
    events = [
        # spot → xyz: $30 into the pool
        {"delta": {"type": "send", "user": USER, "destination": USER,
                   "sourceDex": "spot", "destinationDex": "xyz",
                   "usdcValue": "30.0"}},
        # spot → main: $50 into the pool
        {"delta": {"type": "send", "user": USER, "destination": USER,
                   "sourceDex": "spot", "destinationDex": "",
                   "usdcValue": "50.0"}},
        # main → spot: $20 OUT of the pool
        {"delta": {"type": "send", "user": USER, "destination": USER,
                   "sourceDex": "", "destinationDex": "spot",
                   "usdcValue": "20.0"}},
        # main → xyz: $100 intra-pool — must be NEUTRAL
        {"delta": {"type": "send", "user": USER, "destination": USER,
                   "sourceDex": "", "destinationDex": "xyz",
                   "usdcValue": "100.0"}},
        # xyz → vntl: $40 intra-pool — must be NEUTRAL
        {"delta": {"type": "send", "user": USER, "destination": USER,
                   "sourceDex": "xyz", "destinationDex": "vntl",
                   "usdcValue": "40.0"}},
        # External deposit: $200 into pool
        {"delta": {"type": "deposit", "usdcValue": "200.0"}},
        # External withdrawal: $15 out
        {"delta": {"type": "withdraw", "usdcValue": "15.0"}},
    ]
    monkeypatch.setattr(hl_client, "_http_post", lambda path, payload: events)
    monkeypatch.setattr("hermes_trader.client.universe.list_hip3_dexes",
                        lambda: ["xyz", "vntl", "km"])

    # Net = 30 + 50 - 20 + 0 + 0 + 200 - 15 = 245
    net = hl_client.fetch_aggregate_contributions_since(USER, start_ms=1)
    assert net == 245.0


def test_fetch_aggregate_contributions_skips_when_no_user(monkeypatch):
    """Defensive zero-return when user is empty or start_ms is invalid —
    a missing wallet should never crash the heartbeat."""
    from hermes_trader.client import hl_client
    assert hl_client.fetch_aggregate_contributions_since("", start_ms=1) == 0.0
    assert hl_client.fetch_aggregate_contributions_since("0xUSER", start_ms=0) == 0.0


def test_track_daily_pnl_subtracts_contributions():
    """A $50 spot→perp transfer must not appear as $50 of trading profit."""
    from hermes_trader.agents.memory import AgentMemory
    import time
    m = AgentMemory()
    # Seed start-of-day so the function takes the "established baseline" branch.
    m._start_of_day_equity = 200.0
    m._day_start_ts = int(time.time()) + 1  # in future → won't reset baseline
    # Equity grew $60 since start-of-day, but $50 was a transfer in →
    # only $10 is real trading PnL.
    m.track_daily_pnl(current_equity=260.0, net_contributions=50.0)
    assert m.get_daily_pnl() == 10.0
    # Pure trading gain with no contributions still works.
    m.track_daily_pnl(current_equity=270.0, net_contributions=50.0)
    assert m.get_daily_pnl() == 20.0  # 270 - 200 - 50


# ── enable_crypto / enable_hip3 asset-class toggles ──────────────────────
def _scan_with_config(monkeypatch, cfg):
    """Scaffolding: run perception.scan_once with a fake universe + config,
    return the list of coins that actually got candle-fetched."""
    from hermes_trader.agents import perception
    universe = [
        {"coin": "BTC", "type": "perp", "dex": None, "dayNtlVlm": 9e9},
        {"coin": "ETH", "type": "perp", "dex": None, "dayNtlVlm": 5e9},
        {"coin": "xyz:MU", "type": "perp", "dex": "xyz", "dayNtlVlm": 2.7e8},
        {"coin": "xyz:CRCL", "type": "perp", "dex": "xyz", "dayNtlVlm": 3.4e7},
    ]
    mids = {m["coin"]: "100" for m in universe}
    monkeypatch.setenv("HERMES_MAX_MARKETS", "10")
    monkeypatch.setenv("HERMES_MAX_MARKETS_HIP3", "5")
    monkeypatch.setenv("HERMES_MAX_MARKETS_MOVERS", "0")  # tested separately
    monkeypatch.setattr(perception, "fetch_all_mids", lambda include_hip3=False: mids)
    monkeypatch.setattr(perception, "get_universe", lambda include_hip3=False: universe)
    monkeypatch.setattr("hermes_trader.agents.config_store.read_agent_config",
                        lambda: cfg)
    seen = []
    monkeypatch.setattr(perception, "_scan_single_market",
                        lambda m, mid, c, ms: (seen.append(m["coin"]), (True, None))[1])
    perception.scan_once(min_score=0)
    return seen


def test_scan_crypto_only_skips_hip3(monkeypatch):
    """enable_crypto=True, enable_hip3=False → only native HL markets scanned."""
    seen = _scan_with_config(monkeypatch, {"enable_crypto": True, "enable_hip3": False})
    assert "BTC" in seen and "ETH" in seen
    assert not any(c.startswith("xyz:") for c in seen), seen


def test_scan_hip3_only_skips_crypto(monkeypatch):
    """enable_crypto=False, enable_hip3=True → only HIP-3 markets scanned."""
    seen = _scan_with_config(monkeypatch, {"enable_crypto": False, "enable_hip3": True})
    assert set(seen) == {"xyz:MU", "xyz:CRCL"}, seen


def test_scan_both_disabled_returns_empty(monkeypatch):
    """Both flags off → no-op scan, no candles fetched."""
    seen = _scan_with_config(monkeypatch, {"enable_crypto": False, "enable_hip3": False})
    assert seen == []


def test_scan_default_config_runs_crypto_only(monkeypatch):
    """Missing/empty config defaults to crypto enabled, HIP-3 disabled —
    backwards-compatible with deployments predating the toggle."""
    seen = _scan_with_config(monkeypatch, {})
    assert "BTC" in seen
    assert not any(c.startswith("xyz:") for c in seen)


def test_executor_blocks_hip3_when_disabled(monkeypatch):
    """A stale HIP-3 analysis must not execute when enable_hip3 is False."""
    from hermes_trader.agents import executor
    monkeypatch.setattr(executor, "read_agent_config",
                        lambda: {"mode": "LIVE", "enable_crypto": True, "enable_hip3": False})
    res = executor.maybe_execute({"id": "a1", "coin": "xyz:MU"})
    assert res["executed"] is False
    assert "hip3_disabled" in res["reason"]


def test_executor_blocks_crypto_when_disabled(monkeypatch):
    """A stale crypto analysis must not execute when enable_crypto is False."""
    from hermes_trader.agents import executor
    monkeypatch.setattr(executor, "read_agent_config",
                        lambda: {"mode": "LIVE", "enable_crypto": False, "enable_hip3": True})
    res = executor.maybe_execute({"id": "a2", "coin": "BTC"})
    assert res["executed"] is False
    assert "crypto_disabled" in res["reason"]


def test_scan_picks_low_volume_big_movers(monkeypatch):
    """The movers sub-bucket fetches candles for high-%-move markets that
    don't crack the volume cut — fixing the gap where IO +17%, HMSTR +9.6%,
    DYDX +8.5% were going unscanned because BTC/ETH/SOL dominated the top.

    Setup: 5 quiet high-volume coins (the volume budget happily takes them)
    + 5 low-volume coins with big swings (must end up in the movers slot).
    """
    from hermes_trader.agents import perception

    universe = (
        # 5 quiet crypto majors, sorted by volume
        [{"coin": f"MAJOR{i}", "type": "perp", "dex": None,
          "dayNtlVlm": 1e9 - i, "prevDayPx": 100.0, "midPx": 100.1}  # +0.1% — quiet
         for i in range(5)]
        # 5 low-volume big movers; volume ABOVE the floor so they're eligible
        + [{"coin": f"MOVER{i}", "type": "perp", "dex": None,
            "dayNtlVlm": 2_000_000 - i*1000, "prevDayPx": 100.0, "midPx": 100.0 + (10 + i)}
           for i in range(5)]
        # 1 micro-cap with insane move BUT below the floor — must be excluded
        + [{"coin": "PICO", "type": "perp", "dex": None,
            "dayNtlVlm": 50_000, "prevDayPx": 1.0, "midPx": 1.5}]  # +50% but $50k vol
    )
    mids = {m["coin"]: "100" for m in universe}

    monkeypatch.setenv("HERMES_MAX_MARKETS", "10")
    monkeypatch.setenv("HERMES_MAX_MARKETS_MOVERS", "3")
    monkeypatch.setenv("HERMES_MOVERS_VOL_FLOOR_USD", "1000000")
    monkeypatch.setattr(perception, "fetch_all_mids", lambda include_hip3=False: mids)
    monkeypatch.setattr(perception, "get_universe", lambda include_hip3=False: universe)
    monkeypatch.setattr("hermes_trader.agents.config_store.read_agent_config",
                        lambda: {"enable_crypto": True, "enable_hip3": False})

    seen = []
    monkeypatch.setattr(perception, "_scan_single_market",
                        lambda m, mid, c, ms: (seen.append(m["coin"]), (True, None))[1])
    perception.scan_once(min_score=0)

    # Volume budget = 10 - 3 = 7 → all 5 MAJORs + 2 of the 5 MOVERs by volume
    # Then movers slot adds top-3 by |24h%| among the remaining MOVERs.
    assert any(c == "MAJOR0" for c in seen), seen
    movers_picked = [c for c in seen if c.startswith("MOVER")]
    # At least 3 movers should be picked total (some via volume, top remainder via momentum)
    assert len(movers_picked) >= 3, f"expected >=3 movers, got {movers_picked}"
    # Pico-cap below the volume floor must NEVER be scanned (noise filter)
    assert "PICO" not in seen, f"pico-cap leaked through floor: {seen}"


def test_rehydrate_preserves_trackers_for_unqueried_dexes(monkeypatch, tmp_path):
    """A timeout on the `xyz` HIP-3 dex used to drop every xyz tracker as
    stale and reset peak/floor/phase-2 state on the next cycle. Now the
    rehydrator scopes its stale check to dexes that were actually queried,
    so a transient HL outage leaves DSL state intact."""
    dsl_exit, _ = _isolate_dsl_state(monkeypatch, tmp_path)
    # Register 3 trackers: main BTC, xyz:MU (HIP-3), vntl:NVDA (HIP-3).
    dsl_exit.register_position("BTC", "long", 60000.0)
    dsl_exit.register_position("xyz:MU", "long", 920.0)
    dsl_exit.register_position("vntl:NVDA", "long", 500.0)
    # Bump phase-2 state on the xyz tracker, then persist to disk so it
    # survives the load_state() call inside rehydrate_from_exchange.
    t = dsl_exit._active_positions["xyz:MU_long"]
    t.peak_px = 950.0
    t._last_floor = 935.0
    dsl_exit._save_state()

    # Simulate one cycle where main returned BTC but xyz dex timed out.
    # queried_dexes excludes "xyz" → xyz:MU tracker must be preserved.
    # vntl was queried successfully and returned NVDA → that one stays too.
    positions = [
        {"position": {"coin": "BTC", "szi": "0.1", "entryPx": "60000"}},
        {"position": {"coin": "vntl:NVDA", "szi": "1", "entryPx": "500"}},
    ]
    dsl_exit.rehydrate_from_exchange(positions, queried_dexes={"", "vntl"})

    # xyz:MU was NOT in queried_dexes → preserved with phase-2 state intact.
    assert "xyz:MU_long" in dsl_exit._active_positions, "xyz tracker wrongly dropped"
    assert dsl_exit._active_positions["xyz:MU_long"].peak_px == 950.0
    assert dsl_exit._active_positions["xyz:MU_long"]._last_floor == 935.0

    # And the legacy behavior still works: pass queried_dexes=None and any
    # missing position gets dropped exactly like before.
    dsl_exit.rehydrate_from_exchange(positions, queried_dexes=None)
    assert "xyz:MU_long" not in dsl_exit._active_positions
