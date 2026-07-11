"""W-M2 — day-2 runner persistence (SYRUP did +13% then +15%: real or memory?).

Rule (pre-registered, W-M0_engine.py conventions):
  UTC day d (complete: bars cover the calendar day contiguously, >=20 bars):
    day return = last_close(d) / last_close(d-1) - 1 in [+8%, +20%]
    day dollar volume(d) >= $5M
  -> enter LONG at the OPEN of day d+1's first 1h bar (fill = next bar open,
     since the signal bar is day d's last bar).
  exits: hold {24,48}h x stop {5,8,15}% + KAITO trail = 7 policies
  splits: all / btc20d-up / btc20d-dn; OOS halves; costs 0..50bps
  MC null: same-coin random-DAY entry (first bar of every eligible-volume day,
  no move condition), >=2000 iters (escalated 100k when p<0.005)
  family: 7 x 3 = 21 cells -> Bonferroni alpha 2.38e-03

Output: scratchpad/W-M2_results.json + grid on stdout.
"""
from __future__ import annotations

import importlib.util
import json
import statistics
from pathlib import Path

import numpy as np

_spec = importlib.util.spec_from_file_location(
    "wm0", Path(__file__).resolve().parent / "W-M0_engine.py")
wm0 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wm0)

DAY_MS = 86_400_000
POLICIES = [(h, s) for h in (24, 48) for s in wm0.STOPS] + [("trail", None)]
FAMILY = len(POLICIES) * 3
BONF = 0.05 / FAMILY
FLOOR = 5e6
BAND = (0.08, 0.20)


def day_table(co) -> list[dict]:
    """Per complete UTC day: last-bar index, close, dollar volume."""
    days: dict[int, dict] = {}
    for i in range(co.n):
        d = co.t[i] // DAY_MS
        rec = days.setdefault(d, {"first": i, "last": i, "dv": 0.0, "nb": 0})
        rec["last"] = i
        rec["dv"] += co.dv[i]
        rec["nb"] += 1
    out = []
    for d in sorted(days):
        r = days[d]
        # complete day: >=20 bars and contiguous first..last
        if r["nb"] >= 20 and co.contiguous(r["first"], r["last"]):
            out.append({"day": d, "last_i": r["last"], "dv": r["dv"],
                        "close": co.c[r["last"]]})
    return out


def main() -> None:
    coins = wm0.load_coins()
    reg = wm0.Regime(coins["BTC"])

    # signals + null pool (entry at every eligible day's last bar -> next open)
    sigs = []                                     # (coin, i_sig, t, regime)
    pools: dict[str, dict] = {}                   # coin -> policy -> rows
    for name, co in coins.items():
        dt_ = day_table(co)
        per = {p: [] for p in POLICIES}
        for k in range(1, len(dt_)):
            prev, cur = dt_[k - 1], dt_[k]
            if cur["day"] - prev["day"] != 1 or prev["close"] <= 0:
                continue
            i_sig = cur["last_i"]                  # signal = day d's last bar
            if cur["dv"] < FLOOR:
                continue
            pr = wm0.policy_rets(co, i_sig)
            if pr is None:
                continue
            rg = reg.at(co.t[i_sig])
            for p in POLICIES:                    # null pool: every liquid day
                per[p].append((co.t[i_sig], pr[p], cur["dv"], rg))
            dret = cur["close"] / prev["close"] - 1.0
            if BAND[0] <= dret <= BAND[1]:
                sigs.append((name, i_sig, co.t[i_sig], rg, dret))
        pools[name] = per

    per_policy = {p: [] for p in POLICIES}
    for name, i, t, rg, _ in sigs:
        pr = wm0.policy_rets(coins[name], i)
        for p in POLICIES:
            per_policy[p].append((name, t, pr[p], rg))

    grid = {}
    seed = 9000
    for p in POLICIES:
        allt = [(c, t, r) for c, t, r, _ in per_policy[p]]
        upt = [(c, t, r) for c, t, r, g in per_policy[p] if g == 1]
        dnt = [(c, t, r) for c, t, r, g in per_policy[p] if g == -1]
        for view, tr, rgv in (("all", allt, None), ("up", upt, 1),
                              ("dn", dnt, -1)):
            seed += 1
            grid[f"{wm0.pol_name(p)}|{view}"] = wm0.cell_summary(
                tr, pools, p, FLOOR, rgv, BONF, seed)

    out = wm0.SCRATCH / "W-M2_results.json"
    out.write_text(json.dumps({"meta": {"family": FAMILY, "bonf_alpha": BONF,
                                        "n_signals": len(sigs)},
                               "grid": grid}))
    print(f"signals: {len(sigs)} coin-days in band +8..20% >= $5M")
    print(f"{'cell':28}{'n':>5}{'ev12':>9}{'ev25':>9}{'oos_h1':>9}"
          f"{'oos_h2':>9}{'p':>10}{'wire':>6}")
    for k, v in grid.items():
        if v["n"] == 0:
            print(f"{k:28}{0:>5}")
            continue
        print(f"{k:28}{v['n']:>5}{v['ev12']:>9}{v['ev25']:>9}"
              f"{str(v['oos25_h1']):>9}{str(v['oos25_h2']):>9}"
              f"{str(v['mc_p']):>10}{str(v['wire_eligible']):>6}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
