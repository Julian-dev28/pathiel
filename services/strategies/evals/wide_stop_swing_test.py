"""Wide-stop swing backtest, 2026-11-03/04 (docs/strategy/WIDE_STOP_SWING_
TEST_2026-11-03.md). "Fewer, wider, longer."

Evidence that started this test: the thesis direction is usually right
(70% Sept, 80% Oct on the next-24h move, the replayed journals), but the
live stop sits 0.75 to 0.6 below entry -- under one 1h ATR14_1h (about
0.95 on Brent now) -- so noise stops it out before the direction pays
(September lost 56% at 20x, 11 stops in 21 trades). On 2026-11-03 the desk
went long three times in 30 minutes into a 3.5-point drop; two stopped
(-$27, -$26), the third is still open.

Hypothesis: trade the thesis direction with a WIDER stop, a LOWER
leverage (desk_tick_sim.Params.entry_fit_leverage: the highest of
20/15/10/8/5x that still keeps the stop inside half the way to
liquidation, bracket.check_leverage), the SAME dollar risk (the live 15%
risk cap, live_params.live_brent_params), and FEWER entries per thesis
(Params.reentry_after_stop / max_stop_reentries / reentry_needs_1h_
reclaim). The stop and target need no new field: entry_lookback_h x
stop_buffer_atr already gives "beyond the 48h/24h edge plus N x ATR14",
and target_lookback_h x target_offset_frac already gives "the far
48h edge, 0.5% inside it" on top of the live 24h/0.5% target.

The grid (focused, not a full cartesian sweep -- one axis at a time, then
four combined "fewer wider longer" candidates that each pick one setting
per axis):
  stop48_atr0.5 / stop48_atr1.0   entry_lookback_h=48, stop_buffer_atr 0.5 / 1.0
  stop24_atr2.0 / stop24_atr3.0   entry_lookback_h=24 (live), stop_buffer_atr 2.0 / 3.0
  lev_fit                         entry_fit_leverage=(20,15,10,8,5), everything else live
  entries_one                     reentry_after_stop=False ("one entry per thesis")
  entries_reclaim                 max_stop_reentries=1, reentry_needs_1h_reclaim=True
  target48                        target_lookback_h=48 (0.5% offset carries over from live)
  combo_48_0.5 / combo_48_1.0     stop48_atr{0.5,1.0} + lev_fit + entries_one + target48
  combo_24_2.0 / combo_24_3.0     stop24_atr{2.0,3.0} + lev_fit + entries_reclaim + target48

Datasets, live rules otherwise (`live_params.live_brent_params`: $64, 20x,
market entry off the 24h range, 2R gate, 0.75 stop, 0.5% target offset,
limit target, 15% risk cap, size-aware slippage and funding on Brent):
  brent_journal          the live oil journal (READ ONLY, --oil-theses-dir; skipped when not given)
  brent_replay30         the October replay (/tmp/thesis-replay-30b/oil/theses), skipped when absent
  brent_replay_sep       the September replay (/tmp/thesis-replay-sep/oil/theses), skipped when absent
  brent_<ctrl>_replay    always_long / always_short / daily_sign on the September replay's window
  brent_<ctrl>_30d       the same controls on the last 30 days of fresh data
  brent_chain            every available thesis window above, chained oldest-first, one $64 start
                         (`extended_history_test.chain_runs`/`summarize_chain`)
  silver_<top 2>         the best 2 Brent variants by the thesis-dataset score, re-run on silver's
                         own live rule (`live_silver_params`) and its own datasets, as a secondary check

Adopt bar (operator's rule for this test): beats live on MOST of the
thesis datasets (journal, Oct, Sept -- whichever are present) AND on the
chain; a plateau across neighbouring settings (a lone spike does not
count); max drawdown no worse than live's worst; the stop stays inside
half the way to liquidation at its own leverage.

Today's regression (2026-11-03, the three-long sequence): the live oil
journal's thesis of that day, replayed on real Brent candles over just
2026-11-03 UTC, under live and the top 3 variants.

    python -m services.strategies.evals.wide_stop_swing_test \\
        [--oil-theses-dir <pathia>/.state/strategies/oil/theses]

Writes /tmp/wide-swing/report.json and summary.csv. Reads candles through
the eval disk cache and the venue's public /info; never touches the live
account, the live config or the journal (the oil/metals theses dirs are
READ ONLY). No LLM calls.
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

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import HOUR_MS, Params, Run, simulate
from services.strategies.evals.brent_stop_buffer_test import (
    BASELINE, CONTROLS, MAX_STOP_FRAC_OF_LIQ, _ms, _scores, _window, pick_best, plateau, stop_liq_frac, summarize)
from services.strategies.evals.closer_target_test import per_day_pct
from services.strategies.evals.desk_tick_backtest import DAY_MS, journaled_direction
from services.strategies.evals.extended_history_test import Leg, _max_dd_from_curve, chain_runs
from services.strategies.evals.live_params import live_brent_params, live_silver_params
from services.strategies.evals.stop_cap_test import FIFTEEN_MS, START, _iso, closed_only, same_market

# ── leverage-aware liquidation distance ─────────────────────────────────────
#
# brent_stop_buffer_test.risk_numbers (inside `summarize`) always checks a
# trade's stop against `params.leverage`, the one fixed default -- fine for
# every existing variant (every trade opens at that same leverage), but
# wrong for Params.entry_fit_leverage, where different trades open at
# DIFFERENT leverages on purpose. pullback_correlation_test.py hit the same
# gap for its own per-trade leverage (pb_fit_leverage) and fixed it the same
# way: re-check each trade at ITS OWN leverage, not the Params default.


def max_liq_frac(run: Run) -> float:
    """The widest stop of `run` as a share of the way to liquidation, at
    EACH trade's own leverage (`Trade.leverage`, falling back to
    `params.leverage` when a trade didn't set one -- every variant except
    `entry_fit_leverage`). Pure given `run`."""
    p = run.params or Params()
    out = 0.0
    for t in run.trades:
        lev = t.leverage if t.leverage is not None else p.leverage
        f = stop_liq_frac(t.entry_px, t.orig_stop_px if t.orig_stop_px else t.stop_px,
                          dataclasses.replace(p, leverage=lev))
        if f is not None:
            out = max(out, f)
    return round(out, 3)


def run_dataset(c15, c1h, direction_at, base: Params, start_ms: int, end_ms: int,
                named: Dict[str, Dict[str, Any]], baseline: str = BASELINE, funding=None) -> Dict[str, Any]:
    """`brent_stop_buffer_test.run_dataset`, with `max_stop_liq_frac`
    corrected by `max_liq_frac` (see above) -- the only difference."""
    w15, w1h = _window(c15, c1h, start_ms, end_ms)
    out: Dict[str, Any] = {"window": [_iso(start_ms), _iso(end_ms)], "variants": {}}
    base_p = dataclasses.replace(base, **named[baseline])
    base_run = simulate(w15, w1h, direction_at, base_p, funding)
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        run = base_run if v == baseline else simulate(w15, w1h, direction_at, p, funding)
        s = summarize(run, start_ms, end_ms)
        s["max_stop_liq_frac"] = max_liq_frac(run)
        out["variants"][v] = s
    return out


def summarize_chain(result: Dict[str, Any], params: Params, start_equity: float, start_ms: int,
                    end_ms: int) -> Dict[str, Any]:
    """`extended_history_test.summarize_chain`, with `max_stop_liq_frac`
    corrected by `max_liq_frac` the same way `run_dataset` above is."""
    synthetic = Run(
        trades=result["trades"], equity_curve=result["equity_curve"], final_equity=result["final_equity"],
        max_drawdown_pct=_max_dd_from_curve(result["equity_curve"], start_equity),
        liquidated=result["liquidated"], liquidation_time=result["liquidation_time"],
        fees_total_usd=result["fees_total_usd"], funding_total_usd=result["funding_total_usd"], params=params,
    )
    s = summarize(synthetic, start_ms, end_ms)
    s["max_stop_liq_frac"] = max_liq_frac(synthetic)
    s["legs_run"] = result["legs_run"]
    s["legs_total"] = result["legs_total"]
    return s

# ── the grid (pure) ─────────────────────────────────────────────────────────

LEV_GRID: Tuple[float, ...] = (20.0, 15.0, 10.0, 8.0, 5.0)

STOP_48H = {"entry_lookback_h": 48, "target_lookback_h": 24}
STOP_24H = {"entry_lookback_h": 24, "target_lookback_h": 24}     # live's own entry_lookback_h, named for symmetry
TARGET_48H = {"target_lookback_h": 48}
LEV_FIT = {"entry_fit_leverage": LEV_GRID}
ONE_PER_THESIS = {"reentry_after_stop": False}
ONE_RECLAIM_REENTRY = {"max_stop_reentries": 1, "reentry_needs_1h_reclaim": True}


def variants() -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides on `live_brent_params()`. One axis at a
    time, then four combined "fewer wider longer" candidates (one setting
    per axis: a wider stop, the leverage fit, a fewer-entries rule, the
    wider target)."""
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    out["stop48_atr0.5"] = {**STOP_48H, "stop_buffer_atr": 0.5}
    out["stop48_atr1.0"] = {**STOP_48H, "stop_buffer_atr": 1.0}
    out["stop24_atr2.0"] = {**STOP_24H, "stop_buffer_atr": 2.0}
    out["stop24_atr3.0"] = {**STOP_24H, "stop_buffer_atr": 3.0}
    out["lev_fit"] = dict(LEV_FIT)
    out["entries_one"] = dict(ONE_PER_THESIS)
    out["entries_reclaim"] = dict(ONE_RECLAIM_REENTRY)
    out["target48"] = dict(TARGET_48H)
    out["combo_48_0.5_one"] = {**STOP_48H, "stop_buffer_atr": 0.5, **LEV_FIT, **ONE_PER_THESIS, **TARGET_48H}
    out["combo_48_1.0_reclaim"] = {**STOP_48H, "stop_buffer_atr": 1.0, **LEV_FIT, **ONE_RECLAIM_REENTRY, **TARGET_48H}
    out["combo_24_2.0_one"] = {**STOP_24H, "stop_buffer_atr": 2.0, **LEV_FIT, **ONE_PER_THESIS, **TARGET_48H}
    out["combo_24_3.0_reclaim"] = {**STOP_24H, "stop_buffer_atr": 3.0, **LEV_FIT, **ONE_RECLAIM_REENTRY,
                                   **TARGET_48H}
    return out


# Each family's grid, for the plateau check (a lone spike does not count;
# at least one non-live neighbour on each side must also clear the bar).
FAMILIES: Dict[str, List[str]] = {
    "stop_48": ["stop48_atr0.5", "stop48_atr1.0"],
    "stop_24": ["stop24_atr2.0", "stop24_atr3.0"],
    "entries": ["entries_one", "entries_reclaim"],
    "combo_48": ["combo_48_0.5_one", "combo_48_1.0_reclaim"],
    "combo_24": ["combo_24_2.0_one", "combo_24_3.0_reclaim"],
}


# ── adopt bar (pure) ─────────────────────────────────────────────────────────

def dd_and_liq(results: Dict[str, Any], named: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Per variant, the WORST max-drawdown % and the closest any stop came
    to the liquidation line, across every dataset in `results`. Pure."""
    max_dd = {v: max((r["variants"][v]["max_dd_pct"] for r in results.values()), default=0.0) for v in named}
    max_liq = {v: max((r["variants"][v]["max_stop_liq_frac"] for r in results.values()), default=0.0)
              for v in named}
    return max_dd, max_liq


def adopt_verdict(scores_thesis: Dict[str, Dict[str, Any]], chain_beats: Dict[str, bool],
                  max_dd: Dict[str, float], max_liq: Dict[str, float], name: str,
                  families: Dict[str, List[str]] = FAMILIES, baseline: str = BASELINE) -> Dict[str, Any]:
    """The operator's adopt bar for this test: beats live on most of the
    THESIS datasets (journal/Oct/Sept, not the controls) AND on the
    chain, a plateau (not a lone spike), max drawdown no worse than
    live's worst, and the stop stays inside half the way to liquidation."""
    s = scores_thesis.get(name)
    if s is None:
        return {"adopt": False, "candidate": name, "why": "no candidate"}
    most = s["beats"] > s["of"] / 2.0
    flat = plateau(scores_thesis, name, families)
    chain_ok = bool(chain_beats.get(name))
    dd_ok = max_dd.get(name, 0.0) <= max_dd.get(baseline, 0.0) + 1e-6
    liq_ok = max_liq.get(name, 0.0) <= MAX_STOP_FRAC_OF_LIQ + 1e-9
    why = [w for w, ok in (("beats live on most thesis datasets", most), ("beats live on the chain", chain_ok),
                           ("plateau", flat), ("max DD no worse than live", dd_ok),
                           ("stop inside half the way to liquidation", liq_ok)) if not ok]
    return {"adopt": most and chain_ok and flat and dd_ok and liq_ok, "candidate": name, "beats": s["beats"],
            "loses": s["loses"], "of": s["of"], "geo_mean_ratio": s["geo_mean_ratio"], "plateau": flat,
            "chain_beats": chain_ok, "dd_ok": dd_ok, "liq_ok": liq_ok, "fails": why}


# ── today's regression: 2026-11-03, the three-long sequence ────────────────

TODAY_WINDOW_UTC = ((2026, 11, 3, 0, 0), (2026, 11, 4, 0, 0))


def today_regression(c15: Sequence[Candle], c1h: Sequence[Candle], all_theses: Sequence[D.ThesisDirection],
                     named: Dict[str, Dict[str, Any]], base: Params, funding=None,
                     window: Tuple[Tuple[int, ...], Tuple[int, ...]] = TODAY_WINDOW_UTC) -> Dict[str, Any]:
    """Replay the live journal's thesis of 2026-11-03 (the one active
    through the three-long sequence) over just that UTC day's real Brent
    candles, under live and each named variant -- `run_dataset` on a
    narrower window, same mechanics as every other dataset in this file.
    Not a reconstruction of the exact live fills (their sizes/fees come
    from the real desk's equity at the time, not this file's $64), just
    what each RULE would do on the same price action and the same thesis."""
    start_ms, end_ms = _ms(window[0]), _ms(window[1])
    d_at = journaled_direction(all_theses)
    active = [th for th in all_theses if th.created_at_ms <= start_ms]
    thesis_of_day = max(active, key=lambda th: th.created_at_ms) if active else None
    res = run_dataset(c15, c1h, d_at, base, start_ms, end_ms, named, funding=funding)
    return {"window": res["window"],
            "thesis": None if thesis_of_day is None else {
                "created_at": _iso(thesis_of_day.created_at_ms), "side": thesis_of_day.side,
                "entry_px": thesis_of_day.entry_px, "stop_px": thesis_of_day.stop_px,
                "invalidation_px": thesis_of_day.invalidation_px},
            "variants": res["variants"]}


# ── summary rows ───────────────────────────────────────────────────────────

SUMMARY_FIELDS = ["dataset", "variant", "final_equity", "return_pct", "per_day_pct", "max_dd_pct", "trades",
                  "win_rate", "avg_r", "worst_loss_usd", "worst_loss_pct", "max_stop_liq_frac", "target_exits",
                  "stop_exits", "thesis_close_exits", "other_exits", "open_at_end"]


def summary_csv_rows(results: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for ds, r in results.items():
        for v, s in r["variants"].items():
            exits = s.get("exits") or {}
            thesis_close = exits.get("invalidation", 0) + exits.get("direction_flip", 0)
            accounted = exits.get("target", 0) + exits.get("stop", 0) + thesis_close
            out.append({
                "dataset": ds, "variant": v, "final_equity": s["final_equity"], "return_pct": s["return_pct"],
                "per_day_pct": per_day_pct(s), "max_dd_pct": s["max_dd_pct"], "trades": s["trades"],
                "win_rate": s["win_rate"], "avg_r": s["avg_r"], "worst_loss_usd": s["worst_loss_usd"],
                "worst_loss_pct": s["worst_loss_pct"], "max_stop_liq_frac": s["max_stop_liq_frac"],
                "target_exits": exits.get("target", 0), "stop_exits": exits.get("stop", 0),
                "thesis_close_exits": thesis_close, "other_exits": s["trades"] - accounted,
                "open_at_end": s.get("open_at_end") is not None})
    return out


# ── main ─────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import direction_series, fetch_market_meta, load_coin_candles

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal's theses (skipped when empty)")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses",
                    help="the October replay; skipped when absent/empty")
    ap.add_argument("--oil-replay-sep-dir", default="/tmp/thesis-replay-sep/oil/theses",
                    help="the September replay; skipped when absent/empty")
    ap.add_argument("--metals-theses-dir", default=os.environ.get("PATHIEL_METALS_THESES_DIR", ""),
                    help="READ ONLY: the live metals journal's theses (skipped when empty)")
    ap.add_argument("--silver-theses-dir", default="/tmp/thesis-replay-silver/theses")
    ap.add_argument("--silver-cache-dir", default="/tmp/thesis-backtest/cache")
    ap.add_argument("--silver-max-leverage", type=float, default=None)
    ap.add_argument("--top-n", type=int, default=2, help="top Brent variants re-run on silver")
    ap.add_argument("--cache-dir", default="/tmp/wide-swing/cache", help="fresh Brent + silver candles")
    ap.add_argument("--out-dir", default="/tmp/wide-swing")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} wide-swing: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    def fresh(coin: str) -> Tuple[List[Candle], List[Candle]]:
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        return closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)

    # ── Brent data
    b15, b1h = fresh("xyz:BRENTOIL")
    b_end = b15[-1].t + FIFTEEN_MS
    funding = D.fetch_funding_cached("xyz:BRENTOIL", b15[0].t, b_end, a.cache_dir)
    bp = live_brent_params(size_slippage=True, starting_equity=START)
    named = variants()

    journal_theses = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    thesis_sets: Dict[str, Tuple[List[D.ThesisDirection], str]] = {}
    for label, tdir, src in (("brent_journal", a.oil_theses_dir, "journal"),
                             ("brent_replay30", a.oil_replay_dir, "oct"),
                             ("brent_replay_sep", a.oil_replay_sep_dir, "sep")):
        theses = journal_theses if src == "journal" else (D.load_journaled_theses(tdir) if tdir else [])
        if not theses:
            log(f"{label}: no theses at {tdir!r}, skipped")
            continue
        thesis_sets[label] = (theses, src)

    brent_sets: Dict[str, Tuple[Callable[[int], Any], int, int]] = {}
    legs: List[Leg] = []
    for label, (theses, _src) in sorted(thesis_sets.items(), key=lambda kv: min(t.created_at_ms for t in kv[1][0])):
        start = min(th.created_at_ms for th in theses)
        end = b_end if label == "brent_journal" else min(b_end, max(th.created_at_ms for th in theses) + DAY_MS)
        d_at = journaled_direction(theses)
        brent_sets[label] = (d_at, start, end)
        legs.append((d_at, b15, b1h, funding, start, end))
    rp = brent_sets.get("brent_replay_sep") or next(iter(brent_sets.values()), None)
    for s in CONTROLS:
        if rp:
            brent_sets[f"brent_{s}_replay"] = (direction_series(s, b1h), rp[1], rp[2])
        brent_sets[f"brent_{s}_30d"] = (direction_series(s, b1h), b_end - 30 * DAY_MS, b_end)
    log(f"brent data end {_iso(b_end)}, {len(thesis_sets)} thesis window(s), {len(brent_sets)} dataset(s) total")

    # ── the grid, on every Brent dataset
    res: Dict[str, Any] = {}
    for ds, (d_at, st, en) in brent_sets.items():
        res[ds] = run_dataset(b15, b1h, d_at, bp, st, en, named, funding=funding)
    log(f"brent grid: {len(res)} datasets x {len(named)} variants ({time.time() - t0:.0f}s)")

    # ── the chain: every thesis window chained oldest-first, one $64 start
    chain_summary: Dict[str, Dict[str, Any]] = {}
    if legs:
        chain_start_ms, chain_end_ms = legs[0][4], legs[-1][5]
        for v, ov in named.items():
            p = dataclasses.replace(bp, **ov)
            cr = chain_runs(legs, p, START)
            s = summarize_chain(cr, p, START, chain_start_ms, chain_end_ms)
            s["per_day_pct"] = per_day_pct(s)
            chain_summary[v] = s
        log(f"chain: {len(legs)} leg(s), {chain_summary[BASELINE]['legs_run']}/{chain_summary[BASELINE]['legs_total']}"
            f" run, live final ${chain_summary[BASELINE]['final_equity']:.2f} ({time.time() - t0:.0f}s)")
    else:
        log("chain: no thesis windows available, skipped")

    groups = {"thesis": list(thesis_sets), "controls_replay": [k for k in res if k.endswith("_replay")],
             "controls_30d": [k for k in res if k.endswith("_30d")], "all": list(res)}
    scores = _scores(res, groups)
    scores_thesis = scores.get("thesis", {})
    chain_beats = {v: (chain_summary[v]["final_equity"] > chain_summary[BASELINE]["final_equity"])
                   for v in named} if chain_summary else {v: True for v in named}  # no chain: don't gate on it
    max_dd, max_liq = dd_and_liq(res, named)
    candidates = [v for v in named if v != BASELINE]
    verdicts = {v: adopt_verdict(scores_thesis, chain_beats, max_dd, max_liq, v) for v in candidates}
    adopt = sorted(v for v, x in verdicts.items() if x["adopt"])
    ranked = sorted(candidates, key=lambda v: ((scores_thesis.get(v, {}).get("beats", 0)
                                                - scores_thesis.get(v, {}).get("loses", 0)),
                                               scores_thesis.get(v, {}).get("geo_mean_ratio", 0.0)), reverse=True)
    best = pick_best(scores["all"])
    log(f"scored ({time.time() - t0:.0f}s); adopt {adopt or 'none'}; best overall {best}")

    # ── today's regression: 2026-11-03
    top3 = ranked[:3]
    today_named = {BASELINE: {}, **{v: named[v] for v in top3}}
    today = today_regression(b15, b1h, journal_theses, today_named, bp, funding) if journal_theses else None
    if today is None:
        log("today regression: no live journal theses, skipped")
    else:
        log(f"today ({today['window']}): thesis {today['thesis']}")

    # ── silver secondary check: the top N Brent variants, silver's own
    # live rule and datasets
    top_n = ranked[:max(0, a.top_n)]
    named_silver = {BASELINE: {}, **{v: named[v] for v in top_n}}
    if a.silver_max_leverage is None:
        a.silver_max_leverage = float((fetch_market_meta(["xyz:SILVER"]).get("xyz:SILVER") or {})
                                      .get("maxLeverage") or 20)
    silver = load_coin_candles("xyz:SILVER", a.silver_cache_dir)
    s15, s1h = silver["c15"], silver["c1h"]
    from services.strategies.evals.cross_market_backtest import mean_atr
    atr, _ = mean_atr(s1h)
    sp = live_silver_params(a.silver_max_leverage, atr, starting_equity=START)
    s_theses = D.load_journaled_theses(a.silver_theses_dir)
    silver_sets: Dict[str, Tuple[Sequence[Candle], Sequence[Candle], Callable[[int], Any], int, int]] = {}
    if s_theses:
        s_start = min(th.created_at_ms for th in s_theses)
        s_end = max(c.t for c in s15) + FIFTEEN_MS
        silver_sets["silver_theses"] = (s15, s1h, journaled_direction(s_theses), s_start, s_end)
        for s in CONTROLS:
            silver_sets[f"silver_{s}"] = (s15, s1h, direction_series(s, s1h), s_start, s_end)
    f15, f1h = fresh("xyz:SILVER")
    f_end = f15[-1].t + FIFTEEN_MS
    m_all = D.load_journaled_theses(a.metals_theses_dir) if a.metals_theses_dir else []
    m_theses = same_market(m_all, f1h) if m_all else []
    if m_theses:
        silver_sets["silver_journal"] = (f15, f1h, journaled_direction(m_theses),
                                         min(th.created_at_ms for th in m_theses), f_end)
    else:
        log(f"silver_journal: no theses at {a.metals_theses_dir!r}, skipped")
    for s in CONTROLS:
        silver_sets[f"silver_{s}_recent"] = (f15, f1h, direction_series(s, f1h), f_end - 30 * DAY_MS, f_end)

    res_silver: Dict[str, Any] = {}
    for ds, (c15_, c1h_, d_at, st, en) in silver_sets.items():
        res_silver[ds] = run_dataset(c15_, c1h_, d_at, sp, st, en, named_silver)
    log(f"silver secondary check: {len(res_silver)} datasets x {len(named_silver)} variants "
        f"({time.time() - t0:.0f}s)")
    groups_silver = {"thesis": [k for k in ("silver_theses", "silver_journal") if k in res_silver],
                     "controls": [k for k in res_silver if k not in ("silver_theses", "silver_journal")],
                     "all": list(res_silver)}
    scores_silver = _scores(res_silver, groups_silver)

    # ── write
    with open(os.path.join(a.out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(summary_csv_rows(res))
        w.writerows(summary_csv_rows(res_silver))
        if chain_summary:
            w.writerows([{"dataset": "brent_chain", "variant": v, "final_equity": s["final_equity"],
                          "return_pct": s["return_pct"], "per_day_pct": s.get("per_day_pct"),
                          "max_dd_pct": s["max_dd_pct"], "trades": s["trades"], "win_rate": s["win_rate"],
                          "avg_r": s["avg_r"], "worst_loss_usd": s.get("worst_loss_usd"),
                          "worst_loss_pct": s.get("worst_loss_pct"), "max_stop_liq_frac": s.get("max_stop_liq_frac"),
                          "target_exits": s.get("exits", {}).get("target", 0),
                          "stop_exits": s.get("exits", {}).get("stop", 0),
                          "thesis_close_exits": s.get("exits", {}).get("invalidation", 0)
                          + s.get("exits", {}).get("direction_flip", 0),
                          "other_exits": None, "open_at_end": s.get("open_at_end") is not None}
                         for v, s in chain_summary.items()])
        if today is not None:
            w.writerows([{"dataset": "today_2026-11-03", "variant": v, "final_equity": s["final_equity"],
                          "return_pct": s["return_pct"], "per_day_pct": per_day_pct(s),
                          "max_dd_pct": s["max_dd_pct"], "trades": s["trades"], "win_rate": s["win_rate"],
                          "avg_r": s["avg_r"], "worst_loss_usd": s["worst_loss_usd"],
                          "worst_loss_pct": s["worst_loss_pct"], "max_stop_liq_frac": s["max_stop_liq_frac"],
                          "target_exits": s.get("exits", {}).get("target", 0),
                          "stop_exits": s.get("exits", {}).get("stop", 0),
                          "thesis_close_exits": s.get("exits", {}).get("invalidation", 0)
                          + s.get("exits", {}).get("direction_flip", 0), "other_exits": None,
                          "open_at_end": s.get("open_at_end") is not None}
                         for v, s in today["variants"].items()])

    strip = lambda rr: {k: {kk: vv for kk, vv in r.items() if kk != "entries"} for k, r in rr.items()}  # noqa: E731
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "brent_data_end": _iso(b_end), "silver_data_end": _iso(f_end), "silver_max_leverage": a.silver_max_leverage,
        "variants": named, "families": FAMILIES, "datasets": strip(res), "scores": scores, "groups": groups,
        "max_dd_pct": max_dd, "max_stop_liq_frac": max_liq, "chain": chain_summary, "chain_beats_live": chain_beats,
        "best_overall": best, "ranked_by_thesis": ranked, "verdicts": verdicts, "adopt": adopt,
        "today_2026_11_03": today,
        "silver": {"top_n": top_n, "variants": named_silver, "datasets": strip(res_silver),
                   "scores": scores_silver},
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\nsummary: {os.path.join(a.out_dir, 'summary.csv')}")
    return 0


def _print(rep: Dict[str, Any]) -> None:
    print(f"brent data end {rep['brent_data_end']}, silver data end {rep['silver_data_end']}")
    for g, sc in rep["scores"].items():
        print(f"\nscore [{g}] (beats/loses live of N, geo-mean final/live):")
        for v, x in sorted(sc.items(), key=lambda kv: -kv[1]["geo_mean_ratio"]):
            print(f"  {v:<22} {x['beats']:>2}/{x['loses']:>2} of {x['of']:<2} {x['geo_mean_ratio']:.3f}")
    if rep["chain"]:
        print("\nchain (one $64 start, every thesis window chained):")
        for v, s in rep["chain"].items():
            print(f"  {v:<22} final ${s['final_equity']:>8.2f}  dd {s['max_dd_pct']:>5.1f}%  "
                  f"trades {s['trades']:>3}  legs {s['legs_run']}/{s['legs_total']}")
    print(f"\nbest overall: {rep['best_overall']}; ranked by thesis score: {rep['ranked_by_thesis']}")
    print(f"adopt: {rep['adopt'] or 'none'}")
    for v, x in rep["verdicts"].items():
        print(f"  {v:<22} {json.dumps(x)}")
    if rep["today_2026_11_03"]:
        t = rep["today_2026_11_03"]
        print(f"\n== today 2026-11-03 ({t['window'][0]} -> {t['window'][1]}), thesis: {t['thesis']}")
        for v, s in t["variants"].items():
            print(f"  {v:<22} final ${s['final_equity']:>8.2f}  trades {s['trades']:>3}  "
                  f"stops {s.get('exits', {}).get('stop', 0)}  net {s['return_pct']:>6.1f}%")
    print(f"\n== silver secondary check (top {len(rep['silver']['top_n'])}: {rep['silver']['top_n']})")
    for g, sc in rep["silver"]["scores"].items():
        print(f"  score [{g}]:")
        for v, x in sorted(sc.items(), key=lambda kv: -kv[1]["geo_mean_ratio"]):
            print(f"    {v:<22} {x['beats']:>2}/{x['loses']:>2} of {x['of']:<2} {x['geo_mean_ratio']:.3f}")


if __name__ == "__main__":
    raise SystemExit(main())
