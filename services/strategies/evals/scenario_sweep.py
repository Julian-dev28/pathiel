"""Scenario sweep of the oil desk-tick rules (2026-10-29).

Operator: "test .3% lower targets and then reentry", widened the same day to
a full sweep: "DONT MAKE CHANGES JUST GIVE ME RESULTS". Every combination of

  target_offset_frac  0, 0.1%, 0.2%, 0.3%, 0.5%
  re-entry            on (the sim's behaviour so far) / off (wait for the next call)
  stop                fixed / breakeven after +1R / trail 1 ATR after +1R
  target              limit (live since 2026-10-28) / market
  leverage            10x / 15x / 20x
  stop buffer         0.25 / 0.50 (the oil default) / 0.75

runs on the journaled oil theses over 30 days and the longest window the
venue returns (50 days), from $64, size-aware exit slippage on, 15m clock.
The top 10 by journaled 30-day final equity are re-run on the always-long
control (both windows); the top 5 also on the 1h clock as a check.

Read-only on everything live: candles and funding come through the eval's
disk cache, theses are read from the journal directory and never written.

    python -m services.strategies.evals.scenario_sweep --theses-dir <dir> [--workers 8]
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, Run, simulate
from services.strategies.evals.desk_tick_backtest import (
    COIN, DAY_MS, always_direction, journaled_direction)

OFFSETS = (0.0, 0.001, 0.002, 0.003, 0.005)
REENTRY = (True, False)
STOPS = ("fixed", "breakeven", "trail")
TARGET_MODES = ("limit", "market")
LEVERAGES = (10.0, 15.0, 20.0)
STOP_BUFFERS = (0.25, 0.50, 0.75)
WINDOWS = (30, 50)
START = 64.0

# What the live desk runs today: no offset, re-entry under the same call (the
# tick rests the next setup once flat), fixed stop, limit target, 20x, the
# default $0.50 buffer, 15m clock.
LIVE = {"offset": 0.0, "reentry": True, "stop": "fixed", "target": "limit",
        "leverage": 20.0, "buffer": 0.50}


def grid() -> Iterator[Dict[str, Any]]:
    """Every combination, as plain dicts (picklable for the worker pool)."""
    for off, re_, stop, tgt, lev, buf in itertools.product(
            OFFSETS, REENTRY, STOPS, TARGET_MODES, LEVERAGES, STOP_BUFFERS):
        yield {"offset": off, "reentry": re_, "stop": stop, "target": tgt,
               "leverage": lev, "buffer": buf}


def combo_key(c: Dict[str, Any]) -> Tuple:
    return (c["offset"], c["reentry"], c["stop"], c["target"], c["leverage"], c["buffer"])


def params_for(c: Dict[str, Any], tick_minutes: int = 15, start: float = START) -> Params:
    return Params(target_offset_frac=c["offset"], reentry_after_target=c["reentry"],
                  stop_mode=c["stop"], stop_trigger_r=1.0, trail_atr=1.0,
                  target_mode=c["target"], leverage=c["leverage"], max_leverage=20.0,
                  stop_buffer=c["buffer"], tick_minutes=tick_minutes,
                  starting_equity=start, size_slippage=True)


def summarize(run: Run) -> Dict[str, Any]:
    """The numbers the ranking and the report read."""
    rs = [t.r_multiple for t in run.trades if t.r_multiple is not None]
    wins = [t for t in run.trades if t.net_pnl_usd > 0]
    return {
        "final_equity": round(run.final_equity, 2),
        "total_r": round(sum(rs), 2),
        "trades": len(run.trades),
        "win_rate": round(len(wins) / len(run.trades), 3) if run.trades else None,
        "max_dd": round(run.max_drawdown_pct, 3),
        "liquidated": run.liquidated,
        "targets_hit": sum(1 for t in run.trades if t.exit_reason == "target"),
        "offset_captures": sum(1 for t in run.trades if t.offset_capture),
        "r_list": [round(r, 2) for r in rs],
    }


def rank(rows: Sequence[Dict[str, Any]], window: int = 30) -> List[Dict[str, Any]]:
    """Rows sorted by journaled final equity in `window`, best first; ties by total R."""
    return sorted(rows, key=lambda r: (r[f"j{window}"]["final_equity"], r[f"j{window}"]["total_r"]),
                  reverse=True)


def live_rank(ranked: Sequence[Dict[str, Any]]) -> int:
    """1-based rank of the live config in `ranked`."""
    key = combo_key(LIVE)
    for i, r in enumerate(ranked, 1):
        if combo_key(r["combo"]) == key:
            return i
    raise ValueError("live config missing from the grid")


# ── data + workers ─────────────────────────────────────────────────────────

_DATA: Dict[str, Any] = {}


def load_data(cache_dir: str, theses_dir: str, coin: str = COIN) -> Dict[str, Any]:
    longest = max(WINDOWS)
    c15 = D.fetch_candles_cached(coin, "15m", longest * 24 * 4 + 20, cache_dir)
    c1h = D.fetch_candles_cached(coin, "1h", (longest + 2) * 24, cache_dir)
    now_ms = int(time.time() * 1000)
    out: Dict[str, Any] = {"theses": D.load_journaled_theses(theses_dir), "windows": {}}
    for days in WINDOWS:
        start = now_ms - days * DAY_MS
        w15 = [c for c in c15 if c.t >= start]
        w1h = [c for c in c1h if c.t >= start]
        funding = D.fetch_funding_cached(coin, w15[0].t, now_ms, cache_dir) if w15 else []
        out["windows"][days] = {"c15": w15, "c1h": w1h, "funding": funding,
                                "span_days": round((w15[-1].t - w15[0].t) / DAY_MS, 1) if w15 else 0}
    return out


def _init(data: Dict[str, Any]) -> None:
    _DATA.clear()
    _DATA.update(data)


def _direction(series: str):
    if series == "journaled":
        return journaled_direction(_DATA["theses"])
    return always_direction("long")


def _run(job: Tuple[Dict[str, Any], str, int, int]) -> Tuple[Tuple, str, int, int, Dict[str, Any]]:
    combo, series, days, tick = job
    w = _DATA["windows"][days]
    run = simulate(w["c15"], w["c1h"], _direction(series), params_for(combo, tick), w["funding"])
    return combo_key(combo), series, days, tick, summarize(run)


def run_jobs(jobs: List[Tuple], data: Dict[str, Any], workers: int) -> List[Tuple]:
    if workers <= 1:
        _init(data)
        return [_run(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(data,)) as ex:
        return list(ex.map(_run, jobs, chunksize=8))


# ── analysis ───────────────────────────────────────────────────────────────

def factor_means(rows: Sequence[Dict[str, Any]], factor: str, field: str) -> Dict[str, float]:
    """Mean of `field` (e.g. 'j30') final equity per value of one factor."""
    groups: Dict[str, List[float]] = {}
    for r in rows:
        groups.setdefault(str(r["combo"][factor]), []).append(r[field]["final_equity"])
    return {k: round(sum(v) / len(v), 2) for k, v in groups.items()}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--theses-dir", required=True, help="READ ONLY journaled-thesis directory")
    ap.add_argument("--cache-dir", default="/tmp/pathiel-backtest/cross-market")
    ap.add_argument("--out", default="/tmp/scenario-sweep/report.json")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args(argv)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)

    data = load_data(a.cache_dir, a.theses_dir)
    combos = list(grid())
    # journaled theses + the always-long control, every combo, both windows
    jobs = [(c, series, d, 15) for c in combos for d in WINDOWS for series in ("journaled", "always_long")]
    t0 = time.time()
    res = run_jobs(jobs, data, a.workers)
    by_key: Dict[Tuple, Dict[str, Any]] = {combo_key(c): {"combo": c} for c in combos}
    for key, series, days, tick, summ in res:
        by_key[key][f"j{days}" if series == "journaled" else f"control{days}"] = summ
    rows = list(by_key.values())
    ranked = rank(rows, 30)

    top10 = ranked[:10]
    live_row = by_key[combo_key(LIVE)]
    jobs2 = [(r["combo"], "journaled", 30, 60) for r in ranked[:5]] + [(LIVE, "journaled", 30, 60)]
    for key, series, days, tick, summ in run_jobs(jobs2, data, a.workers):
        by_key[key][f"j{days}_{tick}m"] = summ

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "n_combos": len(combos), "n_theses": len(data["theses"]), "seconds": round(time.time() - t0, 1),
        "windows": {d: data["windows"][d]["span_days"] for d in WINDOWS},
        "live": live_row, "live_rank_j30": live_rank(ranked), "live_rank_j50": live_rank(rank(rows, 50)),
        "top10": top10,
        "factor_means": {f: {w: factor_means(rows, f, w) for w in ("j30", "j50")}
                         for f in ("offset", "reentry", "stop", "target", "leverage", "buffer")},
        "factor_means_control": {f: {w: factor_means(rows, f, w) for w in ("control30", "control50")}
                                 for f in ("offset", "reentry", "stop", "target", "leverage", "buffer")},
        "all": rows,
    }
    with open(a.out, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    print(f"{len(combos)} combos, {report['seconds']}s, live rank j30={report['live_rank_j30']} "
          f"j50={report['live_rank_j50']}; report {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
