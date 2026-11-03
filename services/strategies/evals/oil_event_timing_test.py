"""Oil scheduled-event timing test, 2026-11-03
(docs/strategy/OIL_EVENT_TIMING_TEST_2026-11-03.md).

Operator, 2026-11-03: "sure" to testing whether Brent (xyz:BRENTOIL)
behaves differently around the scheduled events crypto never had --
the weekly EIA petroleum status report, the API inventory report,
OPEC+/JMMC meetings, and the NYMEX/ICE pit/session structure -- rather
than re-running the already-rejected crypto-style "trade only 07:00 to
21:00 UTC" session idea (OIL_STRATEGY.md, 2026-10-30 sweep).

Two studies:

1. **Event study** (`event_study`): for the EIA and API reports, the
   absolute move and range in the 1h/4h before and after the event,
   against a matched null (same minute-of-day, other non-event days in
   the window -- `build_null_anchors`); the stop-out rate of the live
   stop geometry (`desk_tick_sim.live_params().stop_buffer`, 0.75 beyond
   the 24h edge) for a position open across the window; directional
   drift (does the pre-report move reverse after?). For OPEC+ (day-level
   only, no sourced release minute -- see `oil_calendar`'s own
   docstring) the same move/range comparison at daily resolution. For
   session structure, the mean absolute 1h return inside vs outside the
   NYMEX/ICE day session, Asia hours, and the CME weekend window
   (`desk_tick_sim.weekend_closed`). Every comparison's p-value is
   `services.review.stats.permutation_p`, 2000 draws
   (`research/EVIDENCE_DOCTRINE.md`'s floor), and the EIA/API families
   are also split chronologically in half.
2. **Live-desk filters** (`filter_backtest`): on top of
   `live_params.live_brent_params()` (the 15% risk cap included), a new
   `desk_tick_sim.Params.event_blackout_windows` blocks new entries (a)
   X hours before to Y hours after the EIA/API report, swept, and (b) in
   the hour right after a report prints ("wait for the first closed
   bar"). Datasets: the live oil journal (READ ONLY), the September and
   30-day replays, the controls (always_long/always_short/daily_sign) on
   each window's own span and the last 30 days, and the Sept+Oct chain
   (`extended_history_test.chain_runs`). Adopt bar: beats live on most
   datasets with a geo-mean ratio >= 1, a plateau across neighbouring
   X/Y settings, and drawdown no worse (the same rule every other
   2026-11 eval in this repo uses).

    python -m services.strategies.evals.oil_event_timing_test \\
        [--oil-theses-dir <pathia>/.state/strategies/oil/theses]

Writes /tmp/oil-events/report.json and summary.csv. Reads candles through
the eval disk cache (paged, so a 2-month 15m window exceeding the venue's
5000-bar single-request cap still loads) and the venue's public /info;
never touches the live account, the live config or the journal. No LLM
calls.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.review.stats import N_PERM, mean, permutation_p
from services.strategies.backtest import data as D
from services.strategies.backtest import oil_calendar as OC
from services.strategies.backtest.desk_tick_sim import DAY_MS, HOUR_MS, _levels
from services.strategies.evals.brent_stop_buffer_test import (
    BASELINE, CONTROLS, _iso, _scores, pick_best, rule_pass, run_dataset, verdict)
from services.strategies.evals.desk_tick_backtest import journaled_direction
from services.strategies.evals.extended_history_test import chain_runs, summarize_chain
from services.strategies.evals.live_params import live_brent_params
from services.strategies.evals.market_scout import direction_series

FIFTEEN_MS = 900_000
START = 64.0
STOP_BUFFER = 0.75             # live_params().stop_buffer, the 2026-11 geometry
MIN_HALF_N = 3                 # below this, a half-split p-value is reported but flagged low-n


# ── null anchors (pure) ─────────────────────────────────────────────────────

def build_null_anchors(event_times: Sequence[int], start_ms: int, end_ms: int,
                       exclude_around: Optional[Sequence[int]] = None) -> List[int]:
    """Every timestamp sharing a distinct event's own ms-of-day (UTC), on
    every OTHER calendar day in [start_ms, end_ms) -- a day within 1 day
    of any timestamp in `exclude_around` (default: `event_times` itself)
    is skipped, so a null instance never doubles as a real event or its
    immediate neighbour. Several events sharing the same ms-of-day (e.g.
    every normal-week EIA report) contribute the same null day once per
    distinct ms-of-day, not once per event. Pure."""
    if not event_times or end_ms <= start_ms:
        return []
    excl_source = exclude_around if exclude_around is not None else event_times
    excl_days = {t // DAY_MS for t in excl_source}
    excl_days |= {d - 1 for d in excl_days} | {d + 1 for d in excl_days}
    tods = sorted(set(t % DAY_MS for t in event_times))
    day0, day1 = start_ms // DAY_MS, (end_ms - 1) // DAY_MS
    out = []
    for day in range(day0, day1 + 1):
        if day in excl_days:
            continue
        for tod in tods:
            t = day * DAY_MS + tod
            if start_ms <= t < end_ms:
                out.append(t)
    return sorted(out)


# ── per-event window stats (pure) ───────────────────────────────────────────

def window_stats(c1h: Sequence[Candle], t: int, before_h: float, after_h: float) -> Optional[Dict[str, Any]]:
    """The absolute move (%) and range (%) in the `before_h`/`after_h`
    windows around `t`, and whether the after-move reverses the
    before-move's sign. None when either side has no closed 1h bar.
    Pure."""
    before = sorted((c for c in c1h if t - before_h * HOUR_MS <= c.t < t), key=lambda c: c.t)
    after = sorted((c for c in c1h if t <= c.t < t + after_h * HOUR_MS), key=lambda c: c.t)
    if not before or not after:
        return None
    anchor, pre_open, post_close = before[-1].c, before[0].o, after[-1].c
    if anchor <= 0 or pre_open <= 0:
        return None
    pre_sign = (anchor > pre_open) - (anchor < pre_open)
    post_sign = (post_close > anchor) - (post_close < anchor)
    return {
        "move_before_pct": round(abs(anchor / pre_open - 1.0) * 100.0, 4),
        "move_after_pct": round(abs(post_close / anchor - 1.0) * 100.0, 4),
        "range_before_pct": round((max(c.h for c in before) - min(c.l for c in before)) / anchor * 100.0, 4),
        "range_after_pct": round((max(c.h for c in after) - min(c.l for c in after)) / anchor * 100.0, 4),
        "reversal": 1.0 if (pre_sign and post_sign and pre_sign != post_sign) else 0.0,
    }


def stop_out_flags(c1h: Sequence[Candle], c15: Sequence[Candle], t: int, before_h: float, after_h: float,
                   buffer: float = STOP_BUFFER) -> Optional[Dict[str, float]]:
    """Would the live stop geometry (24h-range edge, `buffer` beyond it)
    for a long or a short opened at `t` have been stopped out somewhere
    in [t - before_h, t + after_h)? Uses only 1h bars closed strictly
    before `t` for the levels (no lookahead) and 15m bars for the
    intrabar check. None without >=24 closed 1h bars or no 15m coverage.
    Pure."""
    closed = sorted((c for c in c1h if c.t + HOUR_MS <= t), key=lambda c: c.t)
    if len(closed) < 24:
        return None
    lv = _levels(closed[-40:], entry_h=24, target_h=24)
    if lv is None or lv["entry_low"] is None:
        return None
    long_stop, short_stop = lv["entry_low"] - buffer, lv["entry_high"] + buffer
    win15 = [c for c in c15 if t - before_h * HOUR_MS <= c.t < t + after_h * HOUR_MS]
    if not win15:
        return None
    lo, hi = min(c.l for c in win15), max(c.h for c in win15)
    return {"long_stopped": 1.0 if lo <= long_stop else 0.0, "short_stopped": 1.0 if hi >= short_stop else 0.0}


def day_stats(c1h: Sequence[Candle], day_start_ms: int) -> Optional[Dict[str, Any]]:
    """Whole-UTC-day move (%) and range (%) -- the OPEC+ resolution,
    since no release minute is sourced. Pure."""
    bars = sorted((c for c in c1h if day_start_ms <= c.t < day_start_ms + DAY_MS), key=lambda c: c.t)
    if not bars or bars[0].o <= 0:
        return None
    o, c = bars[0].o, bars[-1].c
    return {"move_pct": round(abs(c / o - 1.0) * 100.0, 4),
            "range_pct": round((max(b.h for b in bars) - min(b.l for b in bars)) / o * 100.0, 4)}


# ── family aggregation (pure given its candle inputs) ──────────────────────

def _half_split(pairs: Sequence[Tuple[int, float]]) -> Tuple[List[float], List[float]]:
    """`pairs` of (time, value), chronological; the first/second half's
    values by count."""
    ordered = [v for _, v in sorted(pairs, key=lambda p: p[0])]
    mid = len(ordered) // 2
    return ordered[:mid], ordered[mid:]


def _compare(label: str, event_vals: Sequence[float], null_vals: Sequence[float], n_perm: int = N_PERM
            ) -> Dict[str, Any]:
    p = permutation_p(event_vals, null_vals, n_perm=n_perm) if event_vals and null_vals else None
    return {"metric": label, "n_event": len(event_vals), "n_null": len(null_vals),
           "event_mean": round(mean(event_vals), 4) if event_vals else None,
           "null_mean": round(mean(null_vals), 4) if null_vals else None, "p_value": p}


def eia_api_study(c1h: Sequence[Candle], c15: Sequence[Candle], event_times: Sequence[int], label: str,
                  start_ms: int, end_ms: int, before_h: float = 1.0, after_h: float = 4.0,
                  n_perm: int = N_PERM) -> Dict[str, Any]:
    """`window_stats` + `stop_out_flags` for every `event_times` instance
    vs a matched null (`build_null_anchors`), plus a chronological half
    split. Pure given its candle inputs."""
    nulls = build_null_anchors(event_times, start_ms, end_ms)
    ev_rows = [(t, window_stats(c1h, t, before_h, after_h)) for t in event_times]
    ev_rows = [(t, r) for t, r in ev_rows if r is not None]
    nu_rows = [(t, window_stats(c1h, t, before_h, after_h)) for t in nulls]
    nu_rows = [(t, r) for t, r in nu_rows if r is not None]
    ev_stop = [(t, stop_out_flags(c1h, c15, t, before_h, after_h)) for t in event_times]
    ev_stop = [(t, r) for t, r in ev_stop if r is not None]
    nu_stop = [(t, stop_out_flags(c1h, c15, t, before_h, after_h)) for t in nulls]
    nu_stop = [(t, r) for t, r in nu_stop if r is not None]

    metrics = {}
    for key in ("move_before_pct", "move_after_pct", "range_before_pct", "range_after_pct", "reversal"):
        ev_vals = [r[key] for _, r in ev_rows]
        nu_vals = [r[key] for _, r in nu_rows]
        row = _compare(key, ev_vals, nu_vals, n_perm)
        ev_h1, ev_h2 = _half_split([(t, r[key]) for t, r in ev_rows])
        nu_h1, nu_h2 = _half_split([(t, r[key]) for t, r in nu_rows])
        row["half1"] = _compare(key, ev_h1, nu_h1, n_perm)
        row["half2"] = _compare(key, ev_h2, nu_h2, n_perm)
        row["halves_agree"] = (bool(ev_h1) and bool(ev_h2) and
                               ((row["half1"]["event_mean"] or 0) - (row["half1"]["null_mean"] or 0) >= 0) ==
                               ((row["half2"]["event_mean"] or 0) - (row["half2"]["null_mean"] or 0) >= 0))
        metrics[key] = row
    for key in ("long_stopped", "short_stopped"):
        ev_vals = [r[key] for _, r in ev_stop]
        nu_vals = [r[key] for _, r in nu_stop]
        metrics[key] = _compare(key, ev_vals, nu_vals, n_perm)

    return {"label": label, "window": [f"-{before_h:g}h", f"+{after_h:g}h"], "n_events": len(event_times),
           "n_events_with_data": len(ev_rows), "n_null_instances": len(nulls), "metrics": metrics}


def opec_study(c1h: Sequence[Candle], event_times: Sequence[int], start_ms: int, end_ms: int,
              n_perm: int = N_PERM) -> Dict[str, Any]:
    """Day-level move/range vs a null of other non-adjacent days. Pure."""
    event_days = sorted({(t // DAY_MS) * DAY_MS for t in event_times if start_ms <= t < end_ms})
    all_days = list(range(start_ms // DAY_MS, (end_ms - 1) // DAY_MS + 1))
    excl = set(event_days) | {(d // DAY_MS - 1) * DAY_MS for d in event_days} | \
        {(d // DAY_MS + 1) * DAY_MS for d in event_days}
    null_days = [d * DAY_MS for d in all_days if d * DAY_MS not in excl]
    ev = [day_stats(c1h, d) for d in event_days]
    ev = [r for r in ev if r is not None]
    nu = [day_stats(c1h, d) for d in null_days]
    nu = [r for r in nu if r is not None]
    metrics = {}
    for key in ("move_pct", "range_pct"):
        metrics[key] = _compare(key, [r[key] for r in ev], [r[key] for r in nu], n_perm)
    return {"label": "opec", "resolution": "day (no sourced release minute)", "n_events": len(event_days),
           "n_events_with_data": len(ev), "n_null_days": len(nu), "metrics": metrics}


def session_study(c1h: Sequence[Candle], n_perm: int = N_PERM) -> Dict[str, Any]:
    """Mean absolute 1h return inside vs outside each session bucket
    (NYMEX/ICE day session, Asia hours, the CME weekend window). Every
    bar falls in exactly one bucket (weekend checked first, matching
    `desk_tick_sim`'s own precedence). Pure."""
    bars = sorted(c1h, key=lambda c: c.t)
    buckets: Dict[str, List[float]] = {"weekend": [], "nymex_day": [], "asia": [], "other": []}
    for b in bars:
        if b.o <= 0:
            continue
        r = abs(b.c / b.o - 1.0) * 100.0
        if OC.weekend_closed(b.t):
            buckets["weekend"].append(r)
        elif OC.in_nymex_day_session(b.t):
            buckets["nymex_day"].append(r)
        elif OC.in_asia_session(b.t):
            buckets["asia"].append(r)
        else:
            buckets["other"].append(r)
    out = {}
    for name, vals in buckets.items():
        rest = [v for other, ov in buckets.items() if other != name for v in ov]
        out[name] = _compare(f"abs_1h_return_pct:{name}", vals, rest, n_perm)
    return out


def event_study(c1h: Sequence[Candle], c15: Sequence[Candle], start_ms: int, end_ms: int,
                n_perm: int = N_PERM) -> Dict[str, Any]:
    eia_times = [t for t, _ in OC.eia_report_times(start_ms, end_ms)]
    api_times = OC.api_report_times(start_ms, end_ms)
    opec_times = [t for t, _ in OC.opec_event_times(start_ms, end_ms)]
    return {
        "window": [_iso(start_ms), _iso(end_ms)],
        "eia_1h_4h": eia_api_study(c1h, c15, eia_times, "eia", start_ms, end_ms, 1.0, 4.0, n_perm),
        "eia_4h_4h": eia_api_study(c1h, c15, eia_times, "eia", start_ms, end_ms, 4.0, 4.0, n_perm),
        "api_1h_4h": eia_api_study(c1h, c15, api_times, "api", start_ms, end_ms, 1.0, 4.0, n_perm),
        "opec": opec_study(c1h, opec_times, start_ms, end_ms, n_perm),
        "sessions": session_study(c1h, n_perm),
        "eia_events": [(_iso(t), why) for t, why in OC.eia_report_times(start_ms, end_ms)],
        "api_events": [_iso(t) for t in api_times],
        "opec_events": [(_iso(t), why) for t, why in OC.opec_event_times(start_ms, end_ms)],
    }


# ── live-desk filter variants (one Params override dict per dataset window) ─

EIA_GRID_HOURS = (0.5, 1.0, 2.0)
API_GRID_HOURS = (0.5, 1.0, 2.0)
FAMILIES = {"eia_window": [f"eia_{h:g}_{h:g}" for h in EIA_GRID_HOURS],
           "api_window": [f"api_{h:g}_{h:g}" for h in API_GRID_HOURS]}


def build_filter_variants(start_ms: int, end_ms: int) -> Dict[str, Dict[str, Any]]:
    """`name -> Params overrides` for one dataset's own [start_ms, end_ms)
    -- the blackout windows are recomputed per dataset since each window
    has its own EIA/API event timestamps. Pure given the window."""
    eia_times = [t for t, _ in OC.eia_report_times(start_ms, end_ms)]
    api_times = OC.api_report_times(start_ms, end_ms)
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    for h in EIA_GRID_HOURS:
        out[f"eia_{h:g}_{h:g}"] = {"event_blackout_windows": OC.blackout_windows(eia_times, h, h)}
    for h in API_GRID_HOURS:
        out[f"api_{h:g}_{h:g}"] = {"event_blackout_windows": OC.blackout_windows(api_times, h, h)}
    out["both_1_1"] = {"event_blackout_windows": (OC.blackout_windows(eia_times, 1.0, 1.0) +
                                                  OC.blackout_windows(api_times, 1.0, 1.0))}
    out["wait_eia_1h"] = {"event_blackout_windows": OC.blackout_windows(eia_times, 0.0, 1.0)}
    out["wait_api_1h"] = {"event_blackout_windows": OC.blackout_windows(api_times, 0.0, 1.0)}
    return out


SUMMARY_FIELDS = ["dataset", "variant", "final_equity", "return_pct", "max_dd_pct", "trades", "win_rate", "avg_r",
                  "worst_loss_usd", "worst_loss_pct", "max_stop_liq_frac", "event_blackout_refused"]


def summary_csv_rows(results: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for ds, r in results.items():
        for v, s in r["variants"].items():
            refused = s.get("refused") or {}
            out.append({"dataset": ds, "variant": v, "final_equity": s["final_equity"],
                       "return_pct": s["return_pct"], "max_dd_pct": s["max_dd_pct"], "trades": s["trades"],
                       "win_rate": s["win_rate"], "avg_r": s["avg_r"], "worst_loss_usd": s["worst_loss_usd"],
                       "worst_loss_pct": s["worst_loss_pct"], "max_stop_liq_frac": s["max_stop_liq_frac"],
                       "event_blackout_refused": refused.get("event_blackout", 0)})
    return out


# ── main ─────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get(
        "PATHIEL_OIL_THESES_DIR", os.path.expanduser("~/Documents/code/pathia/.state/strategies/oil/theses")),
        help="READ ONLY: the live oil journal's theses")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--oil-replay-sep-dir", default="/tmp/thesis-replay-sep/oil/theses")
    ap.add_argument("--cache-dir", default="/tmp/oil-events/cache")
    ap.add_argument("--out-dir", default="/tmp/oil-events")
    ap.add_argument("--fetch-days", type=int, default=70, help="how far back to fetch Brent candles")
    ap.add_argument("--n-perm", type=int, default=N_PERM)
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} oil-events: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    # ── data: a fetch-days-wide window of Brent 15m + 1h, paged (the venue
    # caps a single candleSnapshot request at 5000 bars -- 52 days of 15m).
    end_ms = int(time.time() * 1000)
    end_ms -= end_ms % FIFTEEN_MS
    start_ms = end_ms - a.fetch_days * DAY_MS
    b15 = D.fetch_candles_paged_cached("xyz:BRENTOIL", "15m", start_ms, end_ms, a.cache_dir)
    b1h = D.fetch_candles_paged_cached("xyz:BRENTOIL", "1h", start_ms, end_ms, a.cache_dir)
    if not b15 or not b1h:
        log("no Brent candles fetched; aborting")
        return 1
    data_start, data_end = b15[0].t, b15[-1].t + FIFTEEN_MS
    funding = D.fetch_funding_cached("xyz:BRENTOIL", data_start, data_end, a.cache_dir)
    log(f"Brent data {_iso(data_start)} .. {_iso(data_end)} ({len(b15)} 15m / {len(b1h)} 1h bars)")

    # ── 1. event study, over the full fetched window
    study = event_study(b1h, b15, data_start, data_end, a.n_perm)
    log(f"event study done ({time.time() - t0:.0f}s): {len(study['eia_events'])} EIA, "
        f"{len(study['api_events'])} API, {len(study['opec_events'])} OPEC events in window")

    # ── 2. live-desk filter backtest
    bp = live_brent_params(size_slippage=True, starting_equity=START)
    datasets: Dict[str, Tuple[Callable[[int], Any], int, int]] = {}
    legs: List[Tuple[Callable[[int], Any], Sequence[Candle], Sequence[Candle],
                     Optional[Sequence[Dict[str, Any]]], int, int]] = []
    # Availability per replay leg: "ok (N theses)" or "unavailable (regenerating)"
    # -- /tmp is OS-swept and the October replay (--oil-replay-dir) has gone
    # empty before (2026-11-04, macOS tmp cleanup ~16:00Z); an empty
    # directory is reported here, never silently treated as "zero trades".
    leg_availability: Dict[str, str] = {}
    for label, tdir in (("oil_replay_sep", a.oil_replay_sep_dir), ("oil_replay_30b", a.oil_replay_dir)):
        theses = D.load_journaled_theses(tdir)
        if not theses:
            leg_availability[label] = f"unavailable (regenerating) -- no theses at {tdir!r}"
            log(f"{label}: {leg_availability[label]}")
            continue
        leg_availability[label] = f"ok ({len(theses)} theses)"
        start = min(th.created_at_ms for th in theses)
        end = min(data_end, max(th.created_at_ms for th in theses) + DAY_MS)
        datasets[label] = (journaled_direction(theses), start, end)
        legs.append((journaled_direction(theses), b15, b1h, funding, start, end))
    legs.sort(key=lambda leg: leg[4])   # chronological order for the chain

    journal_theses = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    if journal_theses:
        datasets["oil_journal"] = (journaled_direction(journal_theses),
                                   min(th.created_at_ms for th in journal_theses), data_end)
    else:
        log(f"oil_journal: no theses at {a.oil_theses_dir!r}, skipped")

    for s in CONTROLS:
        datasets[f"{s}_30d"] = (direction_series(s, b1h), data_end - 30 * DAY_MS, data_end)
    if legs:
        rep_start, rep_end = legs[0][4], legs[-1][5]
        for s in CONTROLS:
            datasets[f"{s}_replay"] = (direction_series(s, b1h), rep_start, rep_end)

    res: Dict[str, Any] = {}
    for ds, (d_at, st, en) in datasets.items():
        named = build_filter_variants(st, en)
        res[ds] = run_dataset(b15, b1h, d_at, bp, st, en, named, funding=funding)
    log(f"filter grid: {len(res)} datasets ({time.time() - t0:.0f}s)")

    chain_result = None
    chain_legs_used = [label for label, tdir in (("oil_replay_sep", a.oil_replay_sep_dir),
                                                  ("oil_replay_30b", a.oil_replay_dir))
                      if leg_availability.get(label, "").startswith("ok")]
    if legs:
        chain_named = build_filter_variants(legs[0][4], legs[-1][5])
        chain_variants: Dict[str, Any] = {}
        for name, ov in chain_named.items():
            p = dataclasses.replace(bp, **ov)
            cres = chain_runs(legs, p, START)
            chain_variants[name] = summarize_chain(cres, p, START, legs[0][4], legs[-1][5])
        chain_result = {"window": [_iso(legs[0][4]), _iso(legs[-1][5])], "variants": chain_variants,
                       "legs": chain_legs_used, "leg_availability": leg_availability}
        res["chain"] = chain_result
        label = "Sept+Oct chain" if len(chain_legs_used) > 1 else f"{chain_legs_used[0]} alone (chain is a stand-in)"
        log(f"chain: {label} -- {leg_availability}")
    else:
        log(f"chain: no replay legs present, skipped -- {leg_availability}")

    groups = {"thesis": [k for k in ("oil_journal", "oil_replay_sep", "oil_replay_30b", "chain") if k in res],
             "controls": [k for k in res if k.endswith("_30d") or k.endswith("_replay")], "all": list(res)}
    scores = _scores(res, groups)
    max_liq = {v: max(r["variants"][v]["max_stop_liq_frac"] for r in res.values()) for v in build_filter_variants(
        data_start, data_end)}
    candidates = [v for v in max_liq if v != BASELINE]
    verdicts = {v: verdict(scores["all"], max_liq, v, FAMILIES) for v in candidates}
    adopt = sorted(v for v, x in verdicts.items() if x["adopt"])
    best = pick_best(scores["all"])
    log(f"scored ({time.time() - t0:.0f}s); rule_pass {rule_pass(scores['all'], max_liq, FAMILIES)}")

    # ── write
    with open(os.path.join(a.out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(summary_csv_rows(res))
    strip = lambda rr: {k: {kk: vv for kk, vv in r.items() if kk != "entries"} for k, r in rr.items()}  # noqa: E731
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "data_window": [_iso(data_start), _iso(data_end)], "event_study": study, "variants_families": FAMILIES,
        "datasets": strip(res), "scores": scores, "groups": groups, "max_stop_liq_frac": max_liq, "best": best,
        "verdicts": verdicts, "adopt": adopt, "leg_availability": leg_availability,
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\nsummary: {os.path.join(a.out_dir, 'summary.csv')}")
    return 0


def _print(rep: Dict[str, Any]) -> None:
    print(f"data window {rep['data_window'][0]} .. {rep['data_window'][1]}")
    s = rep["event_study"]
    print(f"\nEIA events: {len(s['eia_events'])}, API events: {len(s['api_events'])}, "
         f"OPEC events: {len(s['opec_events'])}")
    for fam in ("eia_1h_4h", "api_1h_4h"):
        row = s[fam]
        print(f"\n[{fam}] n_events={row['n_events_with_data']} n_null={row['n_null_instances']}")
        for k, m in row["metrics"].items():
            print(f"  {k:<16} event={m['event_mean']} null={m['null_mean']} p={m['p_value']}")
    print(f"\n[opec] {s['opec']}")
    print("\n[sessions]")
    for k, m in s["sessions"].items():
        print(f"  {k:<10} n={m['n_event']} mean={m['event_mean']} rest={m['null_mean']} p={m['p_value']}")
    print("\n\nscore [all] (beats/loses live of N, geo-mean final/live):")
    for v, x in sorted(rep["scores"]["all"].items(), key=lambda kv: -kv[1]["geo_mean_ratio"]):
        print(f"  {v:<14} {x['beats']:>2}/{x['loses']:>2} of {x['of']:<2} {x['geo_mean_ratio']:.3f}")
    print(f"\nbest: {rep['best']}; adopt: {rep['adopt'] or 'none'}")
    for v, x in rep["verdicts"].items():
        print(f"  {v:<14} {json.dumps(x)}")


if __name__ == "__main__":
    raise SystemExit(main())
