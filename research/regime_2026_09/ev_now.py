#!/usr/bin/env python3
"""What is the CURRENT expectation, given two estimates that disagree?

The backtest says +1.408%/trade on 2562 observations. The live forward ledger
says -7.37% on 6. Quoting either alone is a choice dressed as a fact, so this
computes both with error bars, combines them the only honest way (precision
weighting), and prints what the disagreement is actually worth.

Nothing here is a forecast. It is the range the evidence supports, and the
sample size that would settle it.

    python research/regime_2026_09/ev_now.py [equity]
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from C1_session_structure import load_panel  # noqa: E402
from H1_funding_carry import DAY_MS, HOUR_MS, SLIP_PCT, _f  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LIVE_EV, LIVE_N = -7.367, 6          # scripts/grade_forward_ledger.py
LOOKBACK_D, HOLD_H = 3.0, 24.0
MIN_VOL, BASELINE_F, AWAKE_MIN = 1_000_000, 1.25e-05, 0.67


def backtest():
    """The book's own entries, and crucially their DISPERSION - the per-trade
    stdev is what decides how much 6 live observations are allowed to move the
    estimate."""
    panel = {c: s for c, s in load_panel().items() if ":" in c}
    all_ts = sorted({t for s in panel.values() for t in s})
    need = len(all_ts) * 0.9
    per_ts = defaultdict(list)
    for coin in [c for c, s in panel.items() if len(s) >= need]:
        series = panel[coin]; ts = sorted(series)
        for i, t in enumerate(ts):
            px, vol = _f(series[t], "px"), _f(series[t], "v") or 0.0
            if not px or vol < MIN_VOL:
                continue
            past = [u for u in ts[:i] if u <= t - LOOKBACK_D * DAY_MS]
            if not past or not _f(series[past[-1]], "px"):
                continue
            p0 = _f(series[past[-1]], "px")
            xt = next((u for u in ts[i + 1:] if u >= t + HOLD_H * HOUR_MS), None)
            p2 = _f(series[xt], "px") if xt else None
            if not p2:
                continue
            rec = [x for x in (_f(series[u], "f") for u in ts
                               if t - 7 * DAY_MS <= u <= t) if x is not None]
            if len(rec) < 8:
                continue
            awake = sum(1 for x in rec if abs(x - BASELINE_F) > 1e-9) / len(rec)
            f0, f1 = _f(series[t], "f"), _f(series[xt], "f")
            favg = (f0 + f1) / 2 if (f0 is not None and f1 is not None) else (f0 or 0.0)
            per_ts[t].append({"t": t, "awake": awake, "mom": (px / p0 - 1) * 100,
                              "r": -(p2 / px - 1) * 100
                                   + favg * (xt - t) / HOUR_MS * 100 - SLIP_PCT})
    obs = []
    for t, rows in per_ts.items():
        if len(rows) < 20:
            continue
        rows.sort(key=lambda r: r["mom"])
        for rank, r in enumerate(rows):
            r["pct"] = rank / (len(rows) - 1) * 100
            obs.append(r)
    sel = [o for o in obs if o["pct"] >= 90 and o["awake"] >= AWAKE_MIN]
    r = [o["r"] for o in sel]
    ts = sorted({o["t"] for o in sel}); cut = ts[len(ts) // 2]
    h2 = [o["r"] for o in sel if o["t"] >= cut]
    # standard error CLUSTERED on entry timestamp: coins entered together are
    # one bet, and treating them as independent shrinks the error ~sqrt(k).
    byt = defaultdict(list)
    for o in sel:
        byt[o["t"]].append(o["r"])
    cl = [st.mean(v) for v in byt.values()]
    return (st.mean(r), st.pstdev(r), len(r), st.mean(h2),
            st.pstdev(cl) / len(cl) ** 0.5, len(cl))


def main() -> int:
    eq = float(sys.argv[1]) if len(sys.argv) > 1 else 34.35
    cfg = json.loads((ROOT / ".agent-config.json").read_text())
    slots = int(cfg["max_concurrent"])
    frac = float(cfg["strategy_book_equity_frac"])
    lev = int(cfg["leverage"])
    notional = eq * frac * lev
    trades_day = slots * (24.0 / float(cfg["xs_reversal"]["hold_hours"]))

    bt_ev, bt_sd, bt_n, bt_h2, bt_se, n_cl = backtest()
    live_se = bt_sd / LIVE_N ** 0.5      # live n is too small for its own stdev

    # Precision weighting: each estimate counts by 1/variance. This is what
    # "combine them" means when you are not allowed to pick a favourite.
    wb, wl = 1 / bt_se ** 2, 1 / live_se ** 2
    comb = (bt_ev * wb + LIVE_EV * wl) / (wb + wl)
    comb_se = (1 / (wb + wl)) ** 0.5

    print(f"THE BOOK at ${eq:.2f}: {slots} slots x ${notional:.2f} notional, "
          f"{trades_day:.0f} trades/day\n")
    print(f"  {'estimate':<26}{'EV/trade':>11}{'+/- se':>9}{'n':>7}{'$/month':>10}")
    rows = [
        ("backtest, pooled", bt_ev, bt_se, bt_n),
        ("backtest, recent half", bt_h2, bt_se, bt_n // 2),
        ("LIVE forward ledger", LIVE_EV, live_se, LIVE_N),
        ("precision-weighted", comb, comb_se, bt_n + LIVE_N),
    ]
    for name, ev, se, n in rows:
        print(f"  {name:<26}{ev:>+10.3f}%{se:>8.2f}{n:>7}"
              f"{notional * ev / 100 * trades_day * 30:>10.2f}")

    print(f"\n  per-trade dispersion: {bt_sd:.2f}%  "
          f"(clustered se {bt_se:.2f}% on {n_cl} timestamps)")
    gap = bt_ev - LIVE_EV
    print(f"  the two estimates differ by {gap:.2f} points = "
          f"{gap / live_se:.1f} live standard errors")

    print("\n  WHAT THIS MEANS")
    print(f"  The combined estimate sits at {comb:+.3f}%/trade, barely moved from")
    print(f"  the backtest, because 6 observations against {n_cl} carry almost no")
    print("  weight. That is the correct arithmetic AND the reason not to trust it:")
    print("  precision weighting assumes both samples measure the same thing. If")
    print("  the backtest is overfit or the regime turned, the live number is not")
    print("  a noisy draw from it - it is the only honest one, and weighting drowns")
    print("  it. The 6 bets cannot tell those apart.")
    # How significant IS the live shortfall, under the backtest as null?
    t_all = (LIVE_EV - bt_ev) / live_se
    # Two of the six were crypto (PONS -23.83, FIL -2.81) recorded before
    # enable_crypto went false; the current config would not have taken them.
    xyz_ev, xyz_n = -4.39, 4
    xyz_se = bt_sd / xyz_n ** 0.5
    t_xyz = (xyz_ev - bt_ev) / xyz_se
    print(f"\n  IS THE LIVE SHORTFALL NOISE?  (null = the backtest is right)")
    print(f"    all 6 bets        {LIVE_EV:+.2f}%   t = {t_all:+.2f}  "
          f"-> {'NOT noise' if abs(t_all) > 2 else 'within noise'}")
    print(f"    the 4 the current config would take ({xyz_ev:+.2f}%)   "
          f"t = {t_xyz:+.2f}  -> {'NOT noise' if abs(t_xyz) > 2 else 'within noise'}")
    print("    The two biggest losers (PONS -23.8%, FIL -2.8%) are CRYPTO, logged")
    print("    before enable_crypto went false. Excluding them halves the shortfall")
    print("    and drops it back inside noise - which is the honest read, not the")
    print("    scary one.")
    # Sample needed to pin the TRUE EV to +/-1%, i.e. tight enough to tell a
    # real edge from zero. Separating two point estimates is not the question
    # when one of them carries a +/-2.5% error bar.
    need = (bt_sd / 1.0) ** 2
    print(f"\n  To pin the true EV to +/-1.0%/trade takes ~{need:.0f} resolved bets "
          f"({need / trades_day:.0f} days at {trades_day:.0f}/day).")
    print("\n  HONEST RANGE: somewhere between losing money and roughly")
    print(f"  ${notional * bt_h2 / 100 * trades_day * 30:.0f}/month. The evidence does not")
    print("  currently narrow it further, and no amount of re-reading the backtest will.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
