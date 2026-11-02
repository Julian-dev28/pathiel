"""Fluke eval: does the labeller find a planted edge, and leave noise alone?

Synthetic trade streams through the same code the review runs
(analysis.slice_row: bootstrap CI, permutation p, label). Each run builds
one stream: a slice of `n` trades (tagged weekend) and `n_rest` other
trades, R drawn from a seeded distribution, and reads the label of the
weekend slice.

Gated scenarios (200 seeded runs each):

  planted_edge   slice R ~ N(+0.4, 0.75), n=40, rest N(0, 0.75) x 120
                 pass: labelled EDGE in >= 80% of runs
  planted_leak   the mirror, N(-0.4, 0.75): labelled LEAK in >= 80%
  noise          slice and rest both N(0, 0.75): EDGE or LEAK in <= 5%
  noise_bracket  bracket outcomes, -1R (2/3) or +2R (1/3), mean 0, the
                 shape real stops and targets give: EDGE or LEAK in <= 5%
  small_n        n=9 at N(+2, 0.5), far above anything real: EDGE in 0%
                 (the n >= 10 rule holds whatever the effect)

Why sd 0.75: at n=40 a +0.4R mean is only findable 80% of the time when
the spread per trade is about 0.75R or less (a 5% test's power at d=0.53).
The real ledger's spread is near 3R a trade. The power table (`--power`)
shows what that means: how often a +0.4R edge is found at each n and
spread. The review prints the same number for the live ledger ("a real
+0.4R edge needs about N trades").

    python3 -m services.review.evals.fluke_eval [--runs 200] [--power]

Writes /tmp/review-eval/fluke_eval.json. Exit 1 when a gate fails. No
network, no LLM.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from services.review import analysis as A
from services.review import stats as S

N_BOOT = 1000
N_PERM = 1000
OUT = "/tmp/review-eval/fluke_eval.json"

Draw = Callable[[random.Random], float]


def normal(mu: float, sd: float) -> Draw:
    return lambda rng: rng.gauss(mu, sd)


def bracket(win_r: float = 2.0, p_win: float = 1 / 3) -> Draw:
    return lambda rng: win_r if rng.random() < p_win else -1.0


def stream(seed: int, n: int, n_rest: int, slice_draw: Draw, rest_draw: Draw) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    out = []
    for i in range(n + n_rest):
        r = slice_draw(rng) if i < n else rest_draw(rng)
        out.append({"id": f"s{seed}-{i}", "desk": "oil", "side": "long", "weekend": i < n,
                    "r_mult": r, "net_usd": 10.0 * r, "tags": []})
    return out


def label_rate(runs: int, n: int, n_rest: int, slice_draw: Draw, rest_draw: Draw, seed0: int = 0,
               n_boot: int = N_BOOT, n_perm: int = N_PERM) -> Dict[str, float]:
    """Share of runs per label for the planted (weekend) slice."""
    counts = {lab: 0 for lab in S.LABELS}
    for i in range(runs):
        recs = stream(seed0 + i, n, n_rest, slice_draw, rest_draw)
        row = A.slice_row(recs, "week", "weekend", seed=seed0 + i, n_boot=n_boot, n_perm=n_perm)
        counts[row["label"]] += 1
    return {k: v / runs for k, v in counts.items()}


SCENARIOS: Dict[str, Dict[str, Any]] = {
    "planted_edge": {"n": 40, "n_rest": 120, "slice": normal(0.4, 0.75), "rest": normal(0.0, 0.75),
                     "measure": (S.EDGE,), "op": ">=", "threshold": 0.80},
    "planted_leak": {"n": 40, "n_rest": 120, "slice": normal(-0.4, 0.75), "rest": normal(0.0, 0.75),
                     "measure": (S.LEAK,), "op": ">=", "threshold": 0.80},
    "noise": {"n": 40, "n_rest": 120, "slice": normal(0.0, 0.75), "rest": normal(0.0, 0.75),
              "measure": (S.EDGE, S.LEAK), "op": "<=", "threshold": 0.05},
    "noise_bracket": {"n": 40, "n_rest": 120, "slice": bracket(), "rest": bracket(),
                      "measure": (S.EDGE, S.LEAK), "op": "<=", "threshold": 0.05},
    "small_n": {"n": 9, "n_rest": 120, "slice": normal(2.0, 0.5), "rest": normal(0.0, 0.75),
                "measure": (S.EDGE,), "op": "<=", "threshold": 0.0},
}


def run_scenario(name: str, runs: int, seed0: int = 0) -> Dict[str, Any]:
    sc = SCENARIOS[name]
    rates = label_rate(runs, sc["n"], sc["n_rest"], sc["slice"], sc["rest"], seed0)
    value = sum(rates[lab] for lab in sc["measure"])
    ok = value >= sc["threshold"] if sc["op"] == ">=" else value <= sc["threshold"]
    return {"scenario": name, "runs": runs, "rate": round(value, 4), "measure": "+".join(sc["measure"]),
            "gate": f"{sc['op']} {sc['threshold']}", "pass": ok, "labels": {k: round(v, 4) for k, v in rates.items()}}


def power_table(runs: int = 50, effect: float = 0.4, ns=(40, 80, 160, 320), sds=(0.75, 1.5, 3.0)
                ) -> List[Dict[str, Any]]:
    """How often a planted +`effect` R edge is labelled EDGE, by slice size
    and spread (rest = 3 x n at mean 0)."""
    out = []
    for sd in sds:
        for n in ns:
            rates = label_rate(runs, n, 3 * n, normal(effect, sd), normal(0.0, sd), seed0=10_000)
            out.append({"sd": sd, "n": n, "edge_rate": round(rates[S.EDGE], 3)})
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--power", action="store_true", help="also print the power table (about a minute)")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args(argv)
    t0 = time.time()
    results = [run_scenario(name, a.runs) for name in SCENARIOS]
    for r in results:
        print(f"{r['scenario']:<14} {r['measure']:<10} {r['rate']:>6.1%}  gate {r['gate']:<7} "
              f"{'PASS' if r['pass'] else 'FAIL'}")
    rep: Dict[str, Any] = {"results": results, "seconds": round(time.time() - t0, 1)}
    if a.power:
        rep["power"] = power_table()
        print("\npower: share of runs a +0.4R edge is labelled EDGE")
        print("sd \\ n   " + "  ".join(f"{n:>5}" for n in sorted({p['n'] for p in rep['power']})))
        for sd in sorted({p["sd"] for p in rep["power"]}):
            cells = [p for p in rep["power"] if p["sd"] == sd]
            print(f"{sd:<8} " + "  ".join(f"{c['edge_rate']:>5.0%}" for c in cells))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1)
    print(f"\n{a.out} ({rep['seconds']}s)")
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
