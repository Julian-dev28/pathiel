"""Extended history test, 2026-11-03 (docs/strategy/EXTENDED_HISTORY_TEST_2026-11-03.md).

Operator, 2026-11-03: "our current strategy is about a week and a half
old, can we maybe extend it backwards 30 days and see if it would have
had success?" The live oil rule (`desk_tick_sim.live_params()`: market
entry off the 24h range, stop 0.75 beyond structure, target 0.5% inside
the far 24h edge as a limit, 2R gate, 20x, 15-minute ticks, plus the
live `risk_cap_frac` 0.15 since 2026-11-02) had already been backtested on
the live journal (10-18 onward) and 30 replayed theses for 2026-09-30 to
10-30 (/tmp/thesis-replay-30b). This reads ANY list of replay/journal
theses directories, in chronological order, and for the live rule reports
each window on its own, every window chained end to end (equity carried
over, one `--start-equity` for the first leg only), the always_long /
always_short / daily_sign controls on each window, and Brent buy-and-hold
over each window -- the same "beats live / beats the market" questions
CLOSER_TARGET_TEST_2026-11-03.md and BRENT_STOP_BUFFER_TEST_2026-11-02.md
ask, extended to however many windows are given. Also reports how often
each window's thesis direction matched the next 24h/7d price move (the
same direction-hit idea services.strategies.grader.grade_thesis grades a
thesis on, recomputed here directly off entry price and closed 1h bars --
no network call of its own).

    python -m services.strategies.evals.extended_history_test \\
        --window sep=/tmp/thesis-replay-sep/oil/theses \\
        --window oct=/tmp/thesis-replay-30b/oil/theses \\
        [--journal-dir <pathia>/.state/strategies/oil/theses]

Writes /tmp/extended-history/report.json and summary.csv. Reads candles
through the eval disk cache (paged for 15m so a window far enough back
to exceed the venue's 5000-bar single-request cap still loads -- see
`services.strategies.backtest.data.fetch_candles_paged_cached`) and the
venue's public /info; never touches the live account, the live config or
the journal (`--journal-dir`, if given, is READ ONLY and reported as
context, never chained). No LLM calls.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import DirectionAt, Params, Run, Trade, simulate
from services.strategies.evals.brent_stop_buffer_test import CONTROLS, run_dataset, summarize
from services.strategies.evals.closer_target_test import per_day_pct
from services.strategies.evals.desk_tick_backtest import DAY_MS, HOUR_MS, journaled_direction
from services.strategies.evals.market_scout import direction_series
from services.strategies.evals.strategy_sweep import base_params

FIFTEEN_MS = 900_000
BUFFER_DAYS = 5          # warm-up pad before the earliest window, same spirit as replay.py's own +5

Leg = Tuple[DirectionAt, Sequence[Candle], Sequence[Candle], Optional[Sequence[Dict[str, Any]]], int, int]


# ── window argument parsing (pure) ──────────────────────────────────────────

def parse_windows(args: Sequence[str]) -> List[Tuple[str, str]]:
    """`["sep=/a/b", "oct=/c/d"]` -> `[("sep", "/a/b"), ("oct", "/c/d")]`,
    order preserved (the chronological order the caller passed them in,
    which is also the chain order). Raises ValueError on anything without
    exactly one "=", or an empty name/path."""
    out: List[Tuple[str, str]] = []
    for raw in args:
        if raw.count("=") != 1:
            raise ValueError(f"--window must be NAME=PATH, got {raw!r}")
        name, path = raw.split("=", 1)
        if not name or not path:
            raise ValueError(f"--window must be NAME=PATH with both non-empty, got {raw!r}")
        out.append((name, path))
    return out


# ── Brent buy-and-hold (pure) ───────────────────────────────────────────────

def buy_hold_pct(c15: Sequence[Candle], start_ms: int, end_ms: int) -> Optional[float]:
    """Plain price return over [start_ms, end_ms): the first in-window
    bar's open to the last in-window bar's close. None for under 2 bars."""
    window = sorted((c for c in c15 if start_ms <= c.t < end_ms), key=lambda c: c.t)
    if len(window) < 2:
        return None
    return round((window[-1].c / window[0].o - 1.0) * 100.0, 2)


# ── thesis direction hit rate (pure) ────────────────────────────────────────

def direction_hit_rate(theses: Sequence[D.ThesisDirection], c1h: Sequence[Candle],
                       horizon_days: float) -> Dict[str, Any]:
    """Of the theses with a real side (not flat) and an entry price, the
    share whose direction matched the move from `created_at_ms` to the
    closest closed 1h bar at or before `created_at_ms + horizon_days`
    (skipped if no such bar exists -- e.g. the horizon runs past the data
    end). The same sign test `grader.grade_thesis`'s `direction_hit` makes
    (raw return x the call's direction sign > 0), recomputed directly here
    so this eval has no dependency on the full `contracts.Thesis` model
    `D.load_journaled_theses` already strips away."""
    horizon_ms = int(horizon_days * DAY_MS)
    bars = sorted(c1h, key=lambda c: c.t)
    hits = 0
    n = 0
    for th in theses:
        if th.side not in ("long", "short") or not th.entry_px:
            continue
        target_t = th.created_at_ms + horizon_ms
        in_range = [c for c in bars if th.created_at_ms <= c.t <= target_t]
        if not in_range:
            continue
        outcome_px = in_range[-1].c
        sign = 1.0 if th.side == "long" else -1.0
        ret = (outcome_px / th.entry_px - 1.0) * sign
        n += 1
        hits += 1 if ret > 0 else 0
    return {"n": n, "hit_rate": round(hits / n, 3) if n else None}


# ── chained equity run (reuses desk_tick_sim.simulate + brent_stop_buffer_
#    test.summarize via a synthetic Run of the concatenated legs) ──────────

def chain_runs(legs: Sequence[Leg], params: Params, start_equity: float,
               *, simulate_fn: Callable = simulate) -> Dict[str, Any]:
    """Run `params` over each leg in order (direction_at, c15, c1h, funding,
    start_ms, end_ms), carrying the final equity of one leg into the next
    leg's starting equity -- the chained-equity view of several separately
    replayed/journaled windows run back to back from one `start_equity`.
    Stops chaining (and leaves the rest un-run) if a leg liquidates: equity
    is wiped, nothing is left to carry forward. `simulate_fn` defaults to
    `desk_tick_sim.simulate`; injectable so the gate test can hand it a
    stub that returns a canned `Run` per leg instead of actually walking
    candles. Pure given its candle/funding inputs and `simulate_fn`; no I/O
    of its own."""
    equity = start_equity
    all_trades: List[Trade] = []
    all_curve: List[Tuple[int, float]] = []
    fees = funding_total = 0.0
    liquidated = False
    liq_time: Optional[int] = None
    legs_run = 0
    refused: Counter = Counter()
    for direction_at, c15, c1h, funding, st, en in legs:
        if liquidated:
            break
        w15 = [c for c in c15 if st <= c.t < en]
        w1h = [c for c in c1h if c.t < en]
        p = dataclasses.replace(params, starting_equity=equity)
        run = simulate_fn(w15, w1h, direction_at, p, funding)
        all_trades.extend(run.trades)
        all_curve.extend(run.equity_curve)
        fees += run.fees_total_usd
        funding_total += run.funding_total_usd
        refused.update(run.refused)
        equity = run.final_equity
        legs_run += 1
        if run.liquidated:
            liquidated, liq_time = True, run.liquidation_time
    return {
        "legs_run": legs_run, "legs_total": len(legs), "liquidated": liquidated, "liquidation_time": liq_time,
        "final_equity": round(equity, 2), "trades": all_trades, "equity_curve": all_curve,
        "fees_total_usd": round(fees, 2), "funding_total_usd": round(funding_total, 2), "refused": refused,
    }


def _max_dd_from_curve(curve: Sequence[Tuple[int, float]], start_equity: float) -> float:
    """Peak-to-trough fraction over `curve`, peak seeded at `start_equity`
    -- the same `(peak - mark) / peak` desk_tick_sim.simulate_steps itself
    uses, recomputed here because a chained run's curve spans more than
    one `simulate()` call's own drawdown tracking."""
    peak = start_equity
    worst = 0.0
    for _, mark in curve:
        peak = max(peak, mark)
        if peak > 0:
            worst = max(worst, (peak - mark) / peak)
    return worst


def summarize_chain(result: Dict[str, Any], params: Params, start_equity: float,
                    start_ms: int, end_ms: int) -> Dict[str, Any]:
    """`brent_stop_buffer_test.summarize`'s own fields, computed over a
    chained run's concatenated trades/equity curve via a synthetic `Run` --
    reuses the exact same scoring the single-window runs use, so a chained
    row and a single-window row are directly comparable."""
    synthetic = Run(
        trades=result["trades"], equity_curve=result["equity_curve"], final_equity=result["final_equity"],
        max_drawdown_pct=_max_dd_from_curve(result["equity_curve"], start_equity),
        liquidated=result["liquidated"], liquidation_time=result["liquidation_time"],
        fees_total_usd=result["fees_total_usd"], funding_total_usd=result["funding_total_usd"], params=params,
        refused=Counter(result.get("refused") or {}),
    )
    s = summarize(synthetic, start_ms, end_ms)
    s["legs_run"] = result["legs_run"]
    s["legs_total"] = result["legs_total"]
    return s


# ── leverage sweep, 2026-11-03 (operator: "try with 5x 10x and 15x is it
#    better roi?") ───────────────────────────────────────────────────────
#
# Two sizing modes on every leverage: (a) the live rule's own sizing --
# risk_cap_frac stays 0.15, only Params.leverage changes (risk_capped_
# notional's risk-cap branch is leverage-INDEPENDENT -- same `per_dollar`
# whatever the leverage -- so the cap only binds where `full_size_notional`
# (which DOES shrink with leverage) is already at or above what the cap
# would size: roughly leverage >= risk_cap_frac / stop_distance_frac. On
# Brent's real ~0.5-1% stop distances that threshold sits in the mid-to-
# high teens, so empirically it is LEVERAGE, not the cap, that binds most
# entries at 5x/10x -- `size_changed_trades` counts this directly rather
# than assuming it); (b) reference only, full slice margin x leverage,
# `risk_cap_frac=None`, to see leverage's raw effect with the cap out of
# the way. The `leverage` gate in `_setup` (stop inside half the way to
# liquidation, `pathiel.agents.bracket.check_leverage`) is the only gate
# that depends on leverage at all -- lower leverage pushes the liquidation
# price further away for the SAME stop, so it only ever relaxes, never
# tightens, going from 20x down; a trade refused at 20x can pass at a
# lower leverage, never the other way round.

LEVERAGE_SWEEP: Tuple[float, ...] = (5.0, 10.0, 15.0, 20.0)   # 20.0 = live


def leverage_params(base: Params, leverage: float, risk_cap_frac: Optional[float]) -> Params:
    """`base` at a different trading leverage (never the venue's own
    `max_leverage`, which is a fixed 20x read limit this desk does not
    control) and risk cap -- mode (a) keeps `risk_cap_frac`, mode (b)
    passes None."""
    return dataclasses.replace(base, leverage=leverage, risk_cap_frac=risk_cap_frac)


def size_changed_count(base_trades: Sequence[Trade], variant_trades: Sequence[Trade],
                       tol: float = 1e-6) -> int:
    """Of `variant_trades` that share an entry_time with a trade in
    `base_trades` (the same setup fired at both leverages -- true for
    every entry the 20x baseline took, since the leverage gate only ever
    relaxes going down), how many opened at a genuinely different size.
    A trade with no matching entry_time in `base_trades` (one the 20x
    baseline's own leverage gate refused, and this leverage's looser gate
    let through) is a NEW trade, not a size change, and is not counted."""
    base_by_t = {t.entry_time: t.size for t in base_trades}
    n = 0
    for t in variant_trades:
        b = base_by_t.get(t.entry_time)
        if b is not None and abs(b - t.size) > tol:
            n += 1
    return n


def _leverage_row(s: Dict[str, Any], start_equity: float, leverage: float) -> Dict[str, Any]:
    """One leverage's row: net $, per-day %, max DD, the worst single
    loss as a share of the equity it opened against, trades, and how many
    entries the `leverage` gate specifically refused (vs. every other
    gate's refusals, unaffected by leverage, reported alongside for
    context)."""
    refused = s.get("refused") or {}
    return {
        "leverage": leverage, "net_usd": round(s["final_equity"] - start_equity, 2),
        "return_pct": s["return_pct"], "per_day_pct": per_day_pct(s), "max_dd_pct": s["max_dd_pct"],
        "worst_loss_pct": s.get("worst_loss_pct"), "trades": s["trades"],
        "refused_leverage": refused.get("leverage", 0), "refused_total": sum(refused.values()),
    }


def leverage_sweep_single(c15: Sequence[Candle], c1h: Sequence[Candle], direction_at: DirectionAt,
                          base: Params, start_ms: int, end_ms: int, funding: Optional[Sequence[Dict[str, Any]]],
                          start_equity: float, leverages: Sequence[float] = LEVERAGE_SWEEP) -> Dict[str, Any]:
    """The leverage sweep on one (non-chained) window: every leverage, both
    sizing modes, one `simulate` call each. `size_changed_trades` (mode a
    only) is measured against this sweep's own 20x row, so it is always
    present even when 20x is not the live window's own baseline leverage."""
    w15 = [c for c in c15 if start_ms <= c.t < end_ms]
    w1h = [c for c in c1h if c.t < end_ms]
    out: Dict[str, Any] = {"a": {}, "b": {}}
    runs_a = {lev: simulate(w15, w1h, direction_at, leverage_params(base, lev, 0.15), funding) for lev in leverages}
    base_trades_a = runs_a[max(leverages)].trades
    for lev, run in runs_a.items():
        row = _leverage_row(summarize(run, start_ms, end_ms), start_equity, lev)
        row["size_changed_trades"] = size_changed_count(base_trades_a, run.trades)
        out["a"][f"{lev:g}x"] = row
    for lev in leverages:
        run = simulate(w15, w1h, direction_at, leverage_params(base, lev, None), funding)
        out["b"][f"{lev:g}x"] = _leverage_row(summarize(run, start_ms, end_ms), start_equity, lev)
    return out


def leverage_sweep_chain(legs: Sequence[Leg], base: Params, start_equity: float,
                         leverages: Sequence[float] = LEVERAGE_SWEEP) -> Dict[str, Any]:
    """The same sweep, chained across every leg (equity carried over, same
    `chain_runs`/`summarize_chain` the plain chained row uses)."""
    chain_start_ms, chain_end_ms = legs[0][4], legs[-1][5]
    out: Dict[str, Any] = {"a": {}, "b": {}}
    results_a = {lev: chain_runs(legs, leverage_params(base, lev, 0.15), start_equity) for lev in leverages}
    base_trades_a = results_a[max(leverages)]["trades"]
    for lev, res in results_a.items():
        s = summarize_chain(res, leverage_params(base, lev, 0.15), start_equity, chain_start_ms, chain_end_ms)
        row = _leverage_row(s, start_equity, lev)
        row["size_changed_trades"] = size_changed_count(base_trades_a, res["trades"])
        out["a"][f"{lev:g}x"] = row
    for lev in leverages:
        res = chain_runs(legs, leverage_params(base, lev, None), start_equity)
        s = summarize_chain(res, leverage_params(base, lev, None), start_equity, chain_start_ms, chain_end_ms)
        out["b"][f"{lev:g}x"] = _leverage_row(s, start_equity, lev)
    return out


LEVERAGE_SUMMARY_FIELDS = ["dataset", "mode", "leverage", "net_usd", "return_pct", "per_day_pct", "max_dd_pct",
                          "worst_loss_pct", "trades", "refused_leverage", "refused_total", "size_changed_trades"]


def leverage_summary_csv_rows(leverage_sweeps: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, sweep in leverage_sweeps.items():
        for mode, rows in sweep.items():
            for lev_label, row in rows.items():
                out.append({"dataset": name, "mode": mode, **{k: row.get(k) for k in LEVERAGE_SUMMARY_FIELDS[2:]}})
    return out


# ── summary rows ─────────────────────────────────────────────────────────

SUMMARY_FIELDS = ["dataset", "variant", "final_equity", "return_pct", "per_day_pct", "max_dd_pct", "trades",
                  "win_rate", "avg_r", "worst_loss_usd", "worst_loss_pct", "target_exits", "stop_exits",
                  "thesis_close_exits", "other_exits"]


def _summary_row(dataset: str, variant: str, s: Dict[str, Any]) -> Dict[str, Any]:
    exits = s.get("exits") or {}
    thesis_close = exits.get("invalidation", 0) + exits.get("direction_flip", 0)
    accounted = exits.get("target", 0) + exits.get("stop", 0) + thesis_close
    return {
        "dataset": dataset, "variant": variant, "final_equity": s["final_equity"], "return_pct": s["return_pct"],
        "per_day_pct": per_day_pct(s), "max_dd_pct": s["max_dd_pct"], "trades": s["trades"],
        "win_rate": s["win_rate"], "avg_r": s["avg_r"], "worst_loss_usd": s.get("worst_loss_usd"),
        "worst_loss_pct": s.get("worst_loss_pct"), "target_exits": exits.get("target", 0),
        "stop_exits": exits.get("stop", 0), "thesis_close_exits": thesis_close,
        "other_exits": s["trades"] - accounted,
    }


def summary_csv_rows(windows: Dict[str, Any], chain: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, w in windows.items():
        out.append(_summary_row(name, "live", w["thesis"]))
        for ctrl, s in w.get("controls", {}).items():
            out.append(_summary_row(name, ctrl, s))
    if chain is not None:
        out.append(_summary_row("chained", "live", chain))
    return out


# ── fetch (the only network I/O) ────────────────────────────────────────────

def fetch_range(coin: str, start_ms: int, end_ms: int, cache_dir: str
                ) -> Tuple[List[Candle], List[Candle], List[Dict[str, Any]]]:
    """15m (paged -- a window this long routinely exceeds the venue's
    5000-bar single-request cap), 1h (one request; 5000 bars is 208 days,
    comfortably more than this eval ever needs) and hourly funding, all
    disk-cached under `cache_dir`."""
    c15 = D.fetch_candles_paged_cached(coin, "15m", start_ms, end_ms, cache_dir)
    hourly_count = int((end_ms - start_ms) / HOUR_MS) + BUFFER_DAYS * 24 + 60
    c1h = D.fetch_candles_cached(coin, "1h", hourly_count, cache_dir)
    funding = D.fetch_funding_cached(coin, start_ms, end_ms, cache_dir)
    return c15, c1h, funding


# ── main ─────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--window", action="append", default=[], metavar="NAME=PATH",
                    help="repeatable, oldest first: a replay/journal theses dir and its label, "
                        "chained in the given order for the combined-equity run")
    ap.add_argument("--journal-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal, reported as context, never chained")
    ap.add_argument("--coin", default="xyz:BRENTOIL")
    ap.add_argument("--start-equity", type=float, default=64.0)
    ap.add_argument("--cache-dir", default="/tmp/extended-history/cache")
    ap.add_argument("--out-dir", default="/tmp/extended-history")
    a = ap.parse_args(argv)

    try:
        windows_arg = parse_windows(a.window)
    except ValueError as e:
        print(f"FATAL: {e}")
        return 2
    if not windows_arg:
        print("FATAL: pass at least one --window NAME=PATH")
        return 2

    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} extended-history: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    loaded: List[Tuple[str, str, List[D.ThesisDirection]]] = []
    for name, path in windows_arg:
        theses = D.load_journaled_theses(path)
        if not theses:
            log(f"{name}: no theses at {path!r}, skipped")
            continue
        loaded.append((name, path, theses))
    journal_theses = D.load_journaled_theses(a.journal_dir) if a.journal_dir else []
    if not journal_theses and a.journal_dir:
        log(f"journal: no theses at {a.journal_dir!r}, skipped")

    if not loaded:
        print("FATAL: no theses found in any --window dir")
        return 2

    all_for_range = [th for _, _, ths in loaded for th in ths] + list(journal_theses)
    earliest_ms = min(th.created_at_ms for th in all_for_range)
    fetch_start_ms = earliest_ms - BUFFER_DAYS * DAY_MS
    now_ms = int(time.time() * 1000)
    log(f"fetching {a.coin} back to {datetime.fromtimestamp(fetch_start_ms / 1000, tz=timezone.utc).isoformat()} "
        f"({(now_ms - fetch_start_ms) / DAY_MS:.0f} days)")
    c15, c1h, funding = fetch_range(a.coin, fetch_start_ms, now_ms, a.cache_dir)
    if not c15 or not c1h:
        print("FATAL: no candles fetched; cannot run the backtest.")
        return 2
    data_end_ms = max(c.t for c in c15) + FIFTEEN_MS
    log(f"data end {datetime.fromtimestamp(data_end_ms / 1000, tz=timezone.utc).isoformat()}, "
        f"{len(loaded)} window(s), journal={'yes' if journal_theses else 'no'}")

    bp = dataclasses.replace(base_params(), starting_equity=a.start_equity, risk_cap_frac=0.15)

    windows: Dict[str, Any] = {}
    legs: List[Leg] = []
    for name, path, theses in loaded:
        start_ms = min(th.created_at_ms for th in theses)
        end_ms = min(data_end_ms, max(th.created_at_ms for th in theses) + DAY_MS)
        d_at = journaled_direction(theses)
        legs.append((d_at, c15, c1h, funding, start_ms, end_ms))
        thesis_run = run_dataset(c15, c1h, d_at, bp, start_ms, end_ms, {"live": {}}, funding=funding)
        controls = {ctrl: run_dataset(c15, c1h, direction_series(ctrl, c1h), bp, start_ms, end_ms,
                                      {"live": {}}, funding=funding)["variants"]["live"]
                   for ctrl in CONTROLS}
        windows[name] = {
            "path": path, "n_theses": len(theses), "window": thesis_run["window"],
            "thesis": thesis_run["variants"]["live"], "controls": controls,
            "buy_hold_pct": buy_hold_pct(c15, start_ms, end_ms),
            "direction_hit_24h": direction_hit_rate(theses, c1h, 1.0),
            "direction_hit_7d": direction_hit_rate(theses, c1h, 7.0),
        }
        log(f"{name}: {len(theses)} theses, {thesis_run['variants']['live']['trades']} trades, "
            f"final ${thesis_run['variants']['live']['final_equity']:.2f} "
            f"({time.time() - t0:.0f}s)")

    chain_result = chain_runs(legs, bp, a.start_equity)
    chain_start_ms = legs[0][4]
    chain_end_ms = legs[-1][5]
    chain = summarize_chain(chain_result, bp, a.start_equity, chain_start_ms, chain_end_ms)
    chain["window"] = [datetime.fromtimestamp(chain_start_ms / 1000, tz=timezone.utc).isoformat(),
                       datetime.fromtimestamp(chain_end_ms / 1000, tz=timezone.utc).isoformat()]
    chain["per_day_pct"] = per_day_pct(chain)
    log(f"chained: {chain['legs_run']}/{chain['legs_total']} legs, final ${chain['final_equity']:.2f} "
        f"({time.time() - t0:.0f}s)")

    journal_row = None
    j_start = j_end = None
    if journal_theses:
        j_start = min(th.created_at_ms for th in journal_theses)
        j_end = min(data_end_ms, max(th.created_at_ms for th in journal_theses) + DAY_MS)
        j_run = run_dataset(c15, c1h, journaled_direction(journal_theses), bp, j_start, j_end,
                            {"live": {}}, funding=funding)
        journal_row = {"path": a.journal_dir, "n_theses": len(journal_theses), "window": j_run["window"],
                       "thesis": j_run["variants"]["live"],
                       "buy_hold_pct": buy_hold_pct(c15, j_start, j_end)}

    # ── leverage sweep (operator, 2026-11-03: "try with 5x 10x and 15x is
    # it better roi?"): every window this run has (sep/oct-style windows,
    # the chained run, the read-only journal) plus an always_long control
    # over the full chained span, each at 5/10/15/20x, both sizing modes.
    log("leverage sweep: 5x/10x/15x/20x, risk-capped and full-slice, every window")
    leverage_sweeps: Dict[str, Any] = {}
    for name, path, theses in loaded:
        start_ms = min(th.created_at_ms for th in theses)
        end_ms = min(data_end_ms, max(th.created_at_ms for th in theses) + DAY_MS)
        leverage_sweeps[name] = leverage_sweep_single(c15, c1h, journaled_direction(theses), bp,
                                                      start_ms, end_ms, funding, a.start_equity)
    leverage_sweeps["chained"] = leverage_sweep_chain(legs, bp, a.start_equity)
    if journal_row is not None:
        leverage_sweeps["journal"] = leverage_sweep_single(c15, c1h, journaled_direction(journal_theses), bp,
                                                            j_start, j_end, funding, a.start_equity)
    leverage_sweeps["always_long"] = leverage_sweep_single(c15, c1h, direction_series("always_long", c1h), bp,
                                                           chain_start_ms, chain_end_ms, funding, a.start_equity)
    log(f"leverage sweep done ({time.time() - t0:.0f}s)")

    with open(os.path.join(a.out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(summary_csv_rows(windows, chain))
        if journal_row is not None:
            w.writerows([_summary_row("journal", "live", journal_row["thesis"])])
    with open(os.path.join(a.out_dir, "leverage_summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LEVERAGE_SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(leverage_summary_csv_rows(leverage_sweeps))

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "coin": a.coin, "data_end": datetime.fromtimestamp(data_end_ms / 1000, tz=timezone.utc).isoformat(),
        "start_equity": a.start_equity, "windows": windows, "chain": chain, "journal": journal_row,
        "leverage_sweep": leverage_sweeps,
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\nsummary: {os.path.join(a.out_dir, 'summary.csv')}"
          f"\nleverage summary: {os.path.join(a.out_dir, 'leverage_summary.csv')}")
    return 0


def _print(rep: Dict[str, Any]) -> None:
    print(f"coin {rep['coin']}, data end {rep['data_end']}, start equity ${rep['start_equity']:.2f}")
    for name, w in rep["windows"].items():
        t = w["thesis"]
        print(f"\n== {name}  {w['window'][0]} -> {w['window'][1]}  ({w['n_theses']} theses)")
        print(f"  live        final ${t['final_equity']:>8.2f}  ret {t['return_pct']:>6.1f}%  "
              f"dd {t['max_dd_pct']:>5.1f}%  n={t['trades']:>3}  win {t.get('win_rate') or 0:.0%}  "
              f"avgR {t.get('avg_r') or 0:+.2f}")
        for ctrl, s in w["controls"].items():
            print(f"  {ctrl:<10}  final ${s['final_equity']:>8.2f}  ret {s['return_pct']:>6.1f}%  "
                  f"dd {s['max_dd_pct']:>5.1f}%  n={s['trades']:>3}  win {s.get('win_rate') or 0:.0%}  "
                  f"avgR {s.get('avg_r') or 0:+.2f}")
        print(f"  buy&hold    {w['buy_hold_pct']}%")
        print(f"  direction hit  24h {w['direction_hit_24h']}  7d {w['direction_hit_7d']}")
    c = rep["chain"]
    print(f"\n== chained ({c['legs_run']}/{c['legs_total']} legs)  {c['window'][0]} -> {c['window'][1]}")
    print(f"  final ${c['final_equity']:>8.2f}  ret {c['return_pct']:>6.1f}%  dd {c['max_dd_pct']:>5.1f}%  "
          f"n={c['trades']:>3}  win {c.get('win_rate') or 0:.0%}  avgR {c.get('avg_r') or 0:+.2f}  "
          f"liquidated={c['liquidated']}")
    if rep.get("journal"):
        j = rep["journal"]["thesis"]
        print(f"\n== journal (context, not chained)  {rep['journal']['n_theses']} theses")
        print(f"  final ${j['final_equity']:>8.2f}  ret {j['return_pct']:>6.1f}%  dd {j['max_dd_pct']:>5.1f}%  "
              f"n={j['trades']:>3}  buy&hold {rep['journal']['buy_hold_pct']}%")
    if rep.get("leverage_sweep"):
        print("\n== leverage sweep (a: risk-capped 15%, b: full-slice, no cap)")
        for name, sweep in rep["leverage_sweep"].items():
            for mode in ("a", "b"):
                rows = sweep.get(mode) or {}
                print(f"  {name:<12} [{mode}]  "
                      + "  ".join(f"{lev}:${r['net_usd']:+.2f}/{r['max_dd_pct']:.0f}%dd/n{r['trades']}"
                                  f"/ref{r['refused_leverage']}" for lev, r in rows.items()))


if __name__ == "__main__":
    raise SystemExit(main())
