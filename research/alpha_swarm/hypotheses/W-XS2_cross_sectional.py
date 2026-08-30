#!/usr/bin/env python3
"""W-XS2 — cross-sectional momentum/reversal on majors, 833 days.

Everything refuted in this repo so far is a TIME-SERIES rule: this coin's own
chart says buy. Cross-sectional is structurally different — it ranks coins
against each other and is market-neutral by construction, so it does not need
the market to go up to make money. That is why it deserves its own test rather
than being lumped with the saturated candle space.

Prior: memory records xs_momentum at roughly +1.4%/rebalance before it was
ripped out, and separately an extreme-fade-LONG cell at +4.71%. Both were
measured on a wider altcoin universe. This is the out-of-sample re-test on the
majors universe we actually trade now.

PRE-REGISTERED BEFORE RUNNING. Every cell reported:

  universe : BTC ETH SOL BNB XRP + the HIP-3 majors we hold
  data     : 4h bars, ~833 days
  ranking  : trailing return over LOOKBACK bars
  entries  :
    H1 momentum : LONG the top-ranked, SHORT the bottom-ranked
    H2 reversal : LONG the bottom-ranked, SHORT the top-ranked
  lookback : 30, 60, 120 bars  (5d, 10d, 20d)
  hold     : 30 bars (5 days)
  => 2 x 3 = 6 CELLS. Bonferroni 0.05/6 = 0.00833.

Market-neutral by construction (one long, one short), so the null is a random
PAIR at a random time — the correct comparison for a spread, not a directional
null.

Bar: mean > 0 net of 25bps per leg, BOTH OOS halves > 0, p < 0.00833.
"""
from __future__ import annotations

import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from hermes_trader.client.hl_client import fetch_hl_candles  # noqa: E402

COINS = ("BTC", "ETH", "SOL", "BNB", "XRP")
HOLD = 30
FEE_PCT = 0.25 * 2          # two legs
LOOKBACKS = (30, 60, 120)


def spread_return(data, keys, i, lb, mode):
    """One rebalance: rank by trailing return, take the extremes, hold HOLD bars."""
    ranked = []
    for c in keys:
        b = data[c]
        if i < lb or i + HOLD >= len(b):
            return None
        past, now = b[i - lb].c, b[i].c
        if past <= 0 or now <= 0:
            return None
        ranked.append((c, (now - past) / past))
    ranked.sort(key=lambda x: x[1])
    lo, hi = ranked[0][0], ranked[-1][0]
    long_c, short_c = (hi, lo) if mode == "momentum" else (lo, hi)

    def leg(c, side):
        b = data[c]
        e, x = b[i].c, b[i + HOLD].c
        return ((x - e) / e * 100) if side == "long" else ((e - x) / e * 100)

    return leg(long_c, "long") + leg(short_c, "short") - FEE_PCT


def main() -> int:
    data = {}
    for c in COINS:
        b = fetch_hl_candles(c, "4h", 5000)
        if len(b) > max(LOOKBACKS) + HOLD + 100:
            data[c] = b
    keys = sorted(data)
    n_bars = min(len(data[c]) for c in keys)
    for c in keys:
        data[c] = data[c][-n_bars:]
    span = (data[keys[0]][-1].t - data[keys[0]][0].t) / 86_400_000
    print(f"W-XS2 — cross-sectional. {len(keys)} coins, 4h, {span:.0f} days")
    print(f"6 pre-registered cells. Bonferroni {0.05/6:.5f}\n")
    print(f"{'mode':<10}{'lb':>5}{'n':>6}{'win':>6}{'mean%':>8}{'null%':>8}"
          f"{'excess':>8}{'h1':>7}{'h2':>7}{'p':>8}")
    print("-" * 66)

    rnd = random.Random(31)
    survivors = []
    for mode in ("momentum", "reversal"):
        for lb in LOOKBACKS:
            rets = []
            for i in range(lb, n_bars - HOLD, HOLD):
                r = spread_return(data, keys, i, lb, mode)
                if r is not None:
                    rets.append(r)
            if len(rets) < 20:
                print(f"{mode:<10}{lb:>5}{len(rets):>6}   too few")
                continue
            means = []
            for _ in range(2000):
                s = []
                for _ in range(len(rets)):
                    j = rnd.randrange(max(LOOKBACKS), n_bars - HOLD)
                    a, b_ = rnd.sample(keys, 2)

                    def leg(c, side, idx=j):
                        bb = data[c]
                        e, x = bb[idx].c, bb[idx + HOLD].c
                        return ((x - e) / e * 100) if side == "long" else ((e - x) / e * 100)

                    s.append(leg(a, "long") + leg(b_, "short") - FEE_PCT)
                means.append(st.mean(s))
            mean, null = st.mean(rets), st.mean(means)
            p = (sum(1 for m in means if m >= mean) + 1) / (len(means) + 1)
            mid = len(rets) // 2
            h1, h2 = st.mean(rets[:mid]), st.mean(rets[mid:])
            win = sum(1 for r in rets if r > 0) / len(rets)
            ok = mean > 0 and h1 > 0 and h2 > 0 and p < 0.05 / 6
            if ok:
                survivors.append((mode, lb, len(rets), round(mean, 3), round(p, 5)))
            print(f"{mode:<10}{lb:>5}{len(rets):>6}{win:>6.2f}{mean:>8.3f}{null:>8.3f}"
                  f"{mean-null:>8.3f}{h1:>7.2f}{h2:>7.2f}{p:>8.4f}"
                  f"{'  PASS' if ok else ''}")
    print()
    print("SURVIVORS:", survivors if survivors else "NONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
