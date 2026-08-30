#!/usr/bin/env python3
"""W-ME2 — does ANY component of the main-engine trigger stack have edge?

W-ME1 refuted main_engine on ~17 days of 5m bars (n=90, one composite
threshold, long only). That sample was too thin to be the last word, and it
tested the composite as a lump rather than asking which component carries
signal. This is the proper test.

PRE-REGISTERED BEFORE RUNNING — the grid below is fixed and every cell is
reported, whatever it says:

  timeframe : 1h (208 days of history, vs 17 days in W-ME1)
  universe  : BTC ETH SOL BNB XRP (the majors we actually trade)
  entries   : each of the 6 NON-ZERO-WEIGHTED triggers, fired individually
              trendStrength .55 | pctMoveSpike .40 | breakout .30
              volumeSpike   .25 | momentumBurst .20 | volumeBuildup1h .15
  sides     : long, short
  holding   : 24 bars (1 day)
  stop      : 6%
  => 6 x 2 = 12 CELLS. Bonferroni threshold 0.05/12 = 0.00417.

Scored against a matched random-time null on the same coins with the same
holding rule, so the bar is "beats entering at random", never "beats zero".

A cell passes only with: mean > 0 net of fees, BOTH OOS halves > 0, and
p < 0.00417. Anything else is reported and discarded.

The config claims trendStrength was measured at +2.08% lift on n=497 trades.
This is the out-of-sample check on that claim.
"""
from __future__ import annotations

import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from hermes_trader.agents.config import get_config          # noqa: E402
from hermes_trader.client.hl_client import fetch_hl_candles  # noqa: E402
from hermes_trader.indicators import triggers as T           # noqa: E402

TH = get_config()["thresholds"]
COINS = ("BTC", "ETH", "SOL", "BNB", "XRP")
HOLD_BARS = 24
STOP_PCT = 6.0
FEE_PCT = 0.25
WARMUP = 120
COOLDOWN = 12          # bars; one signal per coin per 12h so entries are independent

TRIGGERS = {
    "trendStrength":   lambda w: T.trend_strength(w, TH["adxPeriod"]),
    "pctMoveSpike":    lambda w: T.pct_move_spike(w, TH["sigmaThreshold"]),
    "breakout":        lambda w: T.breakout(w, TH["breakoutLookback"]),
    "volumeSpike":     lambda w: T.volume_spike(w, TH["sigmaThreshold"]),
    "momentumBurst":   lambda w: T.momentum_burst(w, TH["momentumLookback"],
                                                  TH["momentumPct"]),
    "volumeBuildup1h": lambda w: T.volume_buildup_1h(w, TH["volBuildupRatio"]),
}


def outcome(bars, i, side):
    entry = bars[i].c
    if entry <= 0:
        return None
    stop = entry * (1 - STOP_PCT / 100) if side == "long" else entry * (1 + STOP_PCT / 100)
    for f in bars[i + 1:i + 1 + HOLD_BARS]:
        if (f.l <= stop) if side == "long" else (f.h >= stop):
            return -STOP_PCT - FEE_PCT
    last = bars[min(i + HOLD_BARS, len(bars) - 1)].c
    move = (last - entry) if side == "long" else (entry - last)
    return move / entry * 100 - FEE_PCT


def fired(hit) -> bool:
    return bool(getattr(hit, "fired", False) or (isinstance(hit, dict) and hit.get("fired")))


def main() -> int:
    data = {}
    for c in COINS:
        b = fetch_hl_candles(c, "1h", 5000)
        if len(b) > WARMUP + HOLD_BARS + 100:
            data[c] = b
    span = (list(data.values())[0][-1].t - list(data.values())[0][0].t) / 86_400_000
    print(f"W-ME2 — trigger battery. {len(data)} coins, 1h bars, {span:.0f} days")
    print(f"12 pre-registered cells. Bonferroni threshold {0.05/12:.5f}\n")
    print(f"{'trigger':<18}{'side':>6}{'n':>6}{'win':>6}{'mean%':>8}{'null%':>8}"
          f"{'excess':>8}{'h1':>8}{'h2':>8}{'p':>8}")
    print("-" * 84)

    rnd = random.Random(13)
    survivors = []
    for name, fn in TRIGGERS.items():
        for side in ("long", "short"):
            rets = []
            for coin, bars in data.items():
                last = -10**9
                for i in range(WARMUP, len(bars) - HOLD_BARS):
                    if i - last < COOLDOWN:
                        continue
                    if not fired(fn(bars[i - WARMUP:i + 1])):
                        continue
                    r = outcome(bars, i, side)
                    if r is not None:
                        rets.append(r)
                        last = i
            if len(rets) < 30:
                print(f"{name:<18}{side:>6}{len(rets):>6}   too few")
                continue
            means = []
            for _ in range(2000):
                s = []
                for _ in range(len(rets)):
                    coin = rnd.choice(list(data))
                    bars = data[coin]
                    j = rnd.randrange(WARMUP, len(bars) - HOLD_BARS)
                    r = outcome(bars, j, side)
                    if r is not None:
                        s.append(r)
                if s:
                    means.append(st.mean(s))
            mean, null = st.mean(rets), st.mean(means)
            p = (sum(1 for m in means if m >= mean) + 1) / (len(means) + 1)
            mid = len(rets) // 2
            h1, h2 = st.mean(rets[:mid]), st.mean(rets[mid:])
            win = sum(1 for r in rets if r > 0) / len(rets)
            ok = mean > 0 and h1 > 0 and h2 > 0 and p < 0.05 / 12
            if ok:
                survivors.append((name, side, len(rets), mean, p))
            print(f"{name:<18}{side:>6}{len(rets):>6}{win:>6.2f}{mean:>8.3f}"
                  f"{null:>8.3f}{mean-null:>8.3f}{h1:>8.2f}{h2:>8.2f}{p:>8.4f}"
                  f"{'  PASS' if ok else ''}")

    print()
    print("SURVIVORS (mean>0, both halves>0, p<0.00417):",
          survivors if survivors else "NONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
