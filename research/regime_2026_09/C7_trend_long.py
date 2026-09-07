#!/usr/bin/env python3
"""Cycle 7 — is there a TREND-FOLLOWING long, at a lookback C5 never tested?

WHERE THIS CAME FROM
The operator looked at the trend dashboard and asked why nothing is trading the
uptrends: xyz:CL +8.8% over 7d and +20.2% over 30d, xyz:SPCX +7.6%/+12.5%,
xyz:NVDA +6.6%/+3.3%, all flagged STRONG UP. Every live book is short-only, and
xs_reversal is actively SHORTING these names - they are the top decile of 3d
return, which is exactly what it sells.

C5 already rejected the long side, but it is fair to say C5 did not test THIS.
C5 ranked on a 3-DAY lookback and held 24h. That is a reversal horizon. A trend
signal is a different animal: a longer lookback, and a hold measured in days.
Rejecting trend-following on the strength of a 3d/24h test would be answering a
question nobody asked.

THE HYPOTHESES, DECLARED BEFORE THE RUN
T1: LONG the top decile of 30d return  (classic time-series/cross-sectional
    momentum, the horizon CL's +20.2% actually lives on)
T2: LONG the top decile of 7d return   (the intermediate horizon, between C5's
    3d reversal and T1's 30d trend)
each held 24h / 48h / 72h / 96h.

WHY THIS IS A SWEEP, AND WHAT STOPS IT BEING ONE
2 lookbacks x 4 horizons = 8 cells. Eight chances to find a winner is how
spurious results are manufactured, so:
  - Bonferroni: alpha = 0.05/8 = 0.00625, not 0.05.
  - the horizon ladder must be MONOTONE in the direction trend predicts
    (a trend that is real does not appear only at 72h and vanish at 48h and 96h),
  - both time halves must agree in sign,
  - minimum 300 observations,
  - clustered bootstrap on ENTRY TIMESTAMP, because twenty coins entered in one
    snapshot are one bet and not twenty.
A single winning cell with a broken ladder is reported as a sweep artefact.

A 30d lookback on a 72d panel leaves ~42 days of entries, so T1 is the
sample-limited one. That is stated in the output rather than hidden.

    python research/regime_2026_09/C7_trend_long.py
"""
from __future__ import annotations

import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from C1_session_structure import load_panel  # noqa: E402
from H1_funding_carry import DAY_MS, HOUR_MS, SLIP_PCT, _f  # noqa: E402

LOOKBACKS = (7.0, 30.0)
HORIZONS = (24.0, 48.0, 72.0, 96.0)
MIN_VOL = 1_000_000
MIN_N = 300
MIN_CLUSTERS = 100      # independent ENTRY TIMESTAMPS, not observations
MIN_COINS = 25
MIN_DISTINCT_EXITS = 0.80
ALPHA = 0.05 / (len(LOOKBACKS) * len(HORIZONS))


def build(lookback_d: float) -> List[dict]:
    """Entries ranked on `lookback_d` momentum, carrying long AND short net
    return at every horizon. One build per lookback; horizons share a sample so
    the ladder compares like with like."""
    panel = {c: s for c, s in load_panel().items() if ":" in c}
    if not panel:
        return []
    all_ts = sorted({t for s in panel.values() for t in s})
    need = len(all_ts) * 0.9
    universe = [c for c, s in panel.items() if len(s) >= need]
    longest = max(HORIZONS)

    per_ts: Dict[int, List[dict]] = defaultdict(list)
    for coin in universe:
        series = panel[coin]
        ts = sorted(series)
        for i, t in enumerate(ts):
            px, vol = _f(series[t], "px"), _f(series[t], "v") or 0.0
            if not px or vol < MIN_VOL:
                continue
            past = [u for u in ts[:i] if u <= t - lookback_d * DAY_MS]
            if not past:
                continue
            p0 = _f(series[past[-1]], "px")
            if not p0:
                continue
            if not any(u >= t + longest * HOUR_MS for u in ts[i + 1:]):
                continue
            row = {"coin": coin, "t": t, "mom": (px / p0 - 1) * 100}
            ok = True
            for h in HORIZONS:
                xt = next((u for u in ts[i + 1:] if u >= t + h * HOUR_MS), None)
                p2 = _f(series[xt], "px") if xt else None
                if not p2:
                    ok = False
                    break
                f0, f1 = _f(series[t], "f"), _f(series[xt], "f")
                favg = (f0 + f1) / 2 if (f0 is not None and f1 is not None) else (f0 or 0.0)
                hours = (xt - t) / HOUR_MS
                raw, carry = (p2 / px - 1) * 100, favg * hours * 100
                # The long PAYS carry, the short RECEIVES it. Slippage costs both.
                row[f"L{h:.0f}"] = raw - carry - SLIP_PCT
                row[f"S{h:.0f}"] = -raw + carry - SLIP_PCT
                row[f"xt{h:.0f}"] = xt
            if ok:
                per_ts[t].append(row)

    obs: List[dict] = []
    for t, rows in per_ts.items():
        if len(rows) < 20:
            continue
        rows.sort(key=lambda r: r["mom"])
        n = len(rows)
        for rank, r in enumerate(rows):
            r["mom_pct"] = rank / (n - 1) * 100
            obs.append(r)
    return obs


def boot(rows: List[dict], key: str, n_iter: int = 2000) -> float:
    by_t: Dict[int, List[float]] = defaultdict(list)
    for o in rows:
        by_t[o["t"]].append(o[key])
    keys = list(by_t)
    if len(keys) < 10:
        return 1.0
    rng = random.Random(97)
    worse = 0
    for _ in range(n_iter):
        pool: List[float] = []
        for _ in range(len(keys)):
            pool.extend(by_t[rng.choice(keys)])
        if pool and st.mean(pool) <= 0:
            worse += 1
    return worse / n_iter


def main() -> int:
    print("C7 - is there a trend-following LONG on xyz?\n")
    print(f"  {len(LOOKBACKS)} lookbacks x {len(HORIZONS)} horizons = "
          f"{len(LOOKBACKS)*len(HORIZONS)} cells, Bonferroni alpha = {ALPHA:.5f}")
    print(f"  min n = {MIN_N}, both halves must agree, ladder must be monotone\n")

    verdicts = []
    for lb in LOOKBACKS:
        obs = build(lb)
        top = [o for o in obs if o["mom_pct"] >= 90]
        span = ((max(o["t"] for o in obs) - min(o["t"] for o in obs)) / DAY_MS) if obs else 0
        print(f"LOOKBACK {lb:.0f}d — top decile, {len(top)} entries over {span:.0f} days")
        if len(top) < MIN_N:
            print(f"  n={len(top)} under {MIN_N}: a {lb:.0f}d lookback on this panel "
                  f"leaves too few entries to test.\n")
            continue
        times = sorted({o["t"] for o in top})
        cut = times[len(times) // 2]
        means = []
        print(f"  {'hold':>6}{'LONG EV':>10}{'win':>6}{'p':>9}  halves")
        for h in HORIZONS:
            k = f"L{h:.0f}"
            m = st.mean(o[k] for o in top)
            means.append(m)
            win = 100 * sum(1 for o in top if o[k] > 0) / len(top)
            h1 = [o[k] for o in top if o["t"] < cut]
            h2 = [o[k] for o in top if o["t"] >= cut]
            a, b = st.mean(h1), st.mean(h2)
            p = boot(top, k)
            flag = "" if (a > 0) == (b > 0) else "  FLIPS"
            print(f"  {h:>5.0f}h{m:>+9.3f}%{win:>5.0f}%{p:>9.4f}  {a:+.2f}/{b:+.2f}{flag}")
        # GUARD ADDED 2026-09-07 after this script's own first run "passed" a
        # 30d/96h long at +2.131%, p=0.0000, on a ladder that did not exist.
        #
        # The panel is hourly with gaps, so `next(u >= t + h)` can resolve two
        # different horizons to the SAME exit snapshot. On the 30d sample L48
        # and L72 were identical for 100% of rows and L72/L96 for 68%: three of
        # the four "horizons" were one measurement wearing three labels. A
        # monotone-ladder check cannot fail on numbers that are literally equal,
        # so the guard designed to catch a sweep was defeated by the data being
        # thinner than the test assumed.
        #
        # A horizon is only a distinct test if it resolves to a distinct exit.
        dupes = {}
        for a_h, b_h in zip(HORIZONS, HORIZONS[1:]):
            same = sum(1 for o in top if o[f"xt{a_h:.0f}"] == o[f"xt{b_h:.0f}"])
            dupes[(a_h, b_h)] = same / len(top)
        worst = max(dupes.values())
        distinct_ok = worst <= (1.0 - MIN_DISTINCT_EXITS)
        if not distinct_ok:
            bad = [f"{a:.0f}h/{b:.0f}h {v:.0%}" for (a, b), v in dupes.items() if v > 0.2]
            print(f"  EXIT COLLISION: {', '.join(bad)} of rows share one exit "
                  f"snapshot — these horizons are not separate tests")

        # Effective sample is entry timestamps and names, not rows. 320 rows
        # from 59 clusters across 14 coins is not 320 independent observations.
        n_clusters, n_coins = len(times), len({o["coin"] for o in top})
        breadth_ok = n_clusters >= MIN_CLUSTERS and n_coins >= MIN_COINS
        if not breadth_ok:
            print(f"  THIN SAMPLE: {n_clusters} entry timestamps "
                  f"(need {MIN_CLUSTERS}), {n_coins} coins (need {MIN_COINS}) — "
                  f"{len(top)} rows overstates the evidence")

        monotone = all(b >= a - 0.15 for a, b in zip(means, means[1:]))
        best_h = HORIZONS[means.index(max(means))]
        best = max(means)
        p_best = boot(top, f"L{best_h:.0f}")
        h1 = [o[f"L{best_h:.0f}"] for o in top if o["t"] < cut]
        h2 = [o[f"L{best_h:.0f}"] for o in top if o["t"] >= cut]
        stable = (st.mean(h1) > 0) == (st.mean(h2) > 0)
        passed = (best > 0 and p_best < ALPHA and stable and monotone
                  and distinct_ok and breadth_ok)
        print(f"  ladder monotone: {monotone}   best {best_h:.0f}h {best:+.3f}% "
              f"(p={p_best:.4f})   -> {'PASS' if passed else 'REJECT'}")
        # The short side on the SAME entries, so the comparison is like-for-like.
        s24 = st.mean(o["S24"] for o in top)
        print(f"  for contrast, SHORTING this same decile at 24h: {s24:+.3f}%\n")
        verdicts.append((lb, passed, best, best_h))

    print("THE READ")
    winners = [v for v in verdicts if v[1]]
    if not winners:
        print("  No trend-following long clears the bar at any lookback or horizon.")
        print("  The uptrends on the dashboard are real - CL is genuinely +20% over")
        print("  30d - but 'the price went up' and 'buying it pays' are different")
        print("  claims, and only the second one is tradeable. On this panel the")
        print("  second does not hold: the names that ran are not the names that")
        print("  keep running, which is the same asymmetry C5 found and is why the")
        print("  live book sells them instead.")
        print("\n  Nothing to ship. The short book stays the only measured edge.")
    else:
        for lb, _, best, h in winners:
            print(f"  {lb:.0f}d lookback, {h:.0f}h hold: {best:+.3f}% - clears the bar.")
        print("  Wire it live per the operator's standing instruction on EV+ methods.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
