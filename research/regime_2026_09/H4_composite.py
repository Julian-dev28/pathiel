#!/usr/bin/env python3
"""H4 — extended AND crowded. Does funding add anything to the reversal?

PRE-REGISTERED. This is a synthesis of the two results that came before it, not
a new sweep, and the distinction matters: everything tested here was implied by
H1 and H3 before this file existed.

WHAT THE PREVIOUS TESTS ESTABLISHED
-----------------------------------
H1  Shorting the highest funding loses (-0.285%/trade). The carry leg pays
    +0.245% as theory says; the price leg runs -0.323% the other way. High
    funding marks a coin going UP, so on its own it is a momentum tag.
H3  Shorting the top decile of 3-day return pays +1.373%/trade net, IC +0.084,
    bootstrap p 0.0000, and the controls behave — shorting the bottom decile
    loses, shorting everything is flat.

Put together: funding is not a reversal signal by itself, but high funding on an
ALREADY EXTENDED coin is a different object. It says the crowd is paying to
hold a position that has already run. H3 finds the extension; the question here
is whether funding sharpens it or is redundant with it.

THE CLAIM
---------
H4a: within the top momentum decile, coins ALSO in the top third of funding
     revert harder than those in the bottom third.
H4b: the composite beats plain H3 by enough to justify a second input. A signal
     that adds 0.1% and halves the trade count is not an improvement, it is a
     smaller sample wearing a better average.

WHAT WOULD MAKE IT REDUNDANT, STATED IN ADVANCE
------------------------------------------------
If funding and momentum are simply measuring the same thing, the split will
show a spread with no monotonicity and the trade count will fall roughly in
proportion to the gain. Reported either way — a redundant second input is a
useful thing to know, because shipping it would add a data dependency and a
failure mode for nothing.

Also checked: whether the funding cut survives at all once the coins pinned at
the venue baseline are removed. Roughly half the board sits at 1.25e-05 with no
funding signal whatsoever, and letting those through as "low funding" would
manufacture a contrast out of an absence.

    python research/regime_2026_09/H4_composite.py
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
from H1_funding_carry import (DAY_MS, HOUR_MS, SLIP_PCT, _f,  # noqa: E402
                              load_panel, midrank_pct)

LOOKBACK_D = 3.0
FUND_LOOKBACK_D = 30
BASELINE_F = 1.25e-05


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
            f_now = _f(series[t], "f")
            if not px_now or vol < min_vol or f_now is None:
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
            hist = [x for x in (_f(series[u], "f") for u in ts_sorted
                                if t - FUND_LOOKBACK_D * DAY_MS <= u < t) if x is not None]
            fpct = midrank_pct(hist, f_now) if len(hist) >= 20 else None
            f_exit = _f(series[exit_t], "f")
            f_avg = (f_now + f_exit) / 2.0 if f_exit is not None else f_now
            hours = (exit_t - t) / HOUR_MS
            per_ts[t].append({
                "coin": coin, "t": t,
                "mom": (px_now / px_past - 1.0) * 100.0,
                "f_now": f_now,
                "f_pct": fpct,
                "at_baseline": abs(f_now - BASELINE_F) < 1e-9,
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
    rng = random.Random(31)
    worse = 0
    for _ in range(n_iter):
        pool: List[float] = []
        for _ in range(len(keys)):
            pool.extend(by_t[rng.choice(keys)])
        if pool and st.mean(pool) <= 0:
            worse += 1
    return worse / n_iter


def show(rows: List[dict], label: str) -> Optional[float]:
    if len(rows) < 150:
        print(f"  {label:<40} n={len(rows):>5}  too few")
        return None
    m = st.mean(o["short_ret"] for o in rows)
    win = 100.0 * sum(1 for o in rows if o["short_ret"] > 0) / len(rows)
    print(f"  {label:<40} n={len(rows):>5}  {m:+.3f}%   win {win:.0f}%")
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="H4_composite")
    ap.add_argument("--horizon-h", type=float, default=24.0)
    ap.add_argument("--min-vol", type=float, default=1_000_000)
    a = ap.parse_args(argv)

    print(f"H4 — extended AND crowded, {a.horizon_h:.0f}h hold, short side, "
          f"net of funding and {SLIP_PCT}%\n")
    obs = build(a.horizon_h, a.min_vol)
    if not obs:
        print("  not enough panel history")
        return 1
    top = [o for o in obs if o["mom_pct"] >= 90]
    print(f"  {len(obs)} observations; top momentum decile n={len(top)}\n")

    print("BASELINE (H3, for comparison)")
    base = show(top, "short top momentum decile")

    print("\nH4a — funding cut INSIDE the top momentum decile")
    scored = [o for o in top if o["f_pct"] is not None and not o["at_baseline"]]
    pinned = [o for o in top if o["at_baseline"]]
    print(f"  ({len(pinned)} of {len(top)} are pinned at the venue baseline and")
    print("   carry no funding signal at all — scored separately, never as 'low')")
    show(pinned, "  pinned at baseline")
    if scored:
        scored.sort(key=lambda o: o["f_pct"])
        k = max(1, len(scored) // 3)
        lo, hi = scored[:k], scored[-k:]
        m_lo = show(lo, "  bottom third of own funding range")
        m_hi = show(hi, "  top third of own funding range")
        if m_lo is not None and m_hi is not None:
            print(f"\n  funding adds {m_hi - m_lo:+.3f}% between thirds")
            print(f"  p on the crowded+extended leg: {bootstrap_p(hi):.4f}")

            print("\nH4b — is it worth a second input?")
            keep = len(hi) / len(top) * 100
            print(f"  trades kept   {keep:.0f}% of the H3 book ({len(hi)} of {len(top)})")
            if base is not None:
                gain = m_hi - base
                print(f"  edge gained   {gain:+.3f}%/trade over plain H3")
                # A filter that costs more trades than it adds edge is a smaller
                # sample wearing a better average.
                verdict = ("worth it" if gain > 0.3 and keep > 20 else
                           "REDUNDANT — the gain does not pay for the trades lost")
                print(f"  verdict       {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
