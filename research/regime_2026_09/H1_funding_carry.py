#!/usr/bin/env python3
"""H1 — cross-sectional funding carry on Hyperliquid perps.

PRE-REGISTERED BEFORE THE FIRST RUN. The spec below is the whole test; if the
result disappoints, the honest move is to report it, not to widen the window
until something clears.

WHY THIS HYPOTHESIS, FOR THIS REGIME
------------------------------------
The regime read (research/regime_2026_09/regime_read.py, 2026-09-04) says the
index is going nowhere while individual names move a great deal: median return
+0.63% against a 7.08% cross-sectional standard deviation, decile spread +8.4%
to -5.0%. Meanwhile funding is positive on 86% of coins at a median +11%
annualised, so longs are paying shorts across most of the board.

Directionless plus dispersed plus positive carry is the one configuration where
a cross-sectional short-the-carry book is the natural trade: it takes no view on
the index, it is paid to wait, and dispersion is what makes the spread between
the crowded names and the rest worth harvesting.

None of the four live books touches funding or open interest at all. They are
all news and attention books. So this is not a variation on something already
running, and it draws on a panel nobody is using: ~280 coins, funding + OI +
price, roughly every three hours since 2026-06-26.

THE CLAIM
---------
H1: coins whose funding sits in the top decile OF THEIR OWN 30-day history
underperform, over the next 24 hours, coins sitting at their own median — and
the spread survives paying 25bps of round-trip slippage.

Mechanism, stated so it can be wrong in a specific way: unusually high funding
is a crowded long. The crowd pays to hold, which is a direct carry transfer to
the short, and a crowded position is the one that unwinds badly. If the effect
is real it should be strongest where positioning is most extreme and should
appear as a monotone decile ladder, not as one outlier bucket.

SPECIFICATION (frozen)
----------------------
universe    coins with >= 90% panel coverage AND >= $1M 24h volume at entry.
            Thin coins are where a 25bps assumption becomes a fiction.
feature     funding percentile within the coin's OWN trailing 30d of readings,
            MIDRANK so that ties split. Hyperliquid pins funding at its
            1.25e-05 baseline for long stretches; counting ties as "below" puts
            every pinned coin at the 100th percentile and manufactures a signal
            out of a flat series. That bug was live on the dashboard until
            2026-09-02 and is not being repeated here.
            Coins whose trailing window has fewer than 5 distinct values are
            DROPPED, not scored: a percentile over a constant is meaningless.
entry       every snapshot t with >= 24h of panel left after it.
outcome     price return t -> t+24h, PLUS funding collected by a short over
            those 24 hours (positive funding pays the short), MINUS 25bps.
            Quoted from the SHORT's side throughout, since that is the trade.
buckets     deciles of the feature. Headline is D10 (most crowded) minus D5-D6
            (the middle), because the claim is about extremes, not about a
            linear factor.
stats       IC = Spearman(feature, short return) across all observations.
            p from a paired bootstrap over ENTRY TIMESTAMPS, not observations:
            300 coins at one timestamp share one market shock and are nowhere
            near independent, so an observation-level p-value would be
            confidently wrong by roughly the square root of the panel width.
threshold   ship-worthy = D10 short return > 0 net of costs, IC of the right
            sign, bootstrap p < 0.05, AND the sign holds in both halves of the
            sample split by time.

    python research/regime_2026_09/H1_funding_carry.py
    python research/regime_2026_09/H1_funding_carry.py --horizon-h 48
"""
from __future__ import annotations

import argparse
import json
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / ".state" / ".data_funding_oi.jsonl"
HOUR_MS, DAY_MS = 3_600_000, 86_400_000
SLIP_PCT = 0.25            # 25bps round trip, same basis as every _BOOKS verdict
MIN_VOL_USD = 1_000_000
LOOKBACK_D = 30
MIN_DISTINCT = 5


def load_panel() -> Tuple[List[int], Dict[str, Dict[int, dict]]]:
    stamps: List[int] = []
    panel: Dict[str, Dict[int, dict]] = defaultdict(dict)
    for line in LOG.read_text().splitlines():
        if not line.strip():
            continue
        snap = json.loads(line)
        ts = int(snap.get("ts") or 0)
        if not ts:
            continue
        stamps.append(ts)
        for row in snap.get("rows") or []:
            if row.get("c"):
                panel[row["c"]][ts] = row
    return sorted(set(stamps)), panel


def _f(row: Optional[dict], key: str) -> Optional[float]:
    if not row:
        return None
    try:
        v = float(row.get(key))
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def midrank_pct(history: List[float], value: float) -> Optional[float]:
    """Where `value` sits in `history`, ties split.

    Returns None when the history has too few distinct values to rank against:
    a percentile computed over a constant series is not a weak signal, it is
    not a signal, and letting it through as 50 or 100 is how a flat funding
    curve becomes a trade.
    """
    if len(set(history)) < MIN_DISTINCT:
        return None
    below = sum(1 for x in history if x < value)
    tied = sum(1 for x in history if x == value)
    return (below + tied / 2.0) / len(history) * 100.0


def spearman(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    if n < 10:
        return 0.0

    def ranks(v: List[float]) -> List[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    return sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else 0.0


def build(horizon_h: float) -> List[dict]:
    """One row per (coin, entry snapshot) with the feature and the short's P&L."""
    stamps, panel = load_panel()
    if len(stamps) < 40:
        return []
    horizon_ms = int(horizon_h * HOUR_MS)
    need = len(stamps) * 0.9
    universe = [c for c, s in panel.items() if len(s) >= need]

    obs: List[dict] = []
    for coin in universe:
        series = panel[coin]
        ts_sorted = sorted(series)
        for i, t in enumerate(ts_sorted):
            row = series[t]
            f_now = _f(row, "f")
            px_now = _f(row, "px")
            vol = _f(row, "v") or 0.0
            if f_now is None or not px_now or vol < MIN_VOL_USD:
                continue
            hist = [x for x in (_f(series[u], "f") for u in ts_sorted
                                if t - LOOKBACK_D * DAY_MS <= u < t) if x is not None]
            if len(hist) < 20:
                continue
            feat = midrank_pct(hist, f_now)
            if feat is None:
                continue
            exit_t = next((u for u in ts_sorted[i + 1:] if u >= t + horizon_ms), None)
            if exit_t is None:
                continue
            px_exit = _f(series[exit_t], "px")
            if not px_exit:
                continue
            # The short's price P&L is the negative of the coin's return.
            price_leg = -(px_exit / px_now - 1.0) * 100.0
            # Funding a short COLLECTS over the hold. Averaged across the two
            # endpoints rather than assumed constant, because the whole premise
            # is that funding moves.
            f_exit = _f(series[exit_t], "f")
            f_avg = (f_now + f_exit) / 2.0 if f_exit is not None else f_now
            hours = (exit_t - t) / HOUR_MS
            funding_leg = f_avg * hours * 100.0
            obs.append({"coin": coin, "t": t, "feat": feat,
                        "ret": price_leg + funding_leg - SLIP_PCT,
                        "price_leg": price_leg, "funding_leg": funding_leg})
    return obs


def bootstrap_p(obs: List[dict], n_iter: int = 2000) -> float:
    """P(D10 short return <= 0), resampling ENTRY TIMESTAMPS.

    Resampling observations would treat 300 coins at one snapshot as 300
    independent draws. They share a market. Clustering on the timestamp is what
    keeps the interval honest, and it is usually the difference between a
    p-value of 0.001 and one of 0.2.
    """
    by_t: Dict[int, List[float]] = defaultdict(list)
    for o in obs:
        if o["feat"] >= 90:
            by_t[o["t"]].append(o["ret"])
    keys = [t for t, v in by_t.items() if v]
    if len(keys) < 10:
        return 1.0
    rng = random.Random(7)
    worse = 0
    for _ in range(n_iter):
        pool: List[float] = []
        for _ in range(len(keys)):
            pool.extend(by_t[rng.choice(keys)])
        if pool and st.mean(pool) <= 0:
            worse += 1
    return worse / n_iter


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="H1_funding_carry")
    ap.add_argument("--horizon-h", type=float, default=24.0)
    a = ap.parse_args(argv)

    print(f"H1 — cross-sectional funding carry, {a.horizon_h:.0f}h hold, "
          f"short side, net of {SLIP_PCT}% round trip\n")
    obs = build(a.horizon_h)
    if not obs:
        print("  not enough panel history")
        return 1
    coins = len({o["coin"] for o in obs})
    times = len({o["t"] for o in obs})
    print(f"  {len(obs)} observations, {coins} coins, {times} entry snapshots\n")

    obs.sort(key=lambda o: o["feat"])
    k = max(1, len(obs) // 10)
    print(f"  {'decile':<9}{'n':>7}{'funding %ile':>14}{'short ret%':>12}"
          f"{'price leg':>11}{'carry leg':>11}")
    for d in range(10):
        chunk = obs[d * k:(d + 1) * k] if d < 9 else obs[9 * k:]
        if not chunk:
            continue
        print(f"  D{d+1:<8}{len(chunk):>7}{st.mean(o['feat'] for o in chunk):>14.1f}"
              f"{st.mean(o['ret'] for o in chunk):>+12.3f}"
              f"{st.mean(o['price_leg'] for o in chunk):>+11.3f}"
              f"{st.mean(o['funding_leg'] for o in chunk):>+11.3f}")

    top = [o for o in obs if o["feat"] >= 90]
    mid = [o for o in obs if 40 <= o["feat"] <= 60]
    ic = spearman([o["feat"] for o in obs], [o["ret"] for o in obs])
    print(f"\n  D10 (crowded)   {st.mean(o['ret'] for o in top):+.3f}%/trade  n={len(top)}")
    print(f"  middle          {st.mean(o['ret'] for o in mid):+.3f}%/trade  n={len(mid)}")
    print(f"  spread          {st.mean(o['ret'] for o in top) - st.mean(o['ret'] for o in mid):+.3f}%")
    print(f"  IC              {ic:+.4f}   (positive = crowded shorts pay)")

    p = bootstrap_p(top)
    print(f"  bootstrap p     {p:.4f}   (clustered on entry timestamp)")

    cut = sorted({o["t"] for o in obs})[len({o["t"] for o in obs}) // 2]
    h1 = [o["ret"] for o in top if o["t"] < cut]
    h2 = [o["ret"] for o in top if o["t"] >= cut]
    if h1 and h2:
        print(f"  halves          {st.mean(h1):+.3f}% / {st.mean(h2):+.3f}%"
              f"   {'sign holds' if (st.mean(h1) > 0) == (st.mean(h2) > 0) else 'SIGN FLIPS'}")

    ok = (st.mean(o["ret"] for o in top) > 0 and ic > 0 and p < 0.05
          and h1 and h2 and (st.mean(h1) > 0) == (st.mean(h2) > 0))
    print(f"\n  VERDICT: {'meets the pre-registered bar' if ok else 'does NOT meet the bar'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
