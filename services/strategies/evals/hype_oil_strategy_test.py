"""HYPE/oil strategy test, 2026-11-20 (docs/strategy/HYPE_OIL_CORRELATION_2026-11-20.md).

Operator: "build correlations and strateges between hype and oil." Oil
(xyz:BRENTOIL, the xyz HIP-3 dex, 32% of equity, 20x cap) and HYPE (HYPE on
the main dex, 60% of equity, 10x cap, 120h trend gate) are two live desks
that can both hold a position at once. `hype_oil_correlation.py` found a
small, consistently negative 1h return correlation (-0.12, 95% CI
[-0.15, -0.08] over 208 days), no real lead/lag (the one lag that nominally
crosses t>2 does not clear a multiple-testing check against the 48 lags
scanned), and a correlation stable in SIGN across every 30-day rolling
window (100% negative). This module tests whether any of that turns into a
tradeable rule, reusing the desks' REAL live rules (fees, the 2R floor,
leverage caps, HYPE's trend gate) via `sp500_strategy_test`'s engine
(`Costs`, `Tick`/`Order`/`Trade`, `build_ticks`, `simulate`, `summarize`,
`verdict`, `pick`, `split_ms`, `edge_rule`, `oil_as_is` -- unmodified) and
`hype_strategy_test`'s own live-rule constants, exactly as `hype_strategy_test
.py` itself reuses `sp500_strategy_test.py` wholesale.

Four candidate families, per the task brief:

  a_portfolio_guard   Does correlation make a joint drawdown worse when both
                      desks are in the SAME direction at once, and should the
                      second entry to open be sized down or blocked? A
                      portfolio-level sizing question, not a trade generator
                      of its own -- reported by combined-equity drawdown and
                      final multiple (full and held-out tail), not by a
                      per-trade R distribution.
  b_cross_signal      Does the OTHER coin's 120h/72h/168h trend, or a shock/
                      drawdown-day blackout, improve a desk's own live-rule
                      entries? Four sub-families (hype filtered by oil's
                      trend, oil filtered by HYPE's trend, hype blacked out
                      on an oil shock day, oil blacked out on a HYPE
                      drawdown day), each its own small grid, scored and
                      labelled the same way edge24/trend/fib are in the HYPE
                      eval.
  c_lead_lag_entry    SKIPPED. The task's own gate ("only if (1) finds a
                      lead with t > 2") is not met once multiple testing is
                      accounted for (`hype_oil_correlation.
                      lead_lag_significance_check`): 3 of 48 non-zero lags
                      cross |t|>2 against a chance expectation of about 2.2 --
                      indistinguishable from scanning 48 lags under the null.
                      `LEAD_LAG_ENTRY_GATE_MET = False` is checked by a gate
                      test so this file fails loudly if a future data refresh
                      ever flips that finding.
  d_pair_trade        A relative-value entry on the frozen (train-only, no
                      look-ahead) beta of HYPE's cumulative log return on
                      oil's: spread = hype_cum - beta x oil_cum, a trailing
                      z-score (never includes the bar being scored), and a
                      mean-reversion entry on BOTH legs at once when |z|
                      crosses a threshold. Built because the correlation's
                      SIGN was stable across every 30-day window (the task's
                      "only if correlation is stable enough" gate); scored
                      pooling both legs' trades into one family, the same way
                      long and short trades pool within every other family
                      here.

Every family: 70/30 train/tail split, EDGE/LEANING/NOISE exactly as
`sp500_strategy_test.verdict()` defines it (unchanged import -- this file
invents no new bar). Ranking for the report is OUT-OF-SAMPLE (the held-out
tail) mean R per trade, net of fees -- `summarize()`'s own `mean_r` field --
not total net R, so a family with one huge trade cannot outrank one with a
real, repeatable edge.

    .venv/bin/python -m services.strategies.evals.hype_oil_strategy_test \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261120-hype-oil

Writes <out-dir>/report.json (default /tmp/hype-oil-strategy) and
summary.csv. Read only: no account, no live config, no pathiel MCP tool, no
LLM call.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.agents.bracket import check_leverage, check_levels
from pathiel.models.types import Candle
from services.strategies import next_trade as NT
from services.strategies.evals import hype_oil_correlation as CORR
from services.strategies.evals.hype_strategy_test import MEASURED as HYPE_MEASURED
from services.strategies.evals.hype_strategy_test import STRESS as HYPE_STRESS
from services.strategies.evals.hype_strategy_test import VENUE_MAX_LEVERAGE as HYPE_VENUE_MAX_LEVERAGE
from services.strategies.evals.hype_strategy_test import live_variant as hype_live_variant
from services.strategies.evals.sp500_strategy_test import (
    MEASURED as OIL_MEASURED, STRESS as OIL_STRESS, Costs, Dataset, Order, Rule, Tick,
    build_ticks, edge_rule, funding_index, oil_as_is, pick, simulate,
    split_ms, summarize, verdict,
)

OIL = CORR.OIL
HYPE = CORR.HYPE
HOUR_MS = CORR.HOUR_MS
DAY_MS = CORR.DAY_MS

# Live desk numbers, 2026-11-20 (3541404 "docs(desks): live desks are oil
# 32% + hype 60%"; MARKET_SCOUT_2026-10-31.md: "leverage_ladder up to its
# 25x venue max (vs. Brent's 20x)" -- xyz:BRENTOIL's own venue max is 20x,
# not a choice).
OIL_VENUE_MAX_LEVERAGE = 20.0
OIL_LEVERAGE = 20.0
OIL_LEVERAGE_FIT: Tuple[float, ...] = NT.LEVERAGE_FIT          # (20.0, 15.0)
OIL_SHARE = 0.32
HYPE_SHARE = 0.60
RISK_PCT_DEFAULT = 6.0           # oil's own number; both strategy docs recommend lower for HYPE alone

LEAD_LAG_ENTRY_GATE_MET = False  # see the module docstring's c_lead_lag_entry section; a gate test pins this


def oil_live_variant() -> Dict[str, Any]:
    """Oil's real live rule (next_trade's ATR_BUFFER_MULT/STOP_BUFFER_FLOOR_FRAC/
    0.5% offset, 20/15 leverage fit) in `sp500_strategy_test`'s variant shape.
    `sides="both"` is the same no-thesis stand-in every oil/sp500/hype
    backtest in this repo uses (whichever 24h edge is closer) -- there is no
    journaled thesis to replay against this specific 208-day window."""
    return oil_as_is(OIL_LEVERAGE, OIL_LEVERAGE_FIT)


# ── a. portfolio risk: does correlation make a joint drawdown worse? ────────

def classify_overlap(a_side: str, b_side: str) -> str:
    return "same" if a_side == b_side else "opposite"


def apply_overlap_guard(oil_trades: Sequence[Any], hype_trades: Sequence[Any], mode: str,
                        mult: float) -> Tuple[List[Any], List[Any]]:
    """A copy of (`oil_trades`, `hype_trades`) where every trade that OPENS
    while the OTHER desk already holds an open position gets its R
    multiplied by `mult` (0.0 = block, 0.5 = half size) whenever the two
    sides' relation (`classify_overlap`) equals `mode` ('same' or
    'opposite'); `mode='off'` changes nothing (the baseline). Only the
    trade that opens SECOND is ever touched -- by the time it fires, the
    first position is already open, known fact; a blocked/halved trade's R
    is scored as-is at the reduced size (matching
    PULLBACK_AND_CORRELATION_TEST_2026-11-02.md's own convention: "a
    blocked trade counts 0 here; the desk might have entered later").
    Pure: never mutates the inputs."""
    oil_out = [copy.copy(t) for t in oil_trades]
    hype_out = [copy.copy(t) for t in hype_trades]
    if mode == "off":
        return oil_out, hype_out
    for a in oil_out:
        for b in hype_trades:
            if b.entry_t <= a.entry_t < b.exit_t and classify_overlap(a.side, b.side) == mode:
                a.r *= mult
                break
    for b in hype_out:
        for a in oil_trades:
            if a.entry_t <= b.entry_t < a.exit_t and classify_overlap(a.side, b.side) == mode:
                b.r *= mult
                break
    return oil_out, hype_out


def overlap_counts(oil_trades: Sequence[Any], hype_trades: Sequence[Any]) -> Dict[str, int]:
    """How many of each desk's entries opened while the other desk already
    held a position, split same/opposite side. Pure."""
    out = {"oil_opens_into_hype_same": 0, "oil_opens_into_hype_opposite": 0,
          "hype_opens_into_oil_same": 0, "hype_opens_into_oil_opposite": 0}
    for a in oil_trades:
        for b in hype_trades:
            if b.entry_t <= a.entry_t < b.exit_t:
                out[f"oil_opens_into_hype_{classify_overlap(a.side, b.side)}"] += 1
                break
    for b in hype_trades:
        for a in oil_trades:
            if a.entry_t <= b.entry_t < a.exit_t:
                out[f"hype_opens_into_oil_{classify_overlap(a.side, b.side)}"] += 1
                break
    return out


def portfolio_equity(oil_trades: Sequence[Any], hype_trades: Sequence[Any], oil_share: float, hype_share: float,
                     oil_leverage: float, hype_leverage: float, risk_pct: float,
                     start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> Dict[str, Any]:
    """The one account's combined equity curve: each desk's trade changes
    TOTAL equity by r x min(risk_pct%, stop_frac x leverage) x that desk's
    OWN equity_share (`sp500_strategy_test.equity_path`'s per-trade sizing
    formula, extended to a shared account instead of an isolated slice).
    Only trades whose exit falls in [start_ms, end_ms) count (None = no
    bound). Pure."""
    events: List[Tuple[int, str, float, float]] = []
    for tr in oil_trades:
        if (start_ms is None or tr.exit_t >= start_ms) and (end_ms is None or tr.exit_t < end_ms):
            events.append((tr.exit_t, "oil", tr.r, tr.stop_frac))
    for tr in hype_trades:
        if (start_ms is None or tr.exit_t >= start_ms) and (end_ms is None or tr.exit_t < end_ms):
            events.append((tr.exit_t, "hype", tr.r, tr.stop_frac))
    events.sort(key=lambda e: e[0])
    eq = peak = 1.0
    dd = 0.0
    for _, desk, r, stop_frac in events:
        share, lev = (oil_share, oil_leverage) if desk == "oil" else (hype_share, hype_leverage)
        risk = min(risk_pct / 100.0, stop_frac * lev) * share
        eq *= max(0.0, 1.0 + r * risk)
        peak = max(peak, eq)
        dd = max(dd, 1.0 - eq / peak if peak else 0.0)
    return {"final_multiple": round(eq, 4), "max_dd_pct": round(dd * 100, 2), "n_events": len(events)}


def run_portfolio_guard(oil_trades: Sequence[Any], hype_trades: Sequence[Any], start_ms: int, cut_ms: int,
                        end_ms: int, risk_pct: float) -> Dict[str, Any]:
    """Every guard variant's full/train/tail portfolio equity, plus the raw
    overlap counts. Pure."""
    counts = overlap_counts(oil_trades, hype_trades)
    variants = {"baseline_off": ("off", 1.0), "same_block": ("same", 0.0), "same_half": ("same", 0.5),
               "opposite_block": ("opposite", 0.0), "opposite_half": ("opposite", 0.5)}
    rows: Dict[str, Any] = {}
    for name, (mode, mult) in variants.items():
        o, h = apply_overlap_guard(oil_trades, hype_trades, mode, mult)
        rows[name] = {
            "full": portfolio_equity(o, h, OIL_SHARE, HYPE_SHARE, OIL_LEVERAGE, HYPE_VENUE_MAX_LEVERAGE,
                                     risk_pct, start_ms, end_ms),
            "train": portfolio_equity(o, h, OIL_SHARE, HYPE_SHARE, OIL_LEVERAGE, HYPE_VENUE_MAX_LEVERAGE,
                                      risk_pct, start_ms, cut_ms),
            "tail": portfolio_equity(o, h, OIL_SHARE, HYPE_SHARE, OIL_LEVERAGE, HYPE_VENUE_MAX_LEVERAGE,
                                     risk_pct, cut_ms, end_ms),
        }
    return {"overlap_counts": counts, "variants": rows}


# ── b. cross-signal filter ──────────────────────────────────────────────────

def trend_signs(c1h: Sequence[Candle], decision_times: Sequence[int], span_h: int) -> Dict[int, Optional[int]]:
    """{decision time: sign of the span_h-hour return as of that CLOSED-bar
    history} using the exact formula `next_trade.trend_gate` applies live
    (so this filter and the live veto can never silently disagree): -1, 0,
    or +1, else None with too little history or a bad close. Pure."""
    import bisect
    closes_t = [c.t + HOUR_MS for c in c1h]
    closes = [c.c for c in c1h]
    out: Dict[int, Optional[int]] = {}
    for t in decision_times:
        k = bisect.bisect_right(closes_t, t)
        if k < span_h + 1:
            out[t] = None
            continue
        base, last = closes[k - 1 - span_h], closes[k - 1]
        if base <= 0 or last <= 0:
            out[t] = None
            continue
        ret = last / base - 1.0
        out[t] = 1 if ret > 0 else (-1 if ret < 0 else 0)
    return out


def cross_trend_filter_rule(base_rule: Rule, signs_by_t: Dict[int, Optional[int]], mode: str) -> Rule:
    """Wraps `base_rule`: an order only passes when the OTHER coin's trend
    sign (`signs_by_t`, keyed by this coin's own decision time) agrees
    ('agree') or disagrees ('oppose') with the order's own side; missing
    sign data never blocks (same "missing data blocks nothing" convention
    `next_trade.trend_gate` itself follows). Pure."""
    def rule(i: int, ticks: Sequence[Any], bars: Sequence[Candle], st: Dict[str, Any], flat: bool = True) -> Optional[Order]:
        order = base_rule(i, ticks, bars, st, flat)
        if order is None:
            return None
        sign = signs_by_t.get(ticks[i].t)
        if sign is None or sign == 0:
            return order
        want = 1 if order.side == "long" else -1
        if mode == "agree" and sign != want:
            return None
        if mode == "oppose" and sign != -want:
            return None
        return order
    return rule


def day_windows(c1d: Sequence[Candle], threshold_pct: float, *, at_or_below: bool = False) -> List[Tuple[int, int]]:
    """[start_of_UTC_day_ms, end) for every daily candle whose close-to-close
    return breaches `threshold_pct`: `abs(ret) > threshold_pct` normally
    (a "shock" day, either direction), or `ret <= threshold_pct` when
    `at_or_below` (a "drawdown" day, one-sided). Pure."""
    daily = CORR.daily_returns(c1d)
    out: List[Tuple[int, int]] = []
    for day_ord, ret in daily.items():
        hit = (ret * 100.0 <= threshold_pct) if at_or_below else (abs(ret) * 100.0 > threshold_pct)
        if hit:
            d = date.fromordinal(day_ord)
            start = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
            out.append((start, start + DAY_MS))
    return sorted(out)


TREND_FILTER_SPANS: Tuple[int, ...] = (72, 120, 168)
TREND_FILTER_MODES: Tuple[str, ...] = ("agree", "oppose")
SHOCK_THRESHOLDS: Tuple[float, ...] = (2.0, 3.0, 4.0)


def trend_filter_variants(family: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for mode in TREND_FILTER_MODES:
        for span in TREND_FILTER_SPANS:
            out[f"{family}_{mode}_{span}h"] = {"family": family, "kind": "trend_filter",
                                               "kw": {"mode": mode, "span_h": span}}
    return out


def shock_filter_variants(family: str) -> Dict[str, Dict[str, Any]]:
    return {f"{family}_{t:g}pct": {"family": family, "kind": "shock_filter", "kw": {"threshold_pct": t}}
           for t in SHOCK_THRESHOLDS}


def filter_neighbours(name: str, named: Dict[str, Dict[str, Any]]) -> List[str]:
    """One step away on the grid's single numeric axis (span_h for a trend
    filter, same mode; threshold_pct for a shock filter): the same
    one-knob-at-a-time rule every other family in this repo's evals uses.
    Pure."""
    v = named[name]
    out = []
    if v["kind"] == "trend_filter":
        grid = list(TREND_FILTER_SPANS)
        for n, o in named.items():
            if n == name or o["family"] != v["family"] or o["kw"]["mode"] != v["kw"]["mode"]:
                continue
            if abs(grid.index(o["kw"]["span_h"]) - grid.index(v["kw"]["span_h"])) == 1:
                out.append(n)
    else:
        grid = list(SHOCK_THRESHOLDS)
        for n, o in named.items():
            if n == name or o["family"] != v["family"]:
                continue
            if abs(grid.index(o["kw"]["threshold_pct"]) - grid.index(v["kw"]["threshold_pct"])) == 1:
                out.append(n)
    return out


# ── d. pair / relative-value trade ──────────────────────────────────────────

def cumulative_log_returns(bars: Sequence[Candle]) -> List[Tuple[int, float]]:
    """(close_time_ms, cumulative log return from the series' first bar),
    one entry per consecutive pair of `bars`, oldest first. Pure."""
    s = sorted(bars, key=lambda c: c.t)
    out: List[Tuple[int, float]] = []
    total = 0.0
    for a, b in zip(s, s[1:]):
        if a.c > 0 and b.c > 0:
            total += math.log(b.c / a.c)
        out.append((b.t + HOUR_MS, total))
    return out


def rolling_zscore(ts: Sequence[int], xs: Sequence[float], lookback_bars: int) -> Dict[int, Optional[float]]:
    """{t: z-score of xs[i] against the TRAILING `lookback_bars` values
    xs[i-lookback_bars:i]} -- never includes xs[i] itself, so no bar scores
    itself into its own baseline (no look-ahead). None for the first
    `lookback_bars` points and wherever the trailing window has zero
    variance. Pure."""
    out: Dict[int, Optional[float]] = {}
    for i, t in enumerate(ts):
        if i < lookback_bars:
            out[t] = None
            continue
        window = xs[i - lookback_bars:i]
        n = len(window)
        m = sum(window) / n
        var = sum((x - m) ** 2 for x in window) / n
        sd = math.sqrt(var)
        out[t] = (xs[i] - m) / sd if sd > 0 else None
    return out


@dataclass(frozen=True)
class PairTick:
    t: int
    mid: float
    atr: float
    z: Optional[float]
    weekend: bool


def build_pair_ticks(base_ticks: Sequence[Optional[Tick]], z_by_t: Dict[int, Optional[float]]) -> List[Optional[PairTick]]:
    """One `PairTick` per entry of `base_ticks` (`sp500_strategy_test.
    build_ticks`'s own output on this coin's bars), carrying its mid/atr/
    weekend plus the shared cross-asset z-score at that exact decision
    time. None wherever `base_ticks` is None. Pure."""
    out: List[Optional[PairTick]] = []
    for tk in base_ticks:
        if tk is None:
            out.append(None)
            continue
        out.append(PairTick(t=tk.t, mid=tk.mid, atr=tk.atr, z=z_by_t.get(tk.t), weekend=tk.weekend))
    return out


def pair_rule(*, high_z_side: str, entry_z: float, k_atr: float, target_r: float, leverage: float,
             max_leverage: float, min_r: float = 2.0) -> Rule:
    """Mean-reversion on the shared spread's z-score: a flat tick with
    |z| >= entry_z opens a position betting the spread reverts.
    `high_z_side`: the side THIS coin takes when z is HIGH (above
    +entry_z) -- 'short' for HYPE's own leg (spread too high usually means
    HYPE overextended relative to oil's implied level), 'long' for oil's
    own leg (the mirror leg of the same signal); a low z (below -entry_z)
    takes the opposite side on this coin. Stop: k_atr x ATR14 (no
    structural edge exists for a stat-arb entry); target: target_r x that
    risk. The live gates (check_leverage/check_levels), 2R default, no
    leverage fit -- one fixed leverage per coin, the same simplification
    `hype_strategy_test.fib_rule` makes. Flat only. Pure."""
    def rule(i: int, ticks: Sequence[Optional[PairTick]], bars: Sequence[Candle], st: Dict[str, Any],
             flat: bool = True) -> Optional[Order]:
        if not flat:
            return None
        tk = ticks[i]
        if tk.z is None:
            return None
        if tk.z >= entry_z:
            side = high_z_side
        elif tk.z <= -entry_z:
            side = "short" if high_z_side == "long" else "long"
        else:
            return None
        buf = k_atr * tk.atr
        if buf <= 0:
            return None
        stop = tk.mid - buf if side == "long" else tk.mid + buf
        tp = tk.mid + target_r * buf if side == "long" else tk.mid - target_r * buf
        if not check_leverage(tk.mid, stop, leverage, max_leverage).get("ok"):
            return None
        if not check_levels(tk.mid, stop, tp, min_r).get("ok"):
            return None
        return Order(side=side, stop=stop, tp=tp)
    return rule


PAIR_ENTRY_Z: Tuple[float, ...] = (1.5, 2.0, 2.5)
PAIR_LOOKBACK_DAYS: Tuple[int, ...] = (14, 30)


def pair_variants() -> Dict[str, Dict[str, Any]]:
    return {f"pair_z{z:g}_lb{lb}d": {"family": "d_pair_trade", "kw": {"entry_z": z, "lookback_days": lb}}
           for z in PAIR_ENTRY_Z for lb in PAIR_LOOKBACK_DAYS}


def pair_neighbours(name: str, named: Dict[str, Dict[str, Any]]) -> List[str]:
    v = named[name]
    out = []
    for n, o in named.items():
        if n == name:
            continue
        same_z = o["kw"]["entry_z"] == v["kw"]["entry_z"]
        same_lb = o["kw"]["lookback_days"] == v["kw"]["lookback_days"]
        if same_z != same_lb:    # exactly one axis differs: one step
            out.append(n)
    return out


# ── glue: score a {full, train, tail, stress} row the way every family here does ──

def score_rows(trades: Sequence[Any], start_ms: int, cut_ms: int, end_ms: int) -> Dict[str, Any]:
    return {"full": summarize(trades, start_ms, end_ms), "train": summarize(trades, start_ms, cut_ms),
           "tail": summarize(trades, cut_ms, end_ms)}


# ── the run ──────────────────────────────────────────────────────────────────

def run(snapshot_dir: str, out_dir: str, risk_pct: float = RISK_PCT_DEFAULT) -> Dict[str, Any]:
    from services.strategies.backtest import snapshot as SNAP
    oil_snap = SNAP.load_snapshot(snapshot_dir, OIL)
    hype_snap = SNAP.load_snapshot(snapshot_dir, HYPE)
    if not oil_snap.c1h or not hype_snap.c1h:
        raise SystemExit(f"no 1h candles for {OIL} and/or {HYPE} in {snapshot_dir}: "
                         f"run services.strategies.backtest.snapshot for both coins first")

    oil_fund = funding_index(oil_snap.funding)
    hype_fund = funding_index(hype_snap.funding)
    oil_ds = Dataset("oil_1h", oil_snap.c1h, HOUR_MS)
    hype_ds = Dataset("hype_1h", hype_snap.c1h, HOUR_MS)
    oil_ds.ticks = build_ticks(oil_ds.bars, HOUR_MS, oil_snap.c1h)
    hype_ds.ticks = build_ticks(hype_ds.bars, HOUR_MS, hype_snap.c1h)

    start_ms = max(oil_ds.start_ms, hype_ds.start_ms)
    end_ms = min(oil_ds.end_ms, hype_ds.end_ms)
    cut_ms = split_ms(start_ms, end_ms)

    oil_live = oil_live_variant()
    hype_live = hype_live_variant()
    oil_rule = edge_rule(**oil_live["kw"])
    hype_rule = edge_rule(**hype_live["kw"])
    oil_trades = simulate(oil_ds.ticks, oil_ds.bars, HOUR_MS, oil_rule, OIL_MEASURED, oil_fund)
    hype_trades = simulate(hype_ds.ticks, hype_ds.bars, HOUR_MS, hype_rule, HYPE_MEASURED, hype_fund)

    report: Dict[str, Any] = {
        "coins": {"oil": OIL, "hype": HYPE}, "snapshot_dir": snapshot_dir,
        "window": {"start": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                  "end": datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                  "tail_from": datetime.fromtimestamp(cut_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")},
        "live_baseline": {
            "oil": {**score_rows(oil_trades, start_ms, cut_ms, end_ms), "params": oil_live["kw"]},
            "hype": {**score_rows(hype_trades, start_ms, cut_ms, end_ms), "params": hype_live["kw"]},
        },
        "risk_pct": risk_pct,
    }

    # ── a. portfolio guard ──
    report["a_portfolio_guard"] = run_portfolio_guard(oil_trades, hype_trades, start_ms, cut_ms, end_ms, risk_pct)

    # ── b. cross-signal filter: four sub-families ──
    oil_signs_by_span = {s: trend_signs(oil_snap.c1h, [tk.t for tk in hype_ds.ticks if tk], s) for s in TREND_FILTER_SPANS}
    hype_signs_by_span = {s: trend_signs(hype_snap.c1h, [tk.t for tk in oil_ds.ticks if tk], s) for s in TREND_FILTER_SPANS}
    oil_shock_windows_by_pct = {t: day_windows(oil_snap.c1d, t) for t in SHOCK_THRESHOLDS}
    hype_drawdown_windows_by_pct = {t: day_windows(hype_snap.c1d, -t, at_or_below=True) for t in SHOCK_THRESHOLDS}

    families: Dict[str, Any] = {}

    oil_baseline_row = report["live_baseline"]["oil"]
    hype_baseline_row = report["live_baseline"]["hype"]

    def run_trend_family(fam_name: str, ds: Dataset, fund: Any, measured: Costs, stress: Costs, base_rule: Rule,
                         signs_by_span: Dict[int, Dict[int, Optional[int]]], baseline_row: Dict[str, Any]) -> None:
        named = trend_filter_variants(fam_name)
        rows: Dict[str, Any] = {}
        for name, v in named.items():
            r = cross_trend_filter_rule(base_rule, signs_by_span[v["kw"]["span_h"]], v["kw"]["mode"])
            trades = simulate(ds.ticks, ds.bars, HOUR_MS, r, measured, fund)
            rows[name] = score_rows(trades, start_ms, cut_ms, end_ms)
        picked = pick(rows, named, fam_name)

        def stress_fn(name: str) -> Dict[str, Any]:
            v = named[name]
            r = cross_trend_filter_rule(base_rule, signs_by_span[v["kw"]["span_h"]], v["kw"]["mode"])
            st_trades = simulate(ds.ticks, ds.bars, HOUR_MS, r, stress, fund)
            return summarize(st_trades, start_ms, end_ms)

        _finish_family(families, fam_name, named, rows, picked, filter_neighbours, stress_fn, baseline_row)

    def run_shock_family(fam_name: str, ds: Dataset, fund: Any, measured: Costs, stress: Costs, base_rule: Rule,
                         windows_by_pct: Dict[float, List[Tuple[int, int]]], baseline_row: Dict[str, Any]) -> None:
        named = shock_filter_variants(fam_name)
        rows: Dict[str, Any] = {}
        for name, v in named.items():
            trades = simulate(ds.ticks, ds.bars, HOUR_MS, base_rule, measured, fund,
                             blackouts=windows_by_pct[v["kw"]["threshold_pct"]])
            rows[name] = score_rows(trades, start_ms, cut_ms, end_ms)
        picked = pick(rows, named, fam_name)

        def stress_fn(name: str) -> Dict[str, Any]:
            v = named[name]
            st_trades = simulate(ds.ticks, ds.bars, HOUR_MS, base_rule, stress, fund,
                                 blackouts=windows_by_pct[v["kw"]["threshold_pct"]])
            return summarize(st_trades, start_ms, end_ms)

        _finish_family(families, fam_name, named, rows, picked, filter_neighbours, stress_fn, baseline_row)

    run_trend_family("b_hype_x_oil_trend", hype_ds, hype_fund, HYPE_MEASURED, HYPE_STRESS, hype_rule,
                     oil_signs_by_span, hype_baseline_row)
    run_trend_family("b_oil_x_hype_trend", oil_ds, oil_fund, OIL_MEASURED, OIL_STRESS, oil_rule,
                     hype_signs_by_span, oil_baseline_row)
    run_shock_family("b_hype_x_oil_shock", hype_ds, hype_fund, HYPE_MEASURED, HYPE_STRESS, hype_rule,
                     oil_shock_windows_by_pct, hype_baseline_row)
    run_shock_family("b_oil_x_hype_drawdown", oil_ds, oil_fund, OIL_MEASURED, OIL_STRESS, oil_rule,
                     hype_drawdown_windows_by_pct, oil_baseline_row)
    report["b_cross_signal"] = families

    # ── c. lead/lag entry: gate not met, no backtest run ──
    report["c_lead_lag_entry"] = {
        "built": False,
        "reason": "hype_oil_correlation.lead_lag_significance_check found the lead/lag crossings consistent "
                  "with multiple-testing chance (see docs/strategy/HYPE_OIL_CORRELATION_2026-11-20.md); the "
                  "task's own gate (\"only if (1) finds a lead with t > 2\") is not met once that correction "
                  "is applied.",
    }

    # ── d. pair trade ──
    oil_cum = cumulative_log_returns(oil_snap.c1h)
    hype_cum = cumulative_log_returns(hype_snap.c1h)
    ts, oil_cum_v, hype_cum_v = CORR.align(oil_cum, hype_cum)
    train_idx = [i for i, t in enumerate(ts) if t < cut_ms]
    beta_train = CORR.beta_of([oil_cum_v[i] for i in train_idx], [hype_cum_v[i] for i in train_idx]) \
        if len(train_idx) >= 10 else None
    pair_named = pair_variants()
    pair_rows: Dict[str, Any] = {}
    pair_detail: Dict[str, Any] = {}
    pair_ticks_by_name: Dict[str, Tuple[List[Any], List[Any]]] = {}
    if beta_train is not None:
        spread = [h - beta_train * o for o, h in zip(oil_cum_v, hype_cum_v)]
        for name, v in pair_named.items():
            lookback_bars = v["kw"]["lookback_days"] * 24
            z_by_t = rolling_zscore(ts, spread, lookback_bars)
            oil_pt = build_pair_ticks(oil_ds.ticks, z_by_t)
            hype_pt = build_pair_ticks(hype_ds.ticks, z_by_t)
            pair_ticks_by_name[name] = (oil_pt, hype_pt)
            oil_leg_rule = pair_rule(high_z_side="long", entry_z=v["kw"]["entry_z"], k_atr=1.0, target_r=2.0,
                                     leverage=OIL_LEVERAGE, max_leverage=OIL_VENUE_MAX_LEVERAGE)
            hype_leg_rule = pair_rule(high_z_side="short", entry_z=v["kw"]["entry_z"], k_atr=1.0, target_r=2.0,
                                      leverage=HYPE_VENUE_MAX_LEVERAGE, max_leverage=HYPE_VENUE_MAX_LEVERAGE)
            oil_leg_trades = simulate(oil_pt, oil_ds.bars, HOUR_MS, oil_leg_rule, OIL_MEASURED, oil_fund)
            hype_leg_trades = simulate(hype_pt, hype_ds.bars, HOUR_MS, hype_leg_rule, HYPE_MEASURED, hype_fund)
            pooled = list(oil_leg_trades) + list(hype_leg_trades)
            pair_rows[name] = score_rows(pooled, start_ms, cut_ms, end_ms)
            pair_detail[name] = {"n_oil_leg": len(oil_leg_trades), "n_hype_leg": len(hype_leg_trades)}
        picked = pick(pair_rows, pair_named, "d_pair_trade")

        def pair_stress_fn(name: str) -> Dict[str, Any]:
            v = pair_named[name]
            oil_pt, hype_pt = pair_ticks_by_name[name]
            oil_leg_rule = pair_rule(high_z_side="long", entry_z=v["kw"]["entry_z"], k_atr=1.0, target_r=2.0,
                                     leverage=OIL_LEVERAGE, max_leverage=OIL_VENUE_MAX_LEVERAGE)
            hype_leg_rule = pair_rule(high_z_side="short", entry_z=v["kw"]["entry_z"], k_atr=1.0, target_r=2.0,
                                      leverage=HYPE_VENUE_MAX_LEVERAGE, max_leverage=HYPE_VENUE_MAX_LEVERAGE)
            o = simulate(oil_pt, oil_ds.bars, HOUR_MS, oil_leg_rule, OIL_STRESS, oil_fund)
            h = simulate(hype_pt, hype_ds.bars, HOUR_MS, hype_leg_rule, HYPE_STRESS, hype_fund)
            return summarize(list(o) + list(h), start_ms, end_ms)

        families_out: Dict[str, Any] = {}
        _finish_family(families_out, "d_pair_trade", pair_named, pair_rows, picked, pair_neighbours, pair_stress_fn)
        report["d_pair_trade"] = {**families_out["d_pair_trade"], "beta_train": round(beta_train, 4),
                                  "leg_detail": pair_detail}
    else:
        report["d_pair_trade"] = {"built": False, "reason": "fewer than 10 aligned train observations for beta"}

    # ── ranking: every trade-generating candidate by OOS (tail) mean R ──
    report["ranking_oos_mean_r"] = rank_candidates(report)

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _write_csv(report, out_dir)
    _print(report)
    print(f"\nreport: {os.path.join(out_dir, 'report.json')}")
    return report


def baseline_delta(candidate_row: Dict[str, Any], baseline_row: Dict[str, Any]) -> Dict[str, Any]:
    """How a filtered/candidate family's TRAIN and TAIL parts compare to the
    unfiltered live rule's own matching parts: net R and mean R deltas, and
    whether the candidate beats the baseline on BOTH legs out of sample
    (`beats_baseline_oos`). `verdict()`'s bar only checks a candidate's OWN
    absolute profitability and robustness -- it says nothing about whether a
    filter actually improved on doing nothing, so a filtered rule can read
    LEANING/EDGE purely because the UNFILTERED rule it barely touched was
    already profitable. This is the check that catches that: a cross-signal
    filter only counts as real alpha here when it beats its own baseline on
    the held-out tail, not merely when it is independently profitable. Pure."""
    out: Dict[str, Any] = {}
    for part in ("train", "tail"):
        c, b = candidate_row[part], baseline_row[part]
        out[part] = {"candidate_net_r": c["net_r"], "baseline_net_r": b["net_r"],
                    "delta_net_r": round(c["net_r"] - b["net_r"], 2),
                    "candidate_mean_r": c["mean_r"], "baseline_mean_r": b["mean_r"],
                    "delta_mean_r": round(c["mean_r"] - b["mean_r"], 3) if c["mean_r"] is not None and b["mean_r"] is not None else None}
    out["beats_baseline_oos"] = bool(out["tail"]["delta_net_r"] > 0 and (out["tail"]["delta_mean_r"] or 0) > 0)
    return out


def _finish_family(families: Dict[str, Any], fam_name: str, named: Dict[str, Dict[str, Any]],
                   rows: Dict[str, Any], picked: Optional[str], nb_fn,
                   stress_summary_fn: Optional[Any] = None,
                   baseline_row: Optional[Dict[str, Any]] = None) -> None:
    """`stress_summary_fn(picked_name) -> summarize() row`: builds a REAL
    stress-cost row for the picked variant ONLY (never faked by reusing the
    measured-cost `full` row, which would claim cost robustness nothing
    actually tested -- `verdict()`'s `stress_1h` leg exists specifically to
    catch an edge that only survives today's fee tier). `None` when the
    family has no picked variant (nothing to verdict). The `full_15m`/
    `tail_15m` legs always fail here (no 15m dataset was built for these
    families), so a family can reach LEANING at most, the same cap
    `sp500_strategy_test`'s own `orb` family carries."""
    grid = list(named)
    above = sum(1 for n in grid if rows[n]["full"]["net_r"] > 0)
    entry: Dict[str, Any] = {"picked": picked, "variants": len(grid), "variants_net_positive_full": above}
    if picked:
        nb = [rows[n]["full"]["net_r"] for n in nb_fn(picked, named) if n in rows]
        row_for_verdict = {**rows[picked], "stress": stress_summary_fn(picked)}
        jd = verdict(row_for_verdict, None, nb)
        entry.update({"params": named[picked]["kw"], "row": row_for_verdict, "neighbours_net_r": nb, **jd})
        if baseline_row is not None:
            entry["vs_baseline"] = baseline_delta(row_for_verdict, baseline_row)
    else:
        entry.update({"verdict": "NOISE", "failed": ["no variant with enough train trades"]})
    families[fam_name] = entry


def rank_candidates(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every TRADE-GENERATING candidate (the two live baselines, every
    picked b_cross_signal variant, d_pair_trade if built), ranked by
    out-of-sample (held-out tail) mean R per trade -- the operator's own
    ranking ask, not total net R. `a_portfolio_guard` is a sizing rule, not
    a trade generator, and is excluded (it has no per-trade R of its own);
    it is reported separately.

    `beats_baseline_oos`: for a b_cross_signal candidate, whether it beats
    its OWN unfiltered live rule on the held-out tail
    (`baseline_delta`) -- None for a row with no baseline to compare
    against (the two live rules themselves, and the pair trade, which is a
    new strategy, not a filter on an existing one). `verdict()`'s own label
    only checks absolute profitability/robustness; a candidate with a
    positive label but `beats_baseline_oos is False` is NOT real alpha over
    doing nothing, and `real_alpha` says so plainly (True only for a
    positive label that EITHER has no baseline to beat, i.e. a genuinely
    new strategy, OR explicitly beats one). Pure (reads `report`, builds no
    new trades)."""
    rows: List[Dict[str, Any]] = []

    def add(label: str, tail: Dict[str, Any], verdict_label: str, beats_baseline: Optional[bool]) -> None:
        real_alpha = verdict_label in ("EDGE", "LEANING") and beats_baseline is not False
        rows.append({"candidate": label, "label": verdict_label, "n_tail": tail["trades"],
                    "tail_mean_r": tail["mean_r"], "tail_net_r": tail["net_r"],
                    "beats_baseline_oos": beats_baseline, "real_alpha": real_alpha})

    lb = report["live_baseline"]
    add("oil_live_rule", lb["oil"]["tail"], "LIVE", None)
    add("hype_live_rule_trend_gated", lb["hype"]["tail"], "LIVE", None)
    for fam, e in report.get("b_cross_signal", {}).items():
        if e.get("picked"):
            add(f"{fam}:{e['picked']}", e["row"]["tail"], e["verdict"],
               e.get("vs_baseline", {}).get("beats_baseline_oos"))
    d = report.get("d_pair_trade", {})
    if d.get("picked"):
        add(f"d_pair_trade:{d['picked']}", d["row"]["tail"], d["verdict"], None)
    rows.sort(key=lambda r: (-(r["tail_mean_r"] if r["tail_mean_r"] is not None else -999), -r["n_tail"]))
    return rows


def _write_csv(report: Dict[str, Any], out_dir: str) -> None:
    rows = report["ranking_oos_mean_r"]
    if not rows:
        return
    with open(os.path.join(out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _print(report: Dict[str, Any]) -> None:
    w = report["window"]
    print(f"window: {w['start']} to {w['end']} (tail from {w['tail_from']})")
    print("\n-- ranking, out-of-sample (held-out tail) mean R per trade, net of fees --")
    print(f"{'candidate':42} {'label':8} {'n':>5} {'tail meanR':>11} {'tail netR':>10} {'beats baseline':>14} {'real alpha':>10}")
    for r in report["ranking_oos_mean_r"]:
        mr = f"{r['tail_mean_r']:+.3f}" if r["tail_mean_r"] is not None else "n/a"
        nr = f"{r['tail_net_r']:+.2f}" if r["tail_net_r"] is not None else "n/a"
        bb = "n/a" if r["beats_baseline_oos"] is None else ("yes" if r["beats_baseline_oos"] else "NO")
        print(f"{r['candidate']:42} {r['label']:8} {r['n_tail']:>5} {mr:>11} {nr:>10} {bb:>14} {str(r['real_alpha']):>10}")
    if not any(r["real_alpha"] for r in report["ranking_oos_mean_r"]):
        print("\nNo candidate clears NOISE with real out-of-sample alpha. Nothing here earns a live rule change.")
    print("\n-- a. portfolio guard (combined account, full / tail) --")
    for name, v in report["a_portfolio_guard"]["variants"].items():
        print(f"  {name:16} full: x{v['full']['final_multiple']} dd{v['full']['max_dd_pct']}%   "
              f"tail: x{v['tail']['final_multiple']} dd{v['tail']['max_dd_pct']}%")
    print(f"  overlap counts: {report['a_portfolio_guard']['overlap_counts']}")
    print("\n-- b. cross-signal filter families --")
    for fam, e in report["b_cross_signal"].items():
        print(f"  {fam}: picked {e['picked']} ({e.get('variants_net_positive_full')}/{e.get('variants')} "
              f"net positive) -> {e['verdict']} failed={e.get('failed')}")
    print(f"\n-- c. lead/lag entry: built={report['c_lead_lag_entry']['built']} --")
    print(f"  {report['c_lead_lag_entry']['reason']}")
    d = report["d_pair_trade"]
    print(f"\n-- d. pair trade: picked {d.get('picked')} -> {d.get('verdict')} failed={d.get('failed')} --")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--snapshot-dir", required=True)
    ap.add_argument("--out-dir", default="/tmp/hype-oil-strategy")
    ap.add_argument("--risk-pct", type=float, default=RISK_PCT_DEFAULT)
    a = ap.parse_args(argv)
    rep = run(os.path.expanduser(a.snapshot_dir), a.out_dir, a.risk_pct)
    any_real_alpha = any(r["real_alpha"] for r in rep["ranking_oos_mean_r"])
    return 0 if any_real_alpha else 1


if __name__ == "__main__":
    raise SystemExit(main())
