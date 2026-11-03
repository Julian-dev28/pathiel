"""Closer-target backtest, 2026-11-03 (docs/strategy/CLOSER_TARGET_TEST_2026-11-03.md).

Operator, 2026-11-03, on the open long of 38.05 xyz:BRENTOIL from 103.61
(stop 102.99, risk $23.59, target 111.77, about 13R, the live rule's far
24h-range edge with `bracket.TARGET_OFFSET_FRAC` = 0.5% inside it): "whats
the realistic high? ... test it". A quick empirical check over 208 days of
1h bars, setups where price sat in the bottom 35% of its 24h range: the
median 48h high is +2.6%, the median 72h high is +3.3%, and the far-edge
target filled first in only 15% of cases within 72h versus 58% stop-first.
This backtests closer targets against the live far-edge target.

docs/strategy/OIL_STRATEGY.md's 2026-10-30 sweep already rejected fixed
1.5R/2R/3R targets and offsets of 0.75%/1%/1.5% inside the edge, on older
rules and older data. Not yet tested, all on the CURRENT live rules (market
entry off the 24h range, 2R gate, stop 0.75 beyond the edge, limit target,
20x, 15% risk cap since 2026-11-02):

  tr<N>     fixed N x risk from the entry (desk_tick_sim.Params.target_r),
            N in 4, 5, 6, 7, 8, 10
  off_<X>   X% of price inside the far edge (target_offset_frac), X in 2, 3, 4
            (0.75/1/1.5 already rejected; this sweep goes wider)
  cap_<X>   the live far-edge target, capped at entry +/- X% of price,
            whichever is nearer (target_cap_frac, new field), X in
            2, 2.5, 3, 3.5, 4, 5
  rtarget_p75 / rtarget_mid   the 24h range's 75% point / midpoint
            (target_offset_range_frac at 0.25 / 0.5 -- already in
            desk_tick_sim from OIL_REGIME_TEST_2026-11-02.md, so no new
            field was needed for these two)

Datasets, live rules otherwise ($64 Brent / scouting_params silver, 20x,
15-minute ticks, 15% risk cap, size-aware slippage, funding):
  brent_journal          the live oil journal (READ ONLY, --oil-theses-dir; skipped when not given)
  brent_replay30         the 30 replayed Brent theses (/tmp/thesis-replay-30b)
  brent_<ctrl>_replay    always_long / always_short / daily_sign on the replay window
  brent_<ctrl>_30d       the same on the last 30 days
  silver_<top variant>   the top 2-3 Brent variants re-run on silver (its own live params,
                         SILVER_LIVE) as a secondary check, same dataset family

Adopt bar (the other 2026-11-02 tests' rule): beats live on most datasets
with a geo-mean ratio of 1 or more; a plateau across neighbouring settings,
not a single spike; drawdown no worse; the stop stays inside half the way
to liquidation (unaffected here -- only the target moves).

Regression row: the live long of 2026-11-02 18:08Z (entry 103.61, stop
102.99) under every variant -- where its target would sit and whether
Brent's actual move since entry (high fetched fresh each run) would already
have filled it.

    python -m services.strategies.evals.closer_target_test \\
        [--oil-theses-dir <pathia>/.state/strategies/oil/theses]

Writes /tmp/closer-target/report.json and summary.csv. Reads candles
through the eval disk cache and the venue's public /info; never touches the
live account, the live config or the journal. No LLM calls.
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
from services.strategies.backtest.desk_tick_sim import HOUR_MS, Params, _levels, cap_target, offset_target
from services.strategies.evals.brent_stop_buffer_test import (
    BASELINE, CONTROLS, SILVER_LIVE, _ms, _scores, pick_best, rule_pass, run_dataset, verdict)
from services.strategies.evals.desk_tick_backtest import DAY_MS, journaled_direction
from services.strategies.evals.stop_cap_test import FIFTEEN_MS, START, _iso, closed_only

# ── the grid (pure) ─────────────────────────────────────────────────────────

TARGET_RS = (4.0, 5.0, 6.0, 7.0, 8.0, 10.0)
OFFSET_PCTS = (2.0, 3.0, 4.0)               # already rejected: 0.75, 1, 1.5 (live is 0.5)
CAP_PCTS = (2.0, 2.5, 3.0, 3.5, 4.0, 5.0)
RANGE_FRACS = {"rtarget_p75": 0.25, "rtarget_mid": 0.5}   # OIL_REGIME_TEST_2026-11-02.md's field


def variants() -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides on the live rule."""
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    for r in TARGET_RS:
        out[f"tr{r:g}"] = {"target_r": r}
    for x in OFFSET_PCTS:
        out[f"off_{x:g}"] = {"target_offset_frac": round(x / 100.0, 6)}
    for x in CAP_PCTS:
        out[f"cap_{x:g}"] = {"target_cap_frac": round(x / 100.0, 6)}
    for name, f in RANGE_FRACS.items():
        out[name] = {"target_offset_range_frac": f}
    return out


# Each family's grid in order, for the plateau check (brent_stop_buffer_
# test.plateau: a neighbour that IS live leaves that side open).
FAMILIES: Dict[str, List[str]] = {
    "target_r": [f"tr{r:g}" for r in TARGET_RS],
    "offset": [BASELINE] + [f"off_{x:g}" for x in OFFSET_PCTS],
    "cap": [f"cap_{x:g}" for x in CAP_PCTS],
    "range": list(RANGE_FRACS),
}


# ── the current (open) trade regression ──────────────────────────────────────

# Operator's numbers, 2026-11-03: long 38.05 xyz:BRENTOIL from 103.61, stop
# 102.99 (risk $23.59 = 0.62 x 38.05), target 111.77 (the far 24h edge, 0.5%
# inside 112.33 -- a couple of cents off 111.77 because the live tick's exact
# lookback bar can differ by one bar from this script's; see the report's
# `struct_high`/`struct_low`, which the stop reproduces exactly: 103.74 -
# 0.75 = 102.99).
CURRENT_TRADE = {"side": "long", "entry_bar_utc": (2026, 11, 2, 18, 0), "entry_px": 103.61, "size": 38.05,
                 "stop_px": 102.99, "orig_tp_px": 111.77, "risk_usd": 23.59}


def current_trade_targets(c15: Sequence[Candle], c1h: Sequence[Candle], named: Dict[str, Dict[str, Any]],
                          base: Params, trade: Dict[str, Any] = CURRENT_TRADE) -> Dict[str, Any]:
    """Where each variant's target would sit on the live trade, from the
    real 24h range at the entry tick (closed 1h bars only, no lookahead) and
    the real entry/stop, and whether Brent's real move since entry (the 15m
    bars from the entry tick to the data end) would already have filled it.
    Pure given its candle inputs."""
    t = _ms(trade["entry_bar_utc"])
    side, entry, stop = trade["side"], trade["entry_px"], trade["stop_px"]
    risk = abs(entry - stop)
    closed = sorted((c for c in c1h if c.t + HOUR_MS <= t), key=lambda c: c.t)
    need = max(15, base.entry_lookback_h, base.target_lookback_h)
    lv = _levels(closed[-need:], base.entry_lookback_h, base.target_lookback_h)
    bars = sorted((c for c in c15 if c.t >= t), key=lambda c: c.t)
    high_so_far = max((c.h for c in bars), default=None)
    low_so_far = min((c.l for c in bars), default=None)
    out: Dict[str, Any] = {
        "trade": dict(trade), "struct_low": lv["target_low"] if lv else None,
        "struct_high": lv["target_high"] if lv else None, "high_so_far": high_so_far, "low_so_far": low_so_far,
        "data_end": _iso(bars[-1].t + FIFTEEN_MS) if bars else None, "variants": {}}
    if lv is None:
        return out
    struct_target = lv["target_high"] if side == "long" else lv["target_low"]
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        if p.target_r is not None:
            target_px = entry + p.target_r * risk if side == "long" else entry - p.target_r * risk
        elif p.target_offset_range_frac is not None:
            off = p.target_offset_range_frac * (lv["target_high"] - lv["target_low"])
            target_px = struct_target - off if side == "long" else struct_target + off
        else:
            target_px = offset_target(struct_target, side, p.target_offset_frac)
        target_px = cap_target(target_px, entry, side, p.target_cap_frac)
        r_mult = (target_px - entry) / risk if side == "long" else (entry - target_px) / risk
        filled = (high_so_far is not None and high_so_far >= target_px) if side == "long" else (
            low_so_far is not None and low_so_far <= target_px)
        pct = (target_px / entry - 1.0) if side == "long" else (1.0 - target_px / entry)
        out["variants"][v] = {"target_px": round(target_px, 3), "pct_from_entry": round(100 * pct, 2),
                              "r_multiple": round(r_mult, 2), "already_filled": bool(filled)}
    return out


# ── summary rows ───────────────────────────────────────────────────────────

SUMMARY_FIELDS = ["dataset", "variant", "final_equity", "return_pct", "per_day_pct", "max_dd_pct", "trades",
                  "win_rate", "avg_r", "worst_loss_usd", "worst_loss_pct", "max_stop_liq_frac", "target_exits",
                  "stop_exits", "thesis_close_exits", "other_exits", "open_at_end"]


def per_day_pct(s: Dict[str, Any]) -> Optional[float]:
    days = s.get("days")
    ret = s.get("return_pct")
    if not days or days <= 0 or ret is None:
        return None
    return round((((1.0 + ret / 100.0) ** (1.0 / days)) - 1.0) * 100.0, 3)


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
    from services.strategies.evals.market_scout import direction_series, fetch_market_meta, load_coin_candles, \
        scouting_params
    from services.strategies.evals.strategy_sweep import base_params

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal's theses (skipped when empty)")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--metals-theses-dir", default=os.environ.get("PATHIEL_METALS_THESES_DIR", ""),
                    help="READ ONLY: the live metals journal's theses (skipped when empty)")
    ap.add_argument("--silver-theses-dir", default="/tmp/thesis-replay-silver/theses")
    ap.add_argument("--silver-cache-dir", default="/tmp/thesis-backtest/cache")
    ap.add_argument("--silver-max-leverage", type=float, default=None)
    ap.add_argument("--top-n", type=int, default=3, help="top Brent variants re-run on silver")
    ap.add_argument("--cache-dir", default="/tmp/closer-target/cache", help="fresh Brent + silver candles")
    ap.add_argument("--out-dir", default="/tmp/closer-target")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} closer-target: {msg}"
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
    bp = dataclasses.replace(base_params(), starting_equity=START)
    brent_sets: Dict[str, Tuple[Callable[[int], Any], int, int]] = {}
    for label, tdir, end_rule in (("brent_journal", a.oil_theses_dir, "data"),
                                  ("brent_replay30", a.oil_replay_dir, "last+1d")):
        theses = D.load_journaled_theses(tdir) if tdir else []
        if not theses:
            log(f"{label}: no theses at {tdir!r}, skipped")
            continue
        start = min(th.created_at_ms for th in theses)
        end = b_end if end_rule == "data" else min(b_end, max(th.created_at_ms for th in theses) + DAY_MS)
        brent_sets[label] = (journaled_direction(theses), start, end)
    rp = brent_sets.get("brent_replay30")
    for s in CONTROLS:
        if rp:
            brent_sets[f"brent_{s}_replay"] = (direction_series(s, b1h), rp[1], rp[2])
        brent_sets[f"brent_{s}_30d"] = (direction_series(s, b1h), b_end - 30 * DAY_MS, b_end)
    log(f"brent data end {_iso(b_end)}, {len(brent_sets)} datasets")

    # ── the grid, on every Brent dataset
    named = variants()
    res: Dict[str, Any] = {}
    for ds, (d_at, st, en) in brent_sets.items():
        res[ds] = run_dataset(b15, b1h, d_at, bp, st, en, named, funding=funding)
    log(f"brent grid: {len(res)} datasets x {len(named)} variants ({time.time() - t0:.0f}s)")

    groups = {"thesis": [k for k in ("brent_journal", "brent_replay30") if k in res],
             "controls_replay": [k for k in res if k.endswith("_replay")],
             "controls_30d": [k for k in res if k.endswith("_30d")], "all": list(res)}
    scores = _scores(res, groups)
    max_liq = {v: max(r["variants"][v]["max_stop_liq_frac"] for r in res.values()) for v in named}
    candidates = [v for v in named if v != BASELINE]
    verdicts = {v: verdict(scores["all"], max_liq, v, FAMILIES) for v in candidates}
    adopt = sorted(v for v, x in verdicts.items() if x["adopt"])
    best = pick_best(scores["all"])
    log(f"scored ({time.time() - t0:.0f}s); rule_pass {rule_pass(scores['all'], max_liq, FAMILIES)}")

    # ── the current (open) trade regression
    today = current_trade_targets(b15, b1h, named, bp)

    # ── silver secondary check: the top N Brent variants (by beats-of, tie
    # to geo-mean), on silver's own live rule (SILVER_LIVE), same dataset
    # family (replay theses, the live metals journal, controls).
    ranked = sorted(candidates, key=lambda v: (scores["all"][v]["beats"] - scores["all"][v]["loses"],
                                               scores["all"][v]["geo_mean_ratio"]), reverse=True)
    top_n = ranked[:max(0, a.top_n)]
    named_silver = {BASELINE: {}, **{v: named[v] for v in top_n}}

    if a.silver_max_leverage is None:
        a.silver_max_leverage = float((fetch_market_meta(["xyz:SILVER"]).get("xyz:SILVER") or {})
                                      .get("maxLeverage") or 20)
    silver = load_coin_candles("xyz:SILVER", a.silver_cache_dir)
    s15, s1h = silver["c15"], silver["c1h"]
    from services.strategies.evals.cross_market_backtest import mean_atr
    atr, _ = mean_atr(s1h)
    sp = dataclasses.replace(scouting_params(a.silver_max_leverage, atr), starting_equity=START, **SILVER_LIVE)
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
    from services.strategies.evals.stop_cap_test import same_market
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
    silver_liq = {v: max(r["variants"][v]["max_stop_liq_frac"] for r in res_silver.values()) for v in named_silver}
    verdicts_silver = {v: verdict(scores_silver["all"], silver_liq, v, None) for v in top_n}

    # ── write
    with open(os.path.join(a.out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(summary_csv_rows(res))
        w.writerows(summary_csv_rows(res_silver))
    strip = lambda rr: {k: {kk: vv for kk, vv in r.items() if kk != "entries"} for k, r in rr.items()}  # noqa: E731
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "brent_data_end": _iso(b_end), "silver_data_end": _iso(f_end), "silver_max_leverage": a.silver_max_leverage,
        "variants": named, "families": FAMILIES, "datasets": strip(res), "scores": scores, "groups": groups,
        "max_stop_liq_frac": max_liq, "best": best, "verdicts": verdicts, "adopt": adopt,
        "current_trade": today, "silver": {"top_n": top_n, "variants": named_silver, "datasets": strip(res_silver),
                                           "scores": scores_silver, "verdicts": verdicts_silver},
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
            print(f"  {v:<12} {x['beats']:>2}/{x['loses']:>2} of {x['of']:<2} {x['geo_mean_ratio']:.3f}")
    print(f"\nbest (all datasets): {rep['best']}; adopt: {rep['adopt'] or 'none'}")
    for v, x in rep["verdicts"].items():
        print(f"  {v:<12} {json.dumps(x)}")
    print("\n== current (open) trade: 103.61 long")
    t = rep["current_trade"]
    print(f"  struct_low {t['struct_low']} struct_high {t['struct_high']} high_so_far {t['high_so_far']} "
          f"data_end {t['data_end']}")
    for v, r in t["variants"].items():
        print(f"  {v:<12} {json.dumps(r)}")
    print(f"\n== silver secondary check (top {len(rep['silver']['top_n'])}: {rep['silver']['top_n']})")
    for g, sc in rep["silver"]["scores"].items():
        print(f"  score [{g}]:")
        for v, x in sorted(sc.items(), key=lambda kv: -kv[1]["geo_mean_ratio"]):
            print(f"    {v:<12} {x['beats']:>2}/{x['loses']:>2} of {x['of']:<2} {x['geo_mean_ratio']:.3f}")
    for v, x in rep["silver"]["verdicts"].items():
        print(f"  silver {v:<12} {json.dumps(x)}")


if __name__ == "__main__":
    raise SystemExit(main())
