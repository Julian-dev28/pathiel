#!/usr/bin/env python3
"""W-ME1 — does main_engine's DETERMINISTIC trigger have an edge, without the AI?

main_engine's forward ledger is n=47 over 7.5 DAYS. Its "both OOS halves
positive" therefore splits a single market week in half, which is not
out-of-sample in any useful sense, and mc_p=0.066 on that base can neither
validate nor refute the book.

The engine is: scan -> composite TA trigger -> AI verdict -> risk gates ->
execute. The trigger half is pure and replayable; the AI half is not. So this
answers the question that IS answerable: does the trigger alone beat entering at
a random time, over a real sample?

If yes, main_engine can go live as a mechanical book with no AI in the path.
If no, its entire claimed edge must come from the AI, and 7.5 days cannot
establish that.

Scored against a matched random-time null (same coins, same holding rule, same
stop) because a drifting asset makes money and only excess is evidence.
"""
from __future__ import annotations

import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from pathia.agents.config import get_config          # noqa: E402
from pathia.client.hl_client import fetch_hl_candles  # noqa: E402
from pathia.indicators import triggers as T           # noqa: E402

CFG = get_config()
W, TH = CFG["weights"], CFG["thresholds"]
MIN_SCORE = CFG["scan"]["minCompositeScore"]

BARS_PER_DAY = 288          # 5m
WARMUP = 120                # enough history for the longest lookback


def score_at(window):
    hits = [
        T.pct_move_spike(window, TH["sigmaThreshold"]),
        T.volume_spike(window, TH["sigmaThreshold"]),
        T.breakout(window, TH["breakoutLookback"]),
        T.shock_day(window),
        T.range_compression(window, TH["bbLength"], TH["bbStdDev"]),
        T.trend_strength(window, TH["adxPeriod"]),
        T.momentum_burst(window, TH["momentumLookback"], TH["momentumPct"]),
        T.uptrend_momentum(window, TH.get("trendMomentumLookback", 72),
                           TH.get("trendMomentumPct", 3.0)),
        T.downtrend_momentum(window, TH.get("trendMomentumLookback", 72),
                             TH.get("trendMomentumPct", 3.0)),
    ]
    return T.composite_score(hits, W)


def grade(bars, i, side, stop_pct, horizon_bars):
    """Forward outcome in PERCENT, stop-first when a bar spans both."""
    entry = bars[i].c
    if entry <= 0:
        return None
    stop = entry * (1 - stop_pct / 100) if side == "long" else entry * (1 + stop_pct / 100)
    for f in bars[i + 1:i + 1 + horizon_bars]:
        if (f.l <= stop) if side == "long" else (f.h >= stop):
            return -stop_pct
    last = bars[min(i + horizon_bars, len(bars) - 1)].c
    return ((last - entry) / entry * 100) if side == "long" else ((entry - last) / entry * 100)


def run(coins, min_score, stop_pct, horizon_days, draws=2000, seed=7):
    horizon_bars = int(horizon_days * BARS_PER_DAY)
    sigs, pool = [], []
    for coin in coins:
        try:
            bars = fetch_hl_candles(coin, "5m", 5000)
        except Exception:
            continue
        if len(bars) < WARMUP + horizon_bars + 50:
            continue
        pool.append((coin, bars))
        last_fire = -10**9
        for i in range(WARMUP, len(bars) - horizon_bars):
            if i - last_fire < BARS_PER_DAY // 4:      # one signal per coin per 6h
                continue
            s = score_at(bars[i - WARMUP:i + 1])
            if s < min_score:
                continue
            r = grade(bars, i, "long", stop_pct, horizon_bars)
            if r is not None:
                sigs.append(r)
                last_fire = i
    if len(sigs) < 20 or not pool:
        return None

    rnd = random.Random(seed)
    means = []
    for _ in range(draws):
        rs = []
        for _ in range(len(sigs)):
            coin, bars = rnd.choice(pool)
            i = rnd.randrange(WARMUP, len(bars) - horizon_bars)
            r = grade(bars, i, "long", stop_pct, horizon_bars)
            if r is not None:
                rs.append(r)
        if rs:
            means.append(st.mean(rs))
    mean = st.mean(sigs)
    null = st.mean(means)
    p = (sum(1 for m in means if m >= mean) + 1) / (len(means) + 1)
    mid = len(sigs) // 2
    return {"n": len(sigs), "mean": mean, "null": null, "excess": mean - null,
            "p": p, "h1": st.mean(sigs[:mid]), "h2": st.mean(sigs[mid:]),
            "win": sum(1 for r in sigs if r > 0) / len(sigs)}


def main() -> int:
    from pathia.agents.universe import MAJORS
    coins = [c for c in ("BTC", "ETH", "SOL", "BNB", "XRP") if c in MAJORS]
    print(f"W-ME1 — main_engine trigger, no AI. Coins: {coins}")
    print(f"Live gate: minCompositeScore={MIN_SCORE}, stop 6%, horizon 1d\n")
    print(f"{'score':>6}{'stop':>6}{'horiz':>7}{'n':>6}{'win':>6}{'mean%':>8}"
          f"{'null%':>8}{'excess':>8}{'p':>7}{'h1':>8}{'h2':>8}")
    print("-" * 78)
    cells, results = 0, []
    for min_score in (MIN_SCORE, 40):
        for stop_pct in (6.0, 15.0):
            r = run(coins, min_score, stop_pct, 1.0)
            cells += 1
            if r is None:
                print(f"{min_score:>6}{stop_pct:>6.0f}{1.0:>7.1f}   too few signals")
                continue
            results.append((min_score, stop_pct, r))
            flag = " *" if r["p"] < 0.05 else ""
            print(f"{min_score:>6}{stop_pct:>6.0f}{1.0:>7.1f}{r['n']:>6}{r['win']:>6.2f}"
                  f"{r['mean']:>8.3f}{r['null']:>8.3f}{r['excess']:>8.3f}"
                  f"{r['p']:>7.3f}{r['h1']:>8.2f}{r['h2']:>8.2f}{flag}")
    bonf = 0.05 / max(cells, 1)
    print(f"\nBonferroni threshold for {cells} cells: {bonf:.4f}")
    survivors = [(s, st_, r) for s, st_, r in results
                 if r["p"] < bonf and r["h1"] > 0 and r["h2"] > 0]
    print("SURVIVES the corrected bar with both halves positive:",
          survivors if survivors else "NONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
