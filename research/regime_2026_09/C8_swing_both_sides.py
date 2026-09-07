#!/usr/bin/env python3
"""Cycle 8 — three swing structures that need a LONG leg to work at all.

WHY THE EARLIER LONG TESTS WERE ASKING THE WRONG QUESTION
C5 and C7 both tested OUTRIGHT longs: buy the decile, hold, measure. Both lost,
and on this venue they were always going to. Funding on tokenized equity perps
runs positive, so the long PAYS the carry the short RECEIVES; C5's arithmetic
closed exactly, long and short differing by nothing but round-trip cost. An
outright long here is the short trade run backwards with the carry against you.

That is not evidence there is no long trade. It is evidence the long cannot be
DIRECTIONAL exposure. It has to be a leg in a structure where the carry either
cancels or is not the dominant term. All three hypotheses below are built that
way, and all three need both sides.

WHAT IS ACTUALLY DIFFERENT ABOUT THIS VENUE
  1. The underlying cash market is SHUT most of the week. C1 measured weekend
     vol at 0.40% against 1.29% in regular hours. A perp that moves while its
     underlying cannot trade is moving on flow, not on information.
  2. It lists equities AND indices AND commodities on one dex, so an index and
     its own constituents trade side by side - a relationship, not two tickers.
  3. Funding is a structural short subsidy, which taxes any unhedged long.

THE HYPOTHESES, DECLARED BEFORE THE RUN
S1  CROSS-SECTIONAL NEUTRAL. Long the bottom decile of 3d return, short the
    top decile, equal notional. The carry largely cancels between the legs and
    the market move cancels entirely, leaving the reversal spread. This is the
    classic quant-equity structure and nothing here has tested it.

S2  INDEX vs BASKET. When the index (SP500/XYZ100) diverges from the mean of
    its own single names, fade the gap: long the laggard side, short the
    leader. A near-arbitrage in principle, since the index IS the basket.

S3  CLOSED-MARKET FLOW FADE. Fade the largest moves that happen while the cash
    market is SHUT, in BOTH directions - short the big overnight up-moves, LONG
    the big overnight down-moves. This is the one that produces a genuine long
    signal, and the mechanism is specific: with the underlying halted, a large
    perp move cannot be repricing news the cash market has seen, so it is more
    likely inventory and flow, which reverts. C1 found the pooled
    overnight->RTH correlation was -0.033, i.e. nothing; the claim here is
    narrower and is about the TAIL, which pooling averages away.

GUARDS
4 hypotheses -> Bonferroni alpha = 0.0125. Clustered bootstrap on entry
timestamp. Both time halves must agree in sign. Minimum effective breadth in
entry timestamps AND distinct coins, not rows - C7 nearly shipped a book off
320 rows that were 59 timestamps across 14 names.

    python research/regime_2026_09/C8_swing_both_sides.py
"""
from __future__ import annotations

import random
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from C1_session_structure import RTH_END_H, RTH_START_H, load_panel  # noqa: E402
from H1_funding_carry import DAY_MS, HOUR_MS, SLIP_PCT, _f  # noqa: E402

HOLD_H = 24.0
LOOKBACK_D = 3.0
MIN_VOL = 1_000_000
N_HYP = 4
ALPHA = 0.05 / N_HYP
MIN_CLUSTERS = 100
MIN_COINS = 25
INDICES = ("xyz:SP500", "xyz:XYZ100")


def market_open(ts_ms: int) -> bool:
    d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return d.weekday() < 5 and RTH_START_H <= d.hour < RTH_END_H


def build() -> Dict[int, List[dict]]:
    """Per snapshot: every liquid xyz market with its 3d momentum, its short-run
    move, and the net return of BOTH sides over the hold."""
    panel = {c: s for c, s in load_panel().items() if ":" in c}
    all_ts = sorted({t for s in panel.values() for t in s})
    need = len(all_ts) * 0.9
    universe = [c for c, s in panel.items() if len(s) >= need]

    per_ts: Dict[int, List[dict]] = defaultdict(list)
    for coin in universe:
        series = panel[coin]
        ts = sorted(series)
        for i, t in enumerate(ts):
            px, vol = _f(series[t], "px"), _f(series[t], "v") or 0.0
            if not px or vol < MIN_VOL:
                continue
            past = [u for u in ts[:i] if u <= t - LOOKBACK_D * DAY_MS]
            if not past:
                continue
            p0 = _f(series[past[-1]], "px")
            if not p0:
                continue
            xt = next((u for u in ts[i + 1:] if u >= t + HOLD_H * HOUR_MS), None)
            p2 = _f(series[xt], "px") if xt else None
            if not p2:
                continue
            prev_t = ts[i - 1] if i else None
            prev_px = _f(series[prev_t], "px") if prev_t else None
            f0, f1 = _f(series[t], "f"), _f(series[xt], "f")
            favg = (f0 + f1) / 2 if (f0 is not None and f1 is not None) else (f0 or 0.0)
            hours = (xt - t) / HOUR_MS
            raw, carry = (p2 / px - 1) * 100, favg * hours * 100
            per_ts[t].append({
                "coin": coin, "t": t, "xt": xt,
                "mom": (px / p0 - 1) * 100,
                "long": raw - carry - SLIP_PCT,
                "short": -raw + carry - SLIP_PCT,
                # last-interval move and whether it formed with the market shut
                "tick": ((px / prev_px - 1) * 100) if prev_px else None,
                "closed": (prev_t is not None
                           and not market_open(t) and not market_open(prev_t)),
            })
    return per_ts


def boot(pairs: List[tuple], n_iter: int = 2000) -> float:
    """pairs = (entry_ts, return). Resampled by TIMESTAMP: coins entered in one
    snapshot are one bet, not twenty."""
    by_t: Dict[int, List[float]] = defaultdict(list)
    for t, r in pairs:
        by_t[t].append(r)
    keys = list(by_t)
    if len(keys) < 10:
        return 1.0
    rng = random.Random(131)
    worse = 0
    for _ in range(n_iter):
        pool: List[float] = []
        for _ in range(len(keys)):
            pool.extend(by_t[rng.choice(keys)])
        if pool and st.mean(pool) <= 0:
            worse += 1
    return worse / n_iter


def score(name: str, pairs: List[tuple], coins: set) -> Optional[dict]:
    if len(pairs) < 30:
        print(f"  {name:<34} n={len(pairs):>5}  too few, not reported")
        return None
    rets = [r for _, r in pairs]
    ev = st.mean(rets)
    win = 100 * sum(1 for r in rets if r > 0) / len(rets)
    times = sorted({t for t, _ in pairs})
    cut = times[len(times) // 2]
    h1 = [r for t, r in pairs if t < cut]
    h2 = [r for t, r in pairs if t >= cut]
    a, b = (st.mean(h1) if h1 else 0), (st.mean(h2) if h2 else 0)
    stable = (a > 0) == (b > 0)
    p = boot(pairs)
    breadth = len(times) >= MIN_CLUSTERS and len(coins) >= MIN_COINS
    ok = ev > 0 and p < ALPHA and stable and breadth
    note = ""
    if not breadth:
        note = f"  THIN({len(times)}ts/{len(coins)}coins)"
    print(f"  {name:<34} n={len(pairs):>5}  EV {ev:+.3f}%  win {win:>3.0f}%  "
          f"p {p:.4f}  halves {a:+.2f}/{b:+.2f}  {'PASS' if ok else 'fail'}{note}")
    return {"name": name, "ev": ev, "p": p, "ok": ok, "n": len(pairs)}


def main() -> int:
    per_ts = build()
    if not per_ts:
        print("  not enough panel history")
        return 1
    snaps = {t: rows for t, rows in per_ts.items() if len(rows) >= 20}
    print("C8 - swing structures that need both sides\n")
    print(f"  {len(snaps)} usable snapshots, {HOLD_H:.0f}h hold, net of "
          f"{SLIP_PCT}% slippage and funding")
    print(f"  {N_HYP} hypotheses, Bonferroni alpha = {ALPHA}\n")
    results = []

    # ---------------------------------------------------------------- S1
    print("S1 - cross-sectional NEUTRAL (long bottom decile, short top decile)")
    s1, s1_coins = [], set()
    legs_l, legs_s = [], []
    for t, rows in snaps.items():
        rows = sorted(rows, key=lambda r: r["mom"])
        k = max(1, len(rows) // 10)
        bot, top = rows[:k], rows[-k:]
        rl = st.mean(r["long"] for r in bot)
        rs = st.mean(r["short"] for r in top)
        s1.append((t, (rl + rs) / 2))       # equal notional each side
        legs_l.append((t, rl))
        legs_s.append((t, rs))
        s1_coins |= {r["coin"] for r in bot} | {r["coin"] for r in top}
    results.append(score("S1 neutral spread", s1, s1_coins))
    score("   ...its LONG leg alone", legs_l, s1_coins)
    score("   ...its SHORT leg alone", legs_s, s1_coins)

    # ---------------------------------------------------------------- S2
    print("\nS2 - index vs its own constituents")
    s2, s2_coins = [], set()
    for t, rows in snaps.items():
        idx = [r for r in rows if r["coin"] in INDICES]
        names = [r for r in rows if r["coin"] not in INDICES]
        if not idx or len(names) < 10:
            continue
        basket_mom = st.mean(r["mom"] for r in names)
        for ix in idx:
            gap = ix["mom"] - basket_mom
            if abs(gap) < 1.0:
                continue
            # index ran ahead -> short index, long basket; and vice versa
            if gap > 0:
                r = (ix["short"] + st.mean(r["long"] for r in names)) / 2
            else:
                r = (ix["long"] + st.mean(r["short"] for r in names)) / 2
            s2.append((t, r))
            s2_coins.add(ix["coin"])
    results.append(score("S2 index-basket fade", s2, s2_coins | {"basket"} ))

    # ---------------------------------------------------------------- S3
    print("\nS3 - fade the biggest moves made while the cash market is SHUT")
    closed_rows = [(t, r) for t, rows in snaps.items() for r in rows
                   if r["closed"] and r["tick"] is not None]
    if closed_rows:
        ticks = sorted(abs(r["tick"]) for _, r in closed_rows)
        thresh = ticks[int(len(ticks) * 0.90)]
        print(f"  tail threshold = |move| >= {thresh:.2f}% in one closed-market "
              f"interval ({len(closed_rows)} closed-market observations)")
        up = [(t, r["short"]) for t, r in closed_rows if r["tick"] >= thresh]
        dn = [(t, r["long"]) for t, r in closed_rows if r["tick"] <= -thresh]
        results.append(score("S3a SHORT the closed-market pops", up,
                             {r["coin"] for _, r in closed_rows}))
        results.append(score("S3b LONG the closed-market drops", dn,
                             {r["coin"] for _, r in closed_rows}))

    # ---------------------------------------------------------------- read
    print("\nTHE READ")
    passed = [r for r in results if r and r["ok"]]
    if not passed:
        print("  Nothing clears the bar. The short book stays the only measured")
        print("  edge, and the long side stays unavailable on this venue at this")
        print("  sample size.")
    else:
        for r in passed:
            print(f"  {r['name']}: {r['ev']:+.3f}%/trade, p={r['p']:.4f}, n={r['n']}")
        print("\n  Next step is NOT to wire these live off one script. Each needs a")
        print("  pre-registered out-of-sample half and a turnover/capacity check,")
        print("  because C4 established that edge per trade is not the decision")
        print("  metric - dollars per month at 3 slots is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
