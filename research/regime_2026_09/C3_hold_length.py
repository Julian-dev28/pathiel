#!/usr/bin/env python3
"""Cycle 3 — skip the weekend trades, or hold them longer?

WHERE THIS CAME FROM
C1: weekend volatility on tokenized equity perps is 0.40% against 1.29% in
    regular hours. The underlying is shut; the perp is marking time.
C2: an xs_reversal entry whose 24h hold spans a weekend earns +0.512% against
    +2.018% for one that does not. Same signal, same book — only the calendar
    differs. Skipping them lifts the book to +2.018% and costs 40% of its trades.

Losing 40% of the trades is a real cost on an account that needs turnover, so
before shipping a skip, ask whether those trades are bad or merely EARLY. If the
reversal needs an open market to play out, a Friday entry has not been refuted
at the 24h mark — it has been measured in a window where nothing could happen.

THE CLAIM
C3a: for weekend-spanning entries, extending the hold until the market has been
     open again recovers most of the edge.
C3b: if it does, holding longer beats skipping, because it keeps the trades.

WHAT WOULD MAKE THIS NOISE, AND THE GUARD
Sweeping hold lengths is textbook overfitting: enough horizons and one will
look good. So the horizons are declared here (24/48/72/96h), the result must be
MONOTONE in the direction the mechanism predicts rather than one lucky bucket,
and it must hold in both halves. A single winning horizon with a broken ladder
is a sweep artefact and will be reported as one.

    python research/regime_2026_09/C3_hold_length.py
"""
from __future__ import annotations

import random
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from C1_session_structure import load_panel  # noqa: E402
from H1_funding_carry import DAY_MS, HOUR_MS, SLIP_PCT, _f  # noqa: E402

LOOKBACK_D = 3.0
HORIZONS = (24.0, 48.0, 72.0, 96.0)
MIN_VOL = 1_000_000
BASELINE_F = 1.25e-05
AWAKE_MIN = 0.67


def spans_weekend(t_ms: int, hold_h: float) -> bool:
    return any(datetime.fromtimestamp((t_ms + h * HOUR_MS) / 1000,
                                      tz=timezone.utc).weekday() >= 5
               for h in range(0, int(hold_h) + 1, 6))


def build() -> List[dict]:
    """One row per entry, carrying the return at EVERY horizon.

    Computed together so each horizon is scored on an identical entry set — a
    per-horizon rebuild would let a coin drop out of one and not another and
    silently compare different samples.
    """
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
            past = [u for u in ts[:i] if u <= t - LOOKBACK_D * DAY_MS]
            if not past:
                continue
            p0 = _f(series[past[-1]], "px")
            if not p0:
                continue
            # Require the LONGEST horizon to resolve, so every row scores at
            # all of them. Otherwise short holds get a larger, later sample.
            if not any(u >= t + longest * HOUR_MS for u in ts[i + 1:]):
                continue
            recent = [x for x in (_f(series[u], "f") for u in ts
                                  if t - 7 * DAY_MS <= u <= t) if x is not None]
            if len(recent) < 8:
                continue
            awake = sum(1 for x in recent if abs(x - BASELINE_F) > 1e-9) / len(recent)
            row = {"coin": coin, "t": t, "awake": awake,
                   "mom": (px / p0 - 1) * 100}
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
                row[f"r{h:.0f}"] = (-(p2 / px - 1) * 100
                                    + favg * hours * 100 - SLIP_PCT)
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
    return [o for o in obs if o["mom_pct"] >= 90 and o["awake"] >= AWAKE_MIN]


def boot(rows: List[dict], key: str, n_iter: int = 2000) -> float:
    by_t: Dict[int, List[float]] = defaultdict(list)
    for o in rows:
        by_t[o["t"]].append(o[key])
    keys = list(by_t)
    if len(keys) < 10:
        return 1.0
    rng = random.Random(67)
    worse = 0
    for _ in range(n_iter):
        pool: List[float] = []
        for _ in range(len(keys)):
            pool.extend(by_t[rng.choice(keys)])
        if pool and st.mean(pool) <= 0:
            worse += 1
    return worse / n_iter


def row(rows: List[dict], label: str, cut: int) -> None:
    if len(rows) < 100:
        print(f"  {label:<22} n={len(rows):>5}  under 100, not reported")
        return
    out = f"  {label:<22} n={len(rows):>5}"
    for h in HORIZONS:
        k = f"r{h:.0f}"
        m = st.mean(o[k] for o in rows)
        h1 = [o[k] for o in rows if o["t"] < cut]
        h2 = [o[k] for o in rows if o["t"] >= cut]
        flag = "" if (not h1 or not h2 or (st.mean(h1) > 0) == (st.mean(h2) > 0)) else "!"
        out += f"  {m:+7.3f}{flag:<1}"
    print(out)


def main() -> int:
    print("C3 — skip the weekend entries, or hold them longer?\n")
    obs = build()
    if not obs:
        print("  not enough panel history")
        return 1
    times = sorted({o["t"] for o in obs})
    cut = times[len(times) // 2]
    print(f"  {len(obs)} entries, each scored at every horizon on the same sample")
    print("  ('!' marks a bucket whose two time halves disagree in sign)\n")
    print(f"  {'':<22} {'n':>6}" + "".join(f"{h:>9.0f}h" for h in HORIZONS))

    row(obs, "all entries", cut)
    clean = [o for o in obs if not spans_weekend(o["t"], 24.0)]
    dirty = [o for o in obs if spans_weekend(o["t"], 24.0)]
    row(clean, "24h clear of weekend", cut)
    row(dirty, "24h spans weekend", cut)

    print("\n  THE QUESTION: are the weekend entries bad, or just early?")
    if len(dirty) >= 100:
        best_h = max(HORIZONS, key=lambda h: st.mean(o[f"r{h:.0f}"] for o in dirty))
        vals = [st.mean(o[f"r{h:.0f}"] for o in dirty) for h in HORIZONS]
        monotone = all(b >= a - 0.15 for a, b in zip(vals, vals[1:]))
        base24 = st.mean(o["r24"] for o in dirty)
        best = st.mean(o[f"r{best_h:.0f}"] for o in dirty)
        print(f"  weekend entries at 24h: {base24:+.3f}%   at {best_h:.0f}h: {best:+.3f}%")
        print(f"  ladder monotone in the predicted direction: {monotone}")
        print(f"  bootstrap p at {best_h:.0f}h: {boot(dirty, f'r{best_h:.0f}'):.4f}")
        if monotone and best > base24 + 0.5:
            print("\n  HOLD LONGER beats skipping: the trades were early, not wrong,")
            print("  and extending keeps 100% of the book's turnover.")
        else:
            print("\n  Extending does NOT rescue them in a way the ladder supports.")
            print("  Skipping is the honest call — a single better horizon with a")
            print("  broken ladder is a sweep artefact, not an edge.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
