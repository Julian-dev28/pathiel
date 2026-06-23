#!/usr/bin/env python3
"""Larry Williams volatility-breakout on HIP-3 equity/commodity perps + leverage sweep.

The HIP-3 perps trade like stocks (US-hours daily bars) — the current momentum-chase at
10x bled hard (SKHX -15.5%, SMSN -16.7% ROE in 30min, 2026-06-22). Williams' volatility
breakout is built exactly for this: define the day's range, trade the open-range breakout,
fixed 1:1 R/R, auto-close at session end.

Rules (per the brief):
  range R   = prev day's (high - low)
  offset    = 0.25 * R
  LONG  if price breaks ABOVE  open + offset  -> enter there
  SHORT if price breaks BELOW  open - offset  -> enter there
  target/stop = 2*offset (=0.5R) from entry, 1:1 R/R
  time exit  = auto-close at session end if neither hit

Implementation: 1h candles grouped by UTC day; prevH/prevL from the prior day; open = first
bar of the day; walk the day's bars for the FIRST breakout, then resolve TP/SL bar-by-bar
(if one bar straddles both, assume STOP first = conservative); else close at day's last bar.
Reports SPOT EV (leverage-independent sign) + ROE at 3x/5x/10x. cost = round-trip bps.
"""
import os, sys, time, statistics
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

K_OFF, RR_MULT = 0.25, 2.0          # offset = 0.25R; TP/SL = 2*offset = 0.5R
COST_BPS = 25.0                      # xyz round-trip (~5 fee + ~2x ~10 slippage); also sweep
TOPN, VOL_FLOOR = 14, 3e6
FETCH_SLEEP = 0.25


def load():
    uni = [m for m in get_universe(include_hip3=True)
           if ":" in (m.get("coin") or "") and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    out = {}
    for m in uni:
        c = m.get("coin")
        try:
            b = fetch_hl_candles(c, "1h", 500)
            if len(b) >= 72:
                out[c] = b
        except Exception:
            pass
        time.sleep(FETCH_SLEEP)
    return out


def by_day(bars):
    d = defaultdict(list)
    for b in bars:
        day = int(b["t"] // 86_400_000)            # UTC day index
        d[day].append(b)
    return dict(sorted(d.items()))


def run_coin(bars):
    """Yield per-day Williams trades as spot returns (incl. direction). Conservative
    intraday TP/SL ordering (stop-first if a bar straddles both)."""
    days = by_day(bars)
    keys = list(days)
    trades = []
    for i in range(1, len(keys)):
        prev = days[keys[i-1]]; cur = days[keys[i]]
        if len(cur) < 3 or len(prev) < 3:
            continue
        ph = max(candle_val(b, "h") for b in prev)
        pl = min(candle_val(b, "l") for b in prev)
        R = ph - pl
        if R <= 0:
            continue
        off = K_OFF * R
        op = candle_val(cur[0], "o")
        if op <= 0:
            continue
        up, dn = op + off, op - off
        # find first breakout in the day
        side = entry = None; jentry = None
        for j, b in enumerate(cur):
            hi, lo = candle_val(b, "h"), candle_val(b, "l")
            if hi >= up:
                side, entry, jentry = "long", up, j; break
            if lo <= dn:
                side, entry, jentry = "short", dn, j; break
        if side is None:
            continue
        tp = entry + RR_MULT*off if side == "long" else entry - RR_MULT*off
        sl = entry - RR_MULT*off if side == "long" else entry + RR_MULT*off
        ret = None
        for b in cur[jentry:]:
            hi, lo = candle_val(b, "h"), candle_val(b, "l")
            hit_tp = hi >= tp if side == "long" else lo <= tp
            hit_sl = lo <= sl if side == "long" else hi >= sl
            if hit_sl:                               # conservative: stop first if both
                ret = (sl-entry)/entry if side == "long" else (entry-sl)/entry; break
            if hit_tp:
                ret = (tp-entry)/entry if side == "long" else (entry-tp)/entry; break
        if ret is None:                              # session-end auto-close
            c = candle_val(cur[-1], "c")
            ret = (c-entry)/entry if side == "long" else (entry-c)/entry
        trades.append((side, ret, keys[i]))
    return trades


def main():
    print(f"# Williams volatility breakout on HIP-3 perps | offset {K_OFF}R, TP/SL {RR_MULT}*offset (0.5R, 1:1), session-close exit")
    S = load()
    print(f"# {len(S)} perps, 1h candles ~3wk | round-trip {COST_BPS:.0f}bps\n")
    allt = []
    for c, bars in S.items():
        t = run_coin(bars)
        allt += [(c, *x) for x in t]
    if not allt:
        print("no trades"); return
    spot = [x[2] for x in allt]
    cost = COST_BPS/1e4
    net = [r - cost for r in spot]
    w = sum(1 for r in net if r > 0)
    print(f"# {len(allt)} trades | win {w/len(allt)*100:.0f}% | avg SPOT/trade {statistics.mean(spot)*100:+.3f}% "
          f"| net of {COST_BPS:.0f}bps {statistics.mean(net)*100:+.3f}%")
    # OOS halves
    mid = len(allt)//2
    h1 = statistics.mean(net[:mid])*100; h2 = statistics.mean(net[mid:])*100
    print(f"# OOS: h1 {h1:+.3f}% / h2 {h2:+.3f}% {'ROBUST' if h1>0 and h2>0 else 'not robust'}")
    print(f"\n# LEVERAGE SWEEP (ROE = spot net x lev; per-trade stop ~0.5R spot):")
    print(f"  {'lev':>4} {'avg ROE/trade':>14} {'~stop ROE (worst)':>18}")
    worst_spot = min(net)
    for lev in (1,3,5,10):
        print(f"  {lev:>3}x {statistics.mean(net)*lev*100:>+12.2f}% {worst_spot*lev*100:>+16.1f}%")
    print(f"\n# cost sensitivity (avg net/trade SPOT):")
    for bps in (10,25,40,60):
        print(f"  {bps:>3}bps: {statistics.mean([r-bps/1e4 for r in spot])*100:+.3f}%")
    # direction split
    L=[x[2] for x in allt if x[1]=='long']; Sh=[x[2] for x in allt if x[1]=='short']
    print(f"\n# by side: LONG n={len(L)} avg {statistics.mean(L)*100:+.3f}% | SHORT n={len(Sh)} avg {statistics.mean(Sh)*100 if Sh else 0:+.3f}%")
    print("# NOTE: 1h-bar resolution + conservative (stop-first) intraday ordering UNDERSTATES TP hits; "
          "treat as a lower bound. vs current: momentum-chase @10x bled -15/-17% ROE stops.")


if __name__ == "__main__":
    main()
