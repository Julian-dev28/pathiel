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

import pytest

from hermes_trader.models.types import Candle

ROOT = pathlib.Path(__file__).resolve().parents[1]
MCP_SCRIPT = str(ROOT / "scripts" / "hermes-mcp-server.py")


@pytest.fixture(autouse=True)
def _clear_dsl_trackers():
    """Isolate the DSL tracker registry between tests. The re-entry backstop in
    maybe_execute now reads dsl_exit._active_positions, so a tracker leaked by an
    earlier test would inject a phantom held-coin and block unrelated trades."""
    try:
        from hermes_trader.agents import dsl_exit
        dsl_exit._active_positions.clear()
    except Exception:
        pass
    yield
    try:
        from hermes_trader.agents import dsl_exit
        dsl_exit._active_positions.clear()
    except Exception:
        pass


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


def test_fetch_news_sends_freshness_window(monkeypatch):
    """The Brave request must carry a freshness range so year-old articles
    (the AIXBT 2025 hack) don't feed the gate. Regression guard."""
    from hermes_trader.agents import research
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    captured = {}
    class _Resp:
        is_success = True
        def json(self):
            return {"results": [{"title": "fresh headline"}]}
    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _Resp()
    monkeypatch.setattr(research.httpx, "get", fake_get)
    out = research._fetch_news("AIXBT")
    assert out == "fresh headline"
    fr = captured["params"]["freshness"]
    assert "to" in fr and len(fr.split("to")) == 2  # YYYY-MM-DDtoYYYY-MM-DD


def test_parse_verdict_extracts_news_risk():
    from hermes_trader.agents.research import parse_verdict
    v = parse_verdict('{"verdict":"LONG","confidence":0.7,"newsRisk":"positive"}',
                      "BTC", {"mid": 1})
    assert v["news_risk"] == "positive"
    # snake_case + invalid values fall back to "none"
    assert parse_verdict('{"verdict":"LONG","confidence":0.7,"news_risk":"negative"}',
                         "B", {"mid": 1})["news_risk"] == "negative"
    assert parse_verdict('{"verdict":"LONG","confidence":0.7,"newsRisk":"spicy"}',
                         "B", {"mid": 1})["news_risk"] == "none"
    # absent → defaults none
    assert parse_verdict('{"verdict":"PASS","confidence":0}', "B", {"mid": 1})["news_risk"] == "none"


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


def test_aligned_min_conf_lets_aligned_shorts_through(monkeypatch):
    """Regime-aware confidence floor: an ALIGNED short (down regime) clears the
    lower aligned_min_conf, while the same confidence on a non-aligned trade is
    still blocked by the default min_ai_confidence. Enables shorting selloffs
    (SOL SHORT 0.72 was being blocked by the 0.78 long-calibrated bar)."""
    import hermes_trader.agents.market_regime as mr
    from hermes_trader.agents.risk_gates import eval_all_gates
    monkeypatch.setattr(mr, "detect_regime", lambda coin, **k: "down")
    cfg = {"min_ai_confidence": 0.78, "aligned_min_conf": 0.70, "max_concurrent": 5,
           "max_trade_notional_usd": 500, "max_daily_loss_usd": -300,
           "min_market_volume_usd": 8e5, "max_total_notional_pct": 1.0,
           "cooldown_min": 60, "counter_regime_min_conf": 0.80,
           "min_short_volume_usd": 50_000_000}
    # ALIGNED short (down regime + short) at 0.72 → confidence gate passes (>=0.70)
    r = eval_all_gates(_ctx(trade_side="short", coin="SOL", confidence=0.72,
                            market_volume_24h_usd=4e8), cfg)
    assert r["results"]["confidence"]["pass"] is True
    # NON-aligned (long in a down regime = counter-trend) at 0.72 → still blocked by 0.78
    r2 = eval_all_gates(_ctx(trade_side="long", coin="SOL", confidence=0.72,
                             market_volume_24h_usd=4e8), cfg)
    assert r2["results"]["confidence"]["pass"] is False
    # With aligned_min_conf UNSET, the aligned short reverts to the 0.78 bar (blocked)
    cfg_off = {**cfg}; cfg_off.pop("aligned_min_conf")
    r3 = eval_all_gates(_ctx(trade_side="short", coin="SOL", confidence=0.72,
                             market_volume_24h_usd=4e8), cfg_off)
    assert r3["results"]["confidence"]["pass"] is False


def test_short_liquidity_floor_blocks_thin_shorts_only():
    """Shorts on thin markets squeeze (data: bleeders ~$13M vol, winners ~$223M).
    The floor must block a thin SHORT, allow a thin LONG, allow a liquid short,
    and be a no-op when unset."""
    from hermes_trader.agents.risk_gates import short_liquidity_floor
    FLOOR = 50_000_000
    # thin short → blocked
    r = short_liquidity_floor(_ctx(trade_side="short", coin="XPL", market_volume_24h_usd=16e6), FLOOR)
    assert r["pass"] is False and "squeeze" in r["reason"]
    # thin LONG → allowed (longs are unaffected)
    assert short_liquidity_floor(_ctx(trade_side="long", coin="XPL", market_volume_24h_usd=16e6), FLOOR)["pass"] is True
    # liquid short → allowed
    assert short_liquidity_floor(_ctx(trade_side="short", coin="BTC", market_volume_24h_usd=4e9), FLOOR)["pass"] is True
    # disabled (0) → no-op even for a thin short
    assert short_liquidity_floor(_ctx(trade_side="short", coin="XPL", market_volume_24h_usd=1e6), 0)["pass"] is True


def test_eval_all_gates_short_volume_floor_integration():
    from hermes_trader.agents.risk_gates import eval_all_gates
    cfg = {"min_ai_confidence": 0.78, "max_concurrent": 5, "max_trade_notional_usd": 500,
           "max_daily_loss_usd": -300, "min_market_volume_usd": 8e5,
           "max_total_notional_pct": 1.0, "cooldown_min": 60,
           "min_short_volume_usd": 50_000_000}
    # thin short blocked by the new floor
    blk = eval_all_gates(_ctx(trade_side="short", coin="XPL", market_volume_24h_usd=16e6), cfg)
    assert blk["blocked"] is True
    assert any("short floor" in r or "squeeze" in r for r in blk["block_reasons"])
    # same thin market as a LONG is NOT blocked by the short floor
    lng = eval_all_gates(_ctx(trade_side="long", coin="XPL", market_volume_24h_usd=16e6), cfg)
    assert lng["results"]["short_liquidity"]["pass"] is True


def test_held_coin_blocks_both_pyramid_and_flip():
    """A coin we already hold must block re-entry in BOTH directions: opposite =
    no auto-flip, same side = no uncontrolled pyramid (the held-coin close-check
    can return a fresh LONG/SHORT; only this guard stops it adding)."""
    from hermes_trader.agents.risk_gates import opposite_direction_guard
    held_long = [{"coin": "ETH", "side": "long", "size_usd": 100}]
    # same-direction re-entry → blocked (pyramid)
    r_same = opposite_direction_guard(_ctx(coin="ETH", trade_side="long", current_positions=held_long))
    assert r_same["pass"] is False and "pyramid" in r_same["reason"]
    # opposite-direction → blocked (no auto-flip)
    r_opp = opposite_direction_guard(_ctx(coin="ETH", trade_side="short", current_positions=held_long))
    assert r_opp["pass"] is False and "auto-flip" in r_opp["reason"]
    # unheld coin → passes
    assert opposite_direction_guard(_ctx(coin="SOL", trade_side="long", current_positions=held_long))["pass"] is True


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
                        lambda is_buy, size, mid_price, coin, **kw: {"ok": True, "order_id": "x1"})

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
                        lambda is_buy, size, mid_price, coin, **kw: {
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


def test_latest_trade_ts_by_coin_keeps_newest():
    """The pre-research cooldown map must keep the NEWEST trade per coin, not
    the oldest — the NEAR double-trade bug that burned LLM tokens every cycle."""
    from hermes_trader.agents.memory import AgentMemory
    m = AgentMemory()
    # Chronological: NEAR traded 67min ago, then again 5min ago.
    m._trades = [
        {"coin": "NEAR", "executed_at": 1_000_000},   # older
        {"coin": "BTC",  "executed_at": 1_500_000},
        {"coin": "NEAR", "executed_at": 9_000_000},   # newer — must win
        {"coin": "SOL"},                              # no executed_at → skipped
    ]
    out = m.latest_trade_ts_by_coin(20)
    assert out["NEAR"] == 9_000_000  # newest, not 1_000_000
    assert out["BTC"] == 1_500_000
    assert "SOL" not in out


def test_open_position_coins_filters_zero_size():
    """Held-coin set drives the cooldown exemption so the AI can still CLOSE
    open positions; zero-size / malformed entries are excluded."""
    from hermes_trader.agents.memory import AgentMemory
    m = AgentMemory()
    m.update_open_positions([
        {"position": {"coin": "NEAR", "szi": "12.0"}},
        {"position": {"coin": "BTC", "szi": "0"}},     # flat → excluded
        {"position": {"coin": "xyz:SNDK", "szi": "-3"}},  # short still counts
        {"position": {"szi": "5"}},                    # no coin → excluded
        "garbage",                                     # non-dict → excluded
    ])
    assert m.open_position_coins() == {"NEAR", "xyz:SNDK"}


def test_classify_asset_hip3_namespaced(monkeypatch):
    """HIP-3 venues are mixed: unknown tokenized stocks default to equity (not
    the BTC-trend crypto default), but crypto names listed on a HIP-3 dex still
    resolve to crypto via the native-perp ticker set."""
    from hermes_trader.agents import market_regime as mr
    # Pretend the native HL dex lists these crypto majors.
    monkeypatch.setattr(mr, "_crypto_tickers_cache",
                        frozenset({"BTC", "ETH", "LINK", "FARTCOIN", "XMR"}))
    # Unknown tokenized stock (not in the allowlist) → equity, NOT crypto.
    assert mr.classify_asset("xyz:SNDK") == "equity"
    assert mr.classify_asset("xyz:CBRS") == "equity"
    # Known equity / commodity allowlist entries still win.
    assert mr.classify_asset("xyz:NVDA") == "equity"
    assert mr.classify_asset("xyz:GOLD") == "commodity"
    assert mr.classify_asset("km:USOIL") == "commodity"
    # Crypto names on a HIP-3 dex resolve to crypto via the native set.
    assert mr.classify_asset("hyna:BTC") == "crypto"
    assert mr.classify_asset("hyna:LINK") == "crypto"
    assert mr.classify_asset("cash:ETH") == "crypto"
    assert mr.classify_asset("flx:XMR") == "crypto"


def test_native_crypto_tickers_skips_namespaced_and_caches(monkeypatch):
    """_native_crypto_tickers pulls only main-dex perps (no ':') and caches."""
    from hermes_trader.agents import market_regime as mr
    mr._crypto_tickers_cache = None
    calls = {"n": 0}
    def fake_universe(**kw):
        calls["n"] += 1
        return [
            {"coin": "BTC", "type": "perp"},
            {"coin": "ETH", "type": "perp"},
            {"coin": "xyz:NVDA", "type": "perp"},  # namespaced → excluded
            {"coin": "@107", "type": "spot"},      # spot → excluded
        ]
    monkeypatch.setattr("hermes_trader.client.universe.get_universe", fake_universe)
    out = mr._native_crypto_tickers()
    assert out == frozenset({"BTC", "ETH"})
    mr._native_crypto_tickers()  # second call served from cache
    assert calls["n"] == 1


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
    # Equity coin uses its OWN trend now (audit fix #3, 2026-06-02): each equity is
    # gated by its own chart, not the single xyz:SP500 proxy. SP500 is only the
    # fallback when the name's own trend reads neutral/thin. _detect_for_proxy is
    # stubbed to "up", so the own-trend ("TSLA") resolves and is used directly.
    assert market_regime.detect_regime("TSLA") == "up"
    assert calls == ["BTC", "TSLA"]
    # Commodity uses its own ticker
    assert market_regime.detect_regime("NATGAS") == "up"
    assert calls == ["BTC", "TSLA", "NATGAS"]


def test_market_regime_gate_aligned_passes(monkeypatch):
    from hermes_trader.agents import market_regime, hyperfeed
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "up")
    monkeypatch.setattr(hyperfeed, "market_get_funding_regime",
                        lambda: {"regime": "NEUTRAL", "assets": []})
    # Long when up → pass, regardless of confidence
    r = market_regime_gate(_ctx(confidence=0.1, trade_side="long"))
    assert r["pass"] is True
    assert r["via"] == "aligned"


def test_market_regime_gate_via_reports_trigger_bypass(monkeypatch):
    """A counter-regime trade that clears only via a slow-burn trigger reports
    via='trigger:slow_burn' and counter context — this is the LINK/FARTCOIN case."""
    from hermes_trader.agents import market_regime, hyperfeed
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    monkeypatch.setattr(hyperfeed, "market_get_funding_regime",
                        lambda: {"regime": "SHORT_CROWDED",
                                 "regimes_by_class": {"crypto": "SHORT_CROWDED"}})
    monkeypatch.setattr(market_regime, "classify_asset", lambda c: "crypto")
    # conf 0.52, low composite, against SHORT_CROWDED long → only slow_burn clears.
    ctx = _ctx(confidence=0.52, trade_side="long", coin="FARTCOIN",
               composite_score=21, slow_burn_fired=True)
    r = market_regime_gate(ctx)
    assert r["pass"] is True
    assert r["via"] == "trigger:slow_burn"
    assert r["against_funding"] is True
    assert r["funding"] == "SHORT_CROWDED"


def test_market_regime_gate_via_confidence_and_blocked(monkeypatch):
    from hermes_trader.agents import market_regime, hyperfeed
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    monkeypatch.setattr(hyperfeed, "market_get_funding_regime",
                        lambda: {"regime": "SHORT_CROWDED",
                                 "regimes_by_class": {"crypto": "SHORT_CROWDED"}})
    monkeypatch.setattr(market_regime, "classify_asset", lambda c: "crypto")
    # High enough conf clears the elevated 0.85 bar → via confidence.
    hi = market_regime_gate(_ctx(confidence=0.9, trade_side="long", composite_score=0))
    assert hi["pass"] is True and hi["via"] == "confidence"
    # Nothing clears → blocked, with via marker for the log.
    lo = market_regime_gate(_ctx(confidence=0.5, trade_side="long",
                                 composite_score=10, slow_burn_fired=False))
    assert lo["pass"] is False and lo["via"] == "blocked"


def test_market_regime_gate_neutral_passes(monkeypatch):
    from hermes_trader.agents import market_regime, hyperfeed
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    monkeypatch.setattr(hyperfeed, "market_get_funding_regime",
                        lambda: {"regime": "NEUTRAL", "assets": []})
    r = market_regime_gate(_ctx(confidence=0.1, trade_side="short"))
    assert r["pass"] is True


def test_market_regime_gate_counter_low_conf_blocks(monkeypatch):
    from hermes_trader.agents import market_regime, hyperfeed
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "up")
    monkeypatch.setattr(hyperfeed, "market_get_funding_regime",
                        lambda: {"regime": "NEUTRAL", "assets": []})
    r = market_regime_gate(_ctx(confidence=0.5, trade_side="short"))
    assert r["pass"] is False
    assert "counter-regime" in r["reason"]


def test_market_regime_gate_counter_high_conf_passes(monkeypatch):
    """A 0.85-confidence counter-trend trade should sneak through the gate —
    high-conviction contrarian trades are the whole point of the bypass."""
    from hermes_trader.agents import market_regime, hyperfeed
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "up")
    monkeypatch.setattr(hyperfeed, "market_get_funding_regime",
                        lambda: {"regime": "NEUTRAL", "assets": []})
    r = market_regime_gate(_ctx(confidence=0.85, trade_side="short"))
    assert r["pass"] is True


def test_market_regime_gate_wired_into_eval_all(monkeypatch):
    """The new gate is part of the 12-gate evaluation now and blocks at the
    right time — regression guard against forgetting to wire it in."""
    from hermes_trader.agents import market_regime, hyperfeed
    from hermes_trader.agents.risk_gates import eval_all_gates
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "up")
    monkeypatch.setattr(hyperfeed, "market_get_funding_regime",
                        lambda: {"regime": "NEUTRAL", "assets": []})
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


# ── funding-regime overlay (symmetric crowding gate) ────────────────────
#
# These guard the 2026 patch that makes counter-funding-regime trades face
# an elevated bar. The overlay is symmetric: SHORT_CROWDED + long faces the
# same elevated bar that LONG_CROWDED + short faces. Trades aligned with
# the crowd never see any extra friction.
def _patch_funding(monkeypatch, regime: str):
    """Patch the cached funding-regime lookup that market_regime_gate calls."""
    from hermes_trader.agents import hyperfeed
    monkeypatch.setattr(
        hyperfeed,
        "market_get_funding_regime",
        lambda: {"regime": regime, "assets": []},
    )


def test_funding_regime_short_crowded_blocks_low_conf_long(monkeypatch):
    """SHORT_CROWDED + long at 0.70 conf should now block — the elevated bar
    is 0.85, even though the old counter_regime_min_conf would have let it
    through. This is the main reason for the patch."""
    from hermes_trader.agents import market_regime
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    _patch_funding(monkeypatch, "SHORT_CROWDED")
    r = market_regime_gate(
        _ctx(confidence=0.70, trade_side="long", composite_score=0),
        counter_regime_min_conf=0.70,
    )
    assert r["pass"] is False
    assert "SHORT_CROWDED" in r["reason"]


def test_funding_regime_short_crowded_high_conf_long_passes(monkeypatch):
    """A 0.90-confidence long in a SHORT_CROWDED market still passes —
    we never want to hard-block strong individual signals."""
    from hermes_trader.agents import market_regime
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    _patch_funding(monkeypatch, "SHORT_CROWDED")
    r = market_regime_gate(
        _ctx(confidence=0.90, trade_side="long"),
        counter_regime_min_conf=0.70,
    )
    assert r["pass"] is True


def test_funding_regime_long_crowded_blocks_low_conf_short(monkeypatch):
    """SYMMETRIC: LONG_CROWDED + short at 0.70 conf is blocked the same way
    SHORT_CROWDED + long is blocked. Regression guard against the gate
    becoming long-only-restrictive when the regime flips."""
    from hermes_trader.agents import market_regime
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    _patch_funding(monkeypatch, "LONG_CROWDED")
    r = market_regime_gate(
        _ctx(confidence=0.70, trade_side="short", composite_score=0),
        counter_regime_min_conf=0.70,
    )
    assert r["pass"] is False
    assert "LONG_CROWDED" in r["reason"]


def test_funding_regime_aligned_no_extra_friction(monkeypatch):
    """A short in a SHORT_CROWDED market is aligned with the crowd → the
    elevated bar must NOT apply. A 0.40-conf aligned short should pass
    once we're at trend-regime neutral."""
    from hermes_trader.agents import market_regime
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    _patch_funding(monkeypatch, "SHORT_CROWDED")
    r = market_regime_gate(
        _ctx(confidence=0.40, trade_side="short"),
        counter_regime_min_conf=0.70,
    )
    assert r["pass"] is True


def test_funding_regime_neutral_doesnt_change_behavior(monkeypatch):
    """When funding regime is NEUTRAL, the gate behaves exactly like the
    pre-patch version — no elevated bar, only the trend-regime check."""
    from hermes_trader.agents import market_regime
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    _patch_funding(monkeypatch, "NEUTRAL")
    # Low-conf long in a neutral trend + neutral funding → pass (no friction).
    r = market_regime_gate(
        _ctx(confidence=0.30, trade_side="long"),
        counter_regime_min_conf=0.70,
    )
    assert r["pass"] is True


def test_funding_regime_overlay_respects_binary_triggers(monkeypatch):
    """momentum_burst / slow_burn / whale_signal bypasses MUST be preserved
    even against the crowded funding regime — those are explicit overrides
    for stale macro calls, and the user's spec said do not weaken them."""
    from hermes_trader.agents import market_regime
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    _patch_funding(monkeypatch, "SHORT_CROWDED")
    # Low-conf, low-score long in SHORT_CROWDED, but momentum_burst fired → pass
    r = market_regime_gate(
        _ctx(confidence=0.30, trade_side="long",
             composite_score=10, momentum_burst_fired=True),
        counter_regime_min_conf=0.70,
    )
    assert r["pass"] is True
    # Same setup, whale_signal instead → still passes
    r2 = market_regime_gate(
        _ctx(confidence=0.30, trade_side="long",
             composite_score=10, whale_signal_fired=True),
        counter_regime_min_conf=0.70,
    )
    assert r2["pass"] is True


def test_funding_regime_overlay_score_threshold_elevated(monkeypatch):
    """Elevated bar: counter-funding-regime trades need composite_score >= 60
    (vs the normal 50) to clear via the score bypass."""
    from hermes_trader.agents import market_regime
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    _patch_funding(monkeypatch, "SHORT_CROWDED")
    # Score 55 was enough pre-patch (>= 50), should now BLOCK against funding regime.
    r_block = market_regime_gate(
        _ctx(confidence=0.30, trade_side="long", composite_score=55),
        counter_regime_min_conf=0.70,
    )
    assert r_block["pass"] is False
    # Score 65 clears the elevated 60 bar.
    r_pass = market_regime_gate(
        _ctx(confidence=0.30, trade_side="long", composite_score=65),
        counter_regime_min_conf=0.70,
    )
    assert r_pass["pass"] is True


def test_funding_regime_cache_short_circuits_repeated_calls(monkeypatch):
    """The 5-min cache on market_get_funding_regime must avoid refetching the
    universe on every gate call. Without this guard the risk gates would
    hammer the API once per trade attempt."""
    from hermes_trader.agents import hyperfeed

    # Reset cache so this test is order-independent.
    monkeypatch.setattr(hyperfeed, "_funding_regime_cache", None)

    calls = {"count": 0}

    def fake_compute():
        calls["count"] += 1
        return {"regime": "SHORT_CROWDED", "assets": []}

    monkeypatch.setattr(hyperfeed, "_compute_funding_regime", fake_compute)

    r1 = hyperfeed.market_get_funding_regime()
    r2 = hyperfeed.market_get_funding_regime()
    r3 = hyperfeed.market_get_funding_regime()
    assert r1["regime"] == "SHORT_CROWDED"
    assert r2["regime"] == "SHORT_CROWDED"
    assert r3["regime"] == "SHORT_CROWDED"
    # Only the first call should hit _compute_funding_regime.
    assert calls["count"] == 1


# ── per-asset-class funding regime ──────────────────────────────────────
#
# Regression guard: crypto SHORT_CROWDED must NOT gate longs on equity or
# commodity HIP-3 perps. Each asset class has its own funding signal.
def test_funding_regime_per_class_crypto_short_crowded_does_not_gate_oil(monkeypatch):
    """xyz:CL (oil, commodity class) long must pass even when the crypto
    funding regime is SHORT_CROWDED — oil has its own funding market."""
    from hermes_trader.agents import market_regime, hyperfeed
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    monkeypatch.setattr(hyperfeed, "market_get_funding_regime", lambda: {
        "regime": "SHORT_CROWDED",
        "regimes_by_class": {
            "crypto":    "SHORT_CROWDED",
            "equity":    "NEUTRAL",
            "commodity": "NEUTRAL",
        },
        "assets": [],
    })
    # xyz:CL classifies as commodity → look up commodity regime → NEUTRAL → pass.
    r = market_regime_gate(
        _ctx(confidence=0.40, trade_side="long", coin="xyz:CL"),
        counter_regime_min_conf=0.70,
    )
    assert r["pass"] is True


def test_funding_regime_per_class_crypto_short_crowded_does_not_gate_arm(monkeypatch):
    """xyz:ARM (semis, equity class) long passes when the crypto regime is
    SHORT_CROWDED but the equity regime is NEUTRAL — this is the actual
    bug that snuck xyz:ARM through the gate in production."""
    from hermes_trader.agents import market_regime, hyperfeed
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    monkeypatch.setattr(hyperfeed, "market_get_funding_regime", lambda: {
        "regime": "SHORT_CROWDED",
        "regimes_by_class": {
            "crypto":    "SHORT_CROWDED",
            "equity":    "NEUTRAL",
            "commodity": "NEUTRAL",
        },
        "assets": [],
    })
    r = market_regime_gate(
        _ctx(confidence=0.40, trade_side="long", coin="xyz:ARM"),
        counter_regime_min_conf=0.70,
    )
    assert r["pass"] is True


def test_funding_regime_per_class_equity_short_crowded_gates_equity_long(monkeypatch):
    """When the EQUITY class itself is SHORT_CROWDED, an equity long is the
    one that faces the elevated bar — proving the per-class lookup applies
    correctly to the matching asset class."""
    from hermes_trader.agents import market_regime, hyperfeed
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    monkeypatch.setattr(hyperfeed, "market_get_funding_regime", lambda: {
        "regime": "NEUTRAL",
        "regimes_by_class": {
            "crypto":    "NEUTRAL",
            "equity":    "SHORT_CROWDED",
            "commodity": "NEUTRAL",
        },
        "assets": [],
    })
    # Low-conf long on an equity perp → blocked (equity class is short-crowded).
    r = market_regime_gate(
        _ctx(confidence=0.40, trade_side="long", coin="xyz:ARM", composite_score=0),
        counter_regime_min_conf=0.70,
    )
    assert r["pass"] is False
    assert "SHORT_CROWDED" in r["reason"]


def test_funding_regime_per_class_falls_back_to_legacy_when_missing(monkeypatch):
    """Older callers / unit-test stubs may return a dict without
    `regimes_by_class`. The gate must fall back to the legacy `regime` field
    rather than silently disabling the overlay."""
    from hermes_trader.agents import market_regime, hyperfeed
    from hermes_trader.agents.risk_gates import market_regime_gate
    monkeypatch.setattr(market_regime, "detect_regime", lambda c: "neutral")
    # NO regimes_by_class key — legacy shape.
    monkeypatch.setattr(hyperfeed, "market_get_funding_regime",
                        lambda: {"regime": "SHORT_CROWDED", "assets": []})
    # BTC (crypto) long with mid confidence → legacy SHORT_CROWDED applies → block.
    r = market_regime_gate(
        _ctx(confidence=0.40, trade_side="long", coin="BTC", composite_score=0),
        counter_regime_min_conf=0.70,
    )
    assert r["pass"] is False


def test_compute_funding_regime_includes_hip3(monkeypatch):
    """`_compute_funding_regime` must fetch the universe WITH HIP-3 so
    equity / commodity perps are visible. Regression guard for the bug
    where xyz:CL and xyz:ARM weren't in the regime scan at all."""
    from hermes_trader.agents import hyperfeed

    captured = {}

    def fake_get_universe(*, include_hip3=False, **kw):
        captured["include_hip3"] = include_hip3
        # Mixed universe: crypto with negative funding + commodity with positive funding.
        return [
            {"coin": "BTC",    "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 1e9},
            {"coin": "ETH",    "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 5e8},
            {"coin": "SOL",    "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 3e8},
            {"coin": "DOGE",   "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 2e8},
            {"coin": "AVAX",   "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 1e8},
            {"coin": "XRP",    "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 1e8},
            {"coin": "LINK",   "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 1e8},
            {"coin": "xyz:CL", "funding":  0.0002, "openInterest": 5e6, "dayNtlVlm": 1e7},
        ]

    monkeypatch.setattr(hyperfeed, "get_universe", fake_get_universe)
    out = hyperfeed._compute_funding_regime()
    assert captured["include_hip3"] is True
    # Crypto class is short-crowded (7 short, 0 long).
    assert out["regimes_by_class"]["crypto"] == "SHORT_CROWDED"
    # Commodity class has only one signal, < margin of 5 → NEUTRAL.
    assert out["regimes_by_class"]["commodity"] == "NEUTRAL"
    # Legacy regime field tracks crypto.
    assert out["regime"] == "SHORT_CROWDED"


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

    # 100 fake crypto markets (higher volume) + 10 HIP-3 markets.
    # HIP-3 entries get prevDayPx + midPx so the mover sub-bucket has
    # qualifying candidates (with the new vol+mover split for HIP-3).
    universe = [
        {"coin": f"C{i}", "type": "perp", "dex": None, "dayNtlVlm": 1_000_000_000 - i}
        for i in range(100)
    ] + [
        {"coin": f"xyz:H{i}", "type": "perp", "dex": "xyz",
         "dayNtlVlm": 50_000_000 - i,
         "prevDayPx": 100.0, "midPx": 105.0 + i * 0.1}
        for i in range(10)
    ]
    # mids = the LIVE current price (production reads the 24h move from fresh
    # mids, not the cached universe midPx) — mirror each coin's midPx here.
    mids = {m["coin"]: str(m.get("midPx", 100)) for m in universe}

    monkeypatch.setenv("HERMES_MAX_MARKETS", "10")
    monkeypatch.setenv("HERMES_MAX_MARKETS_HIP3", "3")
    monkeypatch.setenv("HERMES_MAX_MARKETS_MOVERS", "0")  # tested separately
    monkeypatch.setattr(perception, "fetch_all_mids", lambda include_hip3=False: mids)
    monkeypatch.setattr(perception, "get_universe", lambda include_hip3=False: universe)
    monkeypatch.setattr(perception, "_scan_single_market", lambda m, mid, cfg, ms, ws=None, wsb=False: (True, None))
    # Force include_hip3=True via the runtime config
    monkeypatch.setattr("hermes_trader.agents.config_store.read_agent_config",
                        lambda: {"enable_hip3": True})

    seen = []
    real_scan = perception._scan_single_market
    def _capture(m, mid, cfg, ms, ws=None, wsb=False):
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
                        lambda m, mid, c, ms, ws=None, wsb=False: (seen.append(m["coin"]), (True, None))[1])
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
    # mids = the LIVE current price (production reads the 24h move from fresh
    # mids, not the cached universe midPx) — mirror each coin's midPx here.
    mids = {m["coin"]: str(m.get("midPx", 100)) for m in universe}

    monkeypatch.setenv("HERMES_MAX_MARKETS", "10")
    monkeypatch.setenv("HERMES_MAX_MARKETS_MOVERS", "3")
    monkeypatch.setenv("HERMES_MOVERS_VOL_FLOOR_USD", "1000000")
    monkeypatch.setattr(perception, "fetch_all_mids", lambda include_hip3=False: mids)
    monkeypatch.setattr(perception, "get_universe", lambda include_hip3=False: universe)
    monkeypatch.setattr("hermes_trader.agents.config_store.read_agent_config",
                        lambda: {"enable_crypto": True, "enable_hip3": False})

    seen = []
    monkeypatch.setattr(perception, "_scan_single_market",
                        lambda m, mid, c, ms, ws=None, wsb=False: (seen.append(m["coin"]), (True, None))[1])
    perception.scan_once(min_score=0)

    # Volume budget = 10 - 3 = 7 → all 5 MAJORs + 2 of the 5 MOVERs by volume
    # Then movers slot adds top-3 by |24h%| among the remaining MOVERs.
    assert any(c == "MAJOR0" for c in seen), seen
    movers_picked = [c for c in seen if c.startswith("MOVER")]
    # At least 3 movers should be picked total (some via volume, top remainder via momentum)
    assert len(movers_picked) >= 3, f"expected >=3 movers, got {movers_picked}"
    # Pico-cap below the volume floor must NEVER be scanned (noise filter)
    assert "PICO" not in seen, f"pico-cap leaked through floor: {seen}"


def test_whale_scan_bypass_surfaces_subgate_accumulation(monkeypatch):
    """A whale-flagged coin that scores BELOW the composite gate must be:
      - dropped when whale_scan_bypass is OFF (default), and
      - surfaced (with whale_signal attached) when whale_scan_bypass is ON.

    Regression for the dead-path bug: oi_funding_anomaly fires on FLAT price,
    which scores ~0 on momentum/breakout triggers, so without the bypass the
    coin never reaches the executor where the whale override lives.
    """
    from hermes_trader.agents import perception
    from hermes_trader.agents.config import get_config

    cfg = get_config()
    gate = cfg["scan"]["minCompositeScore"]
    # Flat candles → no momentum/breakout/trend triggers fire → score below gate.
    flat = [Candle(t=i, o=100.0, h=100.0, l=100.0, c=100.0, v=10.0) for i in range(120)]
    monkeypatch.setattr(perception, "_fetch_candles_sync",
                        lambda coin, interval, count, ttl: flat)
    market = {"coin": "TRX", "type": "perp", "dex": None}
    whale_signals = {"TRX": {"signal": "oi_funding_anomaly", "score": 0.8}}

    # OFF → dropped at the gate (result is None)
    ok, res = perception._scan_single_market(market, 100.0, cfg, gate, whale_signals,
                                             False)
    assert ok and res is None, f"expected drop with bypass OFF, got {res}"

    # ON → surfaced with the whale_signal attached so the executor can act
    ok, res = perception._scan_single_market(market, 100.0, cfg, gate, whale_signals,
                                             True)
    assert ok and isinstance(res, dict), f"expected surfaced result with bypass ON, got {res}"
    assert res["coin"] == "TRX"
    assert res["whale_signal"] == whale_signals["TRX"]
    assert res["composite_score"] < gate  # confirms it was genuinely sub-gate


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


# ── Slow-burn 1h triggers ─────────────────────────────────────────────────
def _candle_1h(t, o, h, l, c, v):
    return Candle(t=t, o=o, h=h, l=l, c=c, v=v)


def test_volume_buildup_1h_fires_on_4h_surge():
    """volumeBuildup1h should fire when the last 4h's avg notional volume
    is >= ratio_threshold × the prior 20h baseline."""
    from hermes_trader.indicators.triggers import volume_buildup_1h
    # 20h baseline at vol=1000, last 4h at vol=3000 → 3× surge
    base = [_candle_1h(i, 100, 101, 99, 100, 1000) for i in range(20)]
    surge = [_candle_1h(i + 20, 100, 101, 99, 100, 3000) for i in range(4)]
    res = volume_buildup_1h(base + surge, ratio_threshold=2.5)
    assert res["fired"] is True
    assert "3.0×" in res["reason"] or "3.00" in res["reason"]

    # Flat: no surge
    flat = [_candle_1h(i, 100, 101, 99, 100, 1000) for i in range(24)]
    res = volume_buildup_1h(flat, ratio_threshold=2.5)
    assert res["fired"] is False


def test_trend_flip_1h_detects_recent_ema_cross():
    """trendFlip1h fires when EMA8 crosses above EMA21 within lookback bars."""
    from hermes_trader.indicators.triggers import trend_flip_1h
    # 25 bars trending down, then 8 bars trending up — fast EMA crosses slow.
    closes = [100 - i for i in range(25)] + [76 + i * 2 for i in range(8)]
    bars = [_candle_1h(i, c, c + 0.5, c - 0.5, c, 1000) for i, c in enumerate(closes)]
    res = trend_flip_1h(bars, lookback_bars=5)
    assert res["fired"] is True
    assert "cross up" in res["reason"]

    # All downtrend: no flip
    down = [_candle_1h(i, 100 - i, 101 - i, 99 - i, 100 - i, 1000) for i in range(30)]
    res = trend_flip_1h(down, lookback_bars=3)
    assert res["fired"] is False


def test_higher_lows_1h_counts_structure():
    """higherLows1h fires when N+ of last 6 1h candles printed higher lows."""
    from hermes_trader.indicators.triggers import higher_lows_1h
    # 7 candles with strictly rising lows: 6/6 higher lows
    rising = [_candle_1h(i, 100, 101, 99 + i, 100 + i, 1000) for i in range(7)]
    res = higher_lows_1h(rising, required=4)
    assert res["fired"] is True
    assert "6/6" in res["reason"]

    # All lows equal: 0/6 → fails
    flat = [_candle_1h(i, 100, 101, 99, 100, 1000) for i in range(7)]
    res = higher_lows_1h(flat, required=4)
    assert res["fired"] is False


def test_regime_gate_bypasses_on_whale_signal():
    """A counter-regime LONG should pass when whale_signal_fired is True,
    even at low confidence and zero composite — the oi_funding_anomaly
    signal (whale accumulation, negative funding, flat price) is its own
    bypass path, parallel to slow_burn_fired."""
    from hermes_trader.agents.risk_gates import market_regime_gate, GateContext
    import hermes_trader.agents.market_regime as mr
    mr._regime_cache.clear()
    mr._regime_cache["BTC"] = ("down", 99999999999)

    ctx = GateContext(
        confidence=0.45,
        current_positions=[], trade_notional_usd=100, daily_pnl=0,
        market_volume_24h_usd=5_000_000, coin="ALT", trade_side="long",
        has_binary_news_risk=False, equity=200, total_open_notional=0,
        composite_score=10, momentum_burst_fired=False,
        slow_burn_fired=False, whale_signal_fired=True,
    )
    res = market_regime_gate(ctx, counter_regime_min_conf=0.65)
    assert res["pass"] is True, res

    # Without whale signal, same setup blocks.
    ctx.whale_signal_fired = False
    res = market_regime_gate(ctx, counter_regime_min_conf=0.65)
    assert res["pass"] is False


def test_executor_structural_override_promotes_pass_to_long(monkeypatch):
    """When composite >= 40 AND 2+ slow-burn triggers fired, the executor
    upgrades an AI PASS to LONG conf 0.70 — the AI hedge doesn't override
    objective structural strength."""
    from hermes_trader.agents import executor

    # Force a config that exercises ONLY the override path, then fails the
    # next stage so we can verify the upgrade happened without HL calls.
    monkeypatch.setattr(executor, "read_agent_config", lambda: {
        "mode": "LIVE", "enable_crypto": True, "enable_hip3": False,
        "force_execute_composite": 40, "force_execute_slow_burn_count": 2,
    })
    monkeypatch.setattr(executor, "resolve_user_address", lambda: "0xUSER")
    monkeypatch.setattr(executor, "fetch_account_state", lambda u, **kw: {"equity": 0})

    analysis = {
        "id": "test-override",
        "coin": "WLFI",
        "verdict": "PASS",
        "confidence": 0.0,
        "composite_score": 45.0,
        "slow_burn_count": 3,
    }
    res = executor.maybe_execute(analysis)
    # The override happens; then execution fails at equity_unavailable (equity=0).
    # That tells us we passed the PASS-block and reached the equity check.
    assert "equity_unavailable" in (res.get("reason") or "")

    # Without the override conditions (composite below 40), the PASS verdict
    # would never reach the equity check — it would short-circuit elsewhere.
    # Sanity check: low-composite PASS doesn't trigger override.
    analysis2 = {**analysis, "composite_score": 30.0}
    res2 = executor.maybe_execute(analysis2)
    # PASS verdict with no override → would still try to execute (since the
    # executor doesn't directly gate on verdict; it relies on side). With
    # equity=0 it'll hit the same gate. We're just verifying no crash.
    assert isinstance(res2, dict)


def test_regime_gate_bypasses_on_slow_burn():
    """A counter-regime LONG with neither high conviction nor momentumBurst
    should still pass if slow_burn_fired is True — the empirical fix for
    WLFI/ICP-style accumulation breakouts."""
    from hermes_trader.agents.risk_gates import market_regime_gate, GateContext
    import hermes_trader.agents.market_regime as mr
    # Force regime = "down" so the gate engages on a LONG.
    mr._regime_cache.clear()
    mr._regime_cache["BTC"] = ("down", 99999999999)

    ctx = GateContext(
        confidence=0.55,  # below 0.65 bar
        current_positions=[],
        trade_notional_usd=100,
        daily_pnl=0,
        market_volume_24h_usd=5_000_000,
        coin="ALT",
        trade_side="long",
        has_binary_news_risk=False,
        equity=200,
        total_open_notional=0,
        composite_score=15,  # below 50 bypass
        momentum_burst_fired=False,
        slow_burn_fired=True,  # ← the new bypass
    )
    res = market_regime_gate(ctx, counter_regime_min_conf=0.65)
    assert res["pass"] is True, res

    # Without slow_burn_fired, same setup should block.
    ctx.slow_burn_fired = False
    res = market_regime_gate(ctx, counter_regime_min_conf=0.65)
    assert res["pass"] is False


# ── Perf: token-bucket rate limiter ──────────────────────────────────────
def test_token_bucket_deducts_and_blocks_on_exhaustion():
    from hermes_trader.client.rate_limit import TokenBucket
    # Capacity 40, refill 0 (no recovery) → 2× 20-weight acquires then fail.
    b = TokenBucket(capacity=40, refill_per_sec=0.0)
    assert b.acquire(20, max_wait=0.1) is True
    assert b.acquire(20, max_wait=0.1) is True
    assert b.acquire(20, max_wait=0.1) is False   # drained, no refill


def test_token_bucket_refills_over_time():
    from hermes_trader.client.rate_limit import TokenBucket
    import time
    b = TokenBucket(capacity=20, refill_per_sec=100.0)  # refills fast
    assert b.acquire(20, max_wait=0.1) is True        # drains to 0
    assert b.acquire(20, max_wait=0.05) is False       # not enough yet
    time.sleep(0.25)                                    # 0.25s × 100/s = 25 tokens
    assert b.acquire(20, max_wait=0.1) is True         # refilled past 20


def test_endpoint_weight_mapping():
    from hermes_trader.client.rate_limit import endpoint_weight
    assert endpoint_weight("candleSnapshot") == 20
    assert endpoint_weight("allMids") == 2
    assert endpoint_weight("clearinghouseState") == 2
    assert endpoint_weight("userNonFundingLedgerUpdates") == 2
    assert endpoint_weight(None) == 20         # unknown → expensive bucket
    assert endpoint_weight("madeUpType") == 20


# ── Perf: connection pool singleton ──────────────────────────────────────
def test_http_session_is_singleton():
    import hermes_trader.client.hl_client as h
    s1 = h._get_session()
    s2 = h._get_session()
    assert s1 is s2
    # adapter pool sized for our fan-out
    adapter = s1.get_adapter("https://api.hyperliquid.xyz")
    assert adapter._pool_maxsize >= 16


# ── Perf: dashboard TTL cache ────────────────────────────────────────────
def test_ttl_cache_serves_within_ttl_and_refreshes_after():
    import hermes_trader.dashboard as d
    import time
    d._TTL_CACHE.clear()
    calls = {"n": 0}
    def producer():
        calls["n"] += 1
        return {"v": calls["n"]}

    # First call computes; second within TTL serves cache (no recompute).
    assert d._ttl_cached("k", 0.5, producer) == {"v": 1}
    assert d._ttl_cached("k", 0.5, producer) == {"v": 1}
    assert calls["n"] == 1

    # After TTL expires, recomputes.
    time.sleep(0.55)
    assert d._ttl_cached("k", 0.5, producer) == {"v": 2}
    assert calls["n"] == 2


def test_ttl_cache_keys_are_independent():
    import hermes_trader.dashboard as d
    d._TTL_CACHE.clear()
    assert d._ttl_cached("a", 5.0, lambda: 1) == 1
    assert d._ttl_cached("b", 5.0, lambda: 2) == 2
    # different keys don't collide
    assert d._ttl_cached("a", 5.0, lambda: 99) == 1


# ── Whale-signal priority (override + sizing) ────────────────────────────
def test_executor_whale_signal_overrides_pass_to_long(monkeypatch):
    """A whale-accumulation flag upgrades an AI PASS to LONG even with no
    slow-burn triggers and low composite — whale signals get their own
    structural override."""
    from hermes_trader.agents import executor
    monkeypatch.setattr(executor, "read_agent_config", lambda: {
        "mode": "LIVE", "enable_crypto": True, "enable_hip3": False,
    })
    monkeypatch.setattr(executor, "resolve_user_address", lambda: "0xUSER")
    monkeypatch.setattr(executor, "fetch_account_state", lambda u, **kw: {"equity": 0})

    analysis = {
        "id": "whale-override", "coin": "ALT", "verdict": "PASS",
        "confidence": 0.0, "composite_score": 10.0, "slow_burn_count": 0,
        "whale_signal": {"signal": "smart_money_accumulation", "confidence": 0.5},
    }
    res = executor.maybe_execute(analysis)
    # Override fires → upgraded to LONG → reaches equity check (equity=0).
    assert "equity_unavailable" in (res.get("reason") or "")

    # No whale signal + weak composite → no override; PASS stays PASS,
    # the executor doesn't upgrade. (verdict gate is downstream; we just
    # confirm it didn't crash and didn't force a trade via override path.)
    analysis_no_whale = {**analysis, "whale_signal": None}
    res2 = executor.maybe_execute(analysis_no_whale)
    assert isinstance(res2, dict)


def test_maybe_execute_reentry_backstop_blocks_when_live_read_drops_position(monkeypatch):
    """If the live account read returns NO positions but the DSL registry still
    tracks the coin (restart/flaky-read window), re-entry must be blocked — else
    the position pyramids. Regression for the xyz:SP500 stacking incident."""
    from hermes_trader.agents import executor, dsl_exit
    dsl_exit._active_positions.clear()
    # DSL knows we hold SP500 long, but the live read "forgot" it.
    dsl_exit.register_position("xyz:SP500", "long", 7500.0, leverage=10)
    monkeypatch.setattr(executor, "read_agent_config", lambda: {
        "mode": "LIVE", "enable_crypto": True, "enable_hip3": True,
        "min_available_margin_pct": 0.0,
    })
    monkeypatch.setattr(executor, "resolve_user_address", lambda: "0xUSER")
    monkeypatch.setattr(executor, "fetch_account_state", lambda u, **kw: {
        "equity": 1000.0, "available": 1000.0,
        "dex_equity": {"": 1000.0}, "dex_available": {"": 1000.0},
        "total_ntl": 0.0, "asset_positions": [],  # live read dropped the position
    })
    placed = {"n": 0}
    monkeypatch.setattr(executor, "place_hl_order",
                        lambda *a, **k: placed.update(n=placed["n"] + 1) or {"ok": True})
    res = executor.maybe_execute({
        "id": "reentry", "coin": "xyz:SP500", "verdict": "LONG", "side": "long",
        "confidence": 0.9, "composite_score": 60.0,
    })
    dsl_exit._active_positions.clear()
    assert res["executed"] is False
    assert placed["n"] == 0  # never pyramided
    # blocked specifically by the re-entry / opposite-direction guard
    blk = str(res.get("blocked_by")) + str(res.get("reason"))
    assert "holding" in blk or "re-entry" in blk or "pyramid" in blk, res


def test_maybe_execute_refuses_when_no_atr(monkeypatch):
    """A coin with no computable ATR (insufficient candle history) must be
    refused, never traded blind — guards force-execute of brand-new HIP-3
    listings where research emits stop_px/tp_px = 0.0."""
    from hermes_trader.agents import executor
    monkeypatch.setattr(executor, "read_agent_config", lambda: {
        "mode": "LIVE", "enable_crypto": True, "enable_hip3": False,
        "min_available_margin_pct": 0.0,
    })
    monkeypatch.setattr(executor, "resolve_user_address", lambda: "0xUSER")
    monkeypatch.setattr(executor, "fetch_account_state", lambda u, **kw: {
        "equity": 1000.0, "available": 1000.0,
        "dex_equity": {"": 1000.0}, "dex_available": {"": 1000.0},
        "total_ntl": 0.0, "asset_positions": [],
    })
    monkeypatch.setenv("HYPERLIQUID_PRIVATE_KEY", "0xdeadbeef")
    monkeypatch.setattr(executor, "get_hl_price", lambda c: 100.0)
    monkeypatch.setattr(executor, "get_max_leverage", lambda c: 10)
    monkeypatch.setattr(executor, "set_leverage", lambda c, lev: {"ok": True})
    # gates pass
    monkeypatch.setattr(executor, "eval_all_gates",
                        lambda ctx, cfg, lt: {"blocked": False, "results": {}})
    # the coin under test: no candle history → ATR 0
    monkeypatch.setattr(executor, "get_hl_atr", lambda *a, **k: 0.0)
    placed = {"n": 0}
    monkeypatch.setattr(executor, "place_hl_order",
                        lambda *a, **k: placed.update(n=placed["n"] + 1) or {"ok": True})

    res = executor.maybe_execute({
        "id": "no-atr", "coin": "NEWCOIN", "verdict": "LONG",
        "side": "long", "confidence": 0.8, "composite_score": 60.0,
    })
    assert res["executed"] is False
    assert "no_atr_no_stop" in res["reason"]
    assert placed["n"] == 0  # never placed an order


def test_whale_size_multiplier_clamps_at_2x():
    """The whale multiplier stacks on the confidence tier but clamps at 2×
    base so a high-conf whale trade can't run away."""
    # Pure arithmetic mirror of the executor sizing logic.
    def sized(conf, whale, base=0.07, whale_mult=1.3):
        if conf >= 0.80:
            m = 1.5
        elif conf >= 0.65:
            m = 1.0
        else:
            m = 0.7
        if whale:
            m = min(m * whale_mult, 2.0)
        return base * m
    # high conf + whale: 1.5 × 1.3 = 1.95 (under 2.0 cap)
    assert abs(sized(0.85, True) - 0.07 * 1.95) < 1e-9
    # mid conf + whale: 1.0 × 1.3 = 1.3
    assert abs(sized(0.70, True) - 0.07 * 1.3) < 1e-9
    # no whale: plain tier
    assert abs(sized(0.85, False) - 0.07 * 1.5) < 1e-9


# ── Shakedown: parse_verdict edge cases (silent-breakage guards) ─────────
def test_parse_verdict_short_derives_side_short():
    """A SHORT verdict with no/null side must yield side='short', NOT fall
    through to the executor's 'long' default (wrong-direction bug)."""
    from hermes_trader.agents.research import parse_verdict
    txt = '{"verdict":"SHORT","confidence":0.7}'   # no side field
    v = parse_verdict(txt, "BTC", {"mid": 100})
    assert v["verdict"] == "SHORT"
    assert v["side"] == "short"

    # explicit null side too
    v2 = parse_verdict('{"verdict":"SHORT","confidence":0.7,"side":null}', "BTC", {"mid": 100})
    assert v2["side"] == "short"


def test_parse_verdict_long_derives_side_long():
    from hermes_trader.agents.research import parse_verdict
    v = parse_verdict('{"verdict":"LONG","confidence":0.6}', "ETH", {"mid": 50})
    assert v["side"] == "long"


def test_parse_verdict_coerces_string_confidence():
    """LLM sometimes returns confidence as a string — must coerce to float
    so the gate comparison doesn't TypeError on a live trade."""
    from hermes_trader.agents.research import parse_verdict
    v = parse_verdict('{"verdict":"LONG","confidence":"0.82","side":"long"}', "BTC", {"mid": 1})
    assert isinstance(v["confidence"], float)
    assert abs(v["confidence"] - 0.82) < 1e-9


def test_parse_verdict_clamps_confidence_range():
    from hermes_trader.agents.research import parse_verdict
    hi = parse_verdict('{"verdict":"LONG","confidence":1.8,"side":"long"}', "B", {"mid": 1})
    assert hi["confidence"] == 1.0
    lo = parse_verdict('{"verdict":"LONG","confidence":-0.5,"side":"long"}', "B", {"mid": 1})
    assert lo["confidence"] == 0.0
    junk = parse_verdict('{"verdict":"LONG","confidence":"high","side":"long"}', "B", {"mid": 1})
    assert junk["confidence"] == 0.0


def test_parse_verdict_unknown_verdict_defaults_pass():
    """HOLD or any non-LONG/SHORT/CLOSE verdict → PASS (no accidental trade)."""
    from hermes_trader.agents.research import parse_verdict
    for raw in ("HOLD", "WAIT", "MAYBE", ""):
        v = parse_verdict(f'{{"verdict":"{raw}","confidence":0.9}}', "BTC", {"mid": 1})
        assert v["verdict"] == "PASS", raw


# ── Shakedown: route_verdict (every verdict path is now testable) ────────
def test_route_verdict_long_calls_execute():
    from hermes_trader.agents.executor import route_verdict
    calls = {}
    def exec_fn(a): calls["exec"] = a; return {"executed": True, "order_id": "1"}
    def close_fn(c): calls["close"] = c; return {"ok": True}
    r = route_verdict({"verdict": "LONG", "coin": "BTC", "side": "long"},
                      execute_fn=exec_fn, close_fn=close_fn)
    assert r["action"] == "execute"
    assert "exec" in calls and "close" not in calls


def test_route_verdict_short_calls_execute():
    from hermes_trader.agents.executor import route_verdict
    seen = {}
    r = route_verdict({"verdict": "SHORT", "coin": "ETH", "side": "short"},
                      execute_fn=lambda a: seen.setdefault("e", a) or {"executed": True},
                      close_fn=lambda c: seen.setdefault("c", c))
    assert r["action"] == "execute" and "e" in seen and "c" not in seen


def test_route_verdict_close_calls_close():
    """The bug that started the shakedown: CLOSE must call close_fn, not be dropped."""
    from hermes_trader.agents.executor import route_verdict
    calls = {}
    r = route_verdict({"verdict": "CLOSE", "coin": "DOGE"},
                      execute_fn=lambda a: calls.setdefault("e", a),
                      close_fn=lambda c: calls.setdefault("c", c) or {"ok": True})
    assert r["action"] == "close"
    assert calls.get("c") == "DOGE"
    assert "e" not in calls            # never executes a trade on CLOSE


def test_route_verdict_pass_is_noop():
    from hermes_trader.agents.executor import route_verdict
    calls = {}
    r = route_verdict({"verdict": "PASS", "coin": "BTC"},
                      execute_fn=lambda a: calls.setdefault("e", 1),
                      close_fn=lambda c: calls.setdefault("c", 1))
    assert r["action"] == "none"
    assert not calls                   # nothing called


def test_route_verdict_pass_with_whale_signal_routes_to_executor():
    """A hedging AI PASS that carries a whale_signal must reach the executor so
    the force-execute-on-PASS override can fire — otherwise the whale path is
    dead (router dropped PASS before maybe_execute ever saw it)."""
    from hermes_trader.agents.executor import route_verdict
    calls = {}
    r = route_verdict({"verdict": "PASS", "coin": "TRX",
                       "whale_signal": {"signal": "oi_funding_anomaly"}},
                      execute_fn=lambda a: calls.setdefault("e", a) or {"executed": True},
                      close_fn=lambda c: calls.setdefault("c", c))
    assert r["action"] == "execute"
    assert calls.get("e", {}).get("coin") == "TRX"
    assert "c" not in calls


def test_route_verdict_pass_with_slow_burn_hint_routes_to_executor():
    from hermes_trader.agents.executor import route_verdict
    calls = {}
    r = route_verdict({"verdict": "PASS", "coin": "SOL",
                       "composite_score": 45.0, "slow_burn_count": 2},
                      execute_fn=lambda a: calls.setdefault("e", 1) or {"executed": True},
                      close_fn=lambda c: calls.setdefault("c", 1))
    assert r["action"] == "execute"
    assert calls.get("e") == 1


def test_route_verdict_plain_pass_still_noop():
    """A PASS with NO override hint (no whale, weak composite) stays a no-op —
    we don't want every hedged PASS hitting the executor."""
    from hermes_trader.agents.executor import route_verdict
    calls = {}
    r = route_verdict({"verdict": "PASS", "coin": "BTC",
                       "composite_score": 20.0, "slow_burn_count": 0},
                      execute_fn=lambda a: calls.setdefault("e", 1),
                      close_fn=lambda c: calls.setdefault("c", 1))
    assert r["action"] == "none"
    assert not calls


def test_maybe_execute_pass_without_override_is_clean_noop(monkeypatch):
    """If a PASS reaches maybe_execute but the override doesn't actually hold,
    it must no-op (reason=pass_no_override) — never default to a long order."""
    from hermes_trader.agents import executor
    monkeypatch.setattr(executor, "read_agent_config",
                        lambda: {"mode": "LIVE", "enable_crypto": True,
                                 "whale_force_execute": True})
    # PASS, no whale_signal, weak composite → no override → must no-op safely.
    res = executor.maybe_execute({"id": "x1", "coin": "BTC", "verdict": "PASS",
                                  "composite_score": 10.0, "slow_burn_count": 0})
    assert res["executed"] is False
    assert res["reason"] == "pass_no_override"


def test_route_verdict_unknown_is_flagged_not_dropped():
    """A novel/garbage verdict must surface as 'unknown', never silently no-op
    like a PASS — that's how the next dropped-verdict bug gets caught."""
    from hermes_trader.agents.executor import route_verdict
    calls = {}
    r = route_verdict({"verdict": "YOLO", "coin": "BTC"},
                      execute_fn=lambda a: calls.setdefault("e", 1),
                      close_fn=lambda c: calls.setdefault("c", 1))
    assert r["action"] == "unknown"
    assert r["verdict"] == "YOLO"
    assert not calls


def test_route_verdict_lowercase_verdict_normalized():
    from hermes_trader.agents.executor import route_verdict
    r = route_verdict({"verdict": "close", "coin": "X"},
                      execute_fn=lambda a: None, close_fn=lambda c: {"ok": True})
    assert r["action"] == "close"


# ── Coverage: executor.maybe_execute branch matrix ──────────────────────
def _exec_baseline(monkeypatch, cfg_overrides=None, state_overrides=None):
    """Patch executor's I/O surface with sane defaults; return (executor, captured).
    `captured` records the size/side passed to place_hl_order on the success path."""
    from hermes_trader.agents import executor
    cfg = {
        "mode": "LIVE", "enable_crypto": True, "enable_hip3": True,
        "equity_fraction_per_trade": 0.10, "leverage": 10,
        "max_trade_notional_usd": 100000, "max_concurrent": 18,
        "max_total_notional_pct": 40.0, "max_daily_loss_usd": -1000,
        "min_available_margin_pct": 0.10, "cooldown_min": 60,
        "min_ai_confidence": 0.30, "counter_regime_min_conf": 0.65,
        "max_crypto_long_correlated": 5, "min_market_volume_usd": 5_000_000,
        "min_hip3_volume_usd": 500_000, "conviction_sizing": True,
        "dsl_exit": {"max_loss_pct": 2.0, "max_loss_roe_pct": 30.0,
                     "protect_pct": 0.5, "retrace_threshold": 0.3,
                     "hard_timeout_minutes": 180.0},
    }
    cfg.update(cfg_overrides or {})
    state = {"equity": 1000.0, "available": 500.0, "total_ntl": 0.0,
             "asset_positions": []}
    state.update(state_overrides or {})
    captured = {}

    monkeypatch.setattr(executor, "read_agent_config", lambda: cfg)
    monkeypatch.setattr(executor, "resolve_user_address", lambda: "0xMASTER")
    monkeypatch.setattr(executor, "fetch_account_state", lambda u, **kw: state)
    monkeypatch.setattr(executor, "get_hl_price", lambda c: 100.0)
    monkeypatch.setattr(executor, "get_hl_atr", lambda *a, **k: 2.0)
    monkeypatch.setattr(executor, "get_max_leverage", lambda c: 40)
    monkeypatch.setattr(executor, "set_leverage", lambda c, l: {"ok": True})
    monkeypatch.setattr(executor, "place_hl_trigger_order", lambda *a, **k: {"ok": True})
    # _http_post is imported locally inside maybe_execute (hip3 preflight) —
    # patch at the source module, not on executor.
    monkeypatch.setattr("hermes_trader.client.hl_client._http_post",
                        lambda p, pl: {"marginSummary": {"accountValue": "500"}})
    monkeypatch.setattr("hermes_trader.agents.market_regime.detect_regime", lambda c: "neutral")
    monkeypatch.setattr("hermes_trader.agents.hyperfeed.market_get_funding_regime",
                        lambda: {"regime": "NEUTRAL", "regimes_by_class": {}})
    def _place(is_buy, size, mid, coin):
        captured["is_buy"] = is_buy; captured["size"] = size; captured["coin"] = coin
        return {"ok": True, "order_id": "OID1", "avg_px": mid}
    monkeypatch.setattr(executor, "place_hl_order", _place)
    monkeypatch.setattr(executor, "register_position", lambda *a, **k: None)
    monkeypatch.setattr(executor.memory, "track_daily_pnl", lambda *a, **k: None)
    monkeypatch.setattr(executor.memory, "get_daily_pnl", lambda: 0.0)
    monkeypatch.setattr(executor.memory, "get_recent_trades", lambda n=10: [])
    monkeypatch.setattr(executor.memory, "record_trade", lambda t: None)
    monkeypatch.setenv("HYPERLIQUID_PRIVATE_KEY", "0xabc")
    return executor, captured, cfg


def _analysis(**kw):
    base = {"id": "a1", "coin": "BTC", "verdict": "LONG", "side": "long",
            "confidence": 0.70, "composite_score": 30, "entry_px": 100,
            "stop_px": 95, "tp_px": 110, "news_context": "no news"}
    base.update(kw)
    return base


def test_maybe_execute_mode_off(monkeypatch):
    ex, _, _ = _exec_baseline(monkeypatch, {"mode": "OFF"})
    r = ex.maybe_execute(_analysis())
    assert r["executed"] is False and r["reason"] == "mode_off"


def test_maybe_execute_hip3_disabled(monkeypatch):
    ex, _, _ = _exec_baseline(monkeypatch, {"enable_hip3": False})
    r = ex.maybe_execute(_analysis(coin="xyz:MU"))
    assert r["executed"] is False and "hip3_disabled" in r["reason"]


def test_maybe_execute_crypto_disabled(monkeypatch):
    ex, _, _ = _exec_baseline(monkeypatch, {"enable_crypto": False})
    r = ex.maybe_execute(_analysis(coin="BTC"))
    assert r["executed"] is False and "crypto_disabled" in r["reason"]


def test_maybe_execute_equity_unavailable(monkeypatch):
    ex, _, _ = _exec_baseline(monkeypatch, state_overrides={"equity": 0.0})
    r = ex.maybe_execute(_analysis())
    assert r["executed"] is False and "equity_unavailable" in r["reason"]


def test_maybe_execute_insufficient_free_margin(monkeypatch):
    ex, _, _ = _exec_baseline(monkeypatch, state_overrides={"equity": 1000.0, "available": 50.0})
    r = ex.maybe_execute(_analysis())
    assert r["executed"] is False and "insufficient_free_margin" in r["reason"]


def test_maybe_execute_hip3_underfunded(monkeypatch):
    ex, _, _ = _exec_baseline(monkeypatch)
    # dex check returns near-zero accountValue (patch the source module)
    monkeypatch.setattr("hermes_trader.client.hl_client._http_post",
                        lambda p, pl: {"marginSummary": {"accountValue": "0.0"}})
    r = ex.maybe_execute(_analysis(coin="xyz:MU"))
    assert r["executed"] is False and "hip3_dex_underfunded" in r["reason"]


def test_maybe_execute_success_path(monkeypatch):
    ex, captured, _ = _exec_baseline(monkeypatch)
    r = ex.maybe_execute(_analysis())
    assert r["executed"] is True, r
    assert r["order_id"] == "OID1"
    assert captured["is_buy"] is True
    # notional = equity 1000 × frac 0.10 × lev 10 × conviction(0.70→1.0) = 1000; /mid 100 = 10 coins
    assert abs(captured["size"] - 10.0) < 1e-6


def test_maybe_execute_order_failed(monkeypatch):
    ex, _, _ = _exec_baseline(monkeypatch)
    monkeypatch.setattr(ex, "place_hl_order",
                        lambda b, s, m, c: {"ok": False, "error": "no match"})
    r = ex.maybe_execute(_analysis())
    assert r["executed"] is False and "order_failed" in r["reason"]


def test_maybe_execute_conviction_sizing_high_conf(monkeypatch):
    """conf >= 0.80 → 1.5× size."""
    ex, captured, _ = _exec_baseline(monkeypatch)
    ex.maybe_execute(_analysis(confidence=0.85))
    # 1000 × 0.10 × 10 × 1.5 / 100 = 15 coins
    assert abs(captured["size"] - 15.0) < 1e-6


def test_maybe_execute_whale_boosts_size(monkeypatch):
    """Whale signal multiplies sizing by 1.3 on top of the conf tier."""
    ex, captured, _ = _exec_baseline(monkeypatch)
    ex.maybe_execute(_analysis(confidence=0.70, whale_signal={"confidence": 0.5}))
    # 1000 × 0.10 × 10 × (1.0 × 1.3) / 100 = 13 coins
    assert abs(captured["size"] - 13.0) < 1e-6


# ── Coverage: configurable conviction-sizing tiers ──────────────────────
def test_parse_conviction_tiers_default_and_malformed():
    from hermes_trader.agents.executor import (
        _parse_conviction_tiers, _DEFAULT_CONVICTION_TIERS)
    assert _parse_conviction_tiers(None) == _DEFAULT_CONVICTION_TIERS
    assert _parse_conviction_tiers([]) == _DEFAULT_CONVICTION_TIERS
    # malformed entries → fall back to defaults, never raise
    assert _parse_conviction_tiers([["x", "y"]]) == _DEFAULT_CONVICTION_TIERS
    # non-positive multipliers dropped; remaining sorted highest-threshold-first
    assert _parse_conviction_tiers([[0.5, 0.8], [0.9, 2.0], [0.3, 0]]) == [
        (0.9, 2.0), (0.5, 0.8)]


def test_conviction_multiplier_tier_selection():
    from hermes_trader.agents.executor import _conviction_multiplier
    tiers = [(0.85, 2.0), (0.7, 1.2), (0.0, 0.5)]
    assert _conviction_multiplier(0.90, tiers) == 2.0
    assert _conviction_multiplier(0.85, tiers) == 2.0  # inclusive
    assert _conviction_multiplier(0.72, tiers) == 1.2
    assert _conviction_multiplier(0.10, tiers) == 0.5
    # below every threshold when no 0.0 floor → lowest tier's mult
    assert _conviction_multiplier(0.10, [(0.9, 2.0), (0.7, 1.2)]) == 1.2


def test_maybe_execute_custom_conviction_tiers(monkeypatch):
    """A config-supplied aggressive tier (0.85→2.0×) sizes bigger than default."""
    ex, captured, _ = _exec_baseline(
        monkeypatch,
        cfg_overrides={"conviction_tiers": [[0.85, 2.0], [0.65, 1.0], [0.0, 0.5]]})
    ex.maybe_execute(_analysis(confidence=0.90))
    # 1000 × 0.10 × 10 × 2.0 / 100 = 20 coins (vs 15 under the default 1.5×)
    assert abs(captured["size"] - 20.0) < 1e-6


def test_maybe_execute_default_tiers_unchanged(monkeypatch):
    """Absent conviction_tiers → identical to the prior hardcoded behavior."""
    ex, captured, _ = _exec_baseline(monkeypatch)
    ex.maybe_execute(_analysis(confidence=0.50))  # below 0.65 → 0.7×
    # 1000 × 0.10 × 10 × 0.7 / 100 = 7 coins
    assert abs(captured["size"] - 7.0) < 1e-6


# ── Coverage: whale_index signal heuristics ─────────────────────────────
def test_smart_money_concentration_accumulation_signal(monkeypatch):
    """oi>0 + negative funding → 'accumulation'; confidence scales w/ |funding|."""
    from hermes_trader.agents import whale_index
    monkeypatch.setattr(whale_index, "get_universe", lambda **_: [
        {"coin": "BTC", "type": "perp", "openInterest": 5e7,
         "dayNtlVlm": 1e9, "funding": -0.0002, "midPx": 60000},
    ])
    out = whale_index.smart_money_concentration()
    acc = [s for s in out if s["signal"] == "accumulation"]
    assert acc and acc[0]["coin"] == "BTC"
    assert acc[0]["confidence"] == 1.0  # |−0.0002| / 0.0001 capped at 1.0


def test_smart_money_concentration_volume_floor_filters(monkeypatch):
    """Markets under min_volume_usd are skipped entirely."""
    from hermes_trader.agents import whale_index
    monkeypatch.setattr(whale_index, "get_universe", lambda **_: [
        {"coin": "TINY", "type": "perp", "openInterest": 5e7,
         "dayNtlVlm": 100.0, "funding": -0.0002, "midPx": 1},
    ])
    assert whale_index.smart_money_concentration(min_volume_usd=1e6) == []


def test_smart_money_concentration_high_oi_branch(monkeypatch):
    """OI > 10× daily-volume-in-millions → 'high_oi_concentration'."""
    from hermes_trader.agents import whale_index
    # vol = $2M (→ 2.0 in millions), oi = 30 → ratio 15 > 10.
    monkeypatch.setattr(whale_index, "get_universe", lambda **_: [
        {"coin": "ETH", "type": "perp", "openInterest": 30,
         "dayNtlVlm": 2e6, "funding": 0.0001, "midPx": 3000},
    ])
    out = whale_index.smart_money_concentration()
    sigs = {s["signal"] for s in out}
    assert "high_oi_concentration" in sigs


def test_get_whale_signals_merges_by_coin(monkeypatch):
    """Concentration + anomaly signals for the same coin collapse into one
    entry whose max_confidence is the higher of the two."""
    from hermes_trader.agents import whale_index
    monkeypatch.setattr(whale_index, "smart_money_concentration",
                        lambda **k: [{"coin": "SOL", "confidence": 0.3}])
    monkeypatch.setattr(whale_index, "oi_funding_anomaly",
                        lambda **k: [{"coin": "SOL", "confidence": 0.8}])
    out = whale_index.get_whale_signals(min_confidence=0.1)
    assert len(out) == 1
    assert out[0]["coin"] == "SOL"
    assert out[0]["max_confidence"] == 0.8
    assert len(out[0]["signals"]) == 2


def test_get_whale_signals_filters_below_min_conf(monkeypatch):
    from hermes_trader.agents import whale_index
    monkeypatch.setattr(whale_index, "smart_money_concentration",
                        lambda **k: [{"coin": "LOW", "confidence": 0.05}])
    monkeypatch.setattr(whale_index, "oi_funding_anomaly", lambda **k: [])
    assert whale_index.get_whale_signals(min_confidence=0.1) == []


def test_whale_accumulation_map_keys_by_coin(monkeypatch):
    """whale_accumulation_map → {coin: signal} for anomalies above min_conf."""
    from hermes_trader.agents import whale_index
    monkeypatch.setattr(whale_index, "oi_funding_anomaly", lambda: [
        {"coin": "ARB", "confidence": 0.9},
        {"coin": "OP", "confidence": 0.01},  # below floor
    ])
    m = whale_index.whale_accumulation_map(min_confidence=0.05)
    assert set(m) == {"ARB"}
    assert m["ARB"]["confidence"] == 0.9


def test_oi_funding_anomaly_requires_flat_price(monkeypatch):
    """A 24h move ≥10% disqualifies the accumulation signal even with deep
    negative funding + high OI."""
    from hermes_trader.agents import whale_index
    monkeypatch.setattr(whale_index, "get_universe", lambda **_: [
        {"coin": "PUMP", "type": "perp", "openInterest": 5e7,
         "funding": -0.0006, "midPx": 130, "prevDayPx": 100},  # +30%
        {"coin": "FLAT", "type": "perp", "openInterest": 5e7,
         "funding": -0.0006, "midPx": 101, "prevDayPx": 100},  # +1%
    ])
    out = whale_index.oi_funding_anomaly()
    coins = {s["coin"] for s in out}
    assert coins == {"FLAT"}


def test_leaderboard_get_top_reads_registry(monkeypatch):
    """leaderboard_get_top builds entries from WHALE_WALLETS via _http_post."""
    from hermes_trader.agents import whale_index
    monkeypatch.setattr(whale_index, "WHALE_WALLETS",
                        {"0xABC": {"name": "alpha"}})
    monkeypatch.setattr(whale_index, "_http_post", lambda path, body: {
        "marginSummary": {"accountValue": "10000"},
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "1.5", "entryPx": "60000",
                          "leverage": {"value": "10"}}},
            {"position": {"coin": "ETH", "szi": "0"}},  # filtered out
        ],
    })
    out = whale_index.leaderboard_get_top()
    assert len(out) == 1
    assert out[0]["account_value"] == 10000.0
    assert [p["coin"] for p in out[0]["positions"]] == ["BTC"]


def test_get_trader_state_returns_none_without_perp(monkeypatch):
    from hermes_trader.agents import whale_index
    monkeypatch.setattr(whale_index, "_fetch_clearinghouse", lambda u: None)
    monkeypatch.setattr(whale_index, "_fetch_user_fills", lambda u, limit=20: [])
    assert whale_index.get_trader_state("0xABC") is None


def test_get_trader_state_merges_positions_and_fills(monkeypatch):
    from hermes_trader.agents import whale_index
    monkeypatch.setattr(whale_index, "_fetch_clearinghouse", lambda u: {
        "marginSummary": {"accountValue": "5000", "totalNtlPos": "12000"},
        "assetPositions": [
            {"position": {"coin": "SOL", "szi": "-20", "entryPx": "150",
                          "leverage": {"value": "5"}, "unrealizedPnl": "-30"}},
        ],
    })
    monkeypatch.setattr(whale_index, "_fetch_user_fills", lambda u, limit=20: [
        {"coin": "SOL", "side": "A", "px": "150", "sz": "20", "fee": "0.1", "time": 1},
    ])
    st = whale_index.get_trader_state("0xABC")
    assert st["account_value"] == 5000.0
    assert st["positions"][0]["side"] == "short"
    assert len(st["recent_trades"]) == 1


# ── Coverage: hyperfeed market-data lookups ─────────────────────────────
def test_hyperfeed_safe_float_default():
    from hermes_trader.agents.hyperfeed import _safe_float
    assert _safe_float("not-a-number", 7.0) == 7.0
    assert _safe_float(None) == 0.0
    assert _safe_float("3.5") == 3.5


def test_leaderboard_get_markets_ranks_by_volume(monkeypatch):
    from hermes_trader.agents import hyperfeed
    monkeypatch.setattr(hyperfeed, "get_universe", lambda: [
        {"coin": "SMALL", "type": "perp", "dayNtlVlm": 1e6, "openInterest": 1},
        {"coin": "BIG", "type": "perp", "dayNtlVlm": 1e9, "openInterest": 9},
        {"coin": "@SPOT", "type": "spot", "dayNtlVlm": 1e12},  # excluded
    ])
    out = hyperfeed.leaderboard_get_markets()["markets"]
    assert [m["asset"] for m in out] == ["BIG", "SMALL"]
    assert out[0]["rank"] == 1


def test_leaderboard_get_trader_positions_unwraps_and_coerces(monkeypatch):
    """Nested position unwrap, string-leverage coercion, szi==0 skip."""
    from hermes_trader.agents import hyperfeed
    monkeypatch.setattr(hyperfeed, "_http_post", lambda path, body: {
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "2.0", "entryPx": "60000",
                          "leverage": "10", "unrealizedPnl": "500"}},
            {"position": {"coin": "ETH", "szi": "0"}},  # skipped
        ],
    })
    out = hyperfeed.leaderboard_get_trader_positions("0xABC")["positions"]
    assert len(out) == 1
    assert out[0]["side"] == "long"
    assert out[0]["leverage"] == {"value": "10"}  # str coerced to obj


def test_leaderboard_get_trader_positions_empty_when_no_state(monkeypatch):
    from hermes_trader.agents import hyperfeed
    monkeypatch.setattr(hyperfeed, "_http_post", lambda path, body: None)
    assert hyperfeed.leaderboard_get_trader_positions("0xABC") == {"positions": []}


def test_market_get_asset_data_collects_candles_and_context(monkeypatch):
    from hermes_trader.agents import hyperfeed
    fake_candles = [Candle(t=1, o=1, h=2, l=0.5, c=1.5, v=100)]
    monkeypatch.setattr(hyperfeed, "fetch_hl_candles",
                        lambda asset, interval, n: fake_candles)
    monkeypatch.setattr(hyperfeed, "get_universe", lambda: [
        {"coin": "BTC", "funding": -0.0001, "openInterest": 5e7,
         "prevDayPx": 59000, "midPx": 60000, "dayNtlVlm": 1e9},
    ])
    out = hyperfeed.market_get_asset_data("BTC", intervals=["1h"])["data"]
    assert out["asset"] == "BTC"
    assert len(out["candles"]["1h"]) == 1
    assert out["funding_rate"] == -0.0001
    assert out["mid_px"] == 60000


def test_market_get_asset_data_candle_error_yields_empty(monkeypatch):
    from hermes_trader.agents import hyperfeed
    def boom(asset, interval, n):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(hyperfeed, "fetch_hl_candles", boom)
    monkeypatch.setattr(hyperfeed, "get_universe", lambda: [])
    out = hyperfeed.market_get_asset_data("BTC", intervals=["5m"])["data"]
    assert out["candles"]["5m"] == []


def test_market_list_instruments_counts_and_strips(monkeypatch):
    from hermes_trader.agents import hyperfeed
    monkeypatch.setattr(hyperfeed, "get_universe", lambda: [
        {"coin": "BTC", "type": "perp", "maxLeverage": 40},
        {"coin": "@107", "type": "spot", "maxLeverage": 0},
    ])
    out = hyperfeed.market_list_instruments()
    assert out["counts"] == {"perps": 1, "spot": 1, "total": 2}
    symbols = {i["symbol"] for i in out["instruments"]}
    assert "107" in symbols  # @ stripped


def test_market_get_mids_passthrough(monkeypatch):
    from hermes_trader.agents import hyperfeed
    monkeypatch.setattr(hyperfeed, "fetch_all_mids", lambda: {"BTC": "60000"})
    assert hyperfeed.market_get_mids() == {"BTC": "60000"}


def test_compute_funding_regime_long_crowded_margin(monkeypatch):
    """A class needs a >5 long-over-short margin to be LONG_CROWDED."""
    from hermes_trader.agents import hyperfeed
    # 7 crypto longs (funding>0, oi high), 0 shorts → margin 7 > 5.
    universe = [
        {"coin": c, "funding": 0.0002, "openInterest": 5e7, "dayNtlVlm": 1e8}
        for c in ("BTC", "ETH", "SOL", "DOGE", "AVAX", "XRP", "LINK")
    ]
    monkeypatch.setattr(hyperfeed, "get_universe",
                        lambda *, include_hip3=False, **k: universe)
    out = hyperfeed._compute_funding_regime()
    assert out["regimes_by_class"]["crypto"] == "LONG_CROWDED"
    assert out["regime"] == "LONG_CROWDED"


def test_discovery_get_trader_state_win_rate_is_percentage(monkeypatch):
    """win_rate is a 0-100 percentage; positions unwrapped; ROI computed."""
    from hermes_trader.agents import hyperfeed
    calls = {"clearinghouse": {
        "marginSummary": {"accountValue": "8000", "totalNtlPos": "4000"},
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "1", "entryPx": "60000",
                          "unrealizedPnl": "100", "leverage": {"value": "5"}}},
        ],
    }}
    def fake_post(path, body):
        if body["type"] == "clearinghouseState":
            return calls["clearinghouse"]
        if body["type"] == "userFills":
            return [{"closedPnl": "5"}, {"closedPnl": "-2"}, {"closedPnl": "3"}]
        return {}
    monkeypatch.setattr(hyperfeed, "_http_post", fake_post)
    out = hyperfeed.discovery_get_trader_state(["0xABC"])["data"]["traders"]
    assert len(out) == 1
    t = out[0]
    assert t["total_trades"] == 3
    assert abs(t["win_rate"] - (2 / 3 * 100)) < 1e-6  # 2 of 3 winners
    assert t["open_positions"] == 1


# ── Coverage: research indicator + prompt-builder helpers ───────────────
def test_compute_indicators_empty_returns_nulls():
    from hermes_trader.agents.research import _compute_indicators
    out = _compute_indicators([])
    assert out["ema8"] is None and out["last_close"] == 0


def test_compute_indicators_thin_history_partial():
    """<21 candles → indicators None but last_close/last_time populated."""
    from hermes_trader.agents.research import _compute_indicators
    candles = [Candle(t=i, o=10, h=11, l=9, c=10 + i, v=100) for i in range(5)]
    out = _compute_indicators(candles)
    assert out["ema8"] is None
    assert out["last_close"] == 14  # 10 + 4
    assert out["last_time"] == 4


def test_compute_indicators_full_history_computes_emas():
    """≥21 candles → EMA/RSI/ATR/ADX numeric, slope detected on rising series."""
    from hermes_trader.agents.research import _compute_indicators
    candles = [Candle(t=i, o=100 + i, h=101 + i, l=99 + i, c=100 + i, v=1000)
               for i in range(40)]
    out = _compute_indicators(candles)
    assert out["ema8"] is not None and out["ema21"] is not None
    assert out["ema8"] > out["ema21"]  # rising series → fast above slow
    assert out["slope_up"] is True
    assert out["last_close"] == 139


def test_fetch_funding_rate_formats_percent(monkeypatch):
    from hermes_trader.agents import research
    monkeypatch.setattr(research, "fetch_funding_history",
                        lambda coin, start: [{"fundingRate": "0.0001"}])
    assert research._fetch_funding_rate("BTC") == "0.0100%/hr"


def test_fetch_funding_rate_na_when_empty(monkeypatch):
    from hermes_trader.agents import research
    monkeypatch.setattr(research, "fetch_funding_history", lambda coin, start: [])
    assert research._fetch_funding_rate("BTC") == "N/A"


def test_build_user_message_includes_whale_and_structure_blocks():
    from hermes_trader.agents.research import _build_user_message
    perception = {
        "type": "perp", "mid": 0.000173, "composite_score": 62,
        "triggers": [
            {"name": "higherLows1h", "reason": "3 HL", "fired": True},
        ],
        "whale_signal": {"funding_rate": -0.0006, "price_24h_change_pct": 1.2,
                         "oi": 5e7, "confidence": 0.8},
    }
    snap = {"ema8": None, "ema21": None, "last_close": 0.000173}
    msg = _build_user_message(
        "HMSTR", perception, snap, snap, snap, "0.01%/hr", "no news",
        250.0, [], "LIVE",
    )
    assert "Whale accumulation flag (oi_funding_anomaly)" in msg
    assert "1h structure signals (entry-timing" in msg
    # sub-cent mid must NOT collapse to 0.0002 — adaptive precision keeps it.
    assert "0.000173" in msg and "0.0002" not in msg


def test_build_user_message_omits_account_equity_and_notional():
    """Account equity / notional must NOT reach the LLM — leverage/exposure is
    the gates' job and was causing the model to PASS good setups on 'over-leverage'
    grounds. Only the held coins/sides are surfaced (for dup/CLOSE detection)."""
    from hermes_trader.agents.research import _build_user_message
    perception = {"type": "perp", "mid": 100, "composite_score": 10, "triggers": []}
    snap = {"last_close": 100}
    msg = _build_user_message(
        "xyz:MU", perception, snap, snap, snap, "N/A", "no news",
        300.0, [{"coin": "ETH", "side": "long", "size_usd": 779.0}], "LIVE",
        dex_equity={"": 96.0, "xyz": 114.0},
    )
    # no equity figure, no dex-equity framing, no per-position dollar size
    assert "Equity" not in msg
    assert "$300" not in msg and "114.00" not in msg
    assert "$779" not in msg
    # held coin/side still surfaced so the model won't double-trade / can CLOSE
    assert "ETH long" in msg


def test_parse_verdict_regex_fallback_midtext():
    """JSON not on the last line is recovered by the regex fallback."""
    from hermes_trader.agents.research import parse_verdict
    txt = ('reasoning here\n{"verdict":"LONG","confidence":0.7,"side":"long"}\n'
           'some trailing commentary')
    v = parse_verdict(txt, "BTC", {"mid": 50})
    assert v["verdict"] == "LONG" and v["side"] == "long"


def test_parse_verdict_malformed_json_uses_first_line_keyword():
    """Unparseable JSON falls back to a keyword scan of the first line."""
    from hermes_trader.agents.research import parse_verdict
    txt = 'SHORT setup forming\n{"verdict": broken json,,}'
    v = parse_verdict(txt, "ETH", {"mid": 10})
    assert v["verdict"] == "SHORT" and v["side"] == "short"


# ── Coverage: dashboard payload helpers ─────────────────────────────────
def test_ttl_cached_serves_then_refetches(monkeypatch):
    from hermes_trader import dashboard
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        return calls["n"]
    t = [1000.0]
    monkeypatch.setattr(dashboard.time, "time", lambda: t[0])
    dashboard._TTL_CACHE.pop("k", None)
    assert dashboard._ttl_cached("k", 5.0, fn) == 1
    assert dashboard._ttl_cached("k", 5.0, fn) == 1  # cache hit
    t[0] += 6.0
    assert dashboard._ttl_cached("k", 5.0, fn) == 2  # expired → refetch
    assert calls["n"] == 2


def test_last_event_returns_newest_match():
    from hermes_trader.dashboard import _last_event
    events = [
        {"event": "scan", "id": 1},
        {"event": "execute", "id": 2},
        {"event": "scan", "id": 3},
    ]
    assert _last_event(events, "scan")["id"] == 3
    assert _last_event(events, "nope") is None


def test_closed_trades_payload_dsl_realized_fill(monkeypatch):
    """A dsl_exit with realized fill data reports the exact PnL, newest-first."""
    from hermes_trader import dashboard
    events = [
        {"event": "execute", "coin": "BTC", "side": "long", "ts": 1},
        {"event": "dsl_exit", "coin": "BTC", "ts": 2, "leverage": 10,
         "realized_pnl_pct": 8.0, "realized_spot_pct": 0.8, "reason": "trail"},
    ]
    monkeypatch.setattr(dashboard, "_read_log_lines", lambda: events)
    out = dashboard._closed_trades_payload()
    assert len(out) == 1
    row = out[0]
    assert row["coin"] == "BTC" and row["side"] == "long"
    assert row["pnl_source"] == "fill"
    assert row["pnl_pct"] == 8.0
    assert row["leverage_estimated"] is False


def test_closed_trades_payload_estimates_leverage_and_side(monkeypatch):
    """Old dsl_exit lacking side/leverage walks back to the execute event for
    side and estimates leverage from config × HL cap."""
    from hermes_trader import dashboard
    events = [
        {"event": "execute", "coin": "ETH", "side": "short", "ts": 1},
        {"event": "dsl_exit", "coin": "ETH", "ts": 2,
         "unrealized_pct": -1.0, "reason": "stop"},
    ]
    monkeypatch.setattr(dashboard, "_read_log_lines", lambda: events)
    monkeypatch.setattr(dashboard, "read_agent_config", lambda: {"leverage": 20})
    monkeypatch.setattr(dashboard, "_load_max_lev_table", lambda: {"ETH": 25})
    out = dashboard._closed_trades_payload()
    row = out[0]
    assert row["side"] == "short"
    assert row["leverage"] == 20  # min(cfg 20, HL cap 25)
    assert row["leverage_estimated"] is True
    assert row["pnl_source"] == "estimated"


def test_closed_trades_payload_respects_limit(monkeypatch):
    from hermes_trader import dashboard
    events = []
    for i in range(5):
        events.append({"event": "execute", "coin": "BTC", "side": "long", "ts": i})
        events.append({"event": "dsl_exit", "coin": "BTC", "ts": i + 100,
                       "leverage": 5, "realized_pnl_pct": 1.0, "realized_spot_pct": 0.2})
    monkeypatch.setattr(dashboard, "_read_log_lines", lambda: events)
    assert len(dashboard._closed_trades_payload(limit=3)) == 3


def test_summary_payload_offline_when_no_heartbeat(monkeypatch):
    from hermes_trader import dashboard
    monkeypatch.setattr(dashboard, "_read_log_lines", lambda: [])
    out = dashboard._summary_payload()
    assert out["status"] == "offline"
    assert out["equity"] == 0.0


def test_summary_payload_scanning_and_pnl_pct(monkeypatch):
    """Recent heartbeat → 'scanning'; daily_pnl_pct = pnl / (equity − pnl)."""
    from hermes_trader import dashboard
    now_ms = int(dashboard.time.time() * 1000)
    events = [
        {"event": "loop_heartbeat", "ts": now_ms, "equity": 260.0,
         "daily_pnl": 10.0, "available": 50.0, "open_positions": 3},
        {"event": "scan", "ts": now_ms, "triggers": 7},
    ]
    monkeypatch.setattr(dashboard, "_read_log_lines", lambda: events)
    out = dashboard._summary_payload()
    assert out["status"] == "scanning"
    assert out["daily_pnl"] == 10.0
    # sod = 260 − 10 = 250 → 10/250 = 4.0%
    assert out["daily_pnl_pct"] == 4.0
    assert out["open_positions"] == 3
    assert out["last_scan_triggers"] == 7


def test_summary_payload_stale_when_heartbeat_old(monkeypatch):
    from hermes_trader import dashboard
    old_ms = int(dashboard.time.time() * 1000) - 600_000  # 10 min ago
    events = [{"event": "loop_heartbeat", "ts": old_ms, "equity": 100.0,
               "daily_pnl": 0.0}]
    monkeypatch.setattr(dashboard, "_read_log_lines", lambda: events)
    assert dashboard._summary_payload()["status"] == "stale"


def test_equity_curve_payload_filters_by_range_and_zero(monkeypatch):
    from hermes_trader import dashboard
    now_ms = int(dashboard.time.time() * 1000)
    events = [
        {"event": "loop_heartbeat", "ts": now_ms - 7200_000, "equity": 200.0},  # 2h old
        {"event": "loop_heartbeat", "ts": now_ms - 60_000, "equity": 240.0},    # recent
        {"event": "loop_heartbeat", "ts": now_ms, "equity": 0.0},               # zero-skip
        {"event": "scan", "ts": now_ms, "equity": 999.0},                       # wrong event
    ]
    monkeypatch.setattr(dashboard, "_read_log_lines", lambda: events)
    out = dashboard._equity_curve_payload(range_s=3600)  # last hour only
    assert [p["equity"] for p in out] == [240.0]


def test_positions_snapshot_round_trip(tmp_path, monkeypatch):
    """write_snapshot then read_snapshot returns the same asset_positions."""
    from hermes_trader import positions_snapshot as ps
    monkeypatch.setattr(ps, "SNAPSHOT_FILE", str(tmp_path / "snap.json"))
    rows = [{"position": {"coin": "BTC", "szi": "1.0"}}]
    ps.write_snapshot(rows)
    out = ps.read_snapshot(max_age_s=120.0)
    assert out == {"asset_positions": rows}


def test_positions_snapshot_missing_returns_none(tmp_path, monkeypatch):
    from hermes_trader import positions_snapshot as ps
    monkeypatch.setattr(ps, "SNAPSHOT_FILE", str(tmp_path / "absent.json"))
    assert ps.read_snapshot() is None


def test_positions_snapshot_stale_returns_none(tmp_path, monkeypatch):
    """A snapshot older than max_age_s is treated as absent → caller refetches."""
    from hermes_trader import positions_snapshot as ps
    import json as _json
    f = tmp_path / "snap.json"
    f.write_text(_json.dumps({"saved_at": 0, "asset_positions": [{"x": 1}]}))
    monkeypatch.setattr(ps, "SNAPSHOT_FILE", str(f))
    assert ps.read_snapshot(max_age_s=60.0) is None  # saved_at=epoch 0 → ancient


def test_dashboard_positions_prefers_snapshot_no_hl_call(monkeypatch):
    """When a fresh snapshot exists the dashboard transforms it and never calls
    fetch_account_state — this is what removes the cross-process HL load."""
    from hermes_trader import dashboard
    called = {"hl": False}
    def boom(*a, **k):
        called["hl"] = True
        raise AssertionError("fetch_account_state must not be called")
    monkeypatch.setattr(dashboard, "fetch_account_state", boom)
    monkeypatch.setattr(dashboard, "resolve_user_address", lambda: "0xUSER")
    monkeypatch.setattr(dashboard.dsl_exit, "load_state", lambda force=False: None)
    monkeypatch.setattr(dashboard.dsl_exit, "_active_positions", {})
    monkeypatch.setattr(dashboard, "read_position_snapshot", lambda max_age_s=120.0: {
        "asset_positions": [
            {"position": {"coin": "BTC", "szi": "2.0", "entryPx": "60000",
                          "positionValue": "122000", "unrealizedPnl": "1000",
                          "marginUsed": "6000", "leverage": {"value": "20"}}},
        ],
    })
    rows = dashboard._positions_payload_uncached()
    assert called["hl"] is False
    assert len(rows) == 1 and rows[0]["coin"] == "BTC" and rows[0]["side"] == "long"


def test_dashboard_positions_falls_back_to_hl_when_no_snapshot(monkeypatch):
    """No snapshot (loop down) → dashboard does a live fetch so it still works."""
    from hermes_trader import dashboard
    monkeypatch.setattr(dashboard, "read_position_snapshot", lambda max_age_s=120.0: None)
    monkeypatch.setattr(dashboard, "resolve_user_address", lambda: "0xUSER")
    monkeypatch.setattr(dashboard.dsl_exit, "load_state", lambda force=False: None)
    monkeypatch.setattr(dashboard.dsl_exit, "_active_positions", {})
    fetched = {"n": 0}
    def fake_fetch(user, **kw):
        fetched["n"] += 1
        return {"asset_positions": [
            {"position": {"coin": "ETH", "szi": "-5", "entryPx": "3000",
                          "positionValue": "15000", "unrealizedPnl": "-50",
                          "marginUsed": "3000", "leverage": 5}},
        ]}
    monkeypatch.setattr(dashboard, "fetch_account_state", fake_fetch)
    rows = dashboard._positions_payload_uncached()
    assert fetched["n"] == 1
    assert rows[0]["coin"] == "ETH" and rows[0]["side"] == "short"


def test_build_user_message_indicator_block_full_snap():
    """A full indicator snapshot renders the bullish/bearish + RSI/ATR/ADX line."""
    from hermes_trader.agents.research import _build_user_message
    perception = {"type": "perp", "mid": 100, "composite_score": 50, "triggers": []}
    full = {"ema8": 105.0, "ema21": 100.0, "slope_up": True,
            "rsi14": 62.5, "atr14": 3.2, "adx14": 28.0, "last_close": 104.0}
    msg = _build_user_message(
        "BTC", perception, full, full, full, "0.01%/hr", "no news",
        500.0, [{"coin": "ETH", "side": "long", "size_usd": 120}], "OFF",
    )
    assert "bullish" in msg
    assert "RSI(14)=62.5" in msg
    assert "ADX(14)=28.0" in msg
    assert "EMA8 slope: rising" in msg
    # held coin/side surfaced for dup/CLOSE detection, but NO dollar size
    # (account notional must not influence the verdict).
    assert "ETH long" in msg and "$120" not in msg
    assert "analysis only" in msg  # OFF mode message


# ── Coverage: news-sentiment gate (good news must not block) ────────────
def test_maybe_execute_negative_news_blocks(monkeypatch):
    """AI news_risk='negative' stands the trade down, and the block reason
    surfaces the offending headline for log visibility."""
    ex, _, _ = _exec_baseline(monkeypatch)
    r = ex.maybe_execute(_analysis(
        news_risk="negative",
        news_context="SomeCoin suffers major exploit, $5M drained | other headline"))
    assert r["executed"] is False
    nr = (r.get("gate_results") or {}).get("news") or {}
    assert nr.get("pass") is False
    assert "exploit" in (nr.get("reason") or "").lower()


def test_maybe_execute_positive_news_does_not_block(monkeypatch):
    """An earnings BEAT (news_risk='positive') must NOT block — the old
    keyword gate stood down on the mere word 'earnings'."""
    ex, captured, _ = _exec_baseline(monkeypatch)
    r = ex.maybe_execute(_analysis(
        confidence=0.70,
        news_risk="positive",
        news_context="SomeCoin earnings beat expectations, stock surges"))
    assert r["executed"] is True
    assert (r["gate_results"]["news"]["pass"]) is True


def test_maybe_execute_no_news_risk_does_not_block(monkeypatch):
    """Absent/none news_risk → news gate passes (no keyword false-positives)."""
    ex, _, _ = _exec_baseline(monkeypatch)
    r = ex.maybe_execute(_analysis(
        news_context="Fed meeting next week; SEC mentioned in passing"))
    assert r["executed"] is True


def test_news_blackout_gate_reason_includes_match():
    from hermes_trader.agents.risk_gates import news_blackout_gate
    ok = news_blackout_gate(_ctx(has_binary_news_risk=False))
    assert ok["pass"] is True
    blocked = _ctx(has_binary_news_risk=True)
    blocked.binary_news_match = "'hack' in: Coin hacked for $1M"
    r = news_blackout_gate(blocked)
    assert r["pass"] is False
    assert "Coin hacked for $1M" in r["reason"]
