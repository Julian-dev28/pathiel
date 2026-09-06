#!/usr/bin/env python3
"""Cycle 2 — is the reversal the same trade on a Friday as on a Tuesday?

WHERE THIS CAME FROM
C1 measured per-interval volatility by session on tokenized equity perps:

  regular hours   1.29%
  overnight       0.97%
  weekend         0.40%

Weekend volatility is a THIRD of regular hours, because the underlying equity
is shut and no news is arriving. C1 also killed the obvious idea in passing:
the overnight-to-open correlation is -0.033, so there is no gap fade to trade.

What survives is a timing question. xs_reversal holds 24 hours. On a Tuesday
that window contains a full regular session. On a Friday it contains a weekend,
where the instrument barely moves — so the trade pays the spread and the funding
and waits in dead tape for its thesis to play out in a market that is closed.

THE CLAIM
C2a: the reversal's edge varies by the session the position is ENTERED in, and
     is weakest for entries whose 24h window is mostly closed market.
C2b: entries made into a weekend underperform enough to be worth skipping.

If true, the fix is free: the same book, declining to enter at certain hours.
No new signal, no new data, no extra risk.

WHAT WOULD MAKE THIS NOISE
Day-of-week effects are the single most over-fitted result in trading research —
with five days and two sessions there are plenty of buckets to find something in.
So: the buckets are declared before the run, the mechanism has to hold across
BOTH halves of the sample, and a bucket needs at least 100 observations to be
reported at all. A single lucky Wednesday is not a strategy.

    python research/regime_2026_09/C2_entry_timing.py
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
from C1_session_structure import RTH_END_H, RTH_START_H, load_panel, session_of  # noqa: E402
from H1_funding_carry import DAY_MS, HOUR_MS, SLIP_PCT, _f  # noqa: E402

LOOKBACK_D = 3.0
HOLD_H = 24.0
MIN_VOL = 1_000_000
BASELINE_F = 1.25e-05
AWAKE_MIN = 0.67
MIN_BUCKET = 100


def build() -> List[dict]:
    """Every xs_reversal-shaped entry, tagged with when it was taken."""
    panel = {c: s for c, s in load_panel().items() if ":" in c}
    if not panel:
        return []
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
            exit_t = next((u for u in ts[i + 1:] if u >= t + HOLD_H * HOUR_MS), None)
            if exit_t is None:
                continue
            p2 = _f(series[exit_t], "px")
            if not p2:
                continue
            recent = [x for x in (_f(series[u], "f") for u in ts
                                  if t - 7 * DAY_MS <= u <= t) if x is not None]
            if len(recent) < 8:
                continue
            awake = sum(1 for x in recent if abs(x - BASELINE_F) > 1e-9) / len(recent)
            f0, f1 = _f(series[t], "f"), _f(series[exit_t], "f")
            favg = (f0 + f1) / 2 if (f0 is not None and f1 is not None) else (f0 or 0.0)
            hours = (exit_t - t) / HOUR_MS
            d = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
            per_ts[t].append({
                "coin": coin, "t": t, "awake": awake,
                "mom": (px / p0 - 1) * 100,
                "short_ret": -(p2 / px - 1) * 100 + favg * hours * 100 - SLIP_PCT,
                "dow": d.weekday(), "session": session_of(t),
                # Does the 24h window from here run into a weekend?
                "into_weekend": any(
                    datetime.fromtimestamp((t + h * HOUR_MS) / 1000,
                                           tz=timezone.utc).weekday() >= 5
                    for h in range(0, int(HOLD_H) + 1, 6)),
            })

    obs: List[dict] = []
    for t, rows in per_ts.items():
        if len(rows) < 20:
            continue
        rows.sort(key=lambda r: r["mom"])
        n = len(rows)
        for rank, r in enumerate(rows):
            r["mom_pct"] = rank / (n - 1) * 100
            obs.append(r)
    # The book's own entry condition, so this measures the live trade.
    return [o for o in obs if o["mom_pct"] >= 90 and o["awake"] >= AWAKE_MIN]


def boot(rows: List[dict], n_iter: int = 2000) -> float:
    by_t: Dict[int, List[float]] = defaultdict(list)
    for o in rows:
        by_t[o["t"]].append(o["short_ret"])
    keys = list(by_t)
    if len(keys) < 10:
        return 1.0
    rng = random.Random(53)
    worse = 0
    for _ in range(n_iter):
        pool: List[float] = []
        for _ in range(len(keys)):
            pool.extend(by_t[rng.choice(keys)])
        if pool and st.mean(pool) <= 0:
            worse += 1
    return worse / n_iter


def show(rows: List[dict], label: str, cut: int) -> None:
    if len(rows) < MIN_BUCKET:
        print(f"  {label:<26} n={len(rows):>5}   under {MIN_BUCKET}, not reported")
        return
    m = st.mean(o["short_ret"] for o in rows)
    win = 100 * sum(1 for o in rows if o["short_ret"] > 0) / len(rows)
    h1 = [o["short_ret"] for o in rows if o["t"] < cut]
    h2 = [o["short_ret"] for o in rows if o["t"] >= cut]
    halves = ""
    if len(h1) > 20 and len(h2) > 20:
        a, b = st.mean(h1), st.mean(h2)
        halves = f"  halves {a:+.2f}/{b:+.2f} {'OK' if (a > 0) == (b > 0) else 'FLIPS'}"
    print(f"  {label:<26} n={len(rows):>5}  {m:+.3f}%  win {win:.0f}%{halves}")


def main() -> int:
    print(f"C2 — does entry timing change the reversal? "
          f"({HOLD_H:.0f}h hold, short side, net)\n")
    obs = build()
    if not obs:
        print("  not enough panel history")
        return 1
    times = sorted({o["t"] for o in obs})
    cut = times[len(times) // 2]
    print(f"  {len(obs)} entries matching the live book's condition "
          f"(top decile, funding awake)\n")

    print("BASELINE")
    show(obs, "all entries", cut)

    print("\nC2a — by session at entry")
    for s in ("rth", "overnight", "weekend"):
        show([o for o in obs if o["session"] == s], f"  entered in {s}", cut)

    print("\nC2b — does the hold run into a weekend?")
    show([o for o in obs if not o["into_weekend"]], "  weekday hold only", cut)
    show([o for o in obs if o["into_weekend"]], "  hold spans a weekend", cut)

    print("\nBY WEEKDAY (reported for completeness; five buckets find things)")
    for d, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        show([o for o in obs if o["dow"] == d], f"  {name}", cut)

    clean = [o for o in obs if not o["into_weekend"]]
    if len(clean) >= MIN_BUCKET:
        base, filt = st.mean(o["short_ret"] for o in obs), st.mean(o["short_ret"] for o in clean)
        print(f"\n  skipping weekend-spanning entries: {base:+.3f}% -> {filt:+.3f}% "
              f"({filt - base:+.3f}%), keeping {100*len(clean)/len(obs):.0f}% of trades")
        print(f"  bootstrap p on the kept set: {boot(clean):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
