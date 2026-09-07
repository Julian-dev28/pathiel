#!/usr/bin/env python3
"""Cycle 10 — search the whole space, then show what survives out of sample.

THE OPERATOR'S ASK, AND THE TRAP IN IT
"Find what would have worked, we need 5-10% EV." Searching a backtest for a
number that large ALWAYS succeeds - that is the problem, not the solution. With
108 cells and 72 days of panel, the best in-sample cell is guaranteed to look
excellent whether or not anything real is there, because the maximum of 108
noisy estimates is a biased estimate of the best true value. Reporting that
maximum as a strategy is how every blown-up quant book starts.

So the search runs, wide and honestly, and is scored the only way that means
anything: SPLIT THE PANEL FIRST. Every cell is fit on the training half and
scored on a held-out half it never touched. The gap between those two numbers
is the overfitting, measured rather than argued.

THE SPACE (pre-registered, 108 cells)
  signal      momentum | funding | volume surge | intraday range
  lookback    1d | 3d | 7d
  hold        24h | 48h | 72h
  selectivity top 10% | top 5% | top 2%
  side        short only - C5/C7/C8 established the long side is dead on this
              venue across six independent tests, and adding it back would
              double the search space to chase a leg measured at +0.049%

WHAT WOULD MAKE A RESULT REAL
  1. positive in the training half (obviously)
  2. positive OUT of sample, at a similar magnitude
  3. survives Bonferroni across all 108 cells (alpha = 0.05/108 = 0.00046)
  4. breadth: >=100 entry timestamps and >=25 coins in the OOS half
A cell that clears 1 but not 2 is the thing this script exists to expose.

    python research/regime_2026_09/C10_wide_search.py
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

SIGNALS = ("momentum", "funding", "volsurge", "range")
LOOKBACKS = (1.0, 3.0, 7.0)
HOLDS = (24.0, 48.0, 72.0)
SELECT = (10.0, 5.0, 2.0)
MIN_VOL = 1_000_000
N_CELLS = len(SIGNALS) * len(LOOKBACKS) * len(HOLDS) * len(SELECT)
ALPHA = 0.05 / N_CELLS
MIN_CLUSTERS, MIN_COINS = 100, 25


def features() -> Dict[int, List[dict]]:
    """Build every feature and every horizon ONCE. Rebuilding per cell would let
    the sample drift between cells and make them incomparable."""
    panel = {c: s for c, s in load_panel().items() if ":" in c}
    all_ts = sorted({t for s in panel.values() for t in s})
    need = len(all_ts) * 0.9
    universe = [c for c, s in panel.items() if len(s) >= need]
    longest = max(HOLDS)

    per_ts: Dict[int, List[dict]] = defaultdict(list)
    for coin in universe:
        series = panel[coin]
        ts = sorted(series)
        for i, t in enumerate(ts):
            px, vol = _f(series[t], "px"), _f(series[t], "v") or 0.0
            if not px or vol < MIN_VOL:
                continue
            if not any(u >= t + longest * HOUR_MS for u in ts[i + 1:]):
                continue
            row = {"coin": coin, "t": t}
            ok = True
            # --- signals -------------------------------------------------
            for lb in LOOKBACKS:
                past = [u for u in ts[:i] if u <= t - lb * DAY_MS]
                p0 = _f(series[past[-1]], "px") if past else None
                if not p0:
                    ok = False
                    break
                row[f"momentum{lb}"] = (px / p0 - 1) * 100
                win = [u for u in ts[:i + 1] if u >= t - lb * DAY_MS]
                fs = [x for x in (_f(series[u], "f") for u in win) if x is not None]
                row[f"funding{lb}"] = st.mean(fs) * 1e4 if fs else 0.0
                vs = [x for x in (_f(series[u], "v") for u in win) if x]
                row[f"volsurge{lb}"] = (vol / st.mean(vs)) if vs else 1.0
                ps = [x for x in (_f(series[u], "px") for u in win) if x]
                row[f"range{lb}"] = ((max(ps) - min(ps)) / px * 100) if ps else 0.0
            if not ok:
                continue
            # --- forward returns ----------------------------------------
            for h in HOLDS:
                xt = next((u for u in ts[i + 1:] if u >= t + h * HOUR_MS), None)
                p2 = _f(series[xt], "px") if xt else None
                if not p2:
                    ok = False
                    break
                f0, f1 = _f(series[t], "f"), _f(series[xt], "f")
                favg = (f0 + f1) / 2 if (f0 is not None and f1 is not None) else (f0 or 0.0)
                row[f"r{h:.0f}"] = (-(p2 / px - 1) * 100
                                    + favg * (xt - t) / HOUR_MS * 100 - SLIP_PCT)
                row[f"xt{h:.0f}"] = xt
            if ok:
                per_ts[t].append(row)
    return {t: r for t, r in per_ts.items() if len(r) >= 20}


def evaluate(snaps, sig, lb, hold, sel):
    """Rank within each snapshot, take the top `sel`%, short it."""
    out = []
    key, rk = f"{sig}{lb}", f"r{hold:.0f}"
    for t, rows in snaps.items():
        rows = sorted(rows, key=lambda r: r[key])
        k = max(1, int(len(rows) * sel / 100))
        for r in rows[-k:]:
            out.append((t, r["coin"], r[rk]))
    return out


def boot(rows, n_iter=1500):
    by_t = defaultdict(list)
    for t, _, v in rows:
        by_t[t].append(v)
    keys = list(by_t)
    if len(keys) < 10:
        return 1.0
    rng = random.Random(313)
    worse = 0
    for _ in range(n_iter):
        pool = [x for _ in keys for x in by_t[rng.choice(keys)]]
        if pool and st.mean(pool) <= 0:
            worse += 1
    return worse / n_iter


def main() -> int:
    snaps = features()
    times = sorted(snaps)
    cut = times[len(times) // 2]
    train = {t: r for t, r in snaps.items() if t < cut}
    test = {t: r for t, r in snaps.items() if t >= cut}
    print("C10 - wide search with a held-out half\n")
    print(f"  {len(snaps)} snapshots -> train {len(train)} / OOS {len(test)}")
    print(f"  {N_CELLS} cells, Bonferroni alpha = {ALPHA:.5f}\n")

    results = []
    for sig in SIGNALS:
        for lb in LOOKBACKS:
            for hold in HOLDS:
                for sel in SELECT:
                    tr = evaluate(train, sig, lb, hold, sel)
                    te = evaluate(test, sig, lb, hold, sel)
                    if len(tr) < 50 or len(te) < 50:
                        continue
                    ev_tr = st.mean(v for _, _, v in tr)
                    ev_te = st.mean(v for _, _, v in te)
                    results.append({
                        "cell": f"{sig} {lb:.0f}d/{hold:.0f}h/top{sel:.0f}%",
                        "train": ev_tr, "oos": ev_te,
                        "n_oos": len(te),
                        "cl": len({t for t, _, _ in te}),
                        "coins": len({c for _, c, _ in te}),
                        "te": te})

    results.sort(key=lambda r: -r["train"])
    print("  TOP 10 BY TRAINING EV — what 'would have worked'")
    print(f"  {'cell':<30}{'train':>9}{'OOS':>9}{'decay':>9}")
    for r in results[:10]:
        print(f"  {r['cell']:<30}{r['train']:>+8.2f}%{r['oos']:>+8.2f}%"
              f"{r['oos'] - r['train']:>+8.2f}")

    best_tr = results[0]
    print(f"\n  The best in-sample cell is {best_tr['cell']} at "
          f"{best_tr['train']:+.2f}%/trade.")
    print(f"  Out of sample it does {best_tr['oos']:+.2f}%. That gap is the "
          f"overfitting,")
    print("  measured on data the cell never saw.")

    # Does ANY cell survive properly?
    print("\n  CELLS THAT SURVIVE OUT OF SAMPLE (positive OOS, p<alpha, breadth)")
    survivors = []
    for r in results:
        if r["oos"] <= 0 or r["cl"] < MIN_CLUSTERS or r["coins"] < MIN_COINS:
            continue
        p = boot(r["te"])
        if p < ALPHA:
            survivors.append((r, p))
    if not survivors:
        print("    none.")
    else:
        survivors.sort(key=lambda x: -x[0]["oos"])
        for r, p in survivors[:8]:
            print(f"    {r['cell']:<30} train {r['train']:+.2f}%  "
                  f"OOS {r['oos']:+.2f}%  p={p:.5f}  n={r['n_oos']}")

    mean_decay = st.mean(r["oos"] - r["train"] for r in results)
    print(f"\n  Across all {len(results)} cells the average OOS decay is "
          f"{mean_decay:+.2f} points.")
    hi = [r for r in results if r["train"] >= 5.0]
    if hi:
        print(f"  {len(hi)} cells hit the requested 5%+ in training; their mean OOS "
              f"is {st.mean(r['oos'] for r in hi):+.2f}%.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
