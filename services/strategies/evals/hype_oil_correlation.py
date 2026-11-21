"""HYPE vs xyz:BRENTOIL correlation research, 2026-11-20
(docs/strategy/HYPE_OIL_CORRELATION_2026-11-20.md).

Operator: "build correlations and strateges between hype and oil." Both are
live desks on separate dexes (oil: xyz:BRENTOIL on the xyz HIP-3 dex, 32% of
equity, 20x cap; hype: HYPE on the main dex, 60% of equity, 10x cap, 120h
trend gate), each with its own one-position-max slot, and both CAN be open
at once. This module answers only the descriptive question -- are the two
coins' returns correlated, and under what conditions -- as pure, deterministic
math on a pinned candle snapshot. The strategy question (does any of that
correlation turn into a tradeable rule) is a separate module,
`hype_oil_strategy_test.py`, which imports this one's `run()` output.

What this computes, on the venue's own 1h candles (and 4h bars built by
aggregating four consecutive 1h bars into one, aligned to UTC's 4-hour grid --
Hyperliquid serves both coins around the clock, oil's HIP-3 perp included, so
no resampling gap exists):

  - Pearson and Spearman correlation of hourly (and 4-hourly) returns,
    overall and as rolling 7d/30d series (`rolling_corr`).
  - Lead/lag cross-correlation from -24h to +24h (`lead_lag`): positive lag
    L means oil's return today lines up with HYPE's return L hours later
    (oil leads); negative L is the reverse (HYPE leads). Joined by exact
    timestamp, never by list index, so a missing bar in either series can
    never silently misalign the pair.
  - Correlation split by regime (`regime_correlations`): oil shock days
    (|1d move| > 3%), HYPE drawdown days (1d move <= -3%), the US and Asia
    sessions (fixed UTC hour buckets, DST-naive -- a documented
    approximation, not a claim about exact exchange hours), and the oil
    futures weekend (`services.strategies.weekend.weekend_closed`: Brent's
    underlying ICE contract is shut Fri 16:00 to Sun 18:00 New York even
    though both HIP-3 and main-dex perps keep printing candles through it).
  - Tail co-movement (`tail_comovement`): of the worst 5% of oil hours, how
    often HYPE is also down that hour, and the reverse.
  - Beta of HYPE's returns on oil's (`beta_of`, OLS slope, no intercept
    reported since the two trade in unrelated price units) plus a bootstrap
    confidence interval on the Pearson correlation and the beta
    (`bootstrap_ci`, fixed seed -- deterministic, not a new random draw
    every run).

No network call of its own: `run()` reads a pinned snapshot built by
`services.strategies.backtest.snapshot` (the same pinning final_strategy_test
and the sp500/hype evals use, so two runs on different days never silently
compare different windows of "the same" history). Every function above this
module's `run()`/`main()` is pure (candles and plain numbers in, numbers out)
and is gate-tested on synthetic fixtures in
`services/strategies/tests/test_hype_oil_correlation.py`, including a
constructed series with a KNOWN lag that `lead_lag` must recover exactly.

    .venv/bin/python -m services.strategies.backtest.snapshot --coin HYPE --days-back 600 \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261120-hype-oil
    .venv/bin/python -m services.strategies.backtest.snapshot --coin xyz:BRENTOIL --days-back 600 \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261120-hype-oil
    .venv/bin/python -m services.strategies.evals.hype_oil_correlation \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261120-hype-oil

Writes <out-dir>/report.json (default /tmp/hype-oil-correlation/report.json)
and prints one summary table. Read only: no account, no live config, no
pathiel MCP tool, no LLM call.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.weekend import weekend_closed

OIL = "xyz:BRENTOIL"
HYPE = "HYPE"
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
MIN_WINDOW_DAYS = 60.0

# Session buckets: fixed UTC hour-of-day ranges, DST-naive by construction
# (a deterministic script cannot silently change its own definition twice a
# year). Asia: 00:00-08:00 UTC covers Tokyo 09:00-17:00 JST and
# Singapore/Hong Kong 08:00-16:00 SGT/HKT. US: 13:00-21:00 UTC covers New
# York 09:00-17:00 in winter (08:00-16:00 in summer) -- close enough to the
# cash session that the regime split is informative; exact DST correctness
# would need a timezone-aware session (weekend.py's own NEW_YORK) which this
# intentionally keeps out of a plain UTC-hour split for determinism.
ASIA_SESSION_UTC: Tuple[int, int] = (0, 8)
US_SESSION_UTC: Tuple[int, int] = (13, 21)

OIL_SHOCK_DAY_ABS_PCT = 3.0        # |1d close-to-close move| over this is a shock day
HYPE_DRAWDOWN_DAY_PCT = -3.0       # 1d close-to-close move at or under this is a drawdown day
TAIL_PCT = 0.05                    # worst/best 5% of hours, for tail co-movement
LEAD_LAG_MAX_H = 24
MIN_REGIME_N = 10                  # a regime cell under this many hours reports no correlation (too thin to read)
MIN_LAG_N = 30                     # a lag shift under this many paired hours reports no correlation
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20261120          # fixed: the CI is reproducible, not a new random draw every run


# ── returns and resampling (pure) ───────────────────────────────────────────

def close_returns(bars: Sequence[Candle], step_ms: int) -> List[Tuple[int, float]]:
    """(close_time_ms, simple close-to-close return) for each consecutive
    pair of `bars`, oldest first. `close_time = bar.t + step_ms` (Candle.t is
    the bar's OPEN time, the same convention `next_trade`/`sp500_strategy_test
    .build_ticks` use for "decision at the bar's close"). A non-positive
    prior close is skipped (never divides by it). Pure."""
    s = sorted(bars, key=lambda c: c.t)
    out: List[Tuple[int, float]] = []
    for a, b in zip(s, s[1:]):
        if a.c > 0:
            out.append((b.t + step_ms, b.c / a.c - 1.0))
    return out


def resample(bars: Sequence[Candle], hours: int) -> List[Candle]:
    """Non-overlapping `hours`-hour bars built from 1h bars `bars`, bucketed
    to UTC boundaries that are multiples of `hours` (00:00, 04:00, 08:00, ...
    for hours=4): open of the first 1h bar, high/low across the group, close
    of the last, volume summed. A bucket with fewer than `hours` 1h bars
    (a partial bucket at either edge of the data) is dropped rather than
    reported short. `hours <= 1` returns `bars` unchanged. Pure."""
    if hours <= 1:
        return sorted(bars, key=lambda c: c.t)
    step = HOUR_MS * hours
    buckets: Dict[int, List[Candle]] = {}
    for b in sorted(bars, key=lambda c: c.t):
        bucket_t = (b.t // step) * step
        buckets.setdefault(bucket_t, []).append(b)
    out: List[Candle] = []
    for bt in sorted(buckets):
        group = sorted(buckets[bt], key=lambda c: c.t)
        if len(group) < hours:
            continue
        out.append(Candle(t=bt, o=group[0].o, h=max(g.h for g in group), l=min(g.l for g in group),
                          c=group[-1].c, v=sum(g.v for g in group)))
    return out


def align(a: Sequence[Tuple[int, float]], b: Sequence[Tuple[int, float]]) -> Tuple[List[int], List[float], List[float]]:
    """Inner join of two (t, value) series on an exact timestamp match,
    sorted by t. Pure."""
    bm = dict(b)
    ts: List[int] = []
    xs: List[float] = []
    ys: List[float] = []
    for t, x in sorted(a, key=lambda r: r[0]):
        if t in bm:
            ts.append(t)
            xs.append(x)
            ys.append(bm[t])
    return ts, xs, ys


# ── correlation math (pure) ──────────────────────────────────────────────────

def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Pearson correlation, or None with fewer than 2 points or zero
    variance in either series. Pure."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def rank(xs: Sequence[float]) -> List[float]:
    """1-based ranks, ties averaged. Pure."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Spearman rank correlation: Pearson on the two rank series. Pure."""
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    return pearson(rank(xs), rank(ys))


def corr_t_stat(r: Optional[float], n: int) -> Optional[float]:
    """The t-statistic of a correlation r over n paired observations
    (n - 2 degrees of freedom): r x sqrt((n-2) / (1-r^2)). None when r is
    unavailable, n is too small, or |r| is (numerically) 1. Pure."""
    if r is None or n < 3 or abs(r) >= 1.0:
        return None
    return r * math.sqrt((n - 2) / (1.0 - r * r))


def beta_of(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """OLS slope of ys on xs (ys = beta * xs + a), i.e. HYPE's beta to oil
    when `xs` is oil's returns and `ys` is HYPE's: cov(x, y) / var(x). None
    with fewer than 2 points or zero variance in x. Pure."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx


def bootstrap_ci(xs: Sequence[float], ys: Sequence[float], func: Callable[[Sequence[float], Sequence[float]], Optional[float]],
                 n_boot: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED,
                 lo_pct: float = 2.5, hi_pct: float = 97.5) -> Dict[str, Any]:
    """A percentile bootstrap CI for `func(xs, ys)` (pearson or beta_of):
    resample pairs with replacement `n_boot` times, report the `lo_pct`/
    `hi_pct` percentiles of the resampled statistic. Deterministic (fixed
    `seed`): the same inputs always give the same interval. Pure."""
    n = len(xs)
    if n < 10:
        return {"lo": None, "hi": None, "n_boot": 0, "n": n}
    rng = random.Random(seed)
    vals: List[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        v = func([xs[i] for i in idx], [ys[i] for i in idx])
        if v is not None:
            vals.append(v)
    if not vals:
        return {"lo": None, "hi": None, "n_boot": 0, "n": n}
    vals.sort()

    def pct(p: float) -> float:
        k = max(0, min(len(vals) - 1, int(round(p / 100.0 * (len(vals) - 1)))))
        return vals[k]

    return {"lo": round(pct(lo_pct), 4), "hi": round(pct(hi_pct), 4), "n_boot": len(vals), "n": n}


# ── rolling correlation ──────────────────────────────────────────────────────

def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def rolling_corr(ts: Sequence[int], xs: Sequence[float], ys: Sequence[float], window_bars: int,
                 step_bars: int) -> List[Dict[str, Any]]:
    """Pearson/Spearman over each trailing window of `window_bars`
    observations, stepped every `step_bars` (e.g. window 168, step 24: a
    daily 7d-rolling-correlation series on hourly data). One row per step,
    oldest first. Pure."""
    out: List[Dict[str, Any]] = []
    n = len(ts)
    i = window_bars
    while i <= n:
        wx, wy = xs[i - window_bars:i], ys[i - window_bars:i]
        out.append({"end_t": ts[i - 1], "end_iso": _iso(ts[i - 1]), "n": window_bars,
                    "pearson": pearson(wx, wy), "spearman": spearman(wx, wy)})
        i += step_bars
    return out


# ── lead/lag cross-correlation ──────────────────────────────────────────────

def lead_lag(ts: Sequence[int], xs: Sequence[float], ys: Sequence[float], max_lag_h: int = LEAD_LAG_MAX_H,
            step_ms: int = HOUR_MS, min_n: int = MIN_LAG_N) -> List[Dict[str, Any]]:
    """Cross-correlation of x (oil) against y (HYPE) shifted by `lag` steps
    of `step_ms`, for every integer lag in [-max_lag_h, max_lag_h]:
    corr(x[t], y[t + lag * step_ms]). A POSITIVE lag that peaks means oil's
    return at t lines up with HYPE's return `lag` hours LATER -- oil leads.
    A NEGATIVE lag that peaks means the reverse -- HYPE leads oil. lag=0 is
    the contemporaneous (same-hour) correlation. Joined by exact timestamp
    (t + lag*step_ms must itself be an observed time in the OTHER series),
    never by list index, so a gap in either series thins the pair count
    rather than silently misaligning it. A lag whose paired count falls
    under `min_n` reports no correlation (too little data to read). Pure."""
    y_by_t = dict(zip(ts, ys))
    x_by_t = dict(zip(ts, xs))
    out: List[Dict[str, Any]] = []
    for lag in range(-max_lag_h, max_lag_h + 1):
        shift = lag * step_ms
        pair_t = [t for t in ts if (t + shift) in y_by_t]
        a = [x_by_t[t] for t in pair_t]
        b = [y_by_t[t + shift] for t in pair_t]
        n = len(a)
        r = pearson(a, b) if n >= min_n else None
        out.append({"lag_h": lag, "n": n, "pearson": r,
                    "spearman": spearman(a, b) if n >= min_n else None,
                    "t_stat": corr_t_stat(r, n) if r is not None else None})
    return out


def best_lag(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The row with the largest |pearson| among rows that have one, or an
    empty/None row if every lag was too thin to read. Pure."""
    cands = [r for r in rows if r.get("pearson") is not None]
    if not cands:
        return {"lag_h": None, "n": 0, "pearson": None, "spearman": None, "t_stat": None}
    return max(cands, key=lambda r: abs(r["pearson"]))


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def lead_lag_significance_check(rows: Sequence[Dict[str, Any]], t_threshold: float = 2.0) -> Dict[str, Any]:
    """Scanning 2 x LEAD_LAG_MAX_H + 1 lags for the biggest correlation is a
    multiple-testing problem: at |t| > 2 (two-sided alpha about 0.0455) pure
    chance alone produces about `alpha x n_tested` false crossings even when
    there is no real lead/lag relationship at all. This counts how many
    non-zero lags in `rows` actually cross `t_threshold` and compares it to
    that chance expectation: `passes_multiple_testing` is True only when the
    count is CLEARLY above chance (more than one hit past the expected
    count), not merely at or near it. The task's "a lead with t > 2" bar
    (step 2c) is read as "a lead whose crossing also clears this check" --
    see docs/strategy/HYPE_OIL_CORRELATION_2026-11-20.md. Pure (the normal
    CDF, not a lookup table, so any `t_threshold` works)."""
    alpha = 2.0 * (1.0 - _normal_cdf(t_threshold))
    tested = [r for r in rows if r["lag_h"] != 0 and r.get("t_stat") is not None]
    hits = [r for r in tested if abs(r["t_stat"]) > t_threshold]
    expected = alpha * len(tested)
    return {"t_threshold": t_threshold, "n_tested": len(tested), "n_over_threshold": len(hits),
            "expected_by_chance": round(expected, 2),
            "passes_multiple_testing": len(hits) > expected + 1.0,
            "hits": [{"lag_h": r["lag_h"], "t_stat": r["t_stat"], "pearson": r["pearson"], "n": r["n"]}
                    for r in sorted(hits, key=lambda r: -abs(r["t_stat"]))]}


def rolling_stability(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Sign-stability summary of a `rolling_corr()` series: how many windows
    read positive/negative, the min/max correlation seen, and the share of
    windows that agree with the majority sign (`same_sign_frac` of 1.0 is
    the strongest case -- every window agreed -- the bar a pair trade needs
    per the task's "only if correlation is stable enough"). Pure."""
    ps = [r["pearson"] for r in rows if r["pearson"] is not None]
    if not ps:
        return {"n": 0, "pos": 0, "neg": 0, "min": None, "max": None, "same_sign_frac": None}
    pos, neg = sum(1 for p in ps if p > 0), sum(1 for p in ps if p < 0)
    return {"n": len(ps), "pos": pos, "neg": neg, "min": round(min(ps), 4), "max": round(max(ps), 4),
            "same_sign_frac": round(max(pos, neg) / len(ps), 3)}


# ── regimes ──────────────────────────────────────────────────────────────────

def utc_hour(t_ms: int) -> int:
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).hour


def in_session(t_ms: int, session: Tuple[int, int]) -> bool:
    lo, hi = session
    return lo <= utc_hour(t_ms) < hi


def day_ordinal(t_ms: int) -> int:
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).date().toordinal()


def daily_returns(c1d: Sequence[Candle]) -> Dict[int, float]:
    """{the UTC day ordinal a bar closes on: that day's close-to-close
    return}, from daily candles (UTC-midnight aligned on Hyperliquid).
    Pure."""
    s = sorted(c1d, key=lambda c: c.t)
    out: Dict[int, float] = {}
    for a, b in zip(s, s[1:]):
        if a.c > 0:
            out[day_ordinal(b.t)] = b.c / a.c - 1.0
    return out


def regime_mask(ts: Sequence[int], kind: str, oil_daily: Dict[int, float], hype_daily: Dict[int, float]) -> List[bool]:
    """The boolean mask selecting which hours of `ts` belong to regime
    `kind`: oil_shock (|oil's day| > OIL_SHOCK_DAY_ABS_PCT%), hype_drawdown
    (HYPE's day <= HYPE_DRAWDOWN_DAY_PCT%), us_session, asia_session,
    weekend, weekday. Pure."""
    if kind == "oil_shock":
        return [day_ordinal(t) in oil_daily and abs(oil_daily[day_ordinal(t)]) * 100.0 > OIL_SHOCK_DAY_ABS_PCT
                for t in ts]
    if kind == "hype_drawdown":
        return [day_ordinal(t) in hype_daily and hype_daily[day_ordinal(t)] * 100.0 <= HYPE_DRAWDOWN_DAY_PCT for t in ts]
    if kind == "us_session":
        return [in_session(t, US_SESSION_UTC) for t in ts]
    if kind == "asia_session":
        return [in_session(t, ASIA_SESSION_UTC) for t in ts]
    if kind == "weekend":
        return [weekend_closed(t) for t in ts]
    if kind == "weekday":
        return [not weekend_closed(t) for t in ts]
    raise ValueError(f"unknown regime {kind!r}")


REGIMES: Tuple[str, ...] = ("oil_shock", "hype_drawdown", "us_session", "asia_session", "weekend", "weekday")


def regime_correlations(ts: Sequence[int], xs: Sequence[float], ys: Sequence[float],
                        oil_daily: Dict[int, float], hype_daily: Dict[int, float],
                        min_n: int = MIN_REGIME_N) -> Dict[str, Dict[str, Any]]:
    """{regime: {n, pearson, spearman}} for every name in REGIMES. A regime
    with fewer than `min_n` hours reports pearson/spearman as None (too thin
    to read), not a number dressed up as one. Pure."""
    out: Dict[str, Dict[str, Any]] = {}
    for name in REGIMES:
        mask = regime_mask(ts, name, oil_daily, hype_daily)
        idx = [i for i, m in enumerate(mask) if m]
        sx, sy = [xs[i] for i in idx], [ys[i] for i in idx]
        enough = len(idx) >= min_n
        out[name] = {"n": len(idx), "pearson": pearson(sx, sy) if enough else None,
                    "spearman": spearman(sx, sy) if enough else None}
    return out


# ── tail co-movement ─────────────────────────────────────────────────────────

def tail_comovement(xs: Sequence[float], ys: Sequence[float], tail_pct: float = TAIL_PCT) -> Dict[str, Any]:
    """Of the worst `tail_pct` share of oil hours (`xs`, lowest return),
    the share where HYPE (`ys`) is also down that same hour, and the
    reverse (worst HYPE hours, share where oil is also down); the same pair
    on the BEST hours (both up). `baseline_*_down_share_pct`: the unconditional
    share of down hours for each coin, so a tail share can be read against
    its own base rate rather than against 50%. Pure."""
    n = len(xs)
    if n == 0:
        return {"tail_pct": tail_pct, "k": 0, "n": 0}
    k = max(1, int(round(n * tail_pct)))
    worst_oil = sorted(range(n), key=lambda i: xs[i])[:k]
    worst_hype = sorted(range(n), key=lambda i: ys[i])[:k]
    best_oil = sorted(range(n), key=lambda i: -xs[i])[:k]
    best_hype = sorted(range(n), key=lambda i: -ys[i])[:k]
    return {
        "tail_pct": tail_pct, "k": k, "n": n,
        "worst_oil_hours_hype_also_down_pct": round(sum(1 for i in worst_oil if ys[i] < 0) / k * 100, 1),
        "worst_hype_hours_oil_also_down_pct": round(sum(1 for i in worst_hype if xs[i] < 0) / k * 100, 1),
        "best_oil_hours_hype_also_up_pct": round(sum(1 for i in best_oil if ys[i] > 0) / k * 100, 1),
        "best_hype_hours_oil_also_up_pct": round(sum(1 for i in best_hype if xs[i] > 0) / k * 100, 1),
        "baseline_hype_down_share_pct": round(sum(1 for y in ys if y < 0) / n * 100, 1),
        "baseline_oil_down_share_pct": round(sum(1 for x in xs if x < 0) / n * 100, 1),
    }


# ── the run ──────────────────────────────────────────────────────────────────

@dataclass
class ReturnSet:
    ts: List[int]
    oil: List[float]
    hype: List[float]


def build_return_set(oil_bars: Sequence[Candle], hype_bars: Sequence[Candle], step_ms: int) -> ReturnSet:
    """Aligned (ts, oil returns, hype returns) off two already-sized bar
    series (1h bars, or `resample()`'s output for a coarser step). Pure."""
    ts, x, y = align(close_returns(oil_bars, step_ms), close_returns(hype_bars, step_ms))
    return ReturnSet(ts=ts, oil=x, hype=y)


def run(snapshot_dir: str, out_dir: str = "/tmp/hype-oil-correlation") -> Dict[str, Any]:
    from services.strategies.backtest import snapshot as SNAP
    oil_snap = SNAP.load_snapshot(snapshot_dir, OIL)
    hype_snap = SNAP.load_snapshot(snapshot_dir, HYPE)
    if not oil_snap.c1h or not hype_snap.c1h:
        raise SystemExit(f"no 1h candles for {OIL} and/or {HYPE} in {snapshot_dir}: "
                         f"run services.strategies.backtest.snapshot for both coins first")

    rs1h = build_return_set(oil_snap.c1h, hype_snap.c1h, HOUR_MS)
    oil_4h, hype_4h = resample(oil_snap.c1h, 4), resample(hype_snap.c1h, 4)
    rs4h = build_return_set(oil_4h, hype_4h, 4 * HOUR_MS)
    if len(rs1h.ts) < 2:
        raise SystemExit("no overlapping 1h hours between the two coins' snapshots")

    days = (rs1h.ts[-1] - rs1h.ts[0]) / DAY_MS
    oil_daily = daily_returns(oil_snap.c1d)
    hype_daily = daily_returns(hype_snap.c1d)

    def overall(rs: ReturnSet) -> Dict[str, Any]:
        r = pearson(rs.oil, rs.hype)
        return {"n": len(rs.ts), "pearson": r, "spearman": spearman(rs.oil, rs.hype),
                "t_stat": corr_t_stat(r, len(rs.ts)),
                "bootstrap_ci_pearson": bootstrap_ci(rs.oil, rs.hype, pearson)}

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_dir": snapshot_dir,
        "window": {"start": _iso(rs1h.ts[0]), "end": _iso(rs1h.ts[-1]), "days": round(days, 1),
                  "n_1h": len(rs1h.ts), "n_4h": len(rs4h.ts), "below_60d_minimum": days < MIN_WINDOW_DAYS},
        "overall": {"1h": overall(rs1h), "4h": overall(rs4h)},
        "rolling_1h": {"7d": rolling_corr(rs1h.ts, rs1h.oil, rs1h.hype, window_bars=24 * 7, step_bars=24),
                      "30d": rolling_corr(rs1h.ts, rs1h.oil, rs1h.hype, window_bars=24 * 30, step_bars=24)},
        "lead_lag_1h": lead_lag(rs1h.ts, rs1h.oil, rs1h.hype, LEAD_LAG_MAX_H),
        "regimes_1h": regime_correlations(rs1h.ts, rs1h.oil, rs1h.hype, oil_daily, hype_daily),
        "tail_comovement_1h": tail_comovement(rs1h.oil, rs1h.hype),
        "beta_hype_on_oil_1h": beta_of(rs1h.oil, rs1h.hype),
        "beta_ci_1h": bootstrap_ci(rs1h.oil, rs1h.hype, beta_of),
    }
    report["lead_lag_best_1h"] = best_lag(report["lead_lag_1h"])
    report["lead_lag_multiple_testing_1h"] = lead_lag_significance_check(report["lead_lag_1h"])
    report["rolling_stability_1h"] = {"7d": rolling_stability(report["rolling_1h"]["7d"]),
                                      "30d": rolling_stability(report["rolling_1h"]["30d"])}

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "report.json")
    with open(path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {path}")
    return report


def _print(report: Dict[str, Any]) -> None:
    w = report["window"]
    print(f"window: {w['start']} to {w['end']} ({w['days']} days, n_1h={w['n_1h']}, n_4h={w['n_4h']})")
    if w["below_60d_minimum"]:
        print(f"  WARNING: below the {MIN_WINDOW_DAYS:g}-day minimum the task asked for")
    print(f"{'':20} {'n':>6} {'pearson':>9} {'spearman':>9} {'t':>7} {'95% CI (pearson)':>20}")
    for k, o in report["overall"].items():
        ci = o["bootstrap_ci_pearson"]
        ci_s = f"[{ci['lo']:+.3f}, {ci['hi']:+.3f}]" if ci.get("lo") is not None else "n/a"
        t_s = f"{o['t_stat']:+.2f}" if o["t_stat"] is not None else "n/a"
        p_s = f"{o['pearson']:+.4f}" if o["pearson"] is not None else "n/a"
        s_s = f"{o['spearman']:+.4f}" if o["spearman"] is not None else "n/a"
        print(f"overall {k:12} {o['n']:>6} {p_s:>9} {s_s:>9} {t_s:>7} {ci_s:>20}")
    print(f"beta (HYPE on oil, 1h): {report['beta_hype_on_oil_1h']:+.3f}  "
          f"95% CI {report['beta_ci_1h']}")
    best = report["lead_lag_best_1h"]
    if best["lag_h"] is not None:
        who = "oil leads" if best["lag_h"] > 0 else ("HYPE leads" if best["lag_h"] < 0 else "contemporaneous")
        print(f"lead/lag (1h): best |pearson| at lag {best['lag_h']:+d}h ({who}), "
              f"r={best['pearson']:+.4f}, n={best['n']}, t={best['t_stat']:+.2f}" if best["t_stat"] is not None
              else f"lead/lag (1h): best |pearson| at lag {best['lag_h']:+d}h ({who}), r={best['pearson']:+.4f}, n={best['n']}")
    else:
        print("lead/lag (1h): no lag had enough paired hours to read")
    mt = report["lead_lag_multiple_testing_1h"]
    print(f"lead/lag multiple-testing check: {mt['n_over_threshold']} of {mt['n_tested']} non-zero lags cross "
          f"|t|>{mt['t_threshold']:g} (chance expects ~{mt['expected_by_chance']}) -> "
          f"{'a real lead/lag signal' if mt['passes_multiple_testing'] else 'consistent with chance, no real lead/lag'}")
    for k, s in report["rolling_stability_1h"].items():
        print(f"rolling {k} stability: {s['n']} windows, {s['neg']} negative / {s['pos']} positive "
              f"(same-sign {s['same_sign_frac']:.0%}), range [{s['min']:+.3f}, {s['max']:+.3f}]"
              if s["n"] else f"rolling {k} stability: no windows")
    print(f"{'regime':20} {'n':>6} {'pearson':>9} {'spearman':>9}")
    for name, r in report["regimes_1h"].items():
        p_s = f"{r['pearson']:+.4f}" if r["pearson"] is not None else "n/a (thin)"
        s_s = f"{r['spearman']:+.4f}" if r["spearman"] is not None else "n/a (thin)"
        print(f"{name:20} {r['n']:>6} {p_s:>9} {s_s:>9}")
    tc = report["tail_comovement_1h"]
    print(f"tail co-movement (worst/best {tc['tail_pct']*100:g}%, k={tc['k']} of {tc['n']}):")
    print(f"  worst oil hours -> HYPE also down: {tc['worst_oil_hours_hype_also_down_pct']}% "
          f"(baseline {tc['baseline_hype_down_share_pct']}%)")
    print(f"  worst HYPE hours -> oil also down: {tc['worst_hype_hours_oil_also_down_pct']}% "
          f"(baseline {tc['baseline_oil_down_share_pct']}%)")
    print(f"  best oil hours -> HYPE also up:    {tc['best_oil_hours_hype_also_up_pct']}%")
    print(f"  best HYPE hours -> oil also up:    {tc['best_hype_hours_oil_also_up_pct']}%")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--snapshot-dir", required=True)
    ap.add_argument("--out-dir", default="/tmp/hype-oil-correlation")
    a = ap.parse_args(argv)
    run(os.path.expanduser(a.snapshot_dir), a.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
