#!/usr/bin/env python3
"""LEGACY full-pipeline experiment.

Walks back HOURS_BACK in TICK_MIN-minute intervals. At each tick:
  - fetches historical 5m + 1h candles ending at that tick
  - runs perception → triggers → composite scoring
  - applies counter-regime / momentum / slow_burn bypass logic
  - for surviving candidates, calls real OpenRouter research (caps total)
  - for LONG/SHORT verdicts, runs a simulated trade through a local DSL walk

This script is expensive, can call real OpenRouter unless --no-llm is passed,
and is not the current strategy truth. Prefer:
  - scripts/backtest_logged.py for logged AI verdict replay
  - scripts/backtest_portfolio.py for concurrency/gross/margin contention
  - scripts/strategy_grid_search.py for config-family sweeps

Usage:
    python3 scripts/backtest_full.py                        # defaults: 12h, 30min ticks
    python3 scripts/backtest_full.py --hours 6 --llm-cap 30
    python3 scripts/backtest_full.py --no-llm               # skip AI, use direction heuristic
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# .env.local + repo root
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_env = _REPO / ".env.local"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from hermes_trader.agents.config import get_config
from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.client.hl_client import _http_post
from hermes_trader.client.universe import get_universe
from hermes_trader.indicators import triggers as trigger_mod
from hermes_trader.indicators.math import ema
from hermes_trader.models.types import Candle

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("backtest")

_INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def fetch_candles_at(coin: str, interval: str, count: int, end_ms: int) -> List[Candle]:
    """HL candleSnapshot ending at `end_ms`. Returns [] on error."""
    step = _INTERVAL_MS[interval]
    start_ms = end_ms - step * count
    payload = {"type": "candleSnapshot",
               "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}}
    try:
        raw = _http_post("/info", payload)
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [Candle(t=c["t"], o=float(c["o"]), h=float(c["h"]), l=float(c["l"]),
                   c=float(c["c"]), v=float(c.get("v", "0"))) for c in raw]


@dataclass
class SimTrade:
    tick_ms: int
    coin: str
    side: str
    entry_px: float
    leverage: int
    notional_usd: float
    verdict_source: str  # "ai" | "heuristic"
    confidence: float
    composite: float
    bars_held: int = 0
    exit_px: float = 0.0
    exit_reason: str = ""
    roe_pct: float = 0.0
    pnl_usd: float = 0.0


def detect_regime_at(end_ms: int, proxy: str = "BTC") -> str:
    """Same logic as market_regime._trend_from_closes, ad-hoc on historical candles."""
    candles = fetch_candles_at(proxy, "1h", 100, end_ms)
    if len(candles) < 50:
        return "neutral"
    closes = [c.c for c in candles]
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    if len(fast) < 9 or len(slow) < 1:
        return "neutral"
    f_now, s_now = fast[-1], slow[-1]
    f_prev = fast[-9]
    if f_prev == 0:
        return "neutral"
    slope = (f_now - f_prev) / abs(f_prev)
    if f_now > s_now and slope > 0.002:
        return "up"
    if f_now < s_now and slope < -0.002:
        return "down"
    return "neutral"


def evaluate_triggers(c5m: List[Candle], c1h: List[Candle], cfg: Dict[str, Any]) -> Tuple[float, List[Dict[str, Any]]]:
    """Run the 9 triggers; return (composite_score, fired_hits)."""
    thr = cfg["thresholds"]
    hits = [
        trigger_mod.pct_move_spike(c5m, thr["sigmaThreshold"]),
        trigger_mod.volume_spike(c5m, thr["sigmaThreshold"]),
        trigger_mod.breakout(c5m, thr["breakoutLookback"]),
        trigger_mod.range_compression(c5m, thr["bbLength"], thr["bbStdDev"]),
        trigger_mod.trend_strength(c5m, thr["adxPeriod"]),
        trigger_mod.momentum_burst(c5m, thr["momentumLookback"], thr["momentumPct"]),
        trigger_mod.volume_buildup_1h(c1h, thr.get("volBuildupRatio", 2.5)),
        trigger_mod.trend_flip_1h(c1h, thr.get("trendFlipBars", 3)),
        trigger_mod.higher_lows_1h(c1h, thr.get("higherLowsRequired", 4)),
    ]
    score = trigger_mod.composite_score(hits, cfg["weights"])
    return score, hits


def passes_counter_regime(side: str, regime: str, confidence: float, composite: float,
                          momentum_burst_fired: bool, slow_burn_fired: bool,
                          min_conf: float) -> bool:
    if regime == "neutral":
        return True
    aligned = (regime == "up" and side == "long") or (regime == "down" and side == "short")
    if aligned:
        return True
    return confidence >= min_conf or composite >= 50 or momentum_burst_fired or slow_burn_fired


def simulate_dsl_exit(entry_px: float, side: str, leverage: int,
                      forward_5m: List[Candle], dsl_cfg: Dict[str, Any]) -> Tuple[float, str, int, float]:
    """Walk forward 5m bars; return (roe_pct, reason, bars_held, exit_px).

    Mirrors agents/dsl_exit logic: max_loss = min(max_loss_pct, max_loss_roe_pct/lev),
    phase-2 trailing once profit_pct >= protect_pct, retrace_threshold of peak gains
    locked, hard_timeout_minutes ceiling.
    """
    max_loss_pct = float(dsl_cfg.get("max_loss_pct", 2.0))
    max_loss_roe_pct = float(dsl_cfg.get("max_loss_roe_pct", 40.0))
    protect_pct = float(dsl_cfg.get("protect_pct", 0.5))
    retrace = float(dsl_cfg.get("retrace_threshold", 0.30))
    hard_timeout_min = float(dsl_cfg.get("hard_timeout_minutes", 180.0))
    timeout_bars = int(hard_timeout_min // 5)  # 5m bars

    lev = max(1, leverage)
    effective_max = min(max_loss_pct, max_loss_roe_pct / lev)

    is_long = side == "long"
    peak = entry_px
    for i, bar in enumerate(forward_5m):
        if i >= timeout_bars:
            spot_pct = (bar.c - entry_px) / entry_px * 100 if is_long else (entry_px - bar.c) / entry_px * 100
            return (spot_pct * lev, "hard_timeout", i, bar.c)

        if is_long:
            loss_pct = (entry_px - bar.l) / entry_px * 100
        else:
            loss_pct = (bar.h - entry_px) / entry_px * 100
        if loss_pct >= effective_max:
            # Stop at the effective_max — approximating intra-bar fill
            stop_px = entry_px * (1 - effective_max / 100) if is_long else entry_px * (1 + effective_max / 100)
            return (-effective_max * lev, "max_loss", i, stop_px)

        if is_long and bar.h > peak:
            peak = bar.h
        elif not is_long and bar.l < peak:
            peak = bar.l

        if is_long:
            profit_pct = (peak - entry_px) / entry_px * 100
            if profit_pct >= protect_pct:
                floor_px = peak - (peak - entry_px) * retrace
                if bar.l <= floor_px:
                    spot_pct = (floor_px - entry_px) / entry_px * 100
                    return (spot_pct * lev, "floor_breach", i, floor_px)
        else:
            profit_pct = (entry_px - peak) / entry_px * 100
            if profit_pct >= protect_pct:
                floor_px = peak + (entry_px - peak) * retrace
                if bar.h >= floor_px:
                    spot_pct = (entry_px - floor_px) / entry_px * 100
                    return (spot_pct * lev, "floor_breach", i, floor_px)

    # Ran out of data
    if not forward_5m:
        return (0.0, "no_data", 0, entry_px)
    last = forward_5m[-1]
    spot_pct = (last.c - entry_px) / entry_px * 100 if is_long else (entry_px - last.c) / entry_px * 100
    return (spot_pct * lev, "end_of_window", len(forward_5m), last.c)


def call_ai_research(coin: str, mid: float, composite: float, c1h: List[Candle], c4h: List[Candle], c1d: List[Candle],
                     slow_burn_hits: List[Dict[str, Any]], prompt_mode: str = "current") -> Tuple[str, float, str]:
    """Real OpenRouter call. Returns (verdict, confidence, reasoning)."""
    from hermes_trader.agents.research import _build_user_message, _call_ai, parse_verdict, _compute_indicators
    from hermes_trader.agents.system_prompt import build_system_prompt

    tf1h = _compute_indicators(c1h)
    tf4h = _compute_indicators(c4h)
    tf1d = _compute_indicators(c1d)

    perception = {
        "coin": coin, "type": "perp", "mid": mid, "composite_score": composite,
        "triggers": slow_burn_hits,
    }
    msg = _build_user_message(coin, perception, tf1h, tf4h, tf1d,
                              "n/a (backtest)", "no news (backtest)", 250.0, [], "LIVE")
    sys_prompt = build_system_prompt("LIVE", 0.0, 0)
    if prompt_mode == "soften":
        # A/B treatment: let a clean multi-TF trend stand on its own, even at low/zero
        # composite. Tests whether the AI's habit of PASSing clean-trend low-composite
        # movers (e.g. EIGEN) is leaving EV on the table. Muddled setups still PASS.
        sys_prompt += (
            "\n\nADDENDUM (mover A/B test): A CLEAN multi-timeframe EMA trend (4h AND 1d "
            "aligned in the same direction) ALONE justifies a directional call at confidence "
            "0.70 — even when the composite trigger score is low or zero and no catalyst is "
            "present. Do NOT PASS a cleanly trend-aligned mover solely because its composite "
            "is low. Muddled or conflicting multi-TF setups still PASS as before."
        )
    text = _call_ai(sys_prompt, msg)
    parsed = parse_verdict(text, coin, perception)
    return (parsed["verdict"], float(parsed["confidence"]), parsed.get("reasoning", "")[:80])


def heuristic_direction(composite: float, hits: List[Dict[str, Any]], c1h: List[Candle]) -> Tuple[str, float]:
    """Deterministic direction substitute when LLM cap is hit / --no-llm flag set.
    Bias long when 1h structure is positive; short on bearish breakout."""
    fired_names = {h["name"] for h in hits if h.get("fired")}
    slow_long = bool({"volumeBuildup1h", "trendFlip1h", "higherLows1h"} & fired_names)
    if len(c1h) >= 2 and c1h[-1].c > c1h[-2].c and slow_long:
        return ("LONG", 0.62)
    if "breakout" in fired_names and len(c1h) >= 2 and c1h[-1].c < c1h[-2].c:
        return ("SHORT", 0.6)
    return ("PASS", 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=12)
    ap.add_argument("--tick-min", type=int, default=30)
    ap.add_argument("--llm-cap", type=int, default=60)
    ap.add_argument("--top-markets", type=int, default=40)
    ap.add_argument("--no-llm", action="store_true",
                    help="skip OpenRouter; use deterministic direction heuristic")
    ap.add_argument("--equity", type=float, default=250.0)
    ap.add_argument("--taker-fee-bps", type=float, default=2.5,
                    help="Per-side taker fee in bps, converted to ROE by leverage")
    ap.add_argument("--slippage-bps", type=float, default=0.0,
                    help="Optional adverse slippage per side in bps for stress tests")
    ap.add_argument("--prompt-mode", choices=["current", "soften"], default="current",
                    help="A/B: 'soften' relaxes the prompt's composite-gating so a clean-trend "
                         "low-composite mover can LONG instead of PASS")
    ap.add_argument("--min-comp-gate", type=float, default=20.0,
                    help="Composite floor before the AI call (default 20; lower to admit "
                         "low-composite movers so the A/B can test them)")
    args = ap.parse_args()

    cfg = get_config()
    live_cfg = read_agent_config()
    dsl_cfg = live_cfg.get("dsl_exit", {})
    counter_regime_min_conf = float(live_cfg.get("counter_regime_min_conf", 0.65))
    enable_hip3 = bool(live_cfg.get("enable_hip3", False))
    enable_crypto = bool(live_cfg.get("enable_crypto", True))
    equity_fraction = float(live_cfg.get("equity_fraction_per_trade", 0.04))
    base_leverage = int(live_cfg.get("leverage", 10))
    min_ai_conf = float(live_cfg.get("min_ai_confidence", 0.35))
    round_trip_cost_roe = ((args.taker_fee_bps + args.slippage_bps) * 2 * base_leverage / 100.0)

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - args.hours * 3600_000
    tick_ms = args.tick_min * 60_000
    ticks = list(range(start_ms, now_ms, tick_ms))

    # Universe snapshot for the whole window (volumes don't shift dramatically
    # over 12h relative to overall rank).
    universe = get_universe(include_hip3=enable_hip3)
    eligible = [m for m in universe
                if m.get("type") != "spot" and not m["coin"].startswith("@")]
    if not enable_crypto:
        eligible = [m for m in eligible if m.get("dex")]
    if not enable_hip3:
        eligible = [m for m in eligible if not m.get("dex")]
    eligible.sort(key=lambda m: m.get("dayNtlVlm", 0), reverse=True)
    top_markets = eligible[:args.top_markets]

    print(f"# Backtest config")
    print(f"  window:       last {args.hours}h ({len(ticks)} ticks at {args.tick_min}min)")
    print(f"  markets:      top {len(top_markets)} by volume")
    print(f"  LLM:          {'OFF (heuristic)' if args.no_llm else f'ON (cap {args.llm_cap} calls)'}")
    print(f"  DSL config:   max_loss={dsl_cfg.get('max_loss_pct')}% spot / "
          f"{dsl_cfg.get('max_loss_roe_pct')}% ROE  protect={dsl_cfg.get('protect_pct')}%  "
          f"timeout={dsl_cfg.get('hard_timeout_minutes')}min")
    print(f"  costs:        taker {args.taker_fee_bps:g}bps/side"
          f"{' + slippage ' + str(args.slippage_bps) + 'bps/side' if args.slippage_bps else ''}"
          f" = {round_trip_cost_roe:.2f}% ROE/trade")
    print(f"  gates:        counter_regime_min_conf={counter_regime_min_conf}  min_ai_conf={min_ai_conf}")
    print()

    trades: List[SimTrade] = []
    llm_calls = 0
    candle_fetches = 0
    skipped_no_candles = 0
    skipped_gates = 0
    skipped_pass = 0

    for tick_i, tick_ms_t in enumerate(ticks):
        regime = detect_regime_at(tick_ms_t, "BTC")
        candle_fetches += 1
        print(f"[tick {tick_i+1:>2}/{len(ticks)}] {_iso(tick_ms_t)}  regime={regime}", flush=True)

        for m in top_markets:
            coin = m["coin"]
            c5m = fetch_candles_at(coin, "5m", 100, tick_ms_t)
            candle_fetches += 1
            if len(c5m) < 50:
                skipped_no_candles += 1
                continue
            c1h = fetch_candles_at(coin, "1h", 48, tick_ms_t)
            candle_fetches += 1

            composite, hits = evaluate_triggers(c5m, c1h, cfg)
            fired = [h for h in hits if h.get("fired")]
            if not fired:
                continue
            burst_fired = any(h["name"] == "momentumBurst" and h["fired"] for h in hits)
            if composite < args.min_comp_gate and not burst_fired:
                continue

            slow_burn_hits = [h for h in hits if h.get("name") in
                              ("volumeBuildup1h", "trendFlip1h", "higherLows1h") and h.get("fired")]
            slow_burn_fired = bool(slow_burn_hits)

            # AI verdict
            if args.no_llm or llm_calls >= args.llm_cap:
                verdict, conf = heuristic_direction(composite, hits, c1h)
                source = "heuristic"
                reasoning = "deterministic"
            else:
                c4h = fetch_candles_at(coin, "4h", 100, tick_ms_t)
                c1d = fetch_candles_at(coin, "1d", 60, tick_ms_t)
                candle_fetches += 2
                try:
                    verdict, conf, reasoning = call_ai_research(coin, c5m[-1].c, composite, c1h, c4h, c1d, hits, args.prompt_mode)
                    llm_calls += 1
                    source = "ai"
                except Exception as e:
                    print(f"    ! research failed for {coin}: {e}")
                    continue

            if verdict == "PASS" or verdict == "CLOSE":
                skipped_pass += 1
                continue
            side = "long" if verdict == "LONG" else "short"

            if conf < min_ai_conf:
                skipped_gates += 1
                continue
            if not passes_counter_regime(side, regime, conf, composite, burst_fired, slow_burn_fired,
                                         counter_regime_min_conf):
                skipped_gates += 1
                continue

            entry_px = c5m[-1].c
            notional = args.equity * equity_fraction * base_leverage
            forward_5m = fetch_candles_at(coin, "5m", int(dsl_cfg.get("hard_timeout_minutes", 180.0) // 5) + 5,
                                          tick_ms_t + int(dsl_cfg.get("hard_timeout_minutes", 180.0) * 60_000))
            candle_fetches += 1
            # We need bars STRICTLY AFTER entry. Filter.
            forward_5m = [b for b in forward_5m if b.t > tick_ms_t]
            gross_roe, exit_reason, bars, exit_px = simulate_dsl_exit(
                entry_px, side, base_leverage, forward_5m, dsl_cfg)
            roe = gross_roe - round_trip_cost_roe
            margin = notional / base_leverage
            pnl_usd = roe / 100 * margin

            trade = SimTrade(
                tick_ms=tick_ms_t, coin=coin, side=side, entry_px=entry_px, leverage=base_leverage,
                notional_usd=notional, verdict_source=source, confidence=conf, composite=composite,
                bars_held=bars, exit_px=exit_px, exit_reason=exit_reason, roe_pct=roe, pnl_usd=pnl_usd,
            )
            trades.append(trade)
            print(f"    + {coin:<14} {side:<5} entry={entry_px:.6g} exit={exit_px:.6g}  "
                  f"{exit_reason:<14} ROE={roe:+6.1f}%  ${pnl_usd:+.2f}  ({source} conf={conf:.2f} comp={composite:.0f})")

    print()
    print("=" * 80)
    print("Summary")
    print("=" * 80)
    n_trades = len(trades)
    total_pnl = sum(t.pnl_usd for t in trades)
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    win_rate = len(wins) / n_trades if n_trades else 0
    by_reason = {}
    for t in trades:
        by_reason.setdefault(t.exit_reason, []).append(t)

    print(f"Trades:         {n_trades}  ({len(wins)} wins, {len(losses)} losses, win rate {win_rate*100:.0f}%)")
    print(f"Total PnL:      ${total_pnl:+.2f}  ({total_pnl/args.equity*100:+.1f}% on ${args.equity:.0f} equity)")
    print(f"LLM calls:      {llm_calls}/{args.llm_cap}")
    print(f"Candle fetches: {candle_fetches}")
    print(f"Skipped:        {skipped_no_candles} no-candles, {skipped_gates} gate-blocked, {skipped_pass} verdict-PASS")
    print()
    print("Exits by reason:")
    for reason, ts in sorted(by_reason.items(), key=lambda x: -sum(t.pnl_usd for t in x[1])):
        p = sum(t.pnl_usd for t in ts)
        avg = sum(t.roe_pct for t in ts) / len(ts)
        print(f"  {reason:<18} n={len(ts):>3}  total=${p:+7.2f}  avg ROE {avg:+6.1f}%")

    print()
    print("Caveats:")
    print("  * Bars are post-close; intra-bar wicks aren't simulated (stops fire on close-or-touch, not tick).")
    print("  * Cross-position gates (correlation, max_concurrent) NOT modeled — each trade simulated in isolation.")
    print("  * Universe snapshot taken at run time; coins listed/delisted within the window aren't reflected.")
    print("  * LLM verdicts are non-deterministic; running twice gives different results on borderline trades.")
    print("  * News context substituted with 'no news' — the binary-news gate is effectively inert.")
    print("  * 'Now-cast' bias: applies current weights/config to historical bars; flatters the new logic.")
    return 0


def _iso(ms: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).strftime("%m-%d %H:%M UTC")


if __name__ == "__main__":
    raise SystemExit(main())
