"""Silver strategy test, 2026-11-04 (docs/strategy/SILVER_STRATEGY_TEST_2026-11-04.md).

Operator, 2026-11-04: "silver seems mad quiet, any strategies we can test
there?" The metals desk (10% of equity, xyz:SILVER on the HIP-3 xyz dex,
live Hyperliquid, real money) has been SHORT for days while silver fell
from about 51.3 to 49.6 -- the thesis called it right -- but the desk never
filled. The live rule only sells at the far edge of the 24h range (a 2R
gate to the far edge, 0.5% offset) and never chases below the thesis's own
entry (`Direction.entry_ceiling`); the stop is max(1.3 x ATR14_1h, 0.8% of
the mid) beyond structure (SILVER_STOP_FLOOR_TEST_2026-11-02.md), 20x,
`live_params.live_silver_params`.

Research only. Never touches the live account, the live config, the live
journal (READ ONLY) or any pathiel MCP tool. Reads candles ONLY from the
pinned snapshot (`services.strategies.backtest.snapshot`) -- no network.

Measure first: the missed-opportunity rate. For every silver thesis call
with a real side, was the call's direction right over the next 24h/72h
(the same sign test `extended_history_test.direction_hit_rate` uses, here
kept per-call instead of only aggregated) while the live rule's own
backtest opened NO trade inside that call's own window? That is the size
of the prize the "never chase, 2R to the far edge" refusal leaves on the
table -- reported before anything else, per dataset and horizon.

Variants, every one built on `live_params.live_silver_params()` (the real
live rule: per-tick ATR stop with its 0.8% floor, the 15% risk cap, market
entry off the 24h range, 2R gate, 20x):
  lev_fit            `entry_fit_leverage` (20/15/10/8/5): the highest
                     candidate whose stop still clears half the way to
                     liquidation (the oil final test's winner).
  ai_levels          try the thesis's OWN entry/stop/first target before
                     the structural rule (`Params.ai_levels`, the 2026-10-30
                     AI strategy test, never adopted on oil).
  entry_lookback_8h  `entry_lookback_h=8` alone (the market-entry rule's
                     chase/break checks don't read it -- included to show
                     whether it does anything on its own, not just as a
                     strawman).
  pullback_8h        `pullback_entry="a1"`, `pb_struct_h=8`, `pb_target=
                     "thesis"`: on a live chase/min_r refusal, sell at the
                     mid with a stop beyond the last 8h's structure (the
                     variant WIDE_STOP_SWING_TEST_2026-11-03.md rejected on
                     oil -- tested again here since silver's regime and
                     ATR-based stop differ).
  breakout_{8,24}h_{measured,thesis}, breakout_24h_r2
                     the new continuation entry (`Params.pullback_entry=
                     "a5"`, added this test): on a live chase/min_r
                     refusal, sell the moment a CLOSED 1h bar breaks the
                     prior 8h/24h low, stop beyond the last 4h's high,
                     target the broken level minus a measured move, the
                     thesis's own target, or (r2) a fixed 2R.
  min_r_1.5          `min_r=1.5` (silver only): relaxes the 2R gate to 1.5R.
  combo_best2        the two single-concern variants above with the best
                     thesis-dataset score, overrides merged (decided by
                     THIS run, not hardcoded).

Datasets: the silver live journal (READ ONLY, --metals-theses-dir, kept
only the silver theses -- `stop_cap_test.same_market` drops the one GOLD
thesis the metals desk logged before it moved to silver), the 30 replayed
silver theses (--replay-theses-dir, 2026-10-01 to 10-30, durable copy), and
always_long/always_short/daily_sign over the snapshot's last 30 days.
Adopt bar: beats live on BOTH thesis datasets (journal, replay -- "most of
2" is both) and on most controls (2 of 3), max drawdown no worse than live
(within 2 points), a plateau across the breakout lookback grid (8h vs 24h)
where one applies, and the stop inside half the liquidation distance at
the trade's own leverage (`final_strategy_test.corrected_max_stop_liq_frac`
for the entry_fit_leverage variants).

    python -m services.strategies.evals.silver_strategy_test \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/<stamp> \\
        [--metals-theses-dir <pathia>/.state/strategies/metals/theses] \\
        [--replay-theses-dir ~/Documents/code/pathiel-replays/metals-silver30/theses]

Writes /tmp/silver-strategy/report.json and summary.csv. No network (reads
the pinned snapshot only), no LLM calls.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import dataclasses
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest import snapshot as SNAP
from services.strategies.backtest.desk_tick_sim import Run, simulate
from services.strategies.evals.brent_stop_buffer_test import (
    CONTROLS, MAX_STOP_FRAC_OF_LIQ, _iso, plateau, run_dataset, score_vs_live)
from services.strategies.evals.closer_target_test import per_day_pct
from services.strategies.evals.cross_market_backtest import mean_atr
from services.strategies.evals.desk_tick_backtest import DAY_MS, journaled_direction
from services.strategies.evals.final_strategy_test import corrected_max_stop_liq_frac
from services.strategies.evals.live_params import live_silver_params
from services.strategies.evals.market_scout import direction_series, fetch_market_meta
from services.strategies.evals.stop_cap_test import same_market
from services.strategies.evals.wide_stop_swing_test import LEV_GRID

FIFTEEN_MS = 900_000
START = 64.0
BASELINE = "live"
THESIS_DATASETS: Tuple[str, ...] = ("journal", "replay")
HORIZONS_DAYS: Tuple[float, ...] = (1.0, 3.0)   # 24h / 72h, the missed-opportunity horizons
DEFAULT_COIN = "xyz:SILVER"
DEFAULT_SILVER_MAX_LEVERAGE = 25.0   # venue max for xyz:SILVER (SILVER_DESK.md), used only if the fetch fails

LEV_FIT_OV: Dict[str, Any] = {"entry_fit_leverage": LEV_GRID}


# ── variants ─────────────────────────────────────────────────────────────

def variants() -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides on `live_params.live_silver_params()`."""
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    out["lev_fit"] = dict(LEV_FIT_OV)
    out["ai_levels"] = {"ai_levels": True}
    out["entry_lookback_8h"] = {"entry_lookback_h": 8}
    out["pullback_8h"] = {"pullback_entry": "a1", "pb_struct_h": 8, "pb_target": "thesis"}
    for lb in (8, 24):
        for tgt in ("measured", "thesis"):
            out[f"breakout_{lb}h_{tgt}"] = {"pullback_entry": "a5", "pb_breakout_lookback_h": lb,
                                            "pb_breakout_stop_h": 4, "pb_target": tgt}
    out["breakout_24h_r2"] = {"pullback_entry": "a5", "pb_breakout_lookback_h": 24, "pb_breakout_stop_h": 4,
                              "pb_target_r": 2.0}
    out["min_r_1.5"] = {"min_r": 1.5}
    return out


# Grid families for the plateau check (brent_stop_buffer_test.plateau):
# a neighbour on EITHER side must also beat live for a name to count as a
# plateau, not a spike. lev_fit/ai_levels/pullback_8h/min_r_1.5 have no
# neighbour in this run (each is its own one-off test), so `adopt_verdict`
# treats them as automatically flat (no grid to fail).
FAMILIES: Dict[str, List[str]] = {
    "breakout_lookback_measured": ["breakout_8h_measured", "breakout_24h_measured"],
    "breakout_lookback_thesis": ["breakout_8h_thesis", "breakout_24h_thesis"],
}


# ── missed-opportunity rate ─────────────────────────────────────────────────

def calls_with_verdict(theses: Sequence[D.ThesisDirection], c1h: Sequence[Candle],
                       horizon_days: float) -> List[Dict[str, Any]]:
    """Per non-flat thesis with an entry price: was its direction right over
    `horizon_days` (the sign test `extended_history_test.direction_hit_rate`
    uses, recomputed per-call here instead of only aggregated), and the
    call's own window -- [created_at_ms, the next call's created_at_ms), or
    open-ended for the last call. Skipped when the horizon runs past the
    data end (no closed bar to judge it against yet). Pure."""
    horizon_ms = int(horizon_days * DAY_MS)
    bars = sorted(c1h, key=lambda c: c.t)
    ordered = sorted(theses, key=lambda th: th.created_at_ms)
    out: List[Dict[str, Any]] = []
    for i, th in enumerate(ordered):
        if th.side not in ("long", "short") or not th.entry_px:
            continue
        target_t = th.created_at_ms + horizon_ms
        in_range = [c for c in bars if th.created_at_ms <= c.t <= target_t]
        if not in_range:
            continue
        outcome_px = in_range[-1].c
        sign = 1.0 if th.side == "long" else -1.0
        ret = (outcome_px / th.entry_px - 1.0) * sign
        window_end = ordered[i + 1].created_at_ms if i + 1 < len(ordered) else None
        out.append({"thesis_id": th.thesis_id, "created_at": _iso(th.created_at_ms), "side": th.side,
                   "entry_px": th.entry_px, "created_at_ms": th.created_at_ms, "window_end_ms": window_end,
                   "direction_right": ret > 0, "ret_pct": round(ret * 100, 3)})
    return out


def missed_opportunity(calls: Sequence[Dict[str, Any]], run: Run) -> Dict[str, Any]:
    """Of `calls` (`calls_with_verdict`'s rows), how many had the direction
    right over the horizon but the LIVE run opened no trade inside that
    call's own window (by entry_time, not exit)? That gap is the size of
    the prize the live rule's "never chase, needs 2R to the far edge"
    refusal leaves on the table. Pure."""
    entry_times = sorted(tr.entry_time for tr in run.trades)

    def traded_in(call: Dict[str, Any]) -> bool:
        lo = bisect.bisect_left(entry_times, call["created_at_ms"])
        if call["window_end_ms"] is None:
            return lo < len(entry_times)
        hi = bisect.bisect_left(entry_times, call["window_end_ms"])
        return lo < hi

    right = [c for c in calls if c["direction_right"]]
    missed = [c for c in right if not traded_in(c)]
    return {"n_calls": len(calls), "n_right": len(right), "n_missed": len(missed),
           "missed_rate_of_calls": round(len(missed) / len(calls), 3) if calls else None,
           "missed_rate_of_right": round(len(missed) / len(right), 3) if right else None,
           "missed_calls": [{"thesis_id": c["thesis_id"], "created_at": c["created_at"], "side": c["side"],
                             "ret_pct": c["ret_pct"]} for c in missed]}


# ── top-2 combo (decided by this run's own scores, not hardcoded) ──────────

def pick_top2(thesis_scores: Dict[str, Dict[str, Any]], exclude: Sequence[str] = ()) -> List[str]:
    """The 2 highest-scoring non-baseline candidates by (beats - loses,
    geo_mean_ratio), name as the final tiebreak (deterministic). Pure."""
    cands = [v for v in thesis_scores if v != BASELINE and v not in exclude]
    ranked = sorted(cands, key=lambda v: (thesis_scores[v]["beats"] - thesis_scores[v]["loses"],
                                          thesis_scores[v]["geo_mean_ratio"], v), reverse=True)
    return ranked[:2]


def combo_overrides(named: Dict[str, Dict[str, Any]], names: Sequence[str]) -> Dict[str, Any]:
    """The union of `names`' own Params overrides, later names winning on a
    key clash (there is none among this run's variants -- each touches its
    own Params fields). Pure."""
    out: Dict[str, Any] = {}
    for v in names:
        out.update(named[v])
    return out


# ── adopt bar ────────────────────────────────────────────────────────────

def _most(s: Optional[Dict[str, Any]]) -> bool:
    return bool(s) and s["of"] > 0 and s["beats"] > s["of"] / 2.0


def dd_and_liq(results: Dict[str, Any], named: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, float], Dict[str, float]]:
    max_dd = {v: max((r["variants"][v]["max_dd_pct"] for r in results.values() if v in r["variants"]), default=0.0)
             for v in named}
    max_liq = {v: max((r["variants"][v]["max_stop_liq_frac"] for r in results.values() if v in r["variants"]),
                      default=0.0) for v in named}
    return max_dd, max_liq


def adopt_verdict(thesis_scores: Dict[str, Dict[str, Any]], control_scores: Dict[str, Dict[str, Any]],
                  max_dd: Dict[str, float], max_liq: Dict[str, float], name: str, live_dd: float) -> Dict[str, Any]:
    """Beats live on BOTH thesis datasets (journal, replay -- "most" of 2 is
    both), beats live on most controls (2 of 3), max DD within 2 points of
    live, a plateau where a grid applies (FAMILIES), and the stop inside
    half the liquidation distance at the trade's own leverage."""
    s = thesis_scores.get(name)
    if s is None:
        return {"adopt": False, "candidate": name, "why": "no score"}
    beats_thesis = _most(s)
    cs = control_scores.get(name)
    beats_controls = _most(cs)
    dd_ok = max_dd.get(name, 0.0) <= live_dd + 2.0
    liq_ok = max_liq.get(name, 0.0) <= MAX_STOP_FRAC_OF_LIQ + 1e-9
    flat = plateau(thesis_scores, name, FAMILIES) if name in sum(FAMILIES.values(), []) else True
    why = [w for w, ok in (("beats live on both thesis datasets", beats_thesis),
                          ("beats live on most controls", beats_controls),
                          ("max DD within 2pts of live", dd_ok), ("plateau", flat),
                          ("stop inside half the liquidation distance", liq_ok)) if not ok]
    return {"adopt": beats_thesis and beats_controls and dd_ok and flat and liq_ok, "candidate": name,
           "thesis_beats": s["beats"], "thesis_of": s["of"], "thesis_geo_mean": s["geo_mean_ratio"],
           "control_beats": (cs or {}).get("beats"), "control_of": (cs or {}).get("of"),
           "max_dd_pct": max_dd.get(name), "max_stop_liq_frac": max_liq.get(name), "plateau": flat, "fails": why}


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
                        "worst_loss_pct": s.get("worst_loss_pct"), "max_stop_liq_frac": s["max_stop_liq_frac"],
                        "stop_exits": exits.get("stop", 0)})
    return out


def _window_report(snap: SNAP.Snapshot, thesis_windows: Dict[str, Tuple[int, int]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"snapshot": {iv: snap.window_iso(iv) for iv in SNAP.INTERVALS}}
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
    ap.add_argument("--coin", default=DEFAULT_COIN)
    ap.add_argument("--metals-theses-dir", default=os.environ.get(
        "PATHIEL_METALS_THESES_DIR", os.path.expanduser("~/Documents/code/pathia/.state/strategies/metals/theses")),
        help="READ ONLY: the live metals journal's theses (same_market drops the old gold one)")
    ap.add_argument("--replay-theses-dir", default=os.path.expanduser(
        "~/Documents/code/pathiel-replays/metals-silver30/theses"))
    ap.add_argument("--silver-max-leverage", type=float, default=None,
                    help="default: fetch the venue's own maxLeverage for --coin")
    ap.add_argument("--out-dir", default="/tmp/silver-strategy")
    a = ap.parse_args(argv)
    if not a.snapshot_dir:
        raise SystemExit("no --snapshot-dir given and none found under " + SNAP.DEFAULT_SNAPSHOT_ROOT)
    os.makedirs(a.out_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} silver-strategy: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    snap = SNAP.load_snapshot(a.snapshot_dir, a.coin)
    c15, c1h, funding = snap.c15, snap.c1h, snap.funding
    if not c15 or not c1h:
        raise SystemExit(f"snapshot at {a.snapshot_dir!r} has no {a.coin} candles -- run snapshot.py first")
    b_start, b_end = snap.window("15m")
    log(f"snapshot {a.snapshot_dir}: 15m {snap.window_iso('15m')}, 1h {snap.window_iso('1h')}")

    if a.silver_max_leverage is None:
        try:
            meta = fetch_market_meta([a.coin]).get(a.coin) or {}
            a.silver_max_leverage = float(meta.get("maxLeverage") or DEFAULT_SILVER_MAX_LEVERAGE)
        except Exception as e:   # pragma: no cover -- network hiccup, fall back rather than abort the whole eval
            log(f"venue maxLeverage fetch failed ({e!r}), using the SILVER_DESK.md default {DEFAULT_SILVER_MAX_LEVERAGE}")
            a.silver_max_leverage = DEFAULT_SILVER_MAX_LEVERAGE
    atr_price, _ = mean_atr(c1h)
    base = live_silver_params(a.silver_max_leverage, atr_price or 0.4, starting_equity=START)
    log(f"silver max leverage {a.silver_max_leverage:g}x, mean 1h ATR14 {atr_price}")

    named = variants()
    results: Dict[str, Any] = {}
    thesis_windows: Dict[str, Tuple[int, int]] = {}
    theses_by_label: Dict[str, List[D.ThesisDirection]] = {}

    for label, tdir, drop_other_coins in (("journal", a.metals_theses_dir, True), ("replay", a.replay_theses_dir, False)):
        raw = D.load_journaled_theses(tdir)
        theses = same_market(raw, c1h) if drop_other_coins else raw
        if not theses:
            log(f"{label}: no theses at {tdir!r}, skipped")
            continue
        theses_by_label[label] = theses
        start = min(th.created_at_ms for th in theses)
        end = b_end if label == "journal" else min(b_end, max(th.created_at_ms for th in theses) + DAY_MS)
        if end <= start:
            log(f"{label}: window empty after clamping to the snapshot, skipped")
            continue
        thesis_windows[label] = (start, end)
        d_at = journaled_direction(theses)
        results[label] = run_dataset(c15, c1h, d_at, base, start, end, named, baseline=BASELINE, funding=funding)
        dropped = len(raw) - len(theses)
        log(f"{label}: {len(theses)} theses{f' ({dropped} dropped, other coin)' if dropped else ''}, "
           f"window {_iso(start)}..{_iso(end)}")

    ctrl_start = b_end - 30 * DAY_MS
    for s in CONTROLS:
        d_at = direction_series(s, c1h)
        results[f"{s}_30d"] = run_dataset(c15, c1h, d_at, base, ctrl_start, b_end, named, baseline=BASELINE,
                                          funding=funding)
    log(f"controls: {', '.join(CONTROLS)} over the snapshot's last 30 days ({_iso(ctrl_start)}..{_iso(b_end)})")

    # ── missed-opportunity rate: measured BEFORE picking a winner ──────────
    missed_report: Dict[str, Any] = {}
    for label in THESIS_DATASETS:
        if label not in theses_by_label:
            continue
        theses = theses_by_label[label]
        start, end = thesis_windows[label]
        w15 = [c for c in c15 if start <= c.t < end]
        w1h = [c for c in c1h if c.t < end]
        base_run = simulate(w15, w1h, journaled_direction(theses), base, funding)
        missed_report[label] = {f"{h:g}d": missed_opportunity(calls_with_verdict(theses, c1h, h), base_run)
                                for h in HORIZONS_DAYS}
        for h in HORIZONS_DAYS:
            m = missed_report[label][f"{h:g}d"]
            log(f"{label} missed-opportunity @{h:g}d: {m['n_missed']}/{m['n_right']} right-but-no-trade "
               f"({m['missed_rate_of_right']} of right calls, {m['n_calls']} calls total)")

    # ── first pass: score every single-concern variant, pick the top 2 ─────
    thesis_scores = score_vs_live({k: results[k]["variants"] for k in THESIS_DATASETS if k in results})
    top2 = pick_top2(thesis_scores)
    log(f"top 2 by thesis score: {top2}")

    # ── second pass: add the combo, re-run everything once more so the
    #    final report is one self-consistent set of runs ───────────────────
    named["combo_best2"] = combo_overrides(named, top2)
    results = {}
    for label, (start, end) in thesis_windows.items():
        theses = theses_by_label[label]
        d_at = journaled_direction(theses)
        results[label] = run_dataset(c15, c1h, d_at, base, start, end, named, baseline=BASELINE, funding=funding)
    for s in CONTROLS:
        d_at = direction_series(s, c1h)
        results[f"{s}_30d"] = run_dataset(c15, c1h, d_at, base, ctrl_start, b_end, named, baseline=BASELINE,
                                          funding=funding)
    log(f"combo_best2 = {named['combo_best2']}")

    thesis_scores = score_vs_live({k: results[k]["variants"] for k in THESIS_DATASETS if k in results})
    control_scores = score_vs_live({f"{s}_30d": results[f"{s}_30d"]["variants"] for s in CONTROLS
                                    if f"{s}_30d" in results})
    max_dd, max_liq = dd_and_liq(results, named)

    # ── corrected stop/liquidation fraction for the entry_fit_leverage
    #    variants (lev_fit, and combo_best2 if it inherited lev_fit) --
    #    `risk_numbers`' own max_stop_liq_frac reads params.leverage, stale
    #    for a trade that opened at a LOWER fitted leverage (see
    #    final_strategy_test.corrected_max_stop_liq_frac's own docstring).
    lev_fit_variants = [v for v, ov in named.items() if "entry_fit_leverage" in ov]
    for v in lev_fit_variants:
        vp = dataclasses.replace(base, **named[v])
        worst = 0.0
        for label, (start, end) in thesis_windows.items():
            theses = theses_by_label[label]
            w15 = [c for c in c15 if start <= c.t < end]
            w1h = [c for c in c1h if c.t < end]
            run = simulate(w15, w1h, journaled_direction(theses), vp, funding)
            worst = max(worst, corrected_max_stop_liq_frac(run, vp))
        max_liq[v] = worst
    log(f"corrected max stop/liq fraction recomputed for {lev_fit_variants}")

    live_dd = max_dd.get(BASELINE, 0.0)
    verdicts = {v: adopt_verdict(thesis_scores, control_scores, max_dd, max_liq, v, live_dd) for v in named}
    adopt = sorted(v for v, ver in verdicts.items() if ver["adopt"])
    log(f"scored ({time.time() - t0:.0f}s); adopt {adopt or 'none'}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "snapshot_dir": a.snapshot_dir,
        "coin": a.coin, "silver_max_leverage": a.silver_max_leverage,
        "snapshot_window": {iv: snap.window_iso(iv) for iv in SNAP.INTERVALS},
        "data_windows": _window_report(snap, thesis_windows),
        "missed_opportunity": missed_report,
        "results": {ds: {"window": r["window"], "variants": r["variants"]} for ds, r in results.items()},
        "thesis_scores": thesis_scores, "control_scores": control_scores,
        "max_dd_pct": max_dd, "max_stop_liq_frac": max_liq,
        "top2_for_combo": top2, "combo_overrides": named["combo_best2"],
        "verdicts": verdicts, "adopt": adopt, "variants": named,
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
    print(f"\nadopt: {rep['adopt'] or 'none'}")
    for label, by_h in rep["missed_opportunity"].items():
        for h, m in by_h.items():
            print(f"missed-opportunity [{label} @{h}]: {m['n_missed']}/{m['n_right']} right-but-no-trade "
                 f"({m['missed_rate_of_right']} of right calls)")
    print("thesis scores (beats/of, geo-mean ratio):")
    for v, s in sorted(rep["thesis_scores"].items(), key=lambda kv: -kv[1]["beats"]):
        print(f"  {v:22s} {s['beats']}/{s['of']}  geo={s['geo_mean_ratio']:.3f}  "
             f"maxDD={rep['max_dd_pct'].get(v):.1f}  maxLiq={rep['max_stop_liq_frac'].get(v):.3f}")
    print(f"top 2 for the combo: {rep['top2_for_combo']}  overrides: {rep['combo_overrides']}")


if __name__ == "__main__":
    raise SystemExit(main())
