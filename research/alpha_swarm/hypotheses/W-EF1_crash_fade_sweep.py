#!/usr/bin/env python3
"""W-EF1 — extreme_fade entry sweep: which crash threshold / hold actually pays?

The live book buys a -12% crash and holds 3d (LONG the bounce). Forward grade is PENDING and
recent realized is 0/5 (-$9), so re-test the ENTRY space cleanly before any live flip.
Sweep crash threshold x lookback x hold on the cached universe, matched same-coin random-time
null, OOS halves, 25bps. A crash-fade only earns its live slot if a cell is EV+ both halves
AND beats the null — same bar as every other book.
"""
from __future__ import annotations

import random
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import alpha_lib as A  # noqa: E402

SEED = 20260723
FEE = 0.0025
O, H_, L_, C = 1, 2, 3, 4
THRESHOLDS = [-0.08, -0.10, -0.12, -0.15, -0.20]
LOOKBACKS = [1, 2]
HOLDS = [1, 2, 3, 5]


def rets(bars):
    return bars


def find_entries(bars, thresh, lb, hold):
    """Days where trailing-lb return <= thresh -> LONG next open, exit +hold open."""
    out = []
    for i in range(lb, len(bars) - hold - 1):
        c0 = bars[i - lb][C]
        c1 = bars[i][C]
        if c0 <= 0:
            continue
        move = c1 / c0 - 1.0
        if move > thresh:
            continue
        entry = bars[i + 1][O]
        exitp = bars[i + 1 + hold][O]
        if entry <= 0:
            continue
        out.append((i, exitp / entry - 1.0))
    return out


def null_ev(bars, n, hold, obs):
    """Matched random-time: n random long entries, same hold. p that random >= obs."""
    valid = [i for i in range(1, len(bars) - hold - 1) if bars[i + 1][O] > 0]
    if len(valid) < n or n == 0:
        return 1.0
    rnd = random.Random(SEED)
    ge = 0
    for _ in range(2000):
        picks = rnd.sample(valid, n)
        ev = st.fmean(bars[i + 1 + hold][O] / bars[i + 1][O] - 1.0 - FEE for i in picks)
        if ev >= obs:
            ge += 1
    return (1 + ge) / 2001


def main():
    d = A.load_dataset()
    coins = d["meta"]["coins"]
    print("crash-fade entry sweep (LONG the crash bounce), 25bps, matched null\n")
    print(f"{'thresh':<8}{'lb':<4}{'hold':<6}{'n':<6}{'EV25/trade':<13}{'halves':<16}{'p':<8}verdict")
    print("-" * 74)
    best = []
    for th in THRESHOLDS:
        for lb in LOOKBACKS:
            for hold in HOLDS:
                allret, per_coin_n = [], []
                for c in coins:
                    bars = A.candles(d, c, "1d")
                    es = find_entries(bars, th, lb, hold)
                    for _, r in es:
                        allret.append((r, c, bars))
                if len(allret) < 15:
                    continue
                evs = [r - FEE for r, _, _ in allret]
                ev = st.fmean(evs)
                srt = sorted(range(len(allret)), key=lambda k: k)  # chronological-ish by coin order
                h = len(evs) // 2
                h1, h2 = st.fmean(evs[:h]), st.fmean(evs[h:])
                # pooled null: aggregate random-time EV across the same coins/counts
                from collections import Counter
                cnt = Counter(c for _, c, _ in allret)
                rnd = random.Random(SEED)
                ge = 0
                bycoin = {c: A.candles(d, c, "1d") for c in cnt}
                for _ in range(2000):
                    tot = []
                    for c, k in cnt.items():
                        b = bycoin[c]
                        valid = [i for i in range(1, len(b) - hold - 1) if b[i + 1][O] > 0]
                        if len(valid) < k:
                            continue
                        for i in rnd.sample(valid, k):
                            tot.append(b[i + 1 + hold][O] / b[i + 1][O] - 1.0 - FEE)
                    if tot and st.fmean(tot) >= ev:
                        ge += 1
                p = (1 + ge) / 2001
                v = ("ROBUST" if ev > 0 and p < 0.05 and h1 > 0 and h2 > 0
                     else "MARGINAL" if ev > 0 and p < 0.10 else "REFUTED")
                mark = " <-LIVE" if (th == -0.12 and hold == 3) else ""
                print(f"{th:<8}{lb:<4}{hold:<6}{len(evs):<6}{ev*100:+.2f}%{'':<7}"
                      f"{h1*100:+.1f}/{h2*100:<9.1f}{p:<8.4f}{v}{mark}")
                if v == "ROBUST":
                    best.append((th, lb, hold, ev, p))
    print()
    if best:
        print("ROBUST cells (EV+ both halves + beat null):")
        for th, lb, hold, ev, p in sorted(best, key=lambda x: -x[3]):
            print(f"  crash{th} lb{lb} hold{hold}: +{ev*100:.2f}%/trade  p={p:.4f}")
    else:
        print("NO robust cell — crash-fade has no clean entry edge on this universe. Keep shadow.")


if __name__ == "__main__":
    main()
