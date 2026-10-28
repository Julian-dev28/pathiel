#!/usr/bin/env python3
"""Eval: the 30-day desk-tick backtest (plan section 5 / section 9 steps 1-4;
docs/overhaul/BACKTEST_PLAN_2026-10-25.md). Fetches (or reads the disk cache
for) xyz:BRENTOIL's last 30 days of 15m/1h candles and hourly funding, runs
the three direction series plus the parameter sweep through the pure
simulator (`services.strategies.backtest.desk_tick_sim`), writes
`report.json` and one trades CSV per run, prints a short table, and exits 1
if the always-long or always-short run liquidates (the mechanics-only
control runs — a liquidation there is the loop's own gates failing, not a
bad call).

Runnable as a script, hits live Hyperliquid data on an empty cache (a
periodic/nightly eval, not a commit gate):

    PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m services.strategies.evals.desk_tick_backtest
    .venv/bin/python -m services.strategies.evals.desk_tick_backtest --cache-dir /tmp/x --out-dir /tmp/y

The pure aggregation (`aggregate`, `calibration_diff`, the direction-series
builders) has its own OFFLINE gate test on injected `Run`s
(tests/test_desk_tick_backtest_eval.py); only the __main__ fetch path and
the thesis/log reads touch anything outside this process.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Direction, Params, Run, Trade, simulate

COIN = "xyz:BRENTOIL"
DAY_MS = D.DAY_MS
HOUR_MS = D.HOUR_MS
FIFTEEN_MIN_MS = 900_000
WINDOW_DAYS = 30
DAILY_SIGN_LOOKBACK_DAYS = 20
DAILY_SIGN_EXTRA_LOOKBACK_DAYS = 25  # extra 1h history fetched so day 1 of the window still has 20 days behind it

# The live desk's journaled theses and tick log live in a SEPARATE repo
# (pathia), not under this one, so there is no portable relative path to
# them — machine-bound by nature, which is exactly why neither is
# hardcoded here (scripts/check_no_absolute_paths.py forbids it, and
# rightly so: a literal /Users/<name>/... would break this eval on any
# other machine or CI runner). Point PATHIEL_OIL_THESES_DIR / --theses-dir
# at that directory to run the journaled-thesis series and the
# calibration diff; both default to "" and degrade gracefully (the
# journaled run is skipped with a note, calibration is skipped) when
# unset, so the eval still runs everywhere else.
DEFAULT_THESES_DIR = os.environ.get("PATHIEL_OIL_THESES_DIR", "")
DEFAULT_PROGRESS_LOG = os.environ.get("PATHIEL_OIL_PROGRESS_LOG", "/tmp/oil-loop/progress.log")

SWEEP_STOP_BUFFERS = (0.10, 0.25, 0.50, 0.75, 1.00)   # 0.75/1.00 added 2026-10-25: is 0.50 the knee?
SWEEP_SLIPPAGE = (0.0002, 0.0005)
SWEEP_LEVERAGE = (10.0, 20.0)
TICK_MINUTES = (15, 60)             # 15m: the loop until 2026-10-25; 60: the hourly loop since

# Moving the stop (operator, 2026-10-28: "we tested that its not more
# profitable right?" -- it had not been). Each runs on the 15m clock with
# every other rule unchanged; "fixed" is the live rule.
STOP_VARIANTS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("fixed", {}),
    ("be_1r", {"stop_mode": "breakeven", "stop_trigger_r": 1.0}),
    ("be_2r", {"stop_mode": "breakeven", "stop_trigger_r": 2.0}),
    ("trail_1atr", {"stop_mode": "trail", "stop_trigger_r": 1.0, "trail_atr": 1.0}),
    ("trail_2atr", {"stop_mode": "trail", "stop_trigger_r": 1.0, "trail_atr": 2.0}),
)


# ── direction series builders ───────────────────────────────────────────────

def always_direction(side: str):
    d = Direction(side=side)
    return lambda t: d


def journaled_direction(theses: Sequence[D.ThesisDirection]):
    """The call in force at `t`: the latest thesis whose `created_at` is
    `<= t`, applied until the next one starts (plan section 2.1). None
    before the first thesis exists."""
    ordered = sorted(theses, key=lambda th: th.created_at_ms)

    def direction_at(t: int) -> Optional[Direction]:
        active = None
        for th in ordered:
            if th.created_at_ms <= t:
                active = th
            else:
                break
        if active is None:
            return None
        return Direction(side=active.side, invalidation_px=active.invalidation_px,
                         entry_ceiling=active.entry_px)

    return direction_at


def _daily_closes(candles_1h: Sequence[Candle]) -> List[Tuple[int, float]]:
    """One (day_index, close) per UTC day that has at least one 1h bar —
    the close of that day's LAST bar, sorted oldest first."""
    by_day: Dict[int, Candle] = {}
    for c in sorted(candles_1h, key=lambda c: c.t):
        day = c.t // DAY_MS
        by_day[day] = c   # later bar in the same day overwrites -> last close wins
    return sorted((day, c.c) for day, c in by_day.items())


def build_daily_sign_direction(candles_1h_long: Sequence[Candle], lookback_days: int = DAILY_SIGN_LOOKBACK_DAYS):
    """Direction = sign of the 20-day return, decided once per day from the
    PRIOR day's close and the close `lookback_days` before that — both
    strictly in the past relative to any tick on the day the sign applies
    to (plan section 2.3). Returns (direction_at, days_with_signal,
    days_without_enough_history) for the results doc to report the exact
    warm-up gap."""
    closes = _daily_closes(candles_1h_long)
    by_day = dict(closes)
    days = [d for d, _ in closes]
    sign_by_day: Dict[int, Optional[str]] = {}
    for day in days:
        prev_day = day - 1
        base_day = day - 1 - lookback_days
        if prev_day not in by_day or base_day not in by_day:
            sign_by_day[day] = None
            continue
        ret = by_day[prev_day] / by_day[base_day] - 1.0
        sign_by_day[day] = "long" if ret >= 0 else "short"

    def direction_at(t: int) -> Optional[Direction]:
        day = t // DAY_MS
        side = sign_by_day.get(day)
        return Direction(side=side) if side else None

    n_signal = sum(1 for v in sign_by_day.values() if v is not None)
    n_no_signal = sum(1 for v in sign_by_day.values() if v is None)
    return direction_at, n_signal, n_no_signal


# ── pure aggregation (gate-tested on injected Run objects) ─────────────────

def aggregate(name: str, run: Run) -> Dict[str, Any]:
    trades = run.trades
    n = len(trades)
    wins = [t for t in trades if t.net_pnl_usd > 0]
    r_values = [t.r_multiple for t in trades if t.r_multiple is not None]
    gross = sum(t.gross_pnl_usd for t in trades)
    fees = run.fees_total_usd
    funding = run.funding_total_usd
    return {
        "name": name,
        "n_trades": n,
        "win_rate": (len(wins) / n) if n else None,
        "avg_r": (sum(r_values) / len(r_values)) if r_values else None,
        "expectancy_r": (sum(r_values) / len(r_values)) if r_values else None,
        "expectancy_usd": (sum(t.net_pnl_usd for t in trades) / n) if n else None,
        "max_drawdown_pct": run.max_drawdown_pct,
        "liquidated": run.liquidated,
        "liquidation_time": run.liquidation_time,
        "rested": run.rested,
        "filled": run.filled,
        "fill_rate": (run.filled / run.rested) if run.rested else None,
        "cancelled_resting": run.cancelled_resting,
        "refused": dict(run.refused),
        "fees_total_usd": fees,
        "funding_total_usd": funding,
        "gross_pnl_usd": gross,
        "fees_share_of_gross": (fees / abs(gross)) if gross else None,
        "funding_share_of_gross": (funding / abs(gross)) if gross else None,
        "final_equity": run.final_equity,
        "starting_equity": run.params.starting_equity if run.params else None,
        "open_position_at_end": run.open_position_at_end,
        "params": asdict(run.params) if run.params else None,
    }


def cut_short(trades: Sequence[Trade], candles_15m: Sequence[Candle]) -> int:
    """How many trades a MOVED stop closed that would have gone on to the
    original target: after the exit, price reached the target before the
    original stop (a bar touching both counts as the stop first, the sim's
    own conservative intrabar read). Trades still short of either when the
    data ends are not counted."""
    bars = sorted(candles_15m, key=lambda c: c.t)
    n = 0
    for tr in trades:
        if tr.exit_reason != "stop" or not tr.stop_moved or tr.exit_time is None or tr.orig_stop_px is None:
            continue
        long = tr.side == "long"
        for b in bars:
            if b.t <= tr.exit_time:
                continue
            if (b.l <= tr.orig_stop_px) if long else (b.h >= tr.orig_stop_px):
                break
            if (b.h >= tr.target_px) if long else (b.l <= tr.target_px):
                n += 1
                break
    return n


def stop_variant_row(series: str, variant: str, run: Run, candles_15m: Sequence[Candle]) -> Dict[str, Any]:
    """One row of the moving-the-stop table: the usual aggregate plus total
    R, the trades whose stop moved, and the winners a moved stop cut short."""
    row = aggregate(f"{series}_{variant}", run)
    r_values = [t.r_multiple for t in run.trades if t.r_multiple is not None]
    row.update(series=series, variant=variant,
               total_r=sum(r_values) if r_values else 0.0,
               n_stop_moved=sum(1 for t in run.trades if t.stop_moved),
               cut_short=cut_short(run.trades, candles_15m))
    return row


# ── calibration against the live log (plan section 6 / step 4) ─────────────

_LOG_RE = re.compile(
    r"DESK-TICK oil (?P<ts>\S+) coin=\S+ mid=\S+ pos=(?P<pos>\S+)"
)


def parse_progress_log(path: str) -> List[Dict[str, Any]]:
    """Every oil DESK-TICK line with a position field, as
    {ts_ms, in_position}. Lines that don't match (SKIP rows, older OIL-TICK
    rows with a different shape, a header) are skipped, not errors —
    `desk_tick_backtest.py --calibration` reports how many lines parsed."""
    out: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return out
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            m = _LOG_RE.search(line)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group("ts"), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            pos = m.group("pos")
            out.append({"ts_ms": int(ts.timestamp() * 1000), "in_position": pos != "flat"})
    return out


def _position_intervals(run: Run) -> List[Tuple[int, int]]:
    """[(entry_time, exit_time)] for every closed trade, plus an open
    interval to "infinity" if the run still held a position at the end."""
    out = [(t.entry_time, t.exit_time) for t in run.trades if t.exit_time is not None]
    if run.open_position_at_end is not None and run.trades:
        # The open position's entry_time isn't on the Run directly; the
        # simulator never force-closes it, so the last bar's equity_curve
        # point is as close as this report can get without changing
        # desk_tick_sim's pure return shape. Treat "still open" as covering
        # from the last trade's exit (or window start) to +inf.
        last_known = run.trades[-1].exit_time if run.trades[-1].exit_time else 0
        out.append((last_known, float("inf")))
    return out


def in_position_at(run: Run, t: int) -> bool:
    return any(a <= t < b for a, b in _position_intervals(run))


def calibration_diff(log_rows: Sequence[Dict[str, Any]], run: Run,
                     window_start_ms: int, window_end_ms: int) -> Dict[str, Any]:
    """Round every log row to its nearest 15m tick inside the run's window
    and compare "in a position" (log's pos != flat) against the simulator's
    own state at that same tick. Pure: no I/O, takes already-parsed rows
    and an already-computed Run."""
    agree = 0
    divergences: List[Dict[str, Any]] = []
    compared = 0
    for row in log_rows:
        t = row["ts_ms"]
        if t < window_start_ms or t > window_end_ms:
            continue
        tick = (t // FIFTEEN_MIN_MS) * FIFTEEN_MIN_MS
        sim_in_pos = in_position_at(run, tick)
        compared += 1
        if sim_in_pos == row["in_position"]:
            agree += 1
        else:
            divergences.append({"ts_ms": t, "tick": tick, "log_in_position": row["in_position"],
                                "sim_in_position": sim_in_pos})
    return {
        "compared": compared, "agree": agree, "disagree": len(divergences),
        "agreement_rate": (agree / compared) if compared else None,
        "divergences_sample": divergences[:10],
    }


# ── run + report ─────────────────────────────────────────────────────────

def run_one(name: str, c15: List[Candle], c1h: List[Candle], direction_at, params: Params,
           funding: Optional[List[Dict[str, Any]]], out_dir: str) -> Dict[str, Any]:
    run = simulate(c15, c1h, direction_at, params, funding_history=funding)
    report = aggregate(name, run)
    _write_trades_csv(name, run.trades, out_dir)
    return report


def _write_trades_csv(name: str, trades: List[Trade], out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"trades_{name}.csv")
    fields = ["side", "entry_time", "entry_px", "size", "stop_px", "target_px", "exit_time",
              "exit_px", "exit_reason", "gross_pnl_usd", "fees_usd", "funding_usd", "net_pnl_usd",
              "risk_usd", "r_multiple", "added"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(fields)
        for t in trades:
            w.writerow([getattr(t, f) for f in fields])
    return path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coin", default=COIN)
    parser.add_argument("--days", type=int, default=WINDOW_DAYS)
    parser.add_argument("--cache-dir", default=None,
                        help="disk cache dir for candles/funding; default $PATHIEL_STATE_DIR/backtest")
    parser.add_argument("--out-dir", default="/tmp/desk-backtest")
    parser.add_argument("--theses-dir", default=DEFAULT_THESES_DIR,
                        help="READ ONLY journaled-thesis directory")
    parser.add_argument("--progress-log", default=DEFAULT_PROGRESS_LOG, help="READ ONLY live-loop tick log")
    parser.add_argument("--no-calibration", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    cache_dir = args.cache_dir or os.path.join(
        os.environ.get("PATHIEL_STATE_DIR", "/tmp/pathiel-backtest"), "backtest")
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)

    n_15m = args.days * 24 * 4 + 20          # a small pad so edge ticks always have a prior bar
    n_1h_long = (args.days + DAILY_SIGN_EXTRA_LOOKBACK_DAYS) * 24

    c15 = D.fetch_candles_cached(args.coin, "15m", n_15m, cache_dir)
    c1h_long = D.fetch_candles_cached(args.coin, "1h", n_1h_long, cache_dir)
    if not c15 or not c1h_long:
        print("FATAL: no candles fetched/cached; cannot run the backtest.")
        return 2

    now_ms = int(time.time() * 1000)
    window_start_ms = now_ms - args.days * DAY_MS
    c15_window = [c for c in c15 if c.t >= window_start_ms]
    c1h_window = [c for c in c1h_long if c.t >= window_start_ms]

    start_ms = c15_window[0].t if c15_window else window_start_ms
    end_ms = now_ms
    funding = D.fetch_funding_cached(args.coin, start_ms, end_ms, cache_dir)

    theses = D.load_journaled_theses(args.theses_dir)
    daily_sign_at, n_signal_days, n_no_signal_days = build_daily_sign_direction(c1h_long)

    # Two clocks: the 15m tick the loop ran on until 2026-10-25 and the 1h
    # tick it runs on since ("change the strategy to hourly, keep leverage at
    # 20x"). The hourly rows are the live strategy; the 15m rows stay so the
    # change itself is measured, not assumed.
    rows: List[Dict[str, Any]] = []
    for tick in TICK_MINUTES:
        base = Params(tick_minutes=tick)
        sfx = "" if tick == 15 else f"_{tick}m"
        rows.append(run_one(f"always_long{sfx}", c15_window, c1h_window, always_direction("long"), base,
                            funding, args.out_dir))
        rows.append(run_one(f"always_short{sfx}", c15_window, c1h_window, always_direction("short"), base,
                            funding, args.out_dir))
        rows.append(run_one(f"daily_sign{sfx}", c15_window, c1h_window, daily_sign_at, base, funding, args.out_dir))
        if theses:
            rows.append(run_one(f"journaled{sfx}", c15_window, c1h_window, journaled_direction(theses), base,
                                funding, args.out_dir))
        else:
            rows.append({"name": f"journaled{sfx}", "n_trades": 0, "note": f"no theses found under {args.theses_dir}"})
    base = Params(tick_minutes=TICK_MINUTES[0])

    sweep_rows: List[Dict[str, Any]] = []
    for tick in TICK_MINUTES:
        for side in ("always_long", "always_short"):
            for sb in SWEEP_STOP_BUFFERS:
                for sl in SWEEP_SLIPPAGE:
                    for lev in SWEEP_LEVERAGE:
                        p = Params(stop_buffer=sb, slippage=sl, leverage=lev, max_leverage=max(lev, 20.0),
                                   tick_minutes=tick)
                        run = simulate(c15_window, c1h_window, always_direction("long" if side == "always_long" else "short"), p)
                        rep = aggregate(f"sweep_{side}_sb{sb}_sl{sl}_lev{lev:g}_t{tick}", run)
                        rep.update(side=side, stop_buffer=sb, slippage=sl, leverage=lev, tick_minutes=tick)
                        sweep_rows.append(rep)

    # Moving the stop: every variant on every direction series, 15m clock.
    stop_series = [("always_long", always_direction("long")), ("always_short", always_direction("short")),
                   ("daily_sign", daily_sign_at)]
    if theses:
        stop_series.insert(0, ("journaled", journaled_direction(theses)))
    stop_rows: List[Dict[str, Any]] = []
    for series, direction_at in stop_series:
        for variant, kw in STOP_VARIANTS:
            run = simulate(c15_window, c1h_window, direction_at, Params(tick_minutes=15, **kw), funding)
            stop_rows.append(stop_variant_row(series, variant, run, c15_window))

    calibration = None
    if not args.no_calibration:
        log_rows = parse_progress_log(args.progress_log)
        if log_rows and theses:
            j_run = simulate(c15_window, c1h_window, journaled_direction(theses), base, funding)
            calibration = calibration_diff(log_rows, j_run, window_start_ms, now_ms)
            calibration["log_lines_parsed"] = len(log_rows)
        else:
            calibration = {"note": "no log lines or no theses; calibration skipped"}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "coin": args.coin, "days": args.days,
        "window_start": datetime.fromtimestamp(window_start_ms / 1000, tz=timezone.utc).isoformat(),
        "window_end": datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
        "daily_sign_days_with_signal": n_signal_days, "daily_sign_days_without_signal": n_no_signal_days,
        "n_theses": len(theses), "runs": rows, "sweep": sweep_rows, "calibration": calibration,
        "stop_variants": stop_rows,
    }
    report_path = os.path.join(args.out_dir, "report.json")
    with open(report_path, "w") as fh:
        json.dump(report, fh, indent=2, default=str)

    _print_table(rows)
    _print_stop_table(stop_rows)
    print(f"\nreport: {report_path}")
    print(f"trades CSVs: {args.out_dir}/trades_<run>.csv")

    liquidated_control = any(r.get("liquidated") for r in rows if r.get("name") in ("always_long", "always_short"))
    if args.as_json:
        print(json.dumps(report, indent=2, default=str))
    if liquidated_control:
        print("\nVERDICT: FAIL -- a mechanics-only control run (always-long/always-short) liquidated.")
        return 1
    print("\nVERDICT: PASS -- no mechanics-only control run liquidated.")
    return 0


def _print_table(rows: List[Dict[str, Any]]) -> None:
    print(f"{'run':<14} {'trades':>7} {'win%':>6} {'exp_R':>7} {'maxDD%':>7} {'liq':>4} {'fill%':>6}")
    for r in rows:
        if r.get("n_trades") is None:
            continue
        win = f"{r['win_rate']:.0%}" if r.get("win_rate") is not None else "n/a"
        exp_r = f"{r['expectancy_r']:.2f}" if r.get("expectancy_r") is not None else "n/a"
        dd = f"{r['max_drawdown_pct']:.1%}" if r.get("max_drawdown_pct") is not None else "n/a"
        fill = f"{r['fill_rate']:.0%}" if r.get("fill_rate") is not None else "n/a"
        print(f"{r['name']:<14} {r['n_trades']:>7} {win:>6} {exp_r:>7} {dd:>7} "
             f"{'YES' if r.get('liquidated') else 'no':>4} {fill:>6}")


def _print_stop_table(rows: List[Dict[str, Any]]) -> None:
    print(f"\n{'series':<13} {'stop':<11} {'trades':>6} {'win%':>5} {'avgR':>6} {'totR':>6} "
          f"{'end$':>8} {'maxDD':>6} {'moved':>5} {'cut':>4}")
    for r in rows:
        win = f"{r['win_rate']:.0%}" if r.get("win_rate") is not None else "n/a"
        avg = f"{r['avg_r']:+.2f}" if r.get("avg_r") is not None else "n/a"
        print(f"{r['series']:<13} {r['variant']:<11} {r['n_trades']:>6} {win:>5} {avg:>6} "
              f"{r['total_r']:>+6.2f} {r['final_equity']:>8.2f} {r['max_drawdown_pct']:>6.1%} "
              f"{r['n_stop_moved']:>5} {r['cut_short']:>4}")


if __name__ == "__main__":
    raise SystemExit(main())
