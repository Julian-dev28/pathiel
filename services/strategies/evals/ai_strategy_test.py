#!/usr/bin/env python3
"""Eval: live_params() vs the AI-informed variants (Params.ai_levels,
Params.conviction_leverage, desk_tick_sim.py) on a journaled-thesis
direction series, using strategy_sweep.per_day for the per-day/per-week
numbers. Built 2026-10-30 (operator: "more AI informed, AI powered
strategy that is more profitable") to run once the 30-day thesis replay
(services/thesis/evals/replay.py) has produced enough theses to measure a
variant honestly -- the 9 real journaled theses alone (2026-10-18 to
10-30) are too few to trust on their own.

Variants, every one starting from the live rules (desk_tick_sim.
live_params()):
  live                  the unmodified live rules
  ai_levels              variant A: try the thesis's own entry/stop/first
                         target before the structural 24h-range rule
  conviction@T           variant B at threshold T in (0.55, 0.65, 0.75):
                         20x leverage at/above T confidence, 15x below
                         (Params.conviction_leverage)
  ai_levels+conviction   A+B together, conviction at 0.65

Runs every variant on the REPLAY theses dir (required) and, read-only, on
the live journaled theses dir (--live-theses-dir) for comparison -- same
direction-series builder (services.strategies.evals.desk_tick_backtest.
journaled_direction), same candle/funding cache, same $64 starting
equity.

    .venv/bin/python -m services.strategies.evals.ai_strategy_test \\
        --theses-dir /tmp/thesis-replay/oil/theses --live-theses-dir <journal dir>

The pure parts (variant_overrides, params_for, run_variants on injected
candles/theses, evaluate_theses_dir) have their own OFFLINE gate test
(tests/test_ai_strategy_test_eval.py) -- only the __main__ fetch path
touches the network.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, live_params, simulate
from services.strategies.evals.desk_tick_backtest import journaled_direction
from services.strategies.evals.strategy_sweep import per_day

COIN = "xyz:BRENTOIL"
DAY_MS = D.DAY_MS
START_EQUITY = 64.0

CONVICTION_THRESHOLDS: tuple = (0.55, 0.65, 0.75)
CONVICTION_HIGH_LEV = 20.0
CONVICTION_LOW_LEV = 15.0
# The A+B combo's own threshold -- the middle of the three swept above,
# matching the task's own worked example (threshold, high_lev, low_lev).
DEFAULT_CONVICTION_THRESHOLD = 0.65


def variant_overrides() -> Dict[str, Dict[str, Any]]:
    """name -> Params override dict, every variant starting from
    live_params(). Pure data; a combo's name says exactly what changed."""
    out: Dict[str, Dict[str, Any]] = {"live": {}, "ai_levels": {"ai_levels": True}}
    for t in CONVICTION_THRESHOLDS:
        out[f"conviction@{t:g}"] = {"conviction_leverage": (t, CONVICTION_HIGH_LEV, CONVICTION_LOW_LEV)}
    out["ai_levels+conviction"] = {
        "ai_levels": True,
        "conviction_leverage": (DEFAULT_CONVICTION_THRESHOLD, CONVICTION_HIGH_LEV, CONVICTION_LOW_LEV),
    }
    return out


def params_for(overrides: Dict[str, Any], *, start_equity: float = START_EQUITY) -> Params:
    """live_params() with `overrides` applied, at `start_equity` (the $64
    the operator trades the live desk at). Raises on an unknown Params
    field, same discipline as strategy_sweep.params_with."""
    return dataclasses.replace(live_params(), starting_equity=start_equity, **overrides)


def run_variants(c15: Sequence[Candle], c1h: Sequence[Candle], funding: Optional[List[Dict[str, Any]]],
                 theses: Sequence[D.ThesisDirection], start_ms: int, end_ms: int,
                 *, start_equity: float = START_EQUITY) -> Dict[str, Dict[str, Any]]:
    """Every variant's per_day() row on the journaled direction series
    built from `theses`. Pure given already-fetched candles/funding -- no
    I/O here; `theses`/`c15`/`c1h` may be synthetic (the gate test) or real
    (the eval's __main__)."""
    direction_at = journaled_direction(theses)
    out: Dict[str, Dict[str, Any]] = {}
    for name, overrides in variant_overrides().items():
        params = params_for(overrides, start_equity=start_equity)
        run = simulate(c15, c1h, direction_at, params, funding)
        row = per_day(run, start_ms, end_ms, start_equity)
        row["overrides"] = overrides
        out[name] = row
    return out


def thesis_window(c15: Sequence[Candle], theses: Sequence[D.ThesisDirection]) -> tuple:
    """(start_ms, end_ms) for a thesis direction series: from the first
    thesis's created_at to the last 15m bar's close -- matches strategy_
    sweep._series's "journaled" window."""
    start_ms = min(th.created_at_ms for th in theses)
    end_ms = (max(c.t for c in c15) + 15 * 60_000) if c15 else start_ms
    return start_ms, end_ms


def evaluate_theses_dir(c15: Sequence[Candle], c1h: Sequence[Candle], funding: Optional[List[Dict[str, Any]]],
                        theses_dir: str, *, start_equity: float = START_EQUITY) -> Dict[str, Any]:
    """Every variant's row for one theses directory, or a `note` dict when
    it has no parseable theses (missing dir, empty dir, no directory
    given at all). READ ONLY on theses_dir (D.load_journaled_theses's own
    contract)."""
    theses = D.load_journaled_theses(theses_dir)
    if not theses:
        return {"n_theses": 0, "theses_dir": theses_dir, "note": f"no theses found under {theses_dir!r}"}
    start_ms, end_ms = thesis_window(c15, theses)
    c15_w = [c for c in c15 if c.t >= start_ms]
    c1h_w = [c for c in c1h if c.t < end_ms]
    rows = run_variants(c15_w, c1h_w, funding, theses, start_ms, end_ms, start_equity=start_equity)
    return {"n_theses": len(theses), "theses_dir": theses_dir, "variants": rows}


# ── __main__: live network fetch + report ───────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--theses-dir", required=True,
                    help="replay theses dir (services/thesis/evals/replay.py's output)")
    ap.add_argument("--live-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY live journaled theses dir, for comparison")
    ap.add_argument("--coin", default=COIN)
    ap.add_argument("--cache-dir", default=None,
                    help="disk cache dir for candles/funding; default $PATHIEL_STATE_DIR/ai-strategy-test")
    ap.add_argument("--out", default="/tmp/ai-strategy-test/report.json")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    cache_dir = args.cache_dir or os.path.join(
        os.environ.get("PATHIEL_STATE_DIR", "/tmp/pathiel-backtest"), "ai-strategy-test")
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    replay_theses = D.load_journaled_theses(args.theses_dir)
    live_theses = D.load_journaled_theses(args.live_theses_dir) if args.live_theses_dir else []
    all_theses = list(replay_theses) + list(live_theses)
    if not all_theses:
        print(f"FATAL: no theses found under --theses-dir {args.theses_dir!r} "
             f"or --live-theses-dir {args.live_theses_dir!r}")
        return 2

    earliest_ms = min(th.created_at_ms for th in all_theses)
    now_ms = int(time.time() * 1000)
    days = max(1, int((now_ms - earliest_ms) / DAY_MS) + 2)   # +2: pad for the ATR/range warm-up

    c15 = D.fetch_candles_cached(args.coin, "15m", days * 24 * 4 + 20, cache_dir)
    c1h = D.fetch_candles_cached(args.coin, "1h", days * 24 + 48, cache_dir)
    if not c15 or not c1h:
        print("FATAL: no candles fetched/cached; cannot run the eval.")
        return 2
    funding = D.fetch_funding_cached(args.coin, c15[0].t, now_ms, cache_dir)

    report: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "coin": args.coin,
        "variant_overrides": variant_overrides(),
        "replay": evaluate_theses_dir(c15, c1h, funding, args.theses_dir),
        "live": (evaluate_theses_dir(c15, c1h, funding, args.live_theses_dir) if args.live_theses_dir
                 else {"note": "no --live-theses-dir given"}),
    }
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    _print(report)
    print(f"\nreport: {args.out}")
    if args.as_json:
        print(json.dumps(report, indent=2, default=str))
    return 0


def _print(report: Dict[str, Any]) -> None:
    for key in ("replay", "live"):
        sec = report[key]
        if "variants" not in sec:
            print(f"\n[{key}] {sec.get('note', 'no data')}")
            continue
        print(f"\n[{key}] {sec['n_theses']} theses, {sec.get('theses_dir')}")
        print(f"{'variant':<22} {'%/day':>7} {'%/week':>8} {'end$':>9} {'trades':>7} {'win%':>6} {'maxDD%':>7}")
        for name, row in sec["variants"].items():
            win = f"{row['win_rate']:.0%}" if row.get("win_rate") is not None else "n/a"
            print(f"{name:<22} {row['daily_pct']:>7.2f} {row['weekly_pct']:>8.2f} {row['final_equity']:>9.2f} "
                  f"{row['trades']:>7} {win:>6} {row['max_dd_pct']:>7.1f}")


if __name__ == "__main__":
    raise SystemExit(main())
