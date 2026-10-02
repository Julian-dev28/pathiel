#!/usr/bin/env python
"""W-WH2 robustness: where does the one surviving cell (A_majors, L=1) break?

NOT a selection step. The verdict in W-WH2_results.json stands on the frozen
parameters alone; nothing here may replace them. This answers five questions
about that verdict:

  1. Is it one coin?        leave-one-coin-out terminal multiple + null p
  2. Is it a knife edge?    3x3x3 grid around the frozen trigger / gap / TP
  3. Is it the leverage?    L = 1, 1.5, 2, 2.5, 3 on the frozen rule
  4. Is it the calendar?    the rule started on each year's first day
  5. Can Pathiel run it?    the executor's mandatory backup stop, modelled

Writes W-WH2_robustness.json.
"""
import importlib.util
import json
import os
import random
import time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("wh2", os.path.join(HERE, "W-WH2_ladder_backtest.py"))
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)

N_NULL = 1000
_CTX = ()


def _init(*ctx):
    global _CTX
    _CTX = ctx


def _draw(seed):
    days, series, max_lev, params, lev, probs = _CTX
    return bt.simulate(days, series, max_lev, params, lev, entry_prob=probs,
                       rng=random.Random(10_000 + seed))["terminal"]


def with_null(days, series, max_lev, params, lev):
    run = bt.simulate(days, series, max_lev, params, lev)
    counts = {c: 0 for c in series}
    for x in run["ladders"]:
        counts[x["coin"]] += 1
    probs = {c: counts[c] / run["flat_days"][c] if run["flat_days"][c] else 0.0 for c in series}
    with Pool(initializer=_init, initargs=(days, series, max_lev, params, lev, probs)) as pool:
        null = sorted(pool.map(_draw, range(N_NULL), chunksize=50))
    p = sum(1 for v in null if v >= run["terminal"]) / N_NULL
    return {"terminal": run["terminal"], "max_dd": run["max_dd"], "liquidated": bt.ts(run["liquidated"]),
            "ladders": len(run["ladders"]), "null_median": null[N_NULL // 2], "p": p}


def main():
    params = bt.frozen_params()
    cache = json.load(open(os.path.join(HERE, "W-WH2_cache_daily.json")))
    today = int(time.time() * 1000) // bt.DAY * bt.DAY
    candles = {c: [b for b in v if b[0] < today] for c, v in cache["candles"].items()}
    max_lev = cache["max_leverage"]
    coins = bt.UNIVERSES["A_majors"]
    out = {"params": params}

    print("== 1. leave one coin out (A_majors, L=1)")
    out["leave_one_out"] = {}
    for drop in coins:
        days, series = bt.align(candles, [c for c in coins if c != drop])
        r = with_null(days, series, max_lev, params, 1)
        out["leave_one_out"][drop] = r
        print(f"  without {drop:<4} terminal {r['terminal']:.3f}x  maxDD {r['max_dd']:+.1%}  "
              f"null median {r['null_median']:.3f}x  p={r['p']:.3f}")
    for only in coins:
        days, series = bt.align(candles, [only])
        r = with_null(days, series, max_lev, params, 1)
        out["leave_one_out"][f"only_{only}"] = r
        print(f"  only    {only:<4} terminal {r['terminal']:.3f}x  maxDD {r['max_dd']:+.1%}  "
              f"null median {r['null_median']:.3f}x  p={r['p']:.3f}")

    days, series = bt.align(candles, coins)
    print("\n== 2. parameter neighbourhood (A_majors, L=1): terminal x / null p")
    out["grid"] = []
    for trig in (-0.15, params["trigger_dd"], -0.30):
        for gap in (-0.05, params["gap"], -0.10):
            row = []
            for tp in (0.03, params["tp"], 0.08):
                p2 = dict(params, trigger_dd=trig, gap=gap, tp=tp)
                r = with_null(days, series, max_lev, p2, 1)
                out["grid"].append(dict(p2, **r))
                row.append(f"tp{tp:+.0%} {r['terminal']:.2f}x p{r['p']:.3f}")
            print(f"  trig {trig:+.0%} gap {gap:+.1%}: " + "  |  ".join(row))

    print("\n== 3. leverage (A_majors, frozen rule)")
    out["leverage"] = {}
    for lev in (1, 1.5, 2, 2.5, 3):
        r = bt.simulate(days, series, max_lev, params, lev)
        out["leverage"][str(lev)] = {"terminal": r["terminal"], "max_dd": r["max_dd"],
                                     "liquidated": bt.ts(r["liquidated"])}
        print(f"  L={lev:<4} terminal {r['terminal']:.3f}x  maxDD {r['max_dd']:+.1%}  liquidated {bt.ts(r['liquidated'])}")

    print("\n== 4. start year (A_majors, L=1, run to today)")
    out["start_year"] = {}
    for year in range(2021, 2026):
        start = next(i for i, t in enumerate(days) if bt.ts(t) >= f"{year}-01-01")
        r = bt.simulate(days, series, max_lev, params, 1, start=start)
        bh = bt.buy_and_hold(days, series, start, len(days))
        out["start_year"][year] = {"terminal": r["terminal"], "max_dd": r["max_dd"], "buy_and_hold": bh}
        print(f"  from {year}: terminal {r['terminal']:.3f}x  maxDD {r['max_dd']:+.1%}  | buy&hold 1x {bh:.3f}x")

    print("\n== 5. the executor's backup stop (A_majors, L=1): can Pathiel run it as tested?")
    out["backup_stop"] = {}
    for frac in (None, 0.60, 0.40, 0.25):
        r = bt.simulate(days, series, max_lev, params, 1, stop_frac=frac)
        stops = [x for x in r["ladders"] if x["result"] == "stop"]
        out["backup_stop"][str(frac)] = {"terminal": r["terminal"], "max_dd": r["max_dd"],
                                         "stopped": len(stops), "ladders": len(r["ladders"]),
                                         "stopped_detail": [dict(x, open=bt.ts(x["open"]), close=bt.ts(x["close"])) for x in stops]}
        label = "none (tested)" if frac is None else f"-{frac:.0%} vs avg entry"
        print(f"  stop {label:<22} terminal {r['terminal']:.3f}x  maxDD {r['max_dd']:+.1%}  "
              f"stopped out {len(stops)} of {len(r['ladders'])}: "
              + ", ".join(f"{x['coin']} {bt.ts(x['close'])}" for x in stops[:6]))

    with open(os.path.join(HERE, "W-WH2_robustness.json"), "w") as f:
        json.dump(out, f, indent=1)
    print("\nwrote W-WH2_robustness.json")


if __name__ == "__main__":
    main()
