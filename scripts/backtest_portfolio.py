#!/usr/bin/env python3
"""PORTFOLIO-level backtest — the one the per-trade replays can't do.

Unlike backtest_logged/backtest (which sim each trade in isolation), this walks a
GLOBAL 5-min clock with a SHARED equity pool and enforces the real portfolio gates:
  - max_concurrent      (only N positions open at once)
  - max_total_notional  (gross exposure cap vs equity)
  - min_available_margin (margin floor)
So capital contention AND correlated drawdowns (many positions stopping in the
same dip → equity drops → margin tightens) actually show up. Drives off the REAL
logged AI verdicts; exits use the live DSL (scalp) config.

Lets you answer "what happens at max_concurrent=N / gross cap=X%" for real.

Usage: python3 scripts/backtest_portfolio.py --max-concurrent 8
       python3 scripts/backtest_portfolio.py --sweep-concurrent 4,6,8,10,15
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from backtest_logged import (  # reuse validated primitives
    fetch_candles_at, detect_regime_at, passes_counter_regime,
)
from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.models.types import Candle

STEP_MS = 300_000  # 5-min clock


class Position:
    """One open trade; step(bar) returns (exit_px, reason) or None."""
    def __init__(self, coin, side, entry_px, lev, notional, margin, dsl):
        self.coin, self.side, self.entry_px = coin, side, entry_px
        self.lev, self.notional, self.margin = lev, notional, margin
        self.peak = entry_px
        self.max_loss = min(float(dsl.get("max_loss_pct", 3.5)),
                            float(dsl.get("max_loss_roe_pct", 18.0)) / max(1, lev))
        self.protect = float(dsl.get("protect_pct", 1.5))
        self.retrace = float(dsl.get("retrace_threshold", 0.30))

    def step(self, bar: Candle):
        is_long = self.side == "long"
        loss = (self.entry_px - bar.l) / self.entry_px * 100 if is_long \
            else (bar.h - self.entry_px) / self.entry_px * 100
        if loss >= self.max_loss:
            px = self.entry_px * (1 - self.max_loss / 100) if is_long \
                else self.entry_px * (1 + self.max_loss / 100)
            return px, "max_loss"
        if is_long and bar.h > self.peak: self.peak = bar.h
        elif not is_long and bar.l < self.peak: self.peak = bar.l
        if is_long:
            if (self.peak - self.entry_px) / self.entry_px * 100 >= self.protect:
                floor = self.peak - (self.peak - self.entry_px) * self.retrace
                if bar.l <= floor:
                    return floor, "floor_breach"
        else:
            if (self.entry_px - self.peak) / self.entry_px * 100 >= self.protect:
                ceil = self.peak + (self.entry_px - self.peak) * self.retrace
                if bar.h >= ceil:
                    return ceil, "floor_breach"
        return None

    def pnl_usd(self, exit_px):
        gross = (exit_px - self.entry_px) / self.entry_px if self.side == "long" \
            else (self.entry_px - exit_px) / self.entry_px
        return self.notional * (gross - 0.0009)  # ~9bps round-trip fee


def build_candidates(args, cfg) -> List[Dict[str, Any]]:
    dsl_cfg = cfg.get("dsl_exit", {})
    min_conf = args.min_conf or float(cfg.get("min_ai_confidence", 0.65))
    counter_min = float(cfg.get("counter_regime_min_conf", 0.8))
    timeout_min = float(dsl_cfg.get("hard_timeout_minutes", 1800.0))
    mem = json.load(open(_REPO / ".agent-memory.json"))
    analyses = sorted(mem.get("analyses", []), key=lambda a: a.get("created_at", 0))
    percs = {p["id"]: p for p in mem.get("perceptions", []) if "id" in p}
    import time as _t
    cutoff = int(_t.time() * 1000) - args.hours * 3600_000
    out = []
    regime_cache: Dict[int, str] = {}
    for a in analyses:
        if a.get("created_at", 0) < cutoff:
            continue
        v = a.get("verdict")
        if v not in ("LONG", "SHORT"):
            continue
        conf = float(a.get("confidence", 0))
        if conf < min_conf:
            continue
        ts = int(a.get("created_at", 0))
        coin = a.get("coin")
        side = "long" if v == "LONG" else "short"
        perc = percs.get(a.get("perception_id"))
        comp = float(perc.get("composite_score", 0)) if perc else 0.0
        trg = (perc or {}).get("triggers", []) or []
        burst = any(t.get("name") == "momentumBurst" and t.get("fired") for t in trg)
        slow = any(t.get("name") in ("volumeBuildup1h", "trendFlip1h", "higherLows1h")
                   and t.get("fired") for t in trg)
        bk = ts // (30 * 60_000)
        if bk not in regime_cache:
            regime_cache[bk] = detect_regime_at(ts)
        if not passes_counter_regime(side, regime_cache[bk], conf, comp, burst, slow, counter_min):
            continue
        fwd = fetch_candles_at(coin, "5m", int(timeout_min // 5) + 10,
                               ts + int(timeout_min * 60_000) + 600_000)
        fwd = [b for b in fwd if b.t >= ts]
        if not fwd:
            continue
        out.append({"ts": ts, "coin": coin, "side": side, "entry_px": fwd[0].o,
                    "candles": fwd[1:], "conf": conf})
    return out


def run(args, cfg, max_concurrent, max_notional_pct) -> Dict[str, Any]:
    dsl_cfg = cfg.get("dsl_exit", {})
    frac = float(cfg.get("equity_fraction_per_trade", 0.28))
    lev = args.leverage or int(cfg.get("leverage", 10))
    min_margin = float(cfg.get("min_available_margin_pct", 0.10))
    equity = args.equity
    cands = sorted(_CANDS, key=lambda c: c["ts"])
    if not cands:
        return {"trades": 0}
    t0, t1 = cands[0]["ts"], max(c["ts"] for c in cands) + int(
        float(dsl_cfg.get("hard_timeout_minutes", 1800)) * 60_000)
    by_entry = {}
    for c in cands:
        by_entry.setdefault(c["ts"] // STEP_MS, []).append(c)

    open_pos: Dict[str, Dict[str, Any]] = {}
    closes, peak_eq, max_dd = [], equity, 0.0
    blk_conc = blk_notional = blk_margin = blk_dup = 0
    clock = t0
    while clock <= t1 or open_pos:
        # exits
        for coin, st in list(open_pos.items()):
            pos, cs = st["pos"], st["candles"]
            while st["i"] < len(cs) and cs[st["i"]].t <= clock:
                ex = pos.step(cs[st["i"]])
                st["i"] += 1
                if ex:
                    px, reason = ex
                    pnl = pos.pnl_usd(px)
                    equity += pnl
                    closes.append({"coin": coin, "pnl": pnl, "reason": reason})
                    del open_pos[coin]
                    break
            else:
                if st["i"] >= len(cs) and coin in open_pos:  # ran out of candles
                    pos = st["pos"]; last = cs[-1] if cs else None
                    if last:
                        gross = (last.c - pos.entry_px) / pos.entry_px if pos.side == "long" \
                            else (pos.entry_px - last.c) / pos.entry_px
                        pnl = pos.notional * (gross - 0.0009)
                        equity += pnl
                        closes.append({"coin": coin, "pnl": pnl, "reason": "end"})
                    del open_pos[coin]
        # entries this step
        for c in by_entry.get(clock // STEP_MS, []):
            coin = c["coin"]
            if coin in open_pos:
                blk_dup += 1; continue
            if len(open_pos) >= max_concurrent:
                blk_conc += 1; continue
            open_notional = sum(s["pos"].notional for s in open_pos.values())
            new_notional = equity * frac * lev
            if open_notional + new_notional > equity * max_notional_pct:
                blk_notional += 1; continue
            used_margin = sum(s["pos"].margin for s in open_pos.values())
            if (equity - used_margin - equity * frac) / equity < min_margin:
                blk_margin += 1; continue
            pos = Position(coin, c["side"], c["entry_px"], lev, new_notional,
                           equity * frac, dsl_cfg)
            open_pos[coin] = {"pos": pos, "candles": c["candles"], "i": 0}
        peak_eq = max(peak_eq, equity)
        max_dd = max(max_dd, peak_eq - equity)
        clock += STEP_MS

    n = len(closes)
    wins = [c for c in closes if c["pnl"] > 0]
    net = sum(c["pnl"] for c in closes)
    return {"trades": n, "win": (len(wins) / n * 100 if n else 0),
            "net": net, "exp": (net / n if n else 0), "end_eq": equity,
            "max_dd": max_dd, "blk_conc": blk_conc, "blk_notional": blk_notional,
            "blk_margin": blk_margin}


_CANDS: List[Dict[str, Any]] = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--equity", type=float, default=200.0)
    ap.add_argument("--leverage", type=int, default=0)
    ap.add_argument("--min-conf", type=float, default=0.0)
    ap.add_argument("--max-concurrent", type=int, default=0)
    ap.add_argument("--max-notional-pct", type=float, default=0.0)
    ap.add_argument("--sweep-concurrent", default="", help="e.g. 4,6,8,10,15")
    args = ap.parse_args()
    cfg = read_agent_config()
    global _CANDS
    print("# building candidates from real AI verdicts (fetching forward candles)...")
    _CANDS = build_candidates(args, cfg)
    mnp = args.max_notional_pct or float(cfg.get("max_total_notional_pct", 8.0))
    lev = args.leverage or int(cfg.get("leverage", 10))
    print(f"# {len(_CANDS)} admitted candidates | equity ${args.equity:.0f} | lev {lev}x | "
          f"gross cap {mnp:.0f}x | last {args.hours}h\n")
    concs = [int(x) for x in args.sweep_concurrent.split(",")] if args.sweep_concurrent \
        else [args.max_concurrent or int(cfg.get("max_concurrent", 15))]
    print(f"{'max_conc':>9} {'trades':>7} {'win%':>6} {'exp/trade':>10} {'net':>9} "
          f"{'endEq':>8} {'maxDD':>7}  blocks(conc/notnl/margin)")
    for mc in concs:
        r = run(args, cfg, mc, mnp)
        if not r.get("trades"):
            print(f"{mc:>9}  no trades"); continue
        print(f"{mc:>9} {r['trades']:>7} {r['win']:>5.0f}% {r['exp']:>+9.2f} "
              f"{r['net']:>+8.2f} {r['end_eq']:>7.0f} {r['max_dd']:>6.1f}  "
              f"{r['blk_conc']}/{r['blk_notional']}/{r['blk_margin']}")


if __name__ == "__main__":
    main()
