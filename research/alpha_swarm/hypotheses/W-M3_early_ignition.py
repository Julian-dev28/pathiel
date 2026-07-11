"""W-M3 — early ignition: volume-spike + small pop BEFORE the coin is a top
mover (the operator's "as early as possible" ask, conditioned on the earliest
signature that is not an already-refuted pure price pattern).

Rule (pre-registered, W-M0_engine.py conventions):
  signal at close of 1h bar i:
    bar dollar volume dv[i] >= 4 x mean(dv[i-24..i-1])   (volume ignition)
    1h return c[i]/c[i-1] - 1 >= +3%                     (direction)
    rolling-24h return r24[i] < +8%                      (NOT yet a top mover)
    trailing-24h dollar volume dv24[i] >= floor, floors {$5M, $20M}
  per-coin 24h dedup; fill at open[i+1]
  exits: full 13-policy grid; splits all / btc-up / btc-dn; costs 0..50bps
  MC null: same-coin random-time pools from W-M0 (same floor + regime)
  family: 2 x 13 x 3 = 78 cells -> Bonferroni alpha 6.41e-04

Output: scratchpad/W-M3_results.json + grid on stdout.
"""
from __future__ import annotations

import importlib.util
import json
import statistics
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "wm0", Path(__file__).resolve().parent / "W-M0_engine.py")
wm0 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wm0)

FLOORS = [5e6, 2e7]
VOL_X, RET1H, MAX_EXT = 4.0, 0.03, 0.08
FAMILY = len(FLOORS) * len(wm0.POLICIES) * 3
BONF = 0.05 / FAMILY


def signals_for(co, floor):
    idx = []
    for i in range(25, co.n - 1):
        if co.dv24[i] is None or co.dv24[i] < floor:
            continue
        if not co.contiguous(i - 24, i):
            continue
        trail_mean = (co.dv24[i] - co.dv[i]) / 24.0   # mean of dv[i-24..i-1]
        if trail_mean <= 0 or co.dv[i] < VOL_X * trail_mean:
            continue
        if co.c[i - 1] <= 0 or co.c[i] / co.c[i - 1] - 1.0 < RET1H:
            continue
        if co.r24[i] is None or co.r24[i] >= MAX_EXT:
            continue
        idx.append(i)
    return wm0.dedup_24h(idx, co)


def main() -> None:
    coins = wm0.load_coins()
    reg = wm0.Regime(coins["BTC"])
    print("building null pools ...", flush=True)
    pools = wm0.build_pools(coins, reg, floors=tuple(FLOORS))

    grid = {}
    seed = 5000
    for floor in FLOORS:
        sigs = []
        for name, co in coins.items():
            for i in signals_for(co, floor):
                sigs.append((name, i, co.t[i], reg.at(co.t[i])))
        per_policy = {p: [] for p in wm0.POLICIES}
        for name, i, t, rg in sigs:
            pr = wm0.policy_rets(coins[name], i)
            if pr is None:
                continue
            for p in wm0.POLICIES:
                per_policy[p].append((name, t, pr[p], rg))
        print(f"floor ${floor/1e6:.0f}M: {len(sigs)} ignition signals", flush=True)
        for p in wm0.POLICIES:
            allt = [(c, t, r) for c, t, r, _ in per_policy[p]]
            upt = [(c, t, r) for c, t, r, g in per_policy[p] if g == 1]
            dnt = [(c, t, r) for c, t, r, g in per_policy[p] if g == -1]
            for view, tr, rgv in (("all", allt, None), ("up", upt, 1),
                                  ("dn", dnt, -1)):
                seed += 1
                grid[f"f{int(floor/1e6)}M|{wm0.pol_name(p)}|{view}"] = \
                    wm0.cell_summary(tr, pools, p, floor, rgv, BONF, seed)

    out = wm0.SCRATCH / "W-M3_results.json"
    out.write_text(json.dumps({"meta": {"family": FAMILY, "bonf_alpha": BONF},
                               "grid": grid}))
    print(f"\n{'cell':32}{'n':>5}{'ev12':>9}{'ev25':>9}{'oos_h1':>9}"
          f"{'oos_h2':>9}{'p':>10}{'wire':>6}")
    for k, v in grid.items():
        if v["n"] == 0:
            print(f"{k:32}{0:>5}")
            continue
        print(f"{k:32}{v['n']:>5}{v['ev12']:>9}{v['ev25']:>9}"
              f"{str(v['oos25_h1']):>9}{str(v['oos25_h2']):>9}"
              f"{str(v['mc_p']):>10}{str(v['wire_eligible']):>6}")
    wires = [k for k, v in grid.items() if v.get("wire_eligible")]
    print("\nwire-eligible:", wires or "NONE")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
