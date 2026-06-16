#!/usr/bin/env python3
"""Re-filter logged AI verdicts through CURRENT gates + DSL config.

Reads ~200 cached analyses from .agent-memory.json, joins each to its
perception for composite/triggers, then simulates execution + DSL exit
on historical 5m bars using the live config. Tells you "what would
today's strategy have done on yesterday's actual AI verdicts."

Free (no LLM calls). Runs in ~30s. The complement to backtest_full.py:
that one re-asks the AI fresh; this one trusts yesterday's AI verdicts.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_env = _REPO / ".env.local"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.client.hl_client import _http_post
from hermes_trader.models.types import Candle

_INTERVAL_MS = {"5m": 300_000, "1h": 3_600_000}


def fetch_candles_at(coin: str, interval: str, count: int, end_ms: int) -> List[Candle]:
    step = _INTERVAL_MS[interval]
    payload = {"type": "candleSnapshot",
               "req": {"coin": coin, "interval": interval,
                       "startTime": end_ms - step * count, "endTime": end_ms}}
    try:
        raw = _http_post("/info", payload)
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [Candle(t=c["t"], o=float(c["o"]), h=float(c["h"]), l=float(c["l"]),
                   c=float(c["c"]), v=float(c.get("v", "0"))) for c in raw]


def detect_regime_at(end_ms: int, proxy: str = "BTC") -> str:
    from hermes_trader.indicators.math import ema
    candles = fetch_candles_at(proxy, "1h", 100, end_ms)
    if len(candles) < 50:
        return "neutral"
    closes = [c.c for c in candles]
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    if len(fast) < 9:
        return "neutral"
    f_prev = fast[-9]
    if f_prev == 0:
        return "neutral"
    slope = (fast[-1] - f_prev) / abs(f_prev)
    if fast[-1] > slow[-1] and slope > 0.002:
        return "up"
    if fast[-1] < slow[-1] and slope < -0.002:
        return "down"
    return "neutral"


def passes_counter_regime(side: str, regime: str, conf: float, composite: float,
                          burst_fired: bool, slow_fired: bool, min_conf: float) -> bool:
    if regime == "neutral":
        return True
    aligned = (regime == "up" and side == "long") or (regime == "down" and side == "short")
    if aligned:
        return True
    return conf >= min_conf or composite >= 50 or burst_fired or slow_fired


def simulate_dsl_exit(entry_px: float, side: str, leverage: int,
                      forward_5m: List[Candle], dsl_cfg: Dict[str, Any]) -> Tuple[float, str, int, float]:
    max_loss_pct = float(dsl_cfg.get("max_loss_pct", 2.0))
    max_loss_roe_pct = float(dsl_cfg.get("max_loss_roe_pct", 40.0))
    protect_pct = float(dsl_cfg.get("protect_pct", 0.5))
    retrace = float(dsl_cfg.get("retrace_threshold", 0.30))
    hard_timeout_min = float(dsl_cfg.get("hard_timeout_minutes", 180.0))
    timeout_bars = int(hard_timeout_min // 5)
    lev = max(1, leverage)
    effective_max = min(max_loss_pct, max_loss_roe_pct / lev)
    is_long = side == "long"
    peak = entry_px

    for i, bar in enumerate(forward_5m):
        if i >= timeout_bars:
            spot_pct = (bar.c - entry_px)/entry_px*100 if is_long else (entry_px - bar.c)/entry_px*100
            return (spot_pct * lev, "hard_timeout", i, bar.c)
        loss_pct = (entry_px - bar.l)/entry_px*100 if is_long else (bar.h - entry_px)/entry_px*100
        if loss_pct >= effective_max:
            stop_px = entry_px * (1 - effective_max/100) if is_long else entry_px * (1 + effective_max/100)
            return (-effective_max * lev, "max_loss", i, stop_px)
        if is_long and bar.h > peak: peak = bar.h
        elif not is_long and bar.l < peak: peak = bar.l
        if is_long:
            profit_pct = (peak - entry_px)/entry_px*100
            if profit_pct >= protect_pct:
                floor_px = peak - (peak - entry_px) * retrace
                if bar.l <= floor_px:
                    return (((floor_px - entry_px)/entry_px*100) * lev, "floor_breach", i, floor_px)
        else:
            profit_pct = (entry_px - peak)/entry_px*100
            if profit_pct >= protect_pct:
                floor_px = peak + (entry_px - peak) * retrace
                if bar.h >= floor_px:
                    return (((entry_px - floor_px)/entry_px*100) * lev, "floor_breach", i, floor_px)

    if not forward_5m:
        return (0.0, "no_data", 0, entry_px)
    last = forward_5m[-1]
    spot_pct = (last.c - entry_px)/entry_px*100 if is_long else (entry_px - last.c)/entry_px*100
    return (spot_pct * lev, "end_of_window", len(forward_5m), last.c)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--equity", type=float, default=250.0)
    ap.add_argument("--dedup-min", type=int, default=30,
                    help="Treat same-coin analyses within N minutes as one trade")
    ap.add_argument("--mode", default="ai", choices=["ai", "lowconf", "force", "sidestep"],
                    help="ai=as-is; lowconf=lower min-conf; force=+composite-force PASS->LONG; "
                         "sidestep=ignore AI, take all TA-confirmed LONGs")
    ap.add_argument("--min-conf", type=float, default=0.60, help="min conf for lowconf mode")
    ap.add_argument("--force-bar", type=float, default=30.0, help="composite bar for force/sidestep")
    ap.add_argument("--leverage", type=int, default=0, help="override leverage (0=use config)")
    ap.add_argument("--equity-fraction", type=float, default=0.0, help="override fraction (0=use config)")
    args = ap.parse_args()

    cfg = read_agent_config()
    dsl_cfg = cfg.get("dsl_exit", {})
    counter_regime_min_conf = float(cfg.get("counter_regime_min_conf", 0.65))
    equity_fraction = float(args.equity_fraction or cfg.get("equity_fraction_per_trade", 0.04))
    base_leverage = int(args.leverage or cfg.get("leverage", 10))
    min_ai_conf = float(cfg.get("min_ai_confidence", 0.35))

    mem = json.load(open(_REPO / ".agent-memory.json"))
    analyses = mem.get("analyses", [])
    perceptions_by_id = {p["id"]: p for p in mem.get("perceptions", []) if "id" in p}

    now_ms = int(time.time() * 1000)
    cutoff = now_ms - args.hours * 3600_000
    analyses = [a for a in analyses if a.get("created_at", 0) >= cutoff]
    analyses.sort(key=lambda a: a.get("created_at", 0))

    print(f"# Counterfactual replay of {len(analyses)} logged analyses (last {args.hours}h)")
    print(f"# Equity ${args.equity:.0f} | leverage {base_leverage}x | fraction {equity_fraction}")
    print(f"# DSL: max_loss={dsl_cfg.get('max_loss_pct')}% / {dsl_cfg.get('max_loss_roe_pct')}% ROE | "
          f"protect={dsl_cfg.get('protect_pct')}% | timeout={dsl_cfg.get('hard_timeout_minutes')}min")
    print()

    # Dedup window: skip same-coin within N minutes of a previous trade
    dedup_ms = args.dedup_min * 60_000
    last_trade_by_coin: Dict[str, int] = {}

    # Cache regime per coarse 30-min bucket to save HL calls
    regime_cache: Dict[int, str] = {}
    def _regime_at(t: int) -> str:
        bucket = t // (30 * 60_000)
        if bucket not in regime_cache:
            regime_cache[bucket] = detect_regime_at(t)
        return regime_cache[bucket]

    pnl_total = 0.0
    wins, losses = [], []
    skipped_pass, skipped_dup, skipped_conf, skipped_regime, skipped_nodata = 0, 0, 0, 0, 0
    by_reason: Dict[str, List[float]] = {}
    trades: List[Tuple[Any, ...]] = []

    n_forced = 0
    for a in analyses:
        verdict = a.get("verdict")
        coin = a.get("coin")
        ts = int(a.get("created_at", 0))
        if not coin or ts == 0:
            continue
        conf = float(a.get("confidence", 0))
        # perception (composite/triggers) — needed for force/sidestep admission
        perc = perceptions_by_id.get(a.get("perception_id"))
        composite = float(perc.get("composite_score", 0)) if perc else 0.0
        triggers = (perc or {}).get("triggers", []) or []
        burst_fired = any(t.get("name") == "momentumBurst" and t.get("fired") for t in triggers)
        slow_fired = any(t.get("name") in ("volumeBuildup1h", "trendFlip1h", "higherLows1h")
                         and t.get("fired") for t in triggers)
        ta_confirmed = composite >= args.force_bar or burst_fired or slow_fired

        # ── mode-aware admission ─────────────────────────────────────────────
        ai_ls = verdict in ("LONG", "SHORT")
        admit, side, forced = False, None, False
        if args.mode == "ai":
            admit = ai_ls and conf >= min_ai_conf
            side = ("long" if verdict == "LONG" else "short") if ai_ls else None
        elif args.mode == "lowconf":
            admit = ai_ls and conf >= args.min_conf
            side = ("long" if verdict == "LONG" else "short") if ai_ls else None
        elif args.mode == "force":
            if ai_ls and conf >= min_ai_conf:
                admit, side = True, ("long" if verdict == "LONG" else "short")
            elif composite >= args.force_bar:            # composite-force PASS -> LONG
                admit, side, forced = True, "long", True
        elif args.mode == "sidestep":                    # ignore AI; take all TA-confirmed LONGs
            if ta_confirmed:
                admit, side, forced = True, "long", (not ai_ls)
            elif ai_ls and conf >= min_ai_conf:
                admit, side = True, ("long" if verdict == "LONG" else "short")
        if not admit:
            skipped_pass += 1
            continue
        if forced:
            n_forced += 1
        if coin in last_trade_by_coin and (ts - last_trade_by_coin[coin]) < dedup_ms:
            skipped_dup += 1
            continue

        regime = _regime_at(ts)
        if not passes_counter_regime(side, regime, conf, composite, burst_fired, slow_fired,
                                     counter_regime_min_conf):
            skipped_regime += 1
            continue

        # Fetch the entry bar + forward 5m bars (DSL window)
        timeout_min = float(dsl_cfg.get("hard_timeout_minutes", 180.0))
        forward_end = ts + int(timeout_min * 60_000) + 600_000  # +10min padding
        forward = fetch_candles_at(coin, "5m", int(timeout_min // 5) + 10, forward_end)
        forward = [b for b in forward if b.t >= ts]
        if not forward:
            skipped_nodata += 1
            continue

        entry_px = forward[0].o  # open of the first bar after analysis
        if entry_px <= 0:
            skipped_nodata += 1
            continue
        forward = forward[1:]  # bars STRICTLY after entry bar's open

        roe, reason, bars, exit_px = simulate_dsl_exit(entry_px, side, base_leverage, forward, dsl_cfg)
        margin = (args.equity * equity_fraction)  # margin = fraction × equity
        pnl_usd = roe / 100 * margin
        pnl_total += pnl_usd
        last_trade_by_coin[coin] = ts
        (wins if pnl_usd > 0 else losses).append(pnl_usd)
        by_reason.setdefault(reason, []).append(roe)
        trades.append((ts, coin, side, conf, composite, roe, reason, pnl_usd))
        print(f"  {_iso(ts)}  {coin:<14} {side:<5} conf={conf:.2f} comp={composite:>4.0f}  "
              f"entry={entry_px:.6g} exit={exit_px:.6g}  {reason:<14} ROE={roe:+6.1f}%  ${pnl_usd:+6.2f}")

    n = len(trades)
    wr = len(wins) / n if n else 0
    print()
    print("=" * 80)
    print(f"Trades:       {n}  ({len(wins)}W / {len(losses)}L, win rate {wr*100:.0f}%)")
    print(f"Total PnL:    ${pnl_total:+.2f}  ({pnl_total/args.equity*100:+.1f}% on ${args.equity:.0f})")
    print(f"Skipped:      {skipped_pass} PASS, {skipped_dup} dedup, {skipped_conf} low-conf, "
          f"{skipped_regime} counter-regime, {skipped_nodata} no-data")
    print()
    print("Exits by reason:")
    for reason in sorted(by_reason.keys(), key=lambda r: -sum(by_reason[r])):
        roes = by_reason[reason]
        avg = sum(roes)/len(roes)
        tot_pnl = sum((r/100) * (args.equity * equity_fraction) for r in roes)
        print(f"  {reason:<14} n={len(roes):>3}  avg ROE {avg:+6.1f}%  total ${tot_pnl:+7.2f}")
    return 0


def _iso(ms: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ms/1000, tz=datetime.timezone.utc).strftime("%m-%d %H:%M")


if __name__ == "__main__":
    raise SystemExit(main())
