"""W-M1 — extension-band momentum entry, big-data re-run (208d x 40 coins x 1h).

Hypothesis (operator instinct): going LONG the first time a liquid coin's
rolling-24h return crosses +B% is EV+, at least in some band / regime.

Rule (pre-registered in W-M0_engine.py):
  signal at close of bar i:  r24[i] >= B  AND  r24[i-1] < B  (fresh crossing)
                             AND trailing-24h dollar volume >= floor
  bands B in {6,8,10,12,15,20,25,30}%, floors {$5M, $20M}
  per-coin 24h signal dedup; fill at open[i+1]
  exits: 13-policy grid (holds x stops + KAITO trail); costs 0..50bps
  splits: all / btc20d-up / btc20d-down; OOS halves; same-coin MC null
  family for Bonferroni: 8 x 2 x 13 x 3 = 624 cells -> alpha 8.01e-05

Output: scratchpad/W-M1_results.json + condensed grid on stdout.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "wm0", Path(__file__).resolve().parent / "W-M0_engine.py")
wm0 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wm0)
(load_coins, Regime, build_pools, policy_rets, cell_summary, dedup_24h,
 pol_name, POLICIES, POOL_STRIDE, SCRATCH) = (
    wm0.load_coins, wm0.Regime, wm0.build_pools, wm0.policy_rets,
    wm0.cell_summary, wm0.dedup_24h, wm0.pol_name, wm0.POLICIES,
    wm0.POOL_STRIDE, wm0.SCRATCH)

BANDS = [0.06, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30]
FLOORS = [5e6, 2e7]
FAMILY = len(BANDS) * len(FLOORS) * 13 * 3
BONF = 0.05 / FAMILY


def signals_for(co, band, floor):
    idx = []
    for i in range(25, co.n - 1):
        if (co.r24[i] is not None and co.r24[i - 1] is not None
                and co.dv24[i] is not None and co.dv24[i] >= floor
                and co.r24[i] >= band and co.r24[i - 1] < band):
            idx.append(i)
    return dedup_24h(idx, co)


def main() -> None:
    coins = load_coins()
    reg = Regime(coins["BTC"])
    print(f"building null pools (stride {POOL_STRIDE}) ...", flush=True)
    pools = build_pools(coins, reg, floors=tuple(FLOORS))

    results = {"meta": {"family": FAMILY, "bonf_alpha": BONF}}
    grid = {}
    seed = 0
    for band in BANDS:
        for floor in FLOORS:
            # collect signals once per (band, floor); shared across policies
            sigs = []                        # (coin, i, t, regime)
            for name, co in coins.items():
                for i in signals_for(co, band, floor):
                    sigs.append((name, i, co.t[i], reg.at(co.t[i])))
            per_policy_trades = {p: [] for p in POLICIES}
            for name, i, t, rg in sigs:
                pr = policy_rets(coins[name], i)
                if pr is None:
                    continue
                for p in POLICIES:
                    per_policy_trades[p].append((name, t, pr[p], rg))
            for p in POLICIES:
                allt = [(c, t, r) for c, t, r, _ in per_policy_trades[p]]
                upt = [(c, t, r) for c, t, r, g in per_policy_trades[p] if g == 1]
                dnt = [(c, t, r) for c, t, r, g in per_policy_trades[p] if g == -1]
                for view, tr, rgv in (("all", allt, None), ("up", upt, 1),
                                      ("dn", dnt, -1)):
                    seed += 1
                    cell = cell_summary(tr, pools, p, floor, rgv, BONF, seed)
                    grid[f"B{int(band*100)}|f{int(floor/1e6)}M|"
                         f"{pol_name(p)}|{view}"] = cell
            done = sum(1 for _ in grid)
            print(f"  band {band:+.0%} floor ${floor/1e6:.0f}M done "
                  f"(n_signals={len(sigs)}, cells so far {done})", flush=True)
    results["grid"] = grid

    wires = {k: v for k, v in grid.items() if v.get("wire_eligible")}
    results["wire_eligible"] = sorted(wires)
    out = SCRATCH / "W-M1_results.json"
    out.write_text(json.dumps(results))
    print(f"\nwrote {out}")

    # condensed EV25 grid (view=all), per floor
    for floor in FLOORS:
        print(f"\n=== EV25 (%/trade), view=all, floor ${floor/1e6:.0f}M ===")
        hdr = "band  " + "".join(f"{pol_name(p):>12}" for p in POLICIES)
        print(hdr)
        for band in BANDS:
            row = f"+{int(band*100):>3}% "
            for p in POLICIES:
                c = grid[f"B{int(band*100)}|f{int(floor/1e6)}M|{pol_name(p)}|all"]
                row += f"{c.get('ev25', float('nan')):>12}"
            print(row)
    print(f"\nwire-eligible cells ({len(wires)}):")
    for k in sorted(wires):
        v = wires[k]
        print(f"  {k}: n={v['n']} ev25={v['ev25']} oos=({v['oos25_h1']},"
              f"{v['oos25_h2']}) p={v['mc_p']}")
    if not wires:
        print("  NONE")


if __name__ == "__main__":
    main()
