#!/usr/bin/env python3
"""W-FND1 — is there edge in FUNDING, where the candle space is saturated?

Every chart-pattern hypothesis this repo has tested has failed: KillaXBT range
structure, Williams patterns, session sweeps (W-SESS1), the whole main-engine
trigger stack (W-ME2). Memory's summary is that candle space is saturated. So
this tests the data that is NOT candles and IS available historically: the
perpetual funding rate.

Funding is a real, mechanical cashflow — longs pay shorts when positive. It is
positioning, not price. That makes it a different coordinate from anything
already refuted here.

PRE-REGISTERED BEFORE RUNNING. Grid fixed, every cell reported:

  universe : BTC ETH SOL BNB XRP (majors — what we trade)
  data     : 251 days of hourly funding, paginated; 1h candles aligned to it
  entries  :
    H1 crowded-long fade : funding z >= +Z  -> SHORT
    H2 crowded-short fade: funding z <= -Z  -> LONG
    H3 carry-with-trend  : funding z >= +Z  -> LONG  (does crowding persist?)
    H4 sustained-positive: funding > 0 for N consecutive hours -> SHORT
  Z        : 2.0, 3.0
  holding  : 24h
  stop     : 6%
  => 4 hypotheses x 2 thresholds = 8 CELLS. Bonferroni 0.05/8 = 0.00625.

Scored against a matched random-time null on the same coins with the same
holding rule. Bar: mean > 0 net of 25bps, BOTH OOS halves > 0, p < 0.00625.

Prior: memory records `funding_spike_short` as VALIDATED +6.2%/episode and
`neg_funding_fade` as REFUTED and ripped out. H1 and H2 are the honest
out-of-sample re-tests of those two claims.
"""
from __future__ import annotations

import random
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from pathia.client.hl_client import (fetch_funding_history,  # noqa: E402
                                            fetch_hl_candles)

COINS = ("BTC", "ETH", "SOL", "BNB", "XRP")
DAY = 86_400_000
HOLD_H = 24
STOP_PCT = 6.0
FEE_PCT = 0.25
ZWIN = 168          # 7d rolling window for the funding z-score
SUSTAIN_H = 24      # H4: consecutive positive-funding hours
COOLDOWN_H = 24


def funding_series(coin: str):
    now = int(time.time() * 1000)
    rows, cursor = [], now
    for _ in range(12):
        r = fetch_funding_history(coin, cursor - 21 * DAY, cursor)
        if not r:
            break
        rows = r + rows
        cursor = r[0]["time"] - 1
        if len(r) < 400:
            break
    seen = {x["time"]: x for x in rows}
    return sorted(seen.values(), key=lambda x: x["time"])


def align(coin: str):
    """Hourly funding joined to the 1h candle that follows it."""
    fund = funding_series(coin)
    bars = fetch_hl_candles(coin, "1h", 5000)
    if not fund or not bars:
        return None
    by_hour = {b.t // 3_600_000: b for b in bars}
    out = []
    for f in fund:
        b = by_hour.get(f["time"] // 3_600_000)
        if b is None:
            continue
        try:
            out.append((b, float(f["fundingRate"])))
        except (TypeError, ValueError):
            continue
    return out if len(out) > ZWIN + HOLD_H + 50 else None


def outcome(series, i, side):
    entry = series[i][0].c
    if entry <= 0:
        return None
    stop = entry * (1 - STOP_PCT / 100) if side == "long" else entry * (1 + STOP_PCT / 100)
    for j in range(i + 1, min(i + 1 + HOLD_H, len(series))):
        b = series[j][0]
        if (b.l <= stop) if side == "long" else (b.h >= stop):
            return -STOP_PCT - FEE_PCT
    last = series[min(i + HOLD_H, len(series) - 1)][0].c
    move = (last - entry) if side == "long" else (entry - last)
    return move / entry * 100 - FEE_PCT


def zscore(vals):
    if len(vals) < 20:
        return None
    m = st.mean(vals)
    s = st.pstdev(vals)
    return None if s == 0 else (vals[-1] - m) / s


def signals(series, kind, z_thr):
    out, last = [], -10**9
    for i in range(ZWIN, len(series) - HOLD_H):
        if i - last < COOLDOWN_H:
            continue
        window = [f for _, f in series[i - ZWIN:i + 1]]
        if kind == "H4":
            if all(f > 0 for f in window[-SUSTAIN_H:]):
                out.append((i, "short")); last = i
            continue
        z = zscore(window)
        if z is None:
            continue
        if kind == "H1" and z >= z_thr:
            out.append((i, "short")); last = i
        elif kind == "H2" and z <= -z_thr:
            out.append((i, "long")); last = i
        elif kind == "H3" and z >= z_thr:
            out.append((i, "long")); last = i
    return out


def main() -> int:
    data = {}
    for c in COINS:
        a = align(c)
        if a:
            data[c] = a
    if not data:
        print("no aligned data")
        return 1
    span = (list(data.values())[0][-1][0].t - list(data.values())[0][0][0].t) / DAY
    print(f"W-FND1 — funding battery. {len(data)} coins, {span:.0f} days hourly")
    print(f"8 pre-registered cells. Bonferroni {0.05/8:.5f}\n")
    print(f"{'hypothesis':<26}{'Z':>4}{'n':>6}{'win':>6}{'mean%':>8}{'null%':>8}"
          f"{'excess':>8}{'h1':>7}{'h2':>7}{'p':>8}")
    print("-" * 83)

    rnd = random.Random(23)
    survivors = []
    names = {"H1": "crowded-long fade SHORT", "H2": "crowded-short fade LONG",
             "H3": "carry-with-trend LONG", "H4": f"{SUSTAIN_H}h positive SHORT"}
    for kind in ("H1", "H2", "H3", "H4"):
        for z_thr in (2.0, 3.0):
            rets, sides = [], []
            for coin, series in data.items():
                for i, side in signals(series, kind, z_thr):
                    r = outcome(series, i, side)
                    if r is not None:
                        rets.append(r); sides.append(side)
            if len(rets) < 30:
                print(f"{names[kind]:<26}{z_thr:>4.1f}{len(rets):>6}   too few")
                if kind == "H4":
                    break
                continue
            means = []
            for _ in range(2000):
                s = []
                for side in sides:
                    coin = rnd.choice(list(data))
                    ser = data[coin]
                    j = rnd.randrange(ZWIN, len(ser) - HOLD_H)
                    r = outcome(ser, j, side)
                    if r is not None:
                        s.append(r)
                if s:
                    means.append(st.mean(s))
            mean, null = st.mean(rets), st.mean(means)
            p = (sum(1 for m in means if m >= mean) + 1) / (len(means) + 1)
            mid = len(rets) // 2
            h1, h2 = st.mean(rets[:mid]), st.mean(rets[mid:])
            win = sum(1 for r in rets if r > 0) / len(rets)
            ok = mean > 0 and h1 > 0 and h2 > 0 and p < 0.05 / 8
            if ok:
                survivors.append((names[kind], z_thr, len(rets), round(mean, 3), round(p, 5)))
            print(f"{names[kind]:<26}{z_thr:>4.1f}{len(rets):>6}{win:>6.2f}{mean:>8.3f}"
                  f"{null:>8.3f}{mean-null:>8.3f}{h1:>7.2f}{h2:>7.2f}{p:>8.4f}"
                  f"{'  PASS' if ok else ''}")
            if kind == "H4":
                break
    print()
    print("SURVIVORS:", survivors if survivors else "NONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
