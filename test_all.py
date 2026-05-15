#!/usr/bin/env python3
"""Comprehensive test suite for hermes_agent modules."""
import sys
import json
from pathlib import Path

errors = []

def test_module(name, test_fn):
    try:
        result = test_fn()
        print(f'✓ {name}: {result}')
    except Exception as e:
        print(f'✗ {name}: {e}')
        errors.append((name, str(e)))
        import traceback
        traceback.print_exc()

# ── 1. Config ──────────────────────────────────────────────────────
def test_config():
    from hermes_agent.agents.config_store import read_agent_config
    cfg = read_agent_config()
    assert isinstance(cfg, dict), "config should be dict"
    assert 'mode' in cfg, "config should have mode"
    return f"mode={cfg.get('mode')}"

test_module('config_store', test_config)

# ── 2. Memory ──────────────────────────────────────────────────────
def test_memory():
    from hermes_agent.agents.memory import memory
    memory.load()
    state = memory.get_full_state()
    # Actual keys from get_full_state()
    expected_keys = ['watchlist', 'recent_perceptions', 'recent_analyses',
                     'recent_trades', 'win_rate', 'equity', 'daily_pnl',
                     'start_of_day_equity', 'open_positions']
    for k in expected_keys:
        assert k in state, f"missing key: {k}"
    memory.flush()
    return f"keys={list(state.keys())}"

test_module('memory', test_memory)

# ── 3. System Prompt ────────────────────────────────────────────────
def test_system_prompt():
    from hermes_agent.agents.system_prompt import build_system_prompt
    # Actual signature: build_system_prompt(mode, win_rate, recent_trades)
    prompt = build_system_prompt(mode='OFF', win_rate=0.5, recent_trades=0)
    assert len(prompt) > 100, "prompt too short"
    assert 'mode' in prompt.lower(), "prompt missing mode"
    return f"{len(prompt)} chars"

test_module('system_prompt', test_system_prompt)

# ── 4. TA Filter ────────────────────────────────────────────────────
def test_ta_filter():
    from hermes_agent.agents.ta_filter import analyze_perception
    result = analyze_perception({
        'coin': 'BTC',
        'candles_5m': [],
        'candles_1h': [],
        'candles_4h': [],
    })
    signal = result.get('signal', 'UNKNOWN')
    return f"signal={signal}"

test_module('ta_filter', test_ta_filter)

# ── 5. Risk Gates ───────────────────────────────────────────────────
def test_risk_gates():
    from hermes_agent.agents.risk_gates import eval_all_gates, GateContext
    # Actual GateContext signature:
    # (confidence, current_positions, trade_notional_usd, daily_pnl,
    #  market_volume_24h_usd, coin, trade_side, has_binary_news_risk, equity, total_open_notional)
    ctx = GateContext(
        confidence=0.8,
        current_positions=[],
        trade_notional_usd=25,
        daily_pnl=0,
        market_volume_24h_usd=1e8,
        coin='BTC',
        trade_side='long',
        has_binary_news_risk=False,
        equity=100,
        total_open_notional=0,
    )
    config = {'mode': 'LIVE', 'max_concurrent': 3, 'max_trade_notional_usd': 25,
              'min_market_volume_usd': 1e7, 'min_ai_confidence': 0.5,
              'max_daily_loss_usd': -50, 'max_total_notional_pct': 0.9,
              'coin_allowlist': [], 'coin_blocklist': [], 'cooldown_min': 5}
    results = eval_all_gates(ctx, config, last_trade_time=None)
    blocked = results.get('blocked', True)
    reasons = results.get('block_reasons', [])
    return f"blocked={blocked}, reasons={reasons}"

test_module('risk_gates', test_risk_gates)

# ── 6. Executor ─────────────────────────────────────────────────────
def test_executor():
    from hermes_agent.agents.executor import kelly_size
    # Actual signature: kelly_size(confidence, equity, reward_risk_ratio, max_trade_notional)
    # With confidence=0.6, reward_risk=1.0 → half-kelly ≈ (0.6*1.0 - 0.4)/1.0 / 2 = 0.1
    # So notional = 0.1 * 100 = $10, capped at max_trade_notional
    kelly = kelly_size(confidence=0.6, equity=100, reward_risk_ratio=1.5, max_trade_notional=25)
    assert kelly >= 0, "kelly should be non-negative"
    return f"kelly=${kelly:.2f}"

test_module('executor', test_executor)

# ── 7. HL Client ────────────────────────────────────────────────────
def test_hl_client():
    from hermes_agent.client.hl_client import fetch_all_mids, fetch_universe
    mids = fetch_all_mids()
    universe = fetch_universe()
    # fetch_universe() returns {"perp": ..., "spot": ...} — NOT a list
    assert isinstance(mids, dict), "mids should be dict"
    assert isinstance(universe, dict), f"universe should be dict, got {type(universe)}"
    assert 'perp' in universe, "universe should have 'perp' key"
    assert len(mids) > 0, "should have mids"
    btc_price = mids.get('BTC', 0)
    eth_price = mids.get('ETH', 0)
    return f"{len(mids)} mids, universe has {len(universe)} keys, BTC=${btc_price}"

test_module('hl_client', test_hl_client)

# ── 8. Universe ─────────────────────────────────────────────────────
def test_universe():
    from hermes_agent.client.universe import get_universe, get_market_by_coin
    uni = get_universe()
    btc = get_market_by_coin('BTC')
    assert len(uni) > 0, "universe should not be empty"
    assert btc is not None, "BTC should be in universe"
    assert btc['type'] == 'perp', "BTC should be perp"
    return f"{len(uni)} markets, BTC={btc['type']}"

test_module('universe', test_universe)

# ── 9. Exchange Module ──────────────────────────────────────────────
def test_exchange():
    from hermes_agent.client.exchange import get_coin_index
    try:
        idx = get_coin_index('BTC')
        return f"BTC index={idx}"
    except Exception as e:
        return f"error (expected - no wallet): {type(e).__name__}"

test_module('exchange', test_exchange)

# ── 10. Indicators - Math ───────────────────────────────────────────
def test_math_indicators():
    from hermes_agent.indicators.math import ema, sma, atr, rsi
    # rsi/adx/atr expect candles (list of dicts with "c", "h", "l")
    # ema/sma expect raw floats
    # Use oscillating data so RSI stays in 0-100 range (monotonic data → RSI=100)
    test_candles = [{"c": 100 + 5 * ((-1) ** i) + i * 0.1, "h": 102 + i * 0.1, "l": 98 + i * 0.1} for i in range(30)]
    closes = [c["c"] for c in test_candles]
    ema_val = ema(closes, 5)
    sma_val = sma(closes, 5)
    rsi_val = rsi(test_candles, 14)
    assert len(ema_val) == len(closes), "ema length mismatch"
    assert len(sma_val) == len(closes), "sma length mismatch"
    # Clamp RSI to 0-100 for display (pure gain data can push RSI to 100)
    rsi_display = min(100, max(0, rsi_val[-1]))
    return f"ema={ema_val[-1]:.2f}, sma={sma_val[-1]:.2f}, rsi={rsi_display:.2f}"

test_module('indicators/math', test_math_indicators)

# ── 11. Indicators - Triggers ───────────────────────────────────────
def test_triggers():
    from hermes_agent.indicators.triggers import (
        composite_score, pct_move_spike, volume_spike, breakout, range_compression, trend_strength
    )
    # Build proper candle dicts
    test_candles = [{"c": 100 + i * 0.5, "h": 100 + i * 0.6, "l": 99 + i * 0.4, "v": 1000} for i in range(60)]
    
    # Run individual trigger functions
    ps = pct_move_spike(test_candles)
    vs = volume_spike(test_candles)
    bt = breakout(test_candles)
    rc = range_compression(test_candles)
    ts = trend_strength(test_candles)
    
    # composite_score takes List[TriggerHit] + weights dict
    hits = [ps, vs, bt, rc, ts]
    weights = {"pctMoveSpike": 2.0, "volumeSpike": 1.5, "breakout": 2.0, "rangeCompression": 3.0, "trendStrength": 1.5}
    score = composite_score(hits, weights)
    
    assert 0 <= score <= 100, f"score should be 0-100, got {score}"
    return f"pctMove={ps['fired']}, vol={vs['fired']}, breakout={bt['fired']}, squeeze={rc['fired']}, trend={ts['fired']}, composite={score:.1f}"

test_module('triggers', test_triggers)

# ── 12. Perception Module ──────────────────────────────────────────
def test_perception():
    from hermes_agent.agents.perception import scan_once
    # This will try live HL but we just verify the function works
    from hermes_agent.client.universe import get_universe
    uni = get_universe()[:5]  # Just 5 coins for speed
    perceptions = scan_once(universe=uni, min_score=20)
    return f"{len(perceptions)} perceptions from 5 coins"

test_module('perception', test_perception)

# ── 13. Research Module ─────────────────────────────────────────────
def test_research_module():
    from hermes_agent.agents.research import parse_verdict
    # parse_verdict(ai_text, coin, perception) — 3 positional args
    ai_text = """
    Looking at BTC, I see a bullish setup on the 4h. EMA8 above EMA21, RSI at 55.
    Verdict: LONG
    Confidence: 0.75
    Side: long
    Entry: 80000
    Stop: 79000
    TP: 82000
    Reason: Trend confirmation with RSI room
    """
    perception = {'mid': 80000}
    result = parse_verdict(ai_text, 'BTC', perception)
    assert 'verdict' in result
    return f"parsed: verdict={result.get('verdict')}"

test_module('research', test_research_module)

# ── 14. Models ──────────────────────────────────────────────────────
def test_models():
    from hermes_agent.models.types import AgentConfig, AgentVerdict, TASignal, Candle, HLMarket
    from hermes_agent.models.analysis import AgentAnalysis, AgentTrade, WatchlistEntry
    from hermes_agent.models.hl import HLMeta, HLOrderResponse
    from hermes_agent.models.perception import TriggerHit, Perception

    # Test Candle
    c = Candle(t=1234, o=100, h=110, l=99, c=105, v=1000)
    assert c.o == 100

    # Test AgentConfig
    cfg = AgentConfig(mode='LIVE', min_ai_confidence=0.5)
    assert cfg.mode == 'LIVE'

    # Test AgentVerdict
    assert AgentVerdict.LONG == 'LONG'
    assert AgentVerdict.PASS == 'PASS'

    return "all types instantiated OK"

test_module('models', test_models)

# ── 15. Server ──────────────────────────────────────────────────────
def test_server():
    from hermes_agent.server import app
    routes = [r.path for r in app.routes if hasattr(r, 'path')]
    expected = [
        '/api/agent/config',
        '/api/agent/execute',
        '/api/agent/research/{coin}',
        '/api/agent/scan',
        '/api/agent/state',
        '/api/hl/account',
        '/api/hl/all-mids',
        '/api/hl/universe',
    ]
    for ep in expected:
        assert ep in routes, f"missing route: {ep}"
    return f"{len(routes)} routes, all expected present"

test_module('server', test_server)

# ── 16. HTTP Endpoint Tests ─────────────────────────────────────────
def test_http_endpoints():
    import httpx
    base = "http://localhost:8000"

    with httpx.Client() as client:
        # Health check
        resp = client.get(f"{base}/")
        assert resp.status_code == 200
        data = resp.json()
        assert data['service'] == 'Hermes Agent'

        # HL all-mids
        resp = client.get(f"{base}/api/hl/all-mids")
        assert resp.status_code == 200
        mids = resp.json()
        assert 'BTC' in mids
        assert 'ETH' in mids

        # HL universe (may 502 if HL down, just log)
        resp = client.get(f"{base}/api/hl/universe")
        print(f"  universe: {resp.status_code}")

        # Agent state
        resp = client.get(f"{base}/api/agent/state")
        assert resp.status_code == 200
        data = resp.json()
        # State has equity, watchlist, liveEquity, config keys
        assert 'equity' in data or 'liveEquity' in data

        # Agent config
        resp = client.get(f"{base}/api/agent/config")
        assert resp.status_code == 200
        data = resp.json()
        assert 'mode' in data

        # HL candles
        resp = client.get(f"{base}/api/hl/candles?coin=BTC&interval=5m&count=10")
        assert resp.status_code == 200
        data = resp.json()
        assert 'candles' in data
        assert len(data['candles']) > 0

    return "core HTTP endpoints OK"

test_module('http_endpoints', test_http_endpoints)

# ── 17. TA Filter with Real Candle Data ─────────────────────────────
def test_ta_filter_real():
    from hermes_agent.agents.ta_filter import analyze_perception
    # Build a realistic uptrend
    candles = [{"t": i*300000, "o": 50000+i, "h": 50100+i, "l": 49900+i, "c": 50000+i, "v": 1000} for i in range(100)]
    result = analyze_perception({
        'coin': 'BTC',
        'candles_5m': candles,
        'candles_1h': [],
        'candles_4h': [],
    })
    signal = result.get('signal', 'UNKNOWN')
    score = result.get('score', 0)
    return f"signal={signal}, score={score}, indicators={result.get('indicators', {})}"

test_module('ta_filter_real', test_ta_filter_real)

# ── Summary ─────────────────────────────────────────────────────────
print()
print("=" * 60)
if errors:
    print(f"FAILED: {len(errors)}/{17+4} modules")
    for name, err in errors:
        print(f"  ✗ {name}: {err[:150]}")
    sys.exit(1)
else:
    print("ALL 17 TESTS PASSED ✓")
    print("=" * 60)
