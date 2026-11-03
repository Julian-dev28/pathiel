"""Final oil-strategy adoption test, 2026-11-04 (docs/strategy/FINAL_STRATEGY_TEST_2026-11-04.md).

Research only. Never touches the live account, the live config, the live
journal (read only) or any pathiel MCP tool. The operator will pick the
live oil strategy off this result, so every number here comes off ONE
pinned candle snapshot (`services.strategies.backtest.snapshot`) instead
of each harness fetching its own -- the bug that made the "September
replay, live rules" row read -$34.45 in one 2026-11-03 eval and +$29.55 in
another, same code, same theses, different candles (the venue's
`candleSnapshot` only serves the latest ~5000 15m bars, so two runs on two
different days see two different Septembers).

Variants, every one built on `live_params.live_brent_params()` (the 15%
risk cap, size-aware slippage):
  live               the unmodified live rule
  lev_fit            WIDE_STOP_SWING_TEST_2026-11-03.md's winner: the
                     highest of 20/15/10/8/5x whose stop still clears half
                     the liquidation distance (same 15% risk cap)
  entries_reclaim    one retry after a stop, gated on a 1h close back
                     inside the range the stop broke out of (max_stop_
                     reentries=1, reentry_needs_1h_reclaim=True)
  eia_2_2            OIL_EVENT_TIMING_TEST_2026-11-03.md's winner: no new
                     entry within 2h either side of an EIA report
  lev_fit_eia        lev_fit + eia_2_2
  lev_fit_reclaim    lev_fit + entries_reclaim
  combined           lev_fit + entries_reclaim + eia_2_2
  live_half_rN       live, plus scale out half the opening size as a
                     resting limit once the best price reaches N R in
                     favour (Params.partial_frac=0.5, partial_r=N),
                     N in (3, 4, 5, 6)
  combined_half_rN   combined, plus the same half-off rule

Datasets: the live oil journal (READ ONLY), the September replay, the
October replay, the two chained with equity carried over
(extended_history_test.chain_runs), and always_long/always_short/
daily_sign over the snapshot's last 30 days. Adopt bar (the operator's
rule, same one every other 2026-11 eval in this repo uses): beats live on
at least 3 of the 4 thesis datasets (journal/oct/sep/chain), max drawdown
no worse than live (within 2 points), a plateau across neighbouring
settings, and every gate decision keeps the stop inside half the
liquidation distance (a slippage-only overshoot on the realized FILL is
reported, not disqualifying).

    python -m services.strategies.evals.final_strategy_test \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/<stamp> \\
        [--oil-theses-dir <pathia>/.state/strategies/oil/theses] \\
        [--sep-theses-dir ~/Documents/code/pathiel-replays/oil-sep/theses] \\
        [--oct-theses-dir ~/Documents/code/pathiel-replays/oil-oct/theses]

Writes /tmp/final-strategy/report.json and summary.csv. No network (reads
the pinned snapshot only), no LLM calls.
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
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest import oil_calendar as OC
from services.strategies.backtest import snapshot as SNAP
from services.strategies.backtest.desk_tick_sim import Params, Run, risk_capped_notional, simulate
from services.strategies.evals.brent_stop_buffer_test import (
    BASELINE, CONTROLS, MAX_STOP_FRAC_OF_LIQ, _iso, _ms, _scores, pick_best, plateau, run_dataset, stop_liq_frac)
from services.strategies.evals.closer_target_test import per_day_pct
from services.strategies.evals.desk_tick_backtest import DAY_MS, journaled_direction
from services.strategies.evals.extended_history_test import Leg, chain_runs, summarize_chain
from services.strategies.evals.live_params import live_brent_params
from services.strategies.evals.market_scout import direction_series
from services.strategies.evals.wide_stop_swing_test import LEV_GRID

FIFTEEN_MS = 900_000
START = 64.0
PARTIAL_RS: Tuple[float, ...] = (3.0, 4.0, 5.0, 6.0)
THESIS_DATASETS: Tuple[str, ...] = ("journal", "oct", "sep", "chain")

LEV_FIT_OV: Dict[str, Any] = {"entry_fit_leverage": LEV_GRID}
RECLAIM_OV: Dict[str, Any] = {"max_stop_reentries": 1, "reentry_needs_1h_reclaim": True}


# ── variants ─────────────────────────────────────────────────────────────

def variants(eia_windows: Tuple[Tuple[int, int], ...]) -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides on `live_brent_params()`."""
    eia_ov = {"event_blackout_windows": eia_windows}
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    out["lev_fit"] = dict(LEV_FIT_OV)
    out["entries_reclaim"] = dict(RECLAIM_OV)
    out["eia_2_2"] = dict(eia_ov)
    out["lev_fit_eia"] = {**LEV_FIT_OV, **eia_ov}
    out["lev_fit_reclaim"] = {**LEV_FIT_OV, **RECLAIM_OV}
    out["combined"] = {**LEV_FIT_OV, **RECLAIM_OV, **eia_ov}
    for r in PARTIAL_RS:
        out[f"live_half_r{r:g}"] = {"partial_frac": 0.5, "partial_r": r}
        out[f"combined_half_r{r:g}"] = {**out["combined"], "partial_frac": 0.5, "partial_r": r}
    return out


FAMILIES: Dict[str, List[str]] = {
    "half_live": [f"live_half_r{r:g}" for r in PARTIAL_RS],
    "half_combined": [f"combined_half_r{r:g}" for r in PARTIAL_RS],
}


# ── leverage distribution + "extra trades" (lev_fit vs live) ───────────────

def leverage_distribution(run: Run, params: Params) -> Dict[str, int]:
    """Count of opened trades per leverage actually used (Trade.leverage,
    falling back to `params.leverage` when a trade didn't set its own --
    every non-lev_fit variant). Keys as e.g. '20x'. Pure."""
    c: Counter = Counter()
    for tr in run.trades:
        lev = tr.leverage if tr.leverage is not None else params.leverage
        c[f"{lev:g}x"] += 1
    return dict(sorted(c.items(), key=lambda kv: -float(kv[0][:-1])))


def extra_trades(base_run: Run, var_run: Run, base_params: Params, var_params: Params) -> Dict[str, Any]:
    """Trades `var_run` opened that `base_run` has no trade at the same
    entry_time for -- i.e. entries the variant's gate let through that the
    baseline's gate refused outright (lev_fit: a stop that only clears
    half the liquidation distance at a LOWER leverage than live's fixed
    20x). Matched by entry_time only (both runs share the same candle
    clock and direction series, so a real "same decision, different gate"
    pair always lands on the same tick); a genuinely new trade from a
    reentry/timing difference is included too and is not double-counted
    (one entry_time -> one trade in each run, re-entries all file in by
    the exact bar they filled on). Pure."""
    base_times = {tr.entry_time for tr in base_run.trades}
    extra = [tr for tr in var_run.trades if tr.entry_time not in base_times]
    rows = [{"entry_time": _iso(tr.entry_time), "side": tr.side,
            "leverage": tr.leverage if tr.leverage is not None else var_params.leverage,
            "entry_px": round(tr.entry_px, 4), "exit_reason": tr.exit_reason,
            "net_usd": round(tr.net_pnl_usd, 2)} for tr in extra]
    return {"count": len(rows), "net_usd_sum": round(sum(r["net_usd"] for r in rows), 2), "trades": rows,
           "base_refused_leverage": base_run.refused.get("leverage", 0),
           "var_refused_leverage": var_run.refused.get("leverage", 0)}


# ── realized stop/liquidation overshoot (slippage only) ────────────────────

def _trade_liq_frac(entry: float, stop: float, leverage: float, params: Params) -> Optional[float]:
    p = dataclasses.replace(params, leverage=leverage)
    return stop_liq_frac(entry, stop, p)


LEV_FIT_VARIANTS: Tuple[str, ...] = ("lev_fit", "lev_fit_eia", "lev_fit_reclaim", "combined",
                                     *(f"combined_half_r{r:g}" for r in PARTIAL_RS))


def corrected_max_stop_liq_frac(run: Run, params: Params) -> float:
    """`brent_stop_buffer_test.risk_numbers`'s own `max_stop_liq_frac`
    computes `stop_liq_frac(entry, stop, params)` off `params.leverage`
    for EVERY trade -- correct for every variant here except the
    `entry_fit_leverage` ones (lev_fit/lev_fit_eia/lev_fit_reclaim/
    combined/combined_half_rN), where a trade's OWN leverage (Trade.
    leverage) can be lower than `params.leverage` (the 20x default the
    other candidates fall back from). Scoring those trades at 20x makes a
    stop that cleared half the liquidation distance at the leverage it
    actually opened at (say 10x, more room to liquidation) look like it
    blew through that line at 20x -- not a real gate violation, a stale
    `params.leverage` read. This recomputes the same ratio, per trade, at
    the trade's own leverage. Across every trade (not just stop exits),
    matching risk_numbers' own scope. Pure."""
    worst = 0.0
    for tr in run.trades:
        lev = tr.leverage if tr.leverage is not None else params.leverage
        stop = tr.orig_stop_px if tr.orig_stop_px is not None else tr.stop_px
        f = _trade_liq_frac(tr.entry_px, stop, lev, params)
        if f is not None:
            worst = max(worst, f)
    return worst


def realized_stop_overshoot(run: Run, params: Params) -> Dict[str, Any]:
    """For every stop-exit trade, the nominal gate's stop_liq_frac (at the
    trade's own leverage, using the stop price the gate actually judged --
    `orig_stop_px`) versus the same ratio recomputed off the REALIZED fill
    price (`exit_px`, which can sit past the nominal stop under slippage/a
    gap). The gate itself only ever sees the nominal stop, so a realized
    overshoot here is a slippage effect, not a gate failure -- reported,
    not disqualifying. Pure."""
    worst_nominal, worst_realized = 0.0, 0.0
    for tr in run.trades:
        if tr.exit_reason != "stop" or tr.exit_px is None:
            continue
        lev = tr.leverage if tr.leverage is not None else params.leverage
        nominal_stop = tr.orig_stop_px if tr.orig_stop_px is not None else tr.stop_px
        nf = _trade_liq_frac(tr.entry_px, nominal_stop, lev, params)
        rf = _trade_liq_frac(tr.entry_px, tr.exit_px, lev, params)
        if nf is not None:
            worst_nominal = max(worst_nominal, nf)
        if rf is not None:
            worst_realized = max(worst_realized, rf)
    return {"max_nominal_stop_liq_frac": round(worst_nominal, 4), "max_realized_stop_liq_frac": round(worst_realized, 4),
           "slippage_overshoot": round(max(0.0, worst_realized - worst_nominal), 4)}


# ── the 2026-11-03 regression long, half-off at 4R/5R (and 3R/6R) ──────────

# Located empirically in the pinned snapshot: a resting long limit filled
# ~103.61 inside the 2026-11-02 16:53Z call's range (24h low near 103.3-
# 103.7 that evening; the call's own suggested stop/target, 100.1/110.36,
# are the THESIS's numbers, not the structural stop/target the live loop
# actually used -- 102.99/111.77, per the operator). Risk = 0.62. Peaked
# 107.30 at 07:15Z on 11-03 (the operator's "about 107.24" -- same bar
# range), stopped trading through 102.99 on the 13:30Z bar (low 101.42),
# a realistic slipped fill ~102.91.
REGRESSION_TRADE: Dict[str, Any] = {
    "side": "long", "entry_utc": (2026, 11, 2, 18, 0), "entry_px": 103.61, "stop_px": 102.99,
    "target_px": 111.77, "stop_fill_px": 102.91,
}


def regression_row(c15: Sequence[Candle], base: Params, trade: Dict[str, Any] = REGRESSION_TRADE,
                   partial_rs: Sequence[float] = PARTIAL_RS) -> Dict[str, Any]:
    """Walks `trade` forward on real 15m candles with and without half-off
    at each R in `partial_rs`, using the SAME partial-fill arithmetic
    `desk_tick_sim._scale_out` uses (price = entry +/- r x risk, maker fee,
    trade-through to fill, never past the target). Entry/exit fees use the
    live rule's taker/maker fees; size off `risk_capped_notional` at
    `base.starting_equity`. Pure given `c15`."""
    side, entry, stop, target = trade["side"], trade["entry_px"], trade["stop_px"], trade["target_px"]
    is_long = side == "long"
    sign = 1.0 if is_long else -1.0
    risk = abs(entry - stop)
    t0 = _ms(trade["entry_utc"])
    bars = [c for c in sorted(c15, key=lambda c: c.t) if c.t > t0]
    notional = risk_capped_notional(base.starting_equity, base, base.leverage, entry, stop)
    size = notional / entry if entry > 0 else 0.0
    entry_fee = base.taker_fee * entry * size

    def walk(partial_frac: float, partial_r: Optional[float]) -> Dict[str, Any]:
        remaining = size
        partial_px = None
        if partial_frac > 0 and partial_r is not None:
            partial_px = entry + sign * partial_r * risk
            if (partial_px >= target) if is_long else (partial_px <= target):
                partial_px = None   # the scale-out sits at/past the target: skip it, same as _scale_out
        partial_done = partial_px is None
        realized = -entry_fee
        step = base.tick_size
        for bar in bars:
            if not partial_done:
                # trade-through, not a touch -- same as _scale_out's resting limit
                through = (bar.h >= partial_px + step - 1e-9) if is_long else (bar.l <= partial_px - step + 1e-9)
                if through:
                    part_size = size * partial_frac
                    realized += sign * (partial_px - entry) * part_size - base.maker_fee * partial_px * part_size
                    remaining -= part_size
                    partial_done = True
            # the stop is a market order: triggers on a touch (replay_locked's own convention)
            stop_hit = (bar.l <= stop + 1e-9) if is_long else (bar.h >= stop - 1e-9)
            if stop_hit:
                px = trade.get("stop_fill_px", stop)
                realized += sign * (px - entry) * remaining - base.taker_fee * px * remaining
                return {"exit": "stop", "exit_time": _iso(bar.t), "exit_px": px, "net_usd": round(realized, 2),
                        "remaining_size_frac": round(remaining / size, 3) if size else None,
                        "partial_filled": partial_px is not None and remaining < size - 1e-9,
                        "partial_px": round(partial_px, 3) if partial_px else None}
            if base.target_mode == "limit":
                tgt_hit = (bar.h >= target + step - 1e-9) if is_long else (bar.l <= target - step + 1e-9)
            else:
                tgt_hit = (bar.h >= target) if is_long else (bar.l <= target)
            if tgt_hit:
                realized += sign * (target - entry) * remaining - base.maker_fee * target * remaining
                return {"exit": "target", "exit_time": _iso(bar.t), "exit_px": target, "net_usd": round(realized, 2),
                        "remaining_size_frac": round(remaining / size, 3) if size else None,
                        "partial_filled": partial_px is not None and remaining < size - 1e-9,
                        "partial_px": round(partial_px, 3) if partial_px else None}
        last = bars[-1] if bars else None
        px = last.c if last else entry
        realized += sign * (px - entry) * remaining - base.taker_fee * px * remaining
        return {"exit": "open_at_data_end", "exit_time": _iso(last.t) if last else None, "exit_px": px,
                "net_usd": round(realized, 2), "remaining_size_frac": round(remaining / size, 3) if size else None,
                "partial_filled": partial_px is not None and remaining < size - 1e-9,
                "partial_px": round(partial_px, 3) if partial_px else None}

    out: Dict[str, Any] = {"trade": dict(trade), "size": round(size, 3), "risk_usd_per_unit": round(risk, 4),
                           "baseline": walk(0.0, None), "half_off": {}}
    for r in partial_rs:
        out["half_off"][f"r{r:g}"] = walk(0.5, r)
    return out


# ── adopt bar ────────────────────────────────────────────────────────────

def dd_and_liq(results: Dict[str, Any], named: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, float], Dict[str, float]]:
    max_dd = {v: max((r["variants"][v]["max_dd_pct"] for r in results.values() if v in r["variants"]), default=0.0)
             for v in named}
    max_liq = {v: max((r["variants"][v]["max_stop_liq_frac"] for r in results.values() if v in r["variants"]),
                      default=0.0) for v in named}
    return max_dd, max_liq


def adopt_verdict(thesis_scores: Dict[str, Dict[str, Any]], max_dd: Dict[str, float], max_liq: Dict[str, float],
                  name: str, live_dd: float) -> Dict[str, Any]:
    """The operator's bar, literally: beats live on >= 3 of the 4 thesis
    datasets, max DD no worse than live within 2 points, a plateau where
    one applies (the half-off R grids; lev_fit/entries_reclaim/eia_2_2 were
    already shown a plateau in their OWN source tests -- see the doc), and
    the stop never past half the liquidation distance."""
    s = thesis_scores.get(name)
    if s is None:
        return {"adopt": False, "candidate": name, "why": "no score"}
    beats_bar = s["beats"] >= 3
    dd_ok = max_dd.get(name, 0.0) <= live_dd + 2.0
    liq_ok = max_liq.get(name, 0.0) <= MAX_STOP_FRAC_OF_LIQ + 1e-9
    flat = plateau(thesis_scores, name, FAMILIES) if name in sum(FAMILIES.values(), []) else True
    why = [w for w, ok in (("beats live on >=3/4 thesis datasets", beats_bar), ("max DD within 2pts of live", dd_ok),
                           ("plateau", flat), ("stop inside half the liquidation distance", liq_ok)) if not ok]
    return {"adopt": beats_bar and dd_ok and flat and liq_ok, "candidate": name, "beats": s["beats"],
           "loses": s["loses"], "of": s["of"], "geo_mean_ratio": s["geo_mean_ratio"], "max_dd_pct": max_dd.get(name),
           "max_stop_liq_frac": max_liq.get(name), "plateau": flat, "fails": why}


# ── CSV / report plumbing ───────────────────────────────────────────────

SUMMARY_FIELDS = ["dataset", "variant", "final_equity", "return_pct", "per_day_pct", "max_dd_pct", "trades",
                  "win_rate", "avg_r", "worst_loss_pct", "max_stop_liq_frac", "stop_exits"]


def summary_csv_rows(results: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for ds, r in results.items():
        for v, s in r["variants"].items():
            exits = s.get("exits") or {}
            out.append({"dataset": ds, "variant": v, "final_equity": s["final_equity"],
                        "return_pct": s["return_pct"], "per_day_pct": per_day_pct(s), "max_dd_pct": s["max_dd_pct"],
                        "trades": s["trades"], "win_rate": s["win_rate"], "avg_r": s["avg_r"],
                        "worst_loss_pct": s["worst_loss_pct"], "max_stop_liq_frac": s["max_stop_liq_frac"],
                        "stop_exits": exits.get("stop", 0)})
    return out


def _window_report(snap: SNAP.Snapshot, thesis_windows: Dict[str, Tuple[int, int]]) -> Dict[str, Any]:
    out = {"snapshot": {iv: snap.window_iso(iv) for iv in SNAP.INTERVALS}}
    for name, (st, en) in thesis_windows.items():
        out[name] = {"requested_start": _iso(st), "effective_start": _iso(max(st, snap.window("15m")[0])),
                     "end": _iso(en)}
    return out


# ── main ─────────────────────────────────────────────────────────────────

def _latest_snapshot_dir() -> Optional[str]:
    root = SNAP.DEFAULT_SNAPSHOT_ROOT
    if not os.path.isdir(root):
        return None
    stamps = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    return os.path.join(root, stamps[-1]) if stamps else None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot-dir", default=_latest_snapshot_dir(),
                    help=f"default: the newest dir under {SNAP.DEFAULT_SNAPSHOT_ROOT}")
    ap.add_argument("--coin", default=SNAP.DEFAULT_COIN)
    ap.add_argument("--oil-theses-dir", default=os.environ.get(
        "PATHIEL_OIL_THESES_DIR", os.path.expanduser("~/Documents/code/pathia/.state/strategies/oil/theses")),
        help="READ ONLY: the live oil journal's theses")
    ap.add_argument("--sep-theses-dir", default=os.path.expanduser("~/Documents/code/pathiel-replays/oil-sep/theses"))
    ap.add_argument("--oct-theses-dir", default=os.path.expanduser("~/Documents/code/pathiel-replays/oil-oct/theses"))
    ap.add_argument("--out-dir", default="/tmp/final-strategy")
    a = ap.parse_args(argv)
    if not a.snapshot_dir:
        raise SystemExit("no --snapshot-dir given and none found under " + SNAP.DEFAULT_SNAPSHOT_ROOT)
    os.makedirs(a.out_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} final-strategy: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    snap = SNAP.load_snapshot(a.snapshot_dir, a.coin)
    c15, c1h, funding = snap.c15, snap.c1h, snap.funding
    if not c15 or not c1h:
        raise SystemExit(f"snapshot at {a.snapshot_dir!r} has no candles -- run snapshot.py first")
    b_start, b_end = snap.window("15m")
    h_start, h_end = snap.window("1h")
    log(f"snapshot {a.snapshot_dir}: 15m {snap.window_iso('15m')}, 1h {snap.window_iso('1h')}")

    base = live_brent_params(size_slippage=True, starting_equity=START)
    eia_times = [t for t, _ in OC.eia_report_times(h_start, h_end)]
    eia_windows = OC.blackout_windows(eia_times, 2.0, 2.0)
    named = variants(eia_windows)
    log(f"{len(named)} variants, {len(eia_times)} EIA reports in window")

    results: Dict[str, Any] = {}
    thesis_windows: Dict[str, Tuple[int, int]] = {}
    chain_legs: List[Tuple[str, Any, int, int]] = []

    for label, tdir, end_rule in (("journal", a.oil_theses_dir, "data"), ("sep", a.sep_theses_dir, "last+1d"),
                                  ("oct", a.oct_theses_dir, "last+1d")):
        theses = D.load_journaled_theses(tdir)
        if not theses:
            log(f"{label}: no theses at {tdir!r}, skipped")
            continue
        start = min(th.created_at_ms for th in theses)
        end = b_end if end_rule == "data" else min(b_end, max(th.created_at_ms for th in theses) + DAY_MS)
        if end <= start:
            log(f"{label}: window empty after clamping to the snapshot ({_iso(start)}..{_iso(end)}), skipped")
            continue
        thesis_windows[label] = (start, end)
        d_at = journaled_direction(theses)
        results[label] = run_dataset(c15, c1h, d_at, base, start, end, named, funding=funding)
        log(f"{label}: {len(theses)} theses, window {_iso(start)}..{_iso(end)}")
        if label in ("sep", "oct"):
            chain_legs.append((label, d_at, start, end))

    if len(chain_legs) == 2:
        chain_legs.sort(key=lambda x: x[2])   # chronological: sep then oct
        chain_window = (chain_legs[0][2], chain_legs[-1][3])
        thesis_windows["chain"] = chain_window
        chain_variants: Dict[str, Any] = {}
        for v, ov in named.items():
            p = dataclasses.replace(base, **ov)
            legs: List[Leg] = [(d_at, c15, c1h, funding, st, en) for (_, d_at, st, en) in chain_legs]
            chained = chain_runs(legs, p, START)
            chain_variants[v] = summarize_chain(chained, p, START, chain_window[0], chain_window[1])
        results["chain"] = {"window": [_iso(chain_window[0]), _iso(chain_window[1])], "variants": chain_variants}
        log("chain: sep+oct, equity carried over")
    else:
        log(f"chain: only {len(chain_legs)}/2 legs available, skipped")

    ctrl_start = b_end - 30 * DAY_MS
    for s in CONTROLS:
        d_at = direction_series(s, c1h)
        results[f"{s}_30d"] = run_dataset(c15, c1h, d_at, base, ctrl_start, b_end, named, funding=funding)
    log(f"controls: {', '.join(CONTROLS)} over the last 30 days ({_iso(ctrl_start)}..{_iso(b_end)})")

    thesis_scores = _scores(results, {"thesis": [d for d in THESIS_DATASETS if d in results]})["thesis"]
    max_dd, max_liq = dd_and_liq(results, named)

    # ── re-run every base dataset's live/lev_fit/combined* runs ONCE, raw
    #    Run objects in hand -- feeds the leverage-correctness fix below,
    #    the leverage distribution / extra-trades report, and the realized
    #    stop/liquidation overshoot report, with no duplicate simulate()
    #    calls across the three. chain's trades are the concatenation of
    #    the sep and oct legs under the SAME params (summarize_chain only
    #    varies starting_equity, which stop_liq_frac never reads), so its
    #    worst case is already covered by sep/oct here -- no separate run.
    base_ds = [d for d in THESIS_DATASETS if d in results and d != "chain"]
    runs: Dict[str, Dict[str, Run]] = {}       # ds -> variant -> Run
    for ds in base_ds:
        w15 = [c for c in c15 if thesis_windows[ds][0] <= c.t < thesis_windows[ds][1]]
        w1h = [c for c in c1h if c.t < thesis_windows[ds][1]]
        theses_dir = {"journal": a.oil_theses_dir, "sep": a.sep_theses_dir, "oct": a.oct_theses_dir}[ds]
        d_at = journaled_direction(D.load_journaled_theses(theses_dir))
        runs[ds] = {BASELINE: simulate(w15, w1h, d_at, base, funding)}
        for v in LEV_FIT_VARIANTS:
            vp = dataclasses.replace(base, **named[v])
            runs[ds][v] = simulate(w15, w1h, d_at, vp, funding)

    for v in LEV_FIT_VARIANTS:
        vp = dataclasses.replace(base, **named[v])
        max_liq[v] = max(corrected_max_stop_liq_frac(runs[ds][v], vp) for ds in base_ds)

    live_dd = max_dd.get(BASELINE, 0.0)
    best = pick_best(thesis_scores)
    verdicts = {v: adopt_verdict(thesis_scores, max_dd, max_liq, v, live_dd) for v in named}
    adopt = sorted(v for v, ver in verdicts.items() if ver["adopt"])
    log(f"scored ({time.time() - t0:.0f}s); adopt {adopt or 'none'}; best by beats {best}")

    # ── leverage distribution + extra trades the operator asked for ──────
    leverage_report: Dict[str, Any] = {}
    for ds in base_ds:
        leverage_report[ds] = {"live": {"leverage_counts": leverage_distribution(runs[ds][BASELINE], base)}}
        for v in ("lev_fit", "combined"):
            vp = dataclasses.replace(base, **named[v])
            leverage_report[ds][v] = {
                "leverage_counts": leverage_distribution(runs[ds][v], vp),
                "extra_trades_vs_live": extra_trades(runs[ds][BASELINE], runs[ds][v], base, vp)}
    log("leverage distribution + extra-trades (lev_fit, combined) computed")

    # ── realized stop/liquidation overshoot, per variant, worst dataset ──
    overshoot_report: Dict[str, Any] = {}
    for v in named:
        if v in LEV_FIT_VARIANTS:
            os_rows = [realized_stop_overshoot(runs[ds][v], dataclasses.replace(base, **named[v])) for ds in base_ds]
        else:
            vp = dataclasses.replace(base, **named[v])
            os_rows = []
            for ds in base_ds:
                w15 = [c for c in c15 if thesis_windows[ds][0] <= c.t < thesis_windows[ds][1]]
                w1h = [c for c in c1h if c.t < thesis_windows[ds][1]]
                theses_dir = {"journal": a.oil_theses_dir, "sep": a.sep_theses_dir, "oct": a.oct_theses_dir}[ds]
                d_at = journaled_direction(D.load_journaled_theses(theses_dir))
                os_rows.append(realized_stop_overshoot(simulate(w15, w1h, d_at, vp, funding), vp))
        worst_n = max((o["max_nominal_stop_liq_frac"] for o in os_rows), default=0.0)
        worst_r = max((o["max_realized_stop_liq_frac"] for o in os_rows), default=0.0)
        overshoot_report[v] = {"max_nominal_stop_liq_frac": round(worst_n, 4),
                               "max_realized_stop_liq_frac": round(worst_r, 4),
                               "slippage_overshoot": round(max(0.0, worst_r - worst_n), 4)}
    log("realized stop/liquidation overshoot computed")

    # ── regression row ──────────────────────────────────────────────────
    regression = regression_row(c15, base)
    log(f"regression row: baseline {regression['baseline']['net_usd']}, "
       f"half-off {[regression['half_off'][k]['net_usd'] for k in regression['half_off']]}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "snapshot_dir": a.snapshot_dir,
        "snapshot_window": {iv: snap.window_iso(iv) for iv in SNAP.INTERVALS},
        "data_windows": _window_report(snap, thesis_windows),
        "results": {ds: {"window": r["window"], "variants": r["variants"]} for ds, r in results.items()},
        "thesis_scores": thesis_scores, "max_dd_pct": max_dd, "max_stop_liq_frac": max_liq,
        "verdicts": verdicts, "adopt": adopt, "best_by_beats": best,
        "leverage_report": leverage_report, "realized_stop_overshoot": overshoot_report,
        "regression_row": regression,
    }
    with open(os.path.join(a.out_dir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    rows = summary_csv_rows(results)
    with open(os.path.join(a.out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(rows)
    log(f"done in {time.time() - t0:.0f}s -> {a.out_dir}/report.json, summary.csv")
    _print(report)
    return 0


def _print(rep: Dict[str, Any]) -> None:
    print(f"\nadopt: {rep['adopt'] or 'none'}; best by beats: {rep['best_by_beats']}")
    print("thesis scores (beats/of, geo-mean ratio):")
    for v, s in sorted(rep["thesis_scores"].items(), key=lambda kv: -kv[1]["beats"]):
        print(f"  {v:22s} {s['beats']}/{s['of']}  geo={s['geo_mean_ratio']:.3f}  "
             f"maxDD={rep['max_dd_pct'].get(v):.1f}  maxLiq={rep['max_stop_liq_frac'].get(v):.3f}")
    print(f"regression row baseline net ${rep['regression_row']['baseline']['net_usd']:.2f}")
    for k, v in rep["regression_row"]["half_off"].items():
        print(f"  half-off {k}: net ${v['net_usd']:.2f}  ({'filled' if v['partial_filled'] else 'never filled'})")


if __name__ == "__main__":
    raise SystemExit(main())
