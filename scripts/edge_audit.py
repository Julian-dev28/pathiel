#!/usr/bin/env python3
"""ALPHA-PLAN audit — stress-test the wired edge (xs-momentum) for overfitting / fragility.

Truth-check beyond "numbers reproduce": a REAL edge survives reasonable perturbation. Re-run the
validated cross-sectional momentum under:
  A. cost sensitivity   (5 / 10 / 20 / 30 bps/name — does it stay +EV when churn is expensive?)
  B. selection size K    (4 / 8 / 12 per leg — not a single magic K)
  C. universe size       (top 20 / 30 / 40 — not dependent on exactly 50 names)
  D. finer sub-periods   (4 quartiles of the rebalance stream — not just 2 OOS halves)
  E. long-only vs L-S    (confirm the edge is the market-NEUTRAL spread, not hidden market beta)
Anything that flips negative under a reasonable setting = a fragility to flag in the plan.
"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

VOL_FLOOR = 5e6
LB, HOLD = 7, 10


def _ymd(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def load(topn=50):
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "") and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot" and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:topn]
    data = {}
    for m in uni:
        bars = get_candles(m["coin"], "1d", 260)
        if len(bars) >= 80:
            data[m["coin"]] = {_ymd(b["t"]): (b["o"], b["c"]) for b in bars}
    return data


def xs_run(data, k, cost, coins=None):
    """Return (ls_returns, lo_returns) per daily rebalance for the given coin subset."""
    d2 = {c: data[c] for c in (coins or data)}
    all_days = sorted({d for oc in d2.values() for d in oc})
    ls, lo = [], []
    for t in range(LB, len(all_days) - HOLD - 1):
        d, d_lb, d_en = all_days[t], all_days[t - LB], all_days[t + 1]
        d_ex = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        ranked = [(c, oc[d][1] / oc[d_lb][1] - 1) for c, oc in d2.items()
                  if d in oc and d_lb in oc and d_en in oc and d_ex in oc and oc[d_lb][1] > 0]
        if len(ranked) < 2 * k + 4:
            continue
        ranked.sort(key=lambda x: x[1], reverse=True)
        L = [c for c, _ in ranked[:k]]; S = [c for c, _ in ranked[-k:]]
        def fwd(c):
            o, _ = d2[c][d_en]; _, cl = d2[c][d_ex]
            return (cl - o) / o if o > 0 else 0.0
        lr = statistics.mean(map(fwd, L)); sr = statistics.mean(map(fwd, S))
        ls.append((lr - sr) - 2 * cost); lo.append(lr - cost)
    return ls, lo


def m(arr): return statistics.mean(arr) * 100 if arr else 0.0


def line(label, arr, extra=""):
    if not arr:
        print(f"  {label:24} n=0"); return
    mid = len(arr) // 2
    h1, h2 = m(arr[:mid]), m(arr[mid:])
    ok = "+EV✓" if statistics.mean(arr) > 0 and h1 > 0 and h2 > 0 else "FRAGILE" if (h1 > 0) != (h2 > 0) else "NEG✗"
    print(f"  {label:24} mean {m(arr):>+6.2f}%  OOS {h1:>+5.2f}/{h2:>+5.2f}  {ok}{extra}")


def main():
    print(f"# AUDIT — xs-momentum (LB={LB}/hold={HOLD}) robustness stress-test\n")
    data = load(50)
    print(f"# {len(data)} coins\n")

    print("# A. cost sensitivity (per-name bps):")
    for bps in (5, 10, 20, 30):
        ls, _ = xs_run(data, 8, bps / 1e4)
        line(f"  cost {bps}bps", ls)

    print("\n# B. selection size K/leg:")
    for k in (4, 8, 12):
        ls, _ = xs_run(data, k, 10 / 1e4)
        line(f"  K={k}", ls)

    print("\n# C. universe size:")
    coins_by_liq = list(data)   # already volume-sorted
    for n in (20, 30, 40, len(data)):
        ls, _ = xs_run(data, 8, 10 / 1e4, coins=coins_by_liq[:n])
        line(f"  top-{n}", ls)

    print("\n# D. finer sub-periods (4 quartiles, base config):")
    ls, _ = xs_run(data, 8, 10 / 1e4)
    q = len(ls) // 4
    for i in range(4):
        seg = ls[i * q:(i + 1) * q] if i < 3 else ls[i * q:]
        print(f"  Q{i+1}  mean {m(seg):>+6.2f}%  n={len(seg)}")

    print("\n# E. long-only vs long-short (is the edge the neutral spread?):")
    ls, looo = xs_run(data, 8, 10 / 1e4)
    line("  long-short", ls)
    line("  long-only", looo, "  (fragile/neg ⇒ edge is the SPREAD, market-neutral)")


if __name__ == "__main__":
    main()
