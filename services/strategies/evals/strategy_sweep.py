"""Strategy sweep for the oil desk, ranked by profit per day, then per week (2026-10-30).

Operator: "expand on various High ROI strategies, anything im failing to
mention here should be tested ... think the most profit PER DAY, THEN PER
WEEK". Starts from the live rules (`desk_tick_sim.live_params()`: 0.5%
closer target, 0.75 stop buffer, limit target, fixed stop, 20x, re-entry)
and tests what the 2026-10-29 sweep did not:

  stage 1  one change at a time from live: fixed-R targets, wider offsets,
           scale-outs (with and without breakeven), time stops, market
           entries, entry/target lookbacks, how far from the edge an entry
           may rest, the R gate, wider stop buffers, trading-hour windows,
           the 1h clock, lower leverage, re-entry off, moving stops.
  stage 2  every combination of the stage-1 values that beat live per day
           on at least two of the three series (journaled theses, 30-day
           and 50-day always-long control), ranked by journaled profit per
           day among the combos that also beat live on BOTH controls (the
           journaled run is 9 long theses in a rising market: on its own it
           rewards anything that buys more).
  stage 3  the top 10 again under stress: double slippage, the 1h clock,
           and each half of the 50-day control on its own.
  stage 4  what each part of the winner adds: every subset of its changes,
           and the winner at 15x and 10x, on all five series.

Profit per day is the compounding daily rate over the days the rules could
trade (journaled: from the first thesis; control: the whole window), so
"per day" and "per week" are comparable across series of different length:
  daily_pct  = (final / start) ** (1 / days) - 1
  weekly_pct = (1 + daily_pct) ** 7 - 1
plus the plain dollars a day from $64, the share of green days and weeks,
and the worst day and week, from the end-of-UTC-day marks.

Read-only on everything live: candles and funding come through the eval
cache, theses are read from the journal directory and never written.

    python -m services.strategies.evals.strategy_sweep --theses-dir <dir> [--workers 8]
"""
from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.strategies.backtest.desk_tick_sim import Params, Run, live_params, simulate
from services.strategies.evals.desk_tick_backtest import DAY_MS, always_direction, journaled_direction
from services.strategies.evals.scenario_sweep import load_data

START = 64.0
WEEK_DAYS = 7

# Stage 1: one change at a time from live_params(). Each value is a dict of
# Params overrides; the key names the change in the report.
STAGE1: Dict[str, List[Dict[str, Any]]] = {
    "target_r": [{"target_r": r, "target_offset_frac": 0.0} for r in (1.5, 2.0, 2.5, 3.0)],
    "offset": [{"target_offset_frac": f} for f in (0.0, 0.0025, 0.0075, 0.01, 0.015)],
    "partial": [{"partial_frac": f, "partial_r": r, "partial_breakeven": be}
                for f, r, be in ((0.5, 1.0, False), (0.5, 1.0, True), (0.33, 1.0, False),
                                 (0.5, 0.75, False), (0.5, 0.75, True), (0.5, 1.5, False))],
    "time_stop": [{"time_stop_hours": h} for h in (6.0, 12.0, 24.0, 48.0)],
    "entry_mode": [{"entry_mode": "market"}],
    "entry_lookback": [{"entry_lookback_h": h} for h in (4, 6, 12, 24)],
    "target_lookback": [{"target_lookback_h": h} for h in (12, 48)],
    "rest_frac": [{"atr_rest_frac": f} for f in (0.5, 1.0)],
    "min_r": [{"min_r": r} for r in (1.0, 1.25, 2.0)],
    "buffer": [{"stop_buffer": b} for b in (0.5, 1.0, 1.25)],
    "session": [{"session_utc": s} for s in ((7, 21), (12, 21), (0, 12))],
    "clock": [{"tick_minutes": 60}],
    "leverage": [{"leverage": x} for x in (10.0, 15.0)],
    "reentry": [{"reentry_after_target": False}],
    "stop_mode": [{"stop_mode": "breakeven"}, {"stop_mode": "trail"}],
}
STRESS = {"slip_x2": {"slippage": 0.0006}, "clock_1h": {"tick_minutes": 60}}


def base_params() -> Params:
    return dataclasses.replace(live_params(), starting_equity=START, size_slippage=True)


def params_with(overrides: Dict[str, Any]) -> Params:
    ov = dict(overrides)
    if isinstance(ov.get("session_utc"), list):
        ov["session_utc"] = tuple(ov["session_utc"])
    return dataclasses.replace(base_params(), **ov)


# ── per-day / per-week (pure, gate-tested) ─────────────────────────────────

def daily_marks(curve: Sequence[Tuple[int, float]], start_ms: int) -> List[Tuple[int, float]]:
    """The last mark of each UTC day at or after `start_ms`, oldest first."""
    by_day: Dict[int, float] = {}
    for t, mark in curve:
        if t >= start_ms:
            by_day[t // DAY_MS] = mark
    return sorted(by_day.items())


def period_returns(marks: Sequence[Tuple[int, float]], start_equity: float, every: int = 1) -> List[float]:
    """Returns over consecutive blocks of `every` daily marks (1 = days,
    7 = weeks), the first measured from `start_equity`. A trailing partial
    block counts as its own period. A block that starts at zero equity (the
    account is gone) returns 0 from then on."""
    out: List[float] = []
    prev = start_equity
    for i in range(every - 1, len(marks) + every - 1, every):
        mark = marks[min(i, len(marks) - 1)][1]
        out.append(mark / prev - 1.0 if prev > 0 else 0.0)
        prev = mark
        if i >= len(marks) - 1:
            break
    return out


def per_day(run: Run, start_ms: int, end_ms: int, start_equity: float = START) -> Dict[str, Any]:
    """The per-day and per-week numbers the ranking reads."""
    days = max((end_ms - start_ms) / DAY_MS, 1e-9)
    final = run.final_equity
    daily_pct = (final / start_equity) ** (1.0 / days) - 1.0 if final > 0 else -1.0
    marks = daily_marks(run.equity_curve, start_ms)
    d = period_returns(marks, start_equity, 1)
    w = period_returns(marks, start_equity, WEEK_DAYS)
    rs = [t.r_multiple for t in run.trades if t.r_multiple is not None]
    return {
        "days": round(days, 2),
        "final_equity": round(final, 2),
        "daily_pct": round(daily_pct * 100, 3),
        "weekly_pct": round(((1.0 + daily_pct) ** WEEK_DAYS - 1.0) * 100, 2),
        "usd_per_day": round((final - start_equity) / days, 2),
        "green_days_pct": round(100 * sum(1 for x in d if x > 0) / len(d), 1) if d else None,
        "worst_day_pct": round(min(d) * 100, 2) if d else None,
        "green_weeks_pct": round(100 * sum(1 for x in w if x > 0) / len(w), 1) if w else None,
        "worst_week_pct": round(min(w) * 100, 2) if w else None,
        "trades": len(run.trades),
        "win_rate": round(sum(1 for t in run.trades if t.net_pnl_usd > 0) / len(run.trades), 3) if run.trades else None,
        "total_r": round(sum(rs), 2),
        "max_dd_pct": round(run.max_drawdown_pct * 100, 1),
        "liquidated": run.liquidated,
        "exits": dict(sorted({r: sum(1 for t in run.trades if t.exit_reason == r)
                              for r in {t.exit_reason for t in run.trades}}.items())),
    }


SERIES = ("journaled", "control30", "control50")


def wins(row: Dict[str, Any], base: Dict[str, Any]) -> int:
    """How many of the three series `row` beats `base` on, per day."""
    return sum(1 for s in SERIES if row[s]["daily_pct"] > base[s]["daily_pct"])


def beats(row: Dict[str, Any], base: Dict[str, Any]) -> bool:
    """A stage-1 change goes on to stage 2 when it beats live on two of three."""
    return wins(row, base) >= 2


def robust(row: Dict[str, Any], base: Dict[str, Any]) -> bool:
    """A stage-2 combo is ranked only if it beats live on both controls."""
    return all(row[s]["daily_pct"] > base[s]["daily_pct"] for s in ("control30", "control50"))


def subsets(overrides: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Every subset of a combo's changes, named by the keys it keeps
    ("live" for none), smallest first."""
    keys = sorted(overrides)
    out: Dict[str, Dict[str, Any]] = {}
    for n in range(len(keys) + 1):
        for ks in itertools.combinations(keys, n):
            out["+".join(ks) or "live"] = {k: overrides[k] for k in ks}
    return out


def stage2_grid(winners: Dict[str, List[Dict[str, Any]]], cap: int = 4000) -> List[Dict[str, Any]]:
    """Every combination of the winning values, one per factor (or none),
    merged into one override dict. Raises past `cap` combos."""
    choices = [[{}] + vals for vals in winners.values()]
    n = 1
    for c in choices:
        n *= len(c)
    if n > cap:
        raise ValueError(f"stage 2 grid has {n} combos, over the cap of {cap}")
    out = []
    for parts in itertools.product(*choices):
        merged: Dict[str, Any] = {}
        for p in parts:
            merged.update(p)
        out.append(merged)
    return out


# ── workers ────────────────────────────────────────────────────────────────

_DATA: Dict[str, Any] = {}


def _init(data: Dict[str, Any]) -> None:
    _DATA.clear()
    _DATA.update(data)


def _series(name: str) -> Tuple[str, int, int, int]:
    """(direction kind, window days, start_ms, end_ms) for a series name:
    journaled | control30 | control50 | control50a (first half) | control50b."""
    if name == "journaled":
        w = _DATA["windows"][30]
        return "journaled", 30, _DATA["first_thesis_ms"], w["c15"][-1].t + 15 * 60_000
    days = 30 if name == "control30" else 50
    w = _DATA["windows"][days]
    t0, t1 = w["c15"][0].t, w["c15"][-1].t + 15 * 60_000
    if name == "control50a":
        return "long", days, t0, (t0 + t1) // 2
    if name == "control50b":
        return "long", days, (t0 + t1) // 2, t1
    return "long", days, t0, t1


def _run(job: Tuple[str, Dict[str, Any], str]) -> Tuple[str, str, Dict[str, Any]]:
    key, overrides, series = job
    kind, days, start_ms, end_ms = _series(series)
    w = _DATA["windows"][days]
    c15 = [c for c in w["c15"] if start_ms <= c.t < end_ms]
    direction = journaled_direction(_DATA["theses"]) if kind == "journaled" else always_direction("long")
    # the 1h history before the start stays in so the levels are warm
    c1h = [c for c in w["c1h"] if c.t < end_ms]
    run = simulate(c15, c1h, direction, params_with(overrides), w["funding"])
    return key, series, per_day(run, start_ms, end_ms)


def run_jobs(jobs: List[Tuple[str, Dict[str, Any], str]], data: Dict[str, Any], workers: int):
    if workers <= 1:
        _init(data)
        return [_run(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(data,)) as ex:
        return list(ex.map(_run, jobs, chunksize=16))


def _label(ov: Dict[str, Any]) -> str:
    return "live" if not ov else ", ".join(f"{k}={v}" for k, v in sorted(ov.items()))


def _evaluate(named: Dict[str, Dict[str, Any]], series: Sequence[str], data, workers) -> Dict[str, Dict[str, Any]]:
    jobs = [(k, ov, s) for k, ov in named.items() for s in series]
    rows: Dict[str, Dict[str, Any]] = {k: {"overrides": ov, "label": _label(ov)} for k, ov in named.items()}
    for key, s, summ in run_jobs(jobs, data, workers):
        rows[key][s] = summ
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--theses-dir", required=True, help="READ ONLY journaled-thesis directory")
    ap.add_argument("--cache-dir", default="/tmp/pathiel-backtest/cross-market")
    ap.add_argument("--out", default="/tmp/strategy-sweep/report.json")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args(argv)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    t_start = time.time()

    data = load_data(a.cache_dir, a.theses_dir)
    data["first_thesis_ms"] = min(th.created_at_ms for th in data["theses"])
    base_series = ("journaled", "control30", "control50")

    # stage 1
    named = {"live": {}}
    for factor, vals in STAGE1.items():
        for i, ov in enumerate(vals):
            named[f"{factor}#{i}"] = ov
    s1 = _evaluate(named, base_series, data, a.workers)
    live = s1["live"]
    winners: Dict[str, List[Dict[str, Any]]] = {}
    for k, row in s1.items():
        if k != "live" and beats(row, live):
            winners.setdefault(k.split("#")[0], []).append(row["overrides"])

    # stage 2
    grid = stage2_grid(winners)
    s2 = _evaluate({f"g{i}": ov for i, ov in enumerate(grid)}, base_series, data, a.workers)
    s2_live = next(r for r in s2.values() if not r["overrides"])
    ranked = sorted((r for r in s2.values() if robust(r, s2_live) or not r["overrides"]),
                    key=lambda r: (r["journaled"]["daily_pct"], r["control30"]["daily_pct"]), reverse=True)
    raw_best = sorted(s2.values(), key=lambda r: r["journaled"]["daily_pct"], reverse=True)[:5]

    # stage 3: the top 10 under stress and on each half of the long control
    top = ranked[:10]
    stress_named = {}
    for i, r in enumerate(top):
        stress_named[f"t{i}"] = r["overrides"]
        for sk, sov in STRESS.items():
            stress_named[f"t{i}|{sk}"] = {**r["overrides"], **sov}
    s3 = _evaluate(stress_named, ("journaled", "control30", "control50a", "control50b"), data, a.workers)
    for i, r in enumerate(top):
        r["halves"] = {h: s3[f"t{i}"][h] for h in ("control50a", "control50b")}
        r["stress"] = {sk: {s: s3[f"t{i}|{sk}"][s] for s in ("journaled", "control30")} for sk in STRESS}
    live_halves = _evaluate({"live": {}}, ("control50a", "control50b"), data, a.workers)["live"]
    live["halves"] = {h: live_halves[h] for h in ("control50a", "control50b")}

    # stage 4: attribution of the winner
    best = ranked[0]["overrides"] if ranked and ranked[0]["overrides"] else {}
    attrib_named = subsets(best)
    if best:
        for lev in (15.0, 10.0):
            attrib_named[f"winner@{lev:g}x"] = {**best, "leverage": lev}
    all_series = ("journaled", "control30", "control50", "control50a", "control50b")
    attribution = _evaluate(attrib_named, all_series, data, a.workers)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "seconds": round(time.time() - t_start, 1),
        "n_theses": len(data["theses"]),
        "journaled_days": live["journaled"]["days"],
        "live": live, "stage1": s1, "winners": winners, "n_stage2": len(grid),
        "n_robust": len(ranked) - 1,
        "live_rank_stage2": next((i + 1 for i, r in enumerate(ranked) if not r["overrides"]), None),
        "raw_best_journaled": raw_best,
        "top10": top, "attribution": attribution, "top_by_control30": sorted(s2.values(), key=lambda r: r["control30"]["daily_pct"],
                                                 reverse=True)[:5],
    }
    with open(a.out, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {a.out}  ({report['seconds']}s)")
    return 0


def _print(rep: Dict[str, Any]) -> None:
    def line(label: str, r: Dict[str, Any]) -> str:
        j, c, c5 = r["journaled"], r["control30"], r.get("control50")
        tail = f" ctl50 {c5['daily_pct']:>6.2f}%/d" if c5 else ""
        return (f"{label[:58]:<58} {j['daily_pct']:>6.2f}%/d {j['weekly_pct']:>7.1f}%/w ${j['final_equity']:>8.2f} "
                f"{j['trades']:>3}t {j['max_dd_pct']:>5.1f}%dd | ctl30 {c['daily_pct']:>6.2f}%/d{tail}")
    print(f"journaled days {rep['journaled_days']}, {rep['n_theses']} theses")
    print(line("LIVE", rep["live"]))
    print("\nstage 1 (one change from live):")
    for k, r in sorted(rep["stage1"].items(), key=lambda kv: -kv[1]["journaled"]["daily_pct"]):
        if k != "live":
            print(line(r["label"], r))
    print(f"\nstage 2: {rep['n_stage2']} combos, {rep['n_robust']} beat live on both controls; "
          f"live ranks {rep['live_rank_stage2']} among those")
    for r in rep["top10"]:
        print(line(r["label"], r))
        if "halves" in r:
            h = r["halves"]; st = r["stress"]
            print(f"    halves {h['control50a']['daily_pct']:.2f}/{h['control50b']['daily_pct']:.2f}%/d  "
                  f"slip x2 {st['slip_x2']['journaled']['daily_pct']:.2f}%/d  1h clock {st['clock_1h']['journaled']['daily_pct']:.2f}%/d")
    print("\nwhat each part of the winner adds (journaled | ctl30 | ctl50 | ctl50 halves), %/day, max dd:")
    for k, x in rep["attribution"].items():
        j, c, c5, ha, hb = (x[s] for s in ("journaled", "control30", "control50", "control50a", "control50b"))
        print(f"  {k[:50]:<50} {j['daily_pct']:6.2f} {j['max_dd_pct']:3.0f}% | {c['daily_pct']:6.2f} {c['max_dd_pct']:3.0f}% | "
              f"{c5['daily_pct']:6.2f} {c5['max_dd_pct']:3.0f}% | {ha['daily_pct']:6.2f}/{hb['daily_pct']:.2f}")
    print("\nbest journaled per day, no control filter:")
    for r in rep["raw_best_journaled"]:
        print(line(r["label"], r))


if __name__ == "__main__":
    raise SystemExit(main())
