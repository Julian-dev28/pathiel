"""Flip-after-target test (2026-10-30).

Operator: "upon long close short down to long entry ... dont make any changes
just run a test on roi". The live rules (`desk_tick_sim.live_params()`) with
`flip_after_target` on: when a long's target fills, the next 15m tick sells
short at market, its target the long's entry, its stop past the 24h high.
Variants: the flip's R gate (1.0 / 1.5 / 2.0), its leverage (10 / 15 / 20x),
its stop buffer (0.75 / 1.5) and a time stop (none / 12h / 24h, the
"intraday" version).

Every variant runs on the journaled theses, the 30- and 50-day always-long
control and each half of the 50 days, and is compared with live (no flip) on
profit per day and per week (`strategy_sweep.per_day`). The flip trades are
also scored on their own (`flip_stats`).

    python -m services.strategies.evals.flip_test --theses-dir <dir> [--workers 8]
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.strategies.backtest.desk_tick_sim import Run, simulate
from services.strategies.evals import strategy_sweep as S
from services.strategies.evals.desk_tick_backtest import always_direction, journaled_direction
from services.strategies.evals.scenario_sweep import load_data

SERIES = ("journaled", "control30", "control50", "control50a", "control50b")
FLIP_MIN_R = (1.0, 1.5, 2.0)
FLIP_LEVERAGE = (10.0, 15.0, 20.0)
FLIP_BUFFER = (0.75, 1.5)
FLIP_TIME = (None, 12.0, 24.0)


def variants() -> Dict[str, Dict[str, Any]]:
    """live (no flip) + every flip combination, as Params overrides."""
    out: Dict[str, Dict[str, Any]] = {"live (no flip)": {}}
    for r, lev, buf, ts in itertools.product(FLIP_MIN_R, FLIP_LEVERAGE, FLIP_BUFFER, FLIP_TIME):
        name = f"flip {r:g}R {lev:g}x buf {buf:g}" + (f" {ts:g}h stop" if ts else "")
        out[name] = {"flip_after_target": True, "flip_min_r": r, "flip_leverage": lev,
                     "flip_stop_buffer": buf, "flip_time_stop_hours": ts}
    return out


def flip_stats(run: Run) -> Dict[str, Any]:
    """The flip trades alone: count, won, net dollars, total R, exits."""
    flips = [t for t in run.trades if t.is_flip]
    rs = [t.r_multiple for t in flips if t.r_multiple is not None]
    exits: Dict[str, int] = {}
    for t in flips:
        exits[t.exit_reason or "?"] = exits.get(t.exit_reason or "?", 0) + 1
    return {"flips": len(flips), "won": sum(1 for t in flips if t.net_pnl_usd > 0),
            "net_usd": round(sum(t.net_pnl_usd for t in flips), 2), "total_r": round(sum(rs), 2),
            "exits": dict(sorted(exits.items())),
            "refused": {k: v for k, v in run.refused.items() if k.startswith("flip_")}}


_DATA: Dict[str, Any] = {}


def _init(data: Dict[str, Any]) -> None:
    _DATA.clear()
    _DATA.update(data)
    S._init(data)


def _run(job: Tuple[str, Dict[str, Any], str]) -> Tuple[str, str, Dict[str, Any]]:
    key, overrides, series = job
    kind, days, start_ms, end_ms = S._series(series)
    w = _DATA["windows"][days]
    c15 = [c for c in w["c15"] if start_ms <= c.t < end_ms]
    c1h = [c for c in w["c1h"] if c.t < end_ms]
    direction = journaled_direction(_DATA["theses"]) if kind == "journaled" else always_direction("long")
    run = simulate(c15, c1h, direction, S.params_with(overrides), w["funding"])
    out = S.per_day(run, start_ms, end_ms)
    out["flip"] = flip_stats(run)
    return key, series, out


def evaluate(named: Dict[str, Dict[str, Any]], series: Sequence[str], data, workers: int):
    jobs = [(k, ov, s) for k, ov in named.items() for s in series]
    rows: Dict[str, Dict[str, Any]] = {k: {"overrides": ov} for k, ov in named.items()}
    if workers <= 1:
        _init(data)
        results = [_run(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(data,)) as ex:
            results = list(ex.map(_run, jobs, chunksize=8))
    for key, s, summ in results:
        rows[key][s] = summ
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--theses-dir", required=True, help="READ ONLY journaled-thesis directory")
    ap.add_argument("--cache-dir", default="/tmp/strategy-sweep/cache")
    ap.add_argument("--out", default="/tmp/flip-test/report.json")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args(argv)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    t0 = time.time()
    data = load_data(a.cache_dir, a.theses_dir)
    data["first_thesis_ms"] = min(th.created_at_ms for th in data["theses"])
    rows = evaluate(variants(), SERIES, data, a.workers)
    live = rows["live (no flip)"]
    ranked = sorted(rows.items(), key=lambda kv: kv[1]["journaled"]["daily_pct"], reverse=True)
    beats_all = [k for k, r in rows.items() if k != "live (no flip)"
                 and all(r[s]["daily_pct"] > live[s]["daily_pct"] for s in ("journaled", "control30", "control50"))]
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
              "seconds": round(time.time() - t0, 1), "n_variants": len(rows),
              "journaled_days": live["journaled"]["days"], "rows": rows,
              "ranked": [k for k, _ in ranked], "beat_live_on_all_three": beats_all}
    with open(a.out, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    print(f"journaled days {live['journaled']['days']}; {len(rows) - 1} flip variants; "
          f"{len(beats_all)} beat live on theses AND both controls")
    print(f"{'setup':<34} | {'j %/d':>6} {'%/wk':>5} {'$end':>7} {'flips':>5} {'flip $':>7} {'dd':>4} |"
          f" {'c30':>6} {'c50':>6} | {'50a':>6} {'50b':>6}")
    for k, r in [("live (no flip)", live)] + [kv for kv in ranked if kv[0] != "live (no flip)"][:15]:
        j = r["journaled"]
        print(f"{k:<34} | {j['daily_pct']:6.2f} {j['weekly_pct']:5.0f} {j['final_equity']:7.2f} "
              f"{j['flip']['flips']:>5} {j['flip']['net_usd']:7.2f} {j['max_dd_pct']:3.0f}% |"
              f" {r['control30']['daily_pct']:6.2f} {r['control50']['daily_pct']:6.2f} |"
              f" {r['control50a']['daily_pct']:6.2f} {r['control50b']['daily_pct']:6.2f}")
    print(f"\nreport: {a.out} ({report['seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
