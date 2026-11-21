"""HYPE desk strategy test, 2026-11-20 (docs/strategy/HYPE_BACKTEST_2026-11-20.md).

Operator: "Start the research on HYPE and desk for hype, maybe." HYPE is a
main-dex Hyperliquid perp (no HIP-3 dex prefix), venue max leverage 10
(confirmed live 2026-11-20: metaAndAssetCtxs, maxLeverage 10, marginTableId
52), and it trades around the clock like the S&P desk's xyz:SP500 -- no
weekly close, no gap. This reuses the sp500 desk's eval engine wholesale
(every import below is unmodified sp500_strategy_test.py code: the same
Costs, Tick/Order/Trade shapes, build_ticks, simulate, summarize, verdict,
score/pick, Dataset, edge_rule, oil_as_is, leverage_budget, noise_table,
event_windows, the K_ATRS/FLOORS/OFFSETS grid). Only HYPE's own constants,
two new families and the orchestration are written here.

Rules (families), per the operator's brief:
  edge24   (a) the oil/sp500 edge rule, unmodified, sides="both" only:
           market entry off the 24h range, stop beyond the 24h edge,
           target the far 24h edge, 2R gate, no entry after a break. Every
           entry is the LIVE `next_trade.next_trade` (bracket.edge_rule).
  trend    (c) the same rule, but gated by the operator's own 120h/24h
           trend filter (sides="trend120"/"trend24" -- next_trade.trend_gate
           in miniature, already in edge_rule's `sides_for`): only the
           pullback entry that agrees with the multi-day trend.
  fib      (b) new. After an N-hour leg (the highest-high bar and the
           lowest-low bar in the last N closed 1h candles; whichever came
           LATER sets the leg's direction), enter in the leg's direction
           once the mid sits in the 50-61.8% retracement band, stop beyond
           the 78.6% retracement (the larger of k_atr x ATR14 and
           floor_frac x mid past it, the same buffer shape as edge24), target
           either the leg's own extreme (continuation back to it) or the
           1.272 extension (27.2% of the leg's own height past the extreme).
           The live gates (bracket.check_leverage/check_levels) are the same
           ones place_bracket runs; 2R, no leverage fit (one leverage: 10x).
There is no HYPE thesis journal yet, so the side comes from each rule: edge24
is `both` (whichever edge is closer), trend is the sign of the 120h/24h
return, fib is the leg's own direction.

Datasets (one pinned snapshot, `services.strategies.backtest.snapshot`, the
venue's own history -- ~208 days of 1h bars, ~52 days of 15m bars, the same
window length the sp500 desk's own snapshot got the same way, same 5,000-bar
venue cap on `candleSnapshot`). Each is split 70/30 by time; a variant is
picked on the first 70% and its row on the last 30% (held out) is reported
beside it. The verdict bar and the honesty legs (cost stress, without-the-
two-best-trades, grid neighbours) are byte-identical to the sp500 eval
(`verdict()`, unchanged). Costs are NOT shared with sp500: HYPE trades the
main dex's real fee tier, not the xyz HIP-3 dex's growth-mode rate -- see
MEASURED/STRESS below.

    .venv/bin/python -m services.strategies.backtest.snapshot --coin HYPE --days-back 600 \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261119-hype
    .venv/bin/python -m services.strategies.backtest.snapshot --coin BTC --days-back 600 \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261119-hype
    .venv/bin/python -m services.strategies.evals.hype_strategy_test \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261119-hype

Writes /tmp/hype-strategy/report.json and summary.csv. Read only: no
account, no live config, no pathiel MCP tool, no LLM call. Exit code 0 when
the live rule's full-sample AND held-out-tail net R are both positive (the
"better than LOSING" bar the operator set -- the same bar the sp500 desk,
itself NOISE, cleared), 1 otherwise.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.agents.bracket import check_leverage, check_levels
from pathiel.models.types import Candle
from services.strategies import next_trade as NT
# Reused wholesale from the sp500 eval: the generic backtest engine. None of
# this is HYPE-specific; see the module docstring.
from services.strategies.evals.sp500_strategy_test import (
    FLOORS, K_ATRS, OFFSETS, Costs, Dataset, Order, Rule,
    build_ticks, edge_name, edge_rule, equity_path, event_windows, funding_index,
    leverage_budget, neighbours, noise_table, oil_as_is, pick, simulate, split_ms,
    summarize, verdict,
)
from services.strategies.weekend import weekend_closed

COIN = "HYPE"
VENUE_MAX_LEVERAGE = 10.0        # live /info meta, 2026-11-20: HYPE maxLeverage 10, marginTableId 52

# Costs. HYPE trades on Hyperliquid's MAIN perp dex, not the xyz HIP-3 dex
# oil/sp500 get a "growth mode" 0.9bps rate on -- confirmed live 2026-11-20
# (get_account detail=full on the account this desk would trade on):
# feeSchedule.cross (taker) 0.00045 = 4.5bps, .add (maker) 0.00015 = 1.5bps,
# no VIP volume tier, no active staking or referral discount. A stop and an
# entry are both market (taker); a target is a resting limit (maker, no
# slippage) -- sp500's Costs has one fee for every leg, so charging the
# TAKER rate on every leg (including the cheaper maker fills a target
# exit gets in reality) is the conservative direction: it UNDERSTATES how
# good a target-heavy rule looks, never overstates it. Measured: 4.5bps +
# 1bp slippage (unchanged: HYPE's book easily absorbs the desk's size, see
# HYPE_STRATEGY.md "liquidity"). Stress: 6bps + 3bps, a worse fee tier or
# wider slippage than today's.
MEASURED = Costs(fee=0.00045, slip=0.0001)
STRESS = Costs(fee=0.00060, slip=0.0003)
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
STEP_MS = {"15m": 900_000, "1h": HOUR_MS}
LEVERAGES: Tuple[float, ...] = (10.0, 7.0, 5.0, 3.0)
SIDES_EDGE: Tuple[str, ...] = ("both",)                 # family (a): the plain range rule
SIDES_TREND: Tuple[str, ...] = ("trend120", "trend24")  # family (c): the trend-filtered variant

# ── the fib family (b) ──────────────────────────────────────────────────

FIB_RETRACE_NEAR = 0.5          # the shallow edge of the entry band
FIB_RETRACE_FAR = 0.618         # the deep edge of the entry band
FIB_STOP_LEVEL = 0.786          # the stop sits beyond this retracement
FIB_EXT = 1.272                 # the extension target, as a multiple of the leg's own height
FIB_LOOKBACKS: Tuple[int, ...] = (12, 24, 48, 72)
FIB_K_ATRS: Tuple[float, ...] = (0.0, 0.5, 1.0)
FIB_FLOORS: Tuple[float, ...] = (0.0, 0.0025, 0.005)
FIB_TARGETS: Tuple[str, ...] = ("extreme", "ext127")


@dataclass(frozen=True)
class FibLeg:
    up: bool            # True: the leg ran low -> high (a pullback DOWN sets up a long)
    high: float
    low: float
    height: float


@dataclass(frozen=True)
class FibTick:
    """Mirrors the fields BASE.simulate()/close_trade() read off a Tick
    (t, mid, weekend) plus what fib_rule needs (atr14, the leg for THIS
    lookback_h -- the one grid axis BASE.Tick has no room for, since its
    low8/high8/low24/high24 are fixed-window by construction)."""
    t: int
    mid: float
    atr: float
    leg: Optional[FibLeg]
    weekend: bool


def leg_from(window: Sequence[Candle]) -> Optional[FibLeg]:
    """The leg inside `window` (closed 1h bars, oldest first): the bar with
    the highest high and the bar with the lowest low: whichever comes LATER
    sets the leg's direction (the high after the low: an up-leg, a pullback
    down sets up a long; the low after the high: a down-leg, a pullback up
    sets up a short). None when they are the same bar (no leg) or the
    window is empty. Pure."""
    if len(window) < 2:
        return None
    hi_i = max(range(len(window)), key=lambda i: window[i].h)
    lo_i = min(range(len(window)), key=lambda i: window[i].l)
    if hi_i == lo_i:
        return None
    high, low = window[hi_i].h, window[lo_i].l
    height = high - low
    if height <= 0:
        return None
    return FibLeg(up=hi_i > lo_i, high=high, low=low, height=height)


def build_fib_ticks(bars: Sequence[Candle], step_ms: int, c1h: Sequence[Candle],
                    lookback_h: int) -> List[Optional[FibTick]]:
    """One FibTick per bar of `bars`, or None while fewer than 25 closed 1h
    bars exist (the same floor BASE.build_ticks uses) -- the leg and the
    ATR14 both read only 1h bars whose close is at or before the decision
    time. The ATR calc is BASE.build_ticks's own (14 true ranges over the
    last 15 closed bars), kept byte-identical so the two families' ATR
    numbers never drift apart. Pure."""
    closes_t = [c.t + HOUR_MS for c in c1h]
    out: List[Optional[FibTick]] = []
    for b in bars:
        t = b.t + step_ms
        k = bisect.bisect_right(closes_t, t)
        if k < max(25, lookback_h):
            out.append(None)
            continue
        w15 = c1h[k - 15:k]
        trs = [max(w15[i].h - w15[i].l, abs(w15[i].h - w15[i - 1].c), abs(w15[i].l - w15[i - 1].c))
               for i in range(1, 15)]
        leg = leg_from(c1h[k - lookback_h:k])
        out.append(FibTick(t=t, mid=b.c, atr=sum(trs) / 14.0, leg=leg, weekend=weekend_closed(t)))
    return out


def fib_rule(*, lookback_h: int, k_atr: float, floor_frac: float, target_mode: str, leverage: float,
            min_r: float = 2.0, max_leverage: float = VENUE_MAX_LEVERAGE) -> Rule:
    """The Fibonacci pullback: flat only, needs `tk.leg` (so the ticks must
    come from `build_fib_ticks(..., lookback_h)` -- the SAME lookback_h this
    rule was built with). `target_mode`: "extreme" (the leg's own high/low)
    or "ext127" (27.2% of the leg's height past it). The live gates
    (bracket.check_leverage/check_levels) are the ones place_bracket runs,
    same as orb_rule in the sp500 eval. Pure."""
    def rule(i: int, ticks: Sequence[Optional[FibTick]], bars: Sequence[Candle], st: Dict[str, Any],
             flat: bool = True) -> Optional[Order]:
        if not flat:
            return None
        tk = ticks[i]
        leg = tk.leg
        if leg is None:
            return None
        buf = max(k_atr * tk.atr, floor_frac * tk.mid)
        if leg.up:                                    # low -> high: pullback DOWN sets up a long
            band_hi = leg.high - FIB_RETRACE_NEAR * leg.height    # the 50% level
            band_lo = leg.high - FIB_RETRACE_FAR * leg.height     # the 61.8% level
            if not (band_lo <= tk.mid <= band_hi):
                return None
            stop = (leg.high - FIB_STOP_LEVEL * leg.height) - buf
            tp = leg.high if target_mode == "extreme" else leg.high + (FIB_EXT - 1.0) * leg.height
            side = "long"
        else:                                         # high -> low: pullback UP sets up a short
            band_lo = leg.low + FIB_RETRACE_NEAR * leg.height
            band_hi = leg.low + FIB_RETRACE_FAR * leg.height
            if not (band_lo <= tk.mid <= band_hi):
                return None
            stop = (leg.low + FIB_STOP_LEVEL * leg.height) + buf
            tp = leg.low if target_mode == "extreme" else leg.low - (FIB_EXT - 1.0) * leg.height
            side = "short"
        if abs(tk.mid - stop) <= 0:
            return None
        if not check_leverage(tk.mid, stop, leverage, max_leverage).get("ok"):
            return None
        if not check_levels(tk.mid, stop, tp, min_r).get("ok"):
            return None
        return Order(side=side, stop=stop, tp=tp)
    return rule


def fib_variants(leverage: float = VENUE_MAX_LEVERAGE) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for lb in FIB_LOOKBACKS:
        for k in FIB_K_ATRS:
            for fl in FIB_FLOORS:
                for tm in FIB_TARGETS:
                    name = f"fib_lb{lb}_k{k:g}_f{fl*100:g}_{tm}_{leverage:g}x"
                    out[name] = {"family": "fib", "kind": "fib",
                                "kw": {"lookback_h": lb, "k_atr": k, "floor_frac": fl, "target_mode": tm,
                                       "leverage": leverage}}
    return out


def fib_neighbours(name: str, named: Dict[str, Dict[str, Any]]) -> List[str]:
    """Same one-step-away-on-one-knob rule as BASE.neighbours, for the fib
    grid's own axes (lookback_h, k_atr, floor_frac), same leverage and
    target_mode held fixed."""
    v = named[name]
    out = []
    for n, o in named.items():
        if n == name or o["family"] != "fib":
            continue
        a, b = v["kw"], o["kw"]
        if a["target_mode"] != b["target_mode"] or a["leverage"] != b["leverage"]:
            continue
        steps = [abs(list(grid).index(a[key]) - list(grid).index(b[key]))
                 for key, grid in (("lookback_h", FIB_LOOKBACKS), ("k_atr", FIB_K_ATRS),
                                   ("floor_frac", FIB_FLOORS))]
        if sorted(steps) == [0, 0, 1]:
            out.append(n)
    return out


# ── variants: edge24 (a) and trend (c) share BASE's grid and edge_rule ──

def edge_variants(family: str, sides: Sequence[str], leverage: float = VENUE_MAX_LEVERAGE) -> Dict[str, Dict[str, Any]]:
    """BASE.edge_rule's own grid (K_ATRS x FLOORS x OFFSETS), restricted to
    `sides` -- SIDES_EDGE for family (a) "edge24", SIDES_TREND for family
    (c) "trend"."""
    out: Dict[str, Dict[str, Any]] = {}
    for k in K_ATRS:
        for fl in FLOORS:
            for off in OFFSETS:
                for sd in sides:
                    out[edge_name("edge24", k, fl, off, sd, leverage)] = {
                        "family": family, "kind": "edge",
                        "kw": {"k_atr": k, "floor_frac": fl, "offset_frac": off, "sides": sd,
                               "entry_lookback_h": 24, "leverage": leverage}}
    return out


def make_rule(v: Dict[str, Any]) -> Rule:
    return edge_rule(**v["kw"]) if v["kind"] == "edge" else fib_rule(**v["kw"])


def score(ds: Dataset, v: Dict[str, Any], fund: Tuple[List[int], List[float]], fib_ticks_by_lb: Dict[int, List],
         *, with_stress: bool = True, blackouts: Sequence[Tuple[int, int]] = ()) -> Dict[str, Any]:
    """Like BASE.score, but routes a fib variant to its OWN ticks (built for
    its `lookback_h`) instead of `ds.ticks`."""
    ticks = fib_ticks_by_lb[v["kw"]["lookback_h"]] if v["kind"] == "fib" else ds.ticks
    trades = simulate(ticks, ds.bars, ds.step_ms, make_rule(v), MEASURED, fund, blackouts=blackouts)
    cut = split_ms(ds.start_ms, ds.end_ms)
    out = {"full": summarize(trades, ds.start_ms, ds.end_ms), "train": summarize(trades, ds.start_ms, cut),
           "tail": summarize(trades, cut, ds.end_ms), "trades": trades}
    if with_stress:
        st = simulate(ticks, ds.bars, ds.step_ms, make_rule(v), STRESS, fund, blackouts=blackouts)
        out["stress"] = summarize(st, ds.start_ms, ds.end_ms)
    return out


def live_variant() -> Dict[str, Any]:
    """The rule as the live code will have it for HYPE (next_trade.HYPE_RULE),
    so this eval scores exactly what the desk would trade. Defined once the
    research below picks a family; see next_trade.py."""
    p = NT.HYPE_RULE
    return {"family": "live", "kind": "edge",
            "kw": {"k_atr": p["k_atr"], "floor_frac": p["floor_frac"], "offset_frac": p["target_offset_frac"],
                   "sides": p["backtest_sides"], "entry_lookback_h": 24, "leverage": NT.HYPE_LEVERAGE,
                   "fit": tuple(NT.leverage_fit_for(COIN))}}


def run(snapshot_dir: str, out_dir: str, risk_pct: float = 6.0) -> Dict[str, Any]:
    from services.strategies.backtest import snapshot as SNAP
    snap = SNAP.load_snapshot(snapshot_dir, COIN)
    if not snap.c1h or not snap.c15:
        raise SystemExit(f"no {COIN} candles in {snapshot_dir}: run services.strategies.backtest.snapshot first")
    fund = funding_index(snap.funding)
    d1h = Dataset("1h", snap.c1h, HOUR_MS)
    d15 = Dataset("15m", snap.c15, STEP_MS["15m"])
    d1h.ticks = build_ticks(d1h.bars, d1h.step_ms, snap.c1h)
    d15.ticks = build_ticks(d15.bars, d15.step_ms, snap.c1h)
    sets = {"1h": d1h, "15m": d15}
    fib_ticks = {
        "1h": {lb: build_fib_ticks(d1h.bars, d1h.step_ms, snap.c1h, lb) for lb in FIB_LOOKBACKS},
        "15m": {lb: build_fib_ticks(d15.bars, d15.step_ms, snap.c1h, lb) for lb in FIB_LOOKBACKS},
    }

    report: Dict[str, Any] = {
        "coin": COIN, "snapshot_dir": snapshot_dir, "venue_max_leverage": VENUE_MAX_LEVERAGE,
        "costs": {"measured": MEASURED.__dict__, "stress": STRESS.__dict__},
        "windows": {k: {"start": _iso(d.start_ms), "end": _iso(d.end_ms),
                        "tail_from": _iso(split_ms(d.start_ms, d.end_ms)),
                        "days": round((d.end_ms - d.start_ms) / DAY_MS, 1),
                        "move_pct": round((d.bars[-1].c / next(b.c for b, t in zip(d.bars, d.ticks) if t) - 1) * 100, 2)}
                    for k, d in sets.items()},
        "leverage_budget": [leverage_budget(lv, VENUE_MAX_LEVERAGE) for lv in LEVERAGES],
        "noise": noise_table(snap.c1h, snap.c15),
    }

    named: Dict[str, Dict[str, Any]] = {}
    named.update(edge_variants("edge24", SIDES_EDGE, VENUE_MAX_LEVERAGE))
    named.update(edge_variants("trend", SIDES_TREND, VENUE_MAX_LEVERAGE))
    named.update(fib_variants(VENUE_MAX_LEVERAGE))

    rows: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in sets}
    for name, v in named.items():
        for k, d in sets.items():
            rows[k][name] = score(d, v, fund, fib_ticks[k], with_stress=False)
    csv_rows: List[Dict[str, Any]] = []
    for k in sets:
        for name, r in rows[k].items():
            for part in ("full", "train", "tail"):
                csv_rows.append({"dataset": k, "variant": name, "family": named[name]["family"], "part": part,
                                 **{f: r[part][f] for f in ("trades", "per_week", "win_rate", "mean_r", "net_r",
                                                            "max_dd_r", "median_stop_pct")},
                                 "long_net_r": r[part]["long"]["net_r"], "short_net_r": r[part]["short"]["net_r"]})

    fams: Dict[str, Any] = {}
    for fam in ("edge24", "trend", "fib"):
        base = "1h"
        name = pick(rows[base], named, fam)
        grid = [n for n, v in named.items() if v["family"] == fam and n in rows[base]]
        above = sum(1 for n in grid if rows[base][n]["full"]["net_r"] > 0)
        entry: Dict[str, Any] = {"picked": name, "picked_on": f"{base} train", "variants": len(grid),
                                 "variants_net_positive_full": above}
        if name:
            v = named[name]
            full = {k: score(sets[k], v, fund, fib_ticks[k]) for k in sets}
            nb_fn = fib_neighbours if fam == "fib" else neighbours
            nb = [rows[base][n]["full"]["net_r"] for n in nb_fn(name, named) if n in rows[base]]
            jd = verdict(full["1h"], full.get("15m"), nb)
            entry.update({"rows": {k: {p: r[p] for p in ("full", "train", "tail", "stress")} for k, r in full.items()},
                          "neighbours_net_r": nb, **jd})
        else:
            entry.update({"verdict": "NOISE", "failed": ["no variant with enough train trades"]})
        fams[fam] = entry
    report["families"] = fams

    report["oil_as_is"] = {}
    for label, lev in (("10x", 10.0), ("5x", 5.0)):
        v = oil_as_is(lev)
        report["oil_as_is"][label] = {k: {p: r[p] for p in ("full", "tail", "stress")}
                                      for k, r in ((k, score(sets[k], v, fund, fib_ticks[k])) for k in sets)}

    if hasattr(NT, "HYPE_RULE"):
        live = live_variant()
        lv_rows = {k: score(sets[k], live, fund, fib_ticks[k]) for k in sets}
        report["live"] = {
            "params": {**live["kw"], "fit": list(live["kw"]["fit"])},
            "rows": {k: {p: r[p] for p in ("full", "train", "tail", "stress")} for k, r in lv_rows.items()},
            "equity": {k: equity_path(r["trades"], max(live["kw"]["fit"] or (live["kw"]["leverage"],)), risk_pct)
                       for k, r in lv_rows.items()},
        }
        by_lev: Dict[str, Any] = {}
        for lev in LEVERAGES:
            v = {**live, "kw": {**live["kw"], "leverage": lev, "fit": ()}}
            by_lev[f"{lev:g}x"] = {}
            for k, d in sets.items():
                r = score(d, v, fund, fib_ticks[k])
                by_lev[f"{lev:g}x"][k] = {"full": r["full"], "tail": r["tail"], "stress": r["stress"],
                                          "equity": equity_path(r["trades"], lev, risk_pct)}
        report["live"]["by_leverage"] = by_lev
        # FOMC/CPI blackout (every desk since 2026-11-05): does holding new
        # entries 2h either side cost or save the rule. HYPE has no EIA or
        # weekend-flat window of its own (it is not in weekend.OIL_COINS).
        report["live"]["event_blackout"] = {
            k: {p: r[p] for p in ("full", "tail")} for k, r in
            ((k, score(d, live, fund, fib_ticks[k], blackouts=event_windows(d.start_ms, d.end_ms)))
             for k, d in sets.items())}
        live_name = edge_name("edge24", live["kw"]["k_atr"], live["kw"]["floor_frac"], live["kw"]["offset_frac"],
                              live["kw"]["sides"], VENUE_MAX_LEVERAGE)
        fam, sides = ("edge24", SIDES_EDGE) if live["kw"]["sides"] == "both" else ("trend", SIDES_TREND)
        live_grid = edge_variants(fam, sides, VENUE_MAX_LEVERAGE)
        nb = [rows["1h"][n]["full"]["net_r"] for n in (neighbours(live_name, live_grid) if live_name in live_grid else [])]
        report["live"].update(verdict(lv_rows["1h"], lv_rows["15m"], nb))
        report["live"]["neighbours_net_r"] = nb
    report["risk_pct"] = risk_pct

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    with open(os.path.join(out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(csv_rows[0].keys()))
        w.writeheader()
        w.writerows(csv_rows)
    return report


def _iso(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _line(label: str, r: Dict[str, Any]) -> str:
    return (f"  {label:34s} n={r['trades']:4d} {r['per_week']:5.2f}/wk win={r['win_rate']} meanR={r['mean_r']} "
            f"netR={r['net_r']:+.2f} ddR={r['max_dd_r']:.2f} L={r['long']['net_r']:+.2f} S={r['short']['net_r']:+.2f} "
            f"stop%={r['median_stop_pct']}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--snapshot-dir", required=True)
    ap.add_argument("--out-dir", default="/tmp/hype-strategy")
    ap.add_argument("--risk-pct", type=float, default=6.0, help="risk per trade, percent of the desk slice")
    a = ap.parse_args(argv)
    rep = run(os.path.expanduser(a.snapshot_dir), a.out_dir, a.risk_pct)
    print(json.dumps({"windows": rep["windows"], "leverage_budget": rep["leverage_budget"], "noise": rep["noise"]},
                     indent=1))
    for fam, e in rep["families"].items():
        print(f"{fam}: picked {e['picked']} ({e.get('variants_net_positive_full')}/{e.get('variants')} variants "
              f"net positive) -> {e['verdict']} failed={e.get('failed')}")
        for k, r in (e.get("rows") or {}).items():
            for part in ("full", "tail", "stress"):
                print(_line(f"{k} {part}", r[part]))
    for label, s in rep["oil_as_is"].items():
        for k, r in s.items():
            print(_line(f"oil_as_is {label} {k} full", r["full"]))
    if "live" in rep:
        lv = rep["live"]
        print(f"live rule {lv['params']} -> {lv['verdict']} failed={lv['failed']}")
        for k, r in lv["rows"].items():
            for part in ("full", "tail", "stress"):
                print(_line(f"live {k} {part}", r[part]))
            print(f"  live {k} equity at {a.risk_pct:g}% risk: {lv['equity'][k]}")
        for lev, s in lv["by_leverage"].items():
            for k, r in s.items():
                print(_line(f"live fixed {lev} {k} full", r["full"]) + f" eq={r['equity']}")
        for k, r in lv["event_blackout"].items():
            print(_line(f"event blackout {k} full", r["full"]))
        print(f"report: {os.path.join(a.out_dir, 'report.json')}")
        return 0 if lv["rows"]["1h"]["full"]["net_r"] > 0 and lv["rows"]["1h"]["tail"]["net_r"] > 0 else 1
    print(f"report: {os.path.join(a.out_dir, 'report.json')} (no NT.HYPE_RULE yet: families only, no live row)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
