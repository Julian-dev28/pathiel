#!/usr/bin/env python3
"""W-FUN2 — the golden ratio (phi): Fibonacci retracement + phi-day timing on crypto.

Golden ratio is at least a REAL TA tool (unlike angel numbers), so it earns a real test.
Two cells, honest matched null, fees, on ETH/BTC/SOL daily bars (dataset.json 1d, ~300d).

CELL A — Fibonacci retracement bounce (the classic "buy the 0.618 pullback").
  Trailing L-day range: pos = (close - lo) / (hi - lo). A 61.8% retracement from the swing
  high sits at pos ~ 0.382. Go LONG when pos is in each fib zone; measure forward h-day
  return. If 0.382 / 0.618 are special, their buckets beat the same-coin random-time null
  AND stand out from the non-fib buckets. Pre-registered zones + a full bucket sweep.

CELL B — phi-day timing (the numerology of phi): LONG on Fibonacci days of the month
  {1,2,3,5,8,13,21} vs the rest. The day_root_odd parallel, for phi.

Costs 10bps/day round trip (1x directional — this is an edge test, not the 25x casino).
Null: 2000x same-coin random-time entries, one-sided p. Seed 20260723. OOS = time halves.
Verdict per cell: ROBUST = EV10>0, p<0.05, both halves>0; else MARGINAL(<0.10)/REFUTED.
"""
from __future__ import annotations

import random
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import alpha_lib as A  # noqa: E402

SEED = 20260723
FEE = 0.0010
COINS = ["ETH", "BTC", "SOL"]
L = 20          # trailing range window (days)
HOLD = 3        # forward hold (days)
O, H_, L_, C = 1, 2, 3, 4
FIB_DAYS = {1, 2, 3, 5, 8, 13, 21}
# fib retracement zones as position-in-range (pos = fraction up from the low)
ZONES = {"0.236": (0.19, 0.28), "0.382": (0.33, 0.43), "0.500": (0.45, 0.55),
         "0.618": (0.57, 0.67), "0.786": (0.74, 0.83)}
FIB_ZONES = {"0.382", "0.618"}


def daily(coin, d):
    return A.candles(d, coin, "1d")


def series(bars):
    """Return per-day dicts: pos in trailing range, forward hold return, date."""
    out = []
    for i in range(L, len(bars) - HOLD):
        win = bars[i - L + 1:i + 1]
        hi = max(b[H_] for b in win)
        lo = min(b[L_] for b in win)
        if hi <= lo or bars[i][C] <= 0:
            continue
        pos = (bars[i][C] - lo) / (hi - lo)
        fwd = bars[i + HOLD][C] / bars[i][C] - 1.0
        dt = datetime.fromtimestamp(bars[i][0] / 1000, timezone.utc)
        out.append({"pos": pos, "fwd": fwd, "day": dt.day, "t": bars[i][0]})
    return out


def null_p(rows_fwd_all, obs, k):
    """obs = observed mean long EV10 of the selected rows; null draws k random rows."""
    rnd = random.Random(SEED)
    allf = [r["fwd"] for r in rows_fwd_all]
    if k < 1 or k > len(allf):
        return 1.0
    ge = 0
    for _ in range(2000):
        samp = rnd.sample(allf, k)
        m = st.fmean(x - FEE for x in samp)
        if m >= obs:
            ge += 1
    return (1 + ge) / 2001


def ev(rows):
    xs = [r["fwd"] - FEE for r in rows]
    return st.fmean(xs) if xs else None


def verdict(sel, allrows):
    if len(sel) < 12:
        return None
    e = ev(sel)
    sel_sorted = sorted(sel, key=lambda r: r["t"])
    h = len(sel_sorted) // 2
    h1, h2 = ev(sel_sorted[:h]), ev(sel_sorted[h:])
    p = null_p(allrows, e, len(sel))
    if e > 0 and p < 0.05 and h1 > 0 and h2 > 0:
        v = "ROBUST"
    elif e > 0 and p < 0.10:
        v = "MARGINAL"
    else:
        v = "REFUTED"
    return {"n": len(sel), "ev10": round(100 * e, 3),
            "h1": round(100 * h1, 3), "h2": round(100 * h2, 3),
            "p": round(p, 4), "verdict": v}


def main():
    d = A.load_dataset()
    print(f"phi = 1.618... | trailing range L={L}d | hold H={HOLD}d | fee {FEE*1e4:.0f}bps\n")
    for coin in COINS:
        rows = series(daily(coin, d))
        print(f"=== {coin} (n={len(rows)} days) ===")
        print("  CELL A — Fibonacci retracement buckets (LONG when pos in zone):")
        for name, (lo, hi) in ZONES.items():
            sel = [r for r in rows if lo <= r["pos"] < hi]
            res = verdict(sel, rows)
            star = " *FIB*" if name in FIB_ZONES else ""
            if res:
                print(f"    pos {name:<5}{star:<6} n={res['n']:<3} EV10 {res['ev10']:+.2f}%  "
                      f"halves {res['h1']:+.2f}/{res['h2']:+.2f}  p={res['p']}  {res['verdict']}")
            else:
                print(f"    pos {name:<5}{star:<6} too few")
        # CELL B — phi-day timing
        fib = [r for r in rows if r["day"] in FIB_DAYS]
        nonfib = [r for r in rows if r["day"] not in FIB_DAYS]
        rb = verdict(fib, rows)
        print("  CELL B — phi-day timing (LONG on Fibonacci days 1,2,3,5,8,13,21):")
        if rb:
            print(f"    fib-day    n={rb['n']:<3} EV10 {rb['ev10']:+.2f}%  "
                  f"halves {rb['h1']:+.2f}/{rb['h2']:+.2f}  p={rb['p']}  {rb['verdict']}")
            print(f"    non-fib-day EV10 {100*ev(nonfib):+.2f}%  (built-in control)")
        print()

    print("=== VERDICT ===")
    print("  If the golden ratio were real, the 0.382/0.618 buckets would beat the null AND")
    print("  their non-fib neighbours, and fib-days would beat non-fib days, on ALL 3 coins")
    print("  with stable OOS halves. Watch whether they do — and whether any 'win' is one")
    print("  coin / one half (multiple buckets x 3 coins = a comparison machine, like W-FUN1).")


if __name__ == "__main__":
    main()
