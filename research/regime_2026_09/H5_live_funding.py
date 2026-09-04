#!/usr/bin/env python3
"""H5 — the reversal only works where the perp market is awake.

PROVENANCE, STATED FIRST BECAUSE IT CHANGES HOW MUCH THIS IS WORTH
------------------------------------------------------------------
This was NOT pre-registered. It fell out of H4, which was testing something
else and failed: funding DIRECTION inside the top momentum decile added nothing
(+2.013% top third vs +2.053% bottom third, a -0.04% difference).

What separated the sample was a line H4 printed only as a control. Coins pinned
at Hyperliquid's 1.25e-05 funding baseline returned +0.380% while every coin
whose funding had actually moved returned about +2.03%.

Found-by-accident results are the ones most likely to be noise, so this file
does not reuse H4's numbers. It re-derives the split, states a mechanism that
could be wrong, and applies the same bar H3 had to clear: monotone behaviour,
bootstrap p clustered on snapshot, sign holding across time quartiles, and
survival at a liquidity floor where 25bps is not a fiction.

THE MECHANISM
-------------
Funding moves when longs and shorts disagree enough to pay each other. A coin
sitting at the venue's baseline rate for weeks has no such disagreement: there
is no crowded side, because there is barely a side. The reversal trade is a bet
that an extended move was driven by positioning that has to unwind — and where
there is no positioning, there is nothing to unwind.

So the prediction is not "low funding is bad". It is that a DEAD funding market
has no reversal to harvest, whichever way the rate points.

H5: within the top decile of 3-day return, coins whose funding has left the
    venue baseline revert materially harder than coins pinned to it.

The distinguishing test, and the one that would embarrass the mechanism: if
this is really about liquidity rather than positioning, then controlling for
volume should collapse the gap. Pinned coins are probably thinner. That is
checked below, and it is the result that decides whether this is a finding or a
proxy for a filter we already have.

    python research/regime_2026_09/H5_live_funding.py
"""
from __future__ import annotations

import argparse
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from H1_funding_carry import DAY_MS, HOUR_MS, SLIP_PCT, _f, load_panel  # noqa: E402

LOOKBACK_D = 3.0
BASELINE_F = 1.25e-05
AWAKE_LOOKBACK_D = 7      # judge "awake" on the recent past, never the future


def build(horizon_h: float, min_vol: float) -> List[dict]:
    stamps, panel = load_panel()
    need = len(stamps) * 0.9
    universe = [c for c, s in panel.items() if len(s) >= need]
    horizon_ms, look_ms = int(horizon_h * HOUR_MS), int(LOOKBACK_D * DAY_MS)

    per_ts: Dict[int, List[dict]] = defaultdict(list)
    for coin in universe:
        series = panel[coin]
        ts_sorted = sorted(series)
        for i, t in enumerate(ts_sorted):
            px_now, vol = _f(series[t], "px"), _f(series[t], "v") or 0.0
            if not px_now or vol < min_vol:
                continue
            past = [u for u in ts_sorted[:i] if u <= t - look_ms]
            if not past:
                continue
            px_past = _f(series[past[-1]], "px")
            if not px_past:
                continue
            exit_t = next((u for u in ts_sorted[i + 1:] if u >= t + horizon_ms), None)
            if exit_t is None:
                continue
            px_exit = _f(series[exit_t], "px")
            if not px_exit:
                continue
            # "Awake" is measured on the TRAILING window only. Judging it on the
            # reading at entry alone would let a single tick decide, and judging
            # it on anything at or after exit_t would be lookahead.
            recent = [x for x in (_f(series[u], "f") for u in ts_sorted
                                  if t - AWAKE_LOOKBACK_D * DAY_MS <= u <= t)
                      if x is not None]
            if len(recent) < 8:
                continue
            off_baseline = sum(1 for x in recent if abs(x - BASELINE_F) > 1e-9)
            awake_frac = off_baseline / len(recent)
            f_now, f_exit = _f(series[t], "f"), _f(series[exit_t], "f")
            f_avg = ((f_now + f_exit) / 2.0
                     if (f_now is not None and f_exit is not None) else (f_now or 0.0))
            hours = (exit_t - t) / HOUR_MS
            per_ts[t].append({
                "coin": coin, "t": t, "vol": vol,
                "mom": (px_now / px_past - 1.0) * 100.0,
                "awake": awake_frac,
                "short_ret": -(px_exit / px_now - 1.0) * 100.0
                             + f_avg * hours * 100.0 - SLIP_PCT,
            })

    obs: List[dict] = []
    for t, rows in per_ts.items():
        if len(rows) < 20:
            continue
        rows.sort(key=lambda r: r["mom"])
        n = len(rows)
        for rank, r in enumerate(rows):
            r["mom_pct"] = rank / (n - 1) * 100.0
            obs.append(r)
    return obs


def bootstrap_p(rows: List[dict], n_iter: int = 3000) -> float:
    by_t: Dict[int, List[float]] = defaultdict(list)
    for o in rows:
        by_t[o["t"]].append(o["short_ret"])
    keys = list(by_t)
    if len(keys) < 10:
        return 1.0
    rng = random.Random(41)
    worse = 0
    for _ in range(n_iter):
        pool: List[float] = []
        for _ in range(len(keys)):
            pool.extend(by_t[rng.choice(keys)])
        if pool and st.mean(pool) <= 0:
            worse += 1
    return worse / n_iter


def show(rows: List[dict], label: str) -> Optional[float]:
    if len(rows) < 120:
        print(f"  {label:<44} n={len(rows):>5}  too few")
        return None
    m = st.mean(o["short_ret"] for o in rows)
    win = 100.0 * sum(1 for o in rows if o["short_ret"] > 0) / len(rows)
    print(f"  {label:<44} n={len(rows):>5}  {m:+.3f}%   win {win:.0f}%")
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="H5_live_funding")
    ap.add_argument("--horizon-h", type=float, default=24.0)
    ap.add_argument("--min-vol", type=float, default=1_000_000)
    a = ap.parse_args(argv)

    print(f"H5 — reversal needs an awake funding market. {a.horizon_h:.0f}h hold, "
          f"vol floor ${a.min_vol/1e6:.0f}M\n")
    obs = build(a.horizon_h, a.min_vol)
    if not obs:
        print("  not enough panel history")
        return 1
    top = [o for o in obs if o["mom_pct"] >= 90]
    print(f"  top momentum decile n={len(top)}\n")

    print("THE LADDER — fraction of the last 7d spent off the venue baseline")
    bands = [(0.0, 0.01, "dead      (0%)"), (0.01, 0.34, "faint     (1-33%)"),
             (0.34, 0.67, "moderate  (34-66%)"), (0.67, 1.01, "awake     (67-100%)")]
    means = []
    for lo, hi, name in bands:
        m = show([o for o in top if lo <= o["awake"] < hi], f"  {name}")
        means.append(m)

    awake = [o for o in top if o["awake"] >= 0.67]
    dead = [o for o in top if o["awake"] < 0.01]
    print("\nHEADLINE")
    m_awake, m_dead = show(awake, "  awake funding market"), show(dead, "  dead funding market")
    if m_awake is not None and m_dead is not None:
        print(f"\n  gap            {m_awake - m_dead:+.3f}%/trade")
        print(f"  trades kept    {len(awake)/len(top)*100:.0f}% of the H3 book")
        print(f"  bootstrap p    {bootstrap_p(awake):.4f}  (awake leg, clustered)")

    print("\nIS IT JUST LIQUIDITY? (the result that would demote this to a proxy)")
    med = st.median(o["vol"] for o in top)
    print(f"  median 24h volume in the decile: ${med/1e6:.1f}M")
    for band, rows in (("liquid half", [o for o in top if o["vol"] >= med]),
                       ("thin half", [o for o in top if o["vol"] < med])):
        aw = [o for o in rows if o["awake"] >= 0.67]
        de = [o for o in rows if o["awake"] < 0.01]
        ma, md = show(aw, f"  {band}: awake"), show(de, f"  {band}: dead")
        if ma is not None and md is not None:
            print(f"      gap within {band}: {ma - md:+.3f}%")

    print("\nSTABILITY")
    ts = sorted({o["t"] for o in obs})
    qs = [ts[len(ts) * i // 4] for i in range(1, 4)]
    for i, (lo, hi) in enumerate(zip([0] + qs, qs + [ts[-1] + 1])):
        show([o for o in awake if lo <= o["t"] < hi], f"  quartile {i+1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
