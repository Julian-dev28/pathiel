#!/usr/bin/env python
"""W-STB1: peg reversion on HL stablecoin/USDC spot pairs, as pre-registered in
findings/W-STB1_stable_peg_reversion.md. Reads W-STB1_cache_1h.json; no network.
Writes W-STB1_results.json.

`simulate` is pure and is what tests/test_wh_ladder.py drives.
"""
from __future__ import annotations

import json
import os
import random
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
THETAS = (0.0010, 0.0025, 0.0050)
HOLD_BARS = 168
N_NULL = 2000
ALPHA = 0.05 / 12
STABLE_PAIRS = ("USDT0", "USDE", "USDH")          # scaleIfStablePair = 0.2
COSTS = {
    "doctrine": {"in": 0.00125, "out": 0.00125, "forced": 0.00125},
    "measured_stable": {"in": 0.00008, "out": 0.00008, "forced": 0.00014},
    "measured_full": {"in": 0.0004, "out": 0.0004, "forced": 0.0007},
}


def measured(pair: str) -> dict:
    return COSTS["measured_stable"] if pair in STABLE_PAIRS else COSTS["measured_full"]


def simulate(bars, theta, cost, start=0, end=None, rng=None, lookback=HOLD_BARS):
    """One pair, one position at a time.

    rng None: the bid rests at 1 - theta and the exit ask at 1.0 (the peg).
    rng given: the matched null. Each new bid anchors on the close of a random
    bar from the previous `lookback` bars instead of the peg.
    Bars are [t, o, h, l, c, v]. A zero-volume bar fills nothing.
    """
    end = len(bars) if end is None else end
    trades = []
    i = start
    anchor = None
    entry_i = entry_px = None
    while i < end:
        if entry_i is None:
            if anchor is None:
                if rng is None:
                    anchor = 1.0
                else:
                    lo = max(start, i - lookback)
                    if i <= lo:
                        i += 1
                        continue
                    anchor = bars[rng.randrange(lo, i)][4]
            t, o, h, l, c, v = bars[i]
            if v > 0 and l < anchor - theta:
                entry_i, entry_px = i, anchor - theta
            i += 1
            continue
        t, o, h, l, c, v = bars[i]
        if v > 0 and h > anchor:
            trades.append(_trade(entry_i, i, entry_px, anchor, cost["in"] + cost["out"], "target"))
            entry_i = anchor = None
        elif i - entry_i >= HOLD_BARS:
            trades.append(_trade(entry_i, i, entry_px, c, cost["in"] + cost["forced"], "timeout"))
            entry_i = anchor = None
        i += 1
    if entry_i is not None:
        c = bars[end - 1][4]
        trades.append(_trade(entry_i, end - 1, entry_px, c, cost["in"] + cost["forced"], "open_at_end"))
    return {"total": sum(x["ret"] for x in trades), "trades": trades}


def _trade(i0, i1, px_in, px_out, cost, how):
    return {"i_in": i0, "i_out": i1, "px_in": px_in, "px_out": px_out,
            "ret": px_out / px_in - 1 - cost, "how": how}


def summarize(run):
    tr = run["trades"]
    return {"total": run["total"], "n": len(tr),
            "wins": sum(1 for x in tr if x["ret"] > 0),
            "by_exit": {k: sum(1 for x in tr if x["how"] == k)
                        for k in ("target", "timeout", "open_at_end")},
            "worst": min((x["ret"] for x in tr), default=0.0),
            "mean_hold_h": (sum(x["i_out"] - x["i_in"] for x in tr) / len(tr)) if tr else 0.0}


_CTX = ()


def _init(*ctx):
    global _CTX
    _CTX = ctx


def _draw(seed):
    bars, theta, cost = _CTX
    return simulate(bars, theta, cost, rng=random.Random(seed))["total"]


def main():
    with open(os.path.join(HERE, "W-STB1_cache_1h.json")) as f:
        cache = json.load(f)
    # drop the still-forming last bar
    candles = {p: v[:-1] for p, v in cache["candles"].items()}
    out = {"span": {}, "cells": {}}
    for pair, bars in candles.items():
        mid = len(bars) // 2
        out["span"][pair] = [bars[0][0], bars[-1][0], len(bars)]
        for theta in THETAS:
            cell = f"{pair}_{round(theta * 1e4)}bps"
            res = {}
            for tier, cost in (("doctrine", COSTS["doctrine"]), ("measured", measured(pair))):
                full = simulate(bars, theta, cost)
                h1 = simulate(bars, theta, cost, 0, mid)
                h2 = simulate(bars, theta, cost, mid, len(bars))
                with Pool(initializer=_init, initargs=(bars, theta, cost)) as pool:
                    null = sorted(pool.map(_draw, range(N_NULL), chunksize=50))
                p = sum(1 for v in null if v >= full["total"]) / N_NULL
                res[tier] = {"full": summarize(full), "half1": summarize(h1), "half2": summarize(h2),
                             "null_median": null[N_NULL // 2], "null_p95": null[N_NULL * 19 // 20],
                             "p": p}
            d = res["doctrine"]
            res["verdict"] = ("VALIDATED" if d["full"]["total"] > 0 and d["half1"]["total"] > 0
                              and d["half2"]["total"] > 0 and d["p"] < ALPHA else "REFUTED")
            m = res["measured"]
            res["passes_at_measured_fees"] = (m["full"]["total"] > 0 and m["half1"]["total"] > 0
                                              and m["half2"]["total"] > 0 and m["p"] < ALPHA)
            out["cells"][cell] = res
            for tier in ("doctrine", "measured"):
                r = res[tier]
                f_ = r["full"]
                print(f"{cell:<14} {tier:<9} total {f_['total'] * 1e4:+8.1f}bps  n={f_['n']:>3} "
                      f"(target {f_['by_exit']['target']}, timeout {f_['by_exit']['timeout']}, "
                      f"open {f_['by_exit']['open_at_end']})  halves {r['half1']['total'] * 1e4:+.1f}/"
                      f"{r['half2']['total'] * 1e4:+.1f}  worst {f_['worst'] * 1e4:+.1f}  "
                      f"null med {r['null_median'] * 1e4:+.1f} p={r['p']:.4f}")
            print(f"{'':<14} -> {res['verdict']}"
                  + ("  (passes at measured fees)" if res["passes_at_measured_fees"] else ""))
    with open(os.path.join(HERE, "W-STB1_results.json"), "w") as f:
        json.dump(out, f, indent=1)
    print("wrote W-STB1_results.json")


if __name__ == "__main__":
    main()
