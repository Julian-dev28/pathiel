"""Funding-extreme z-score, causal and pure (FUNDING_EXTREME_TEST_2026-11-03.md;
research/alpha_swarm/findings/D4_funding_extreme_reversion.md and
D5_basis_premium_signal.md, read from git history commit
a7710a4532b8d6cc41ccaf2efd90344b60266db0).

    funding_zscore_series(funding_sorted, lookback_h, dist_days) -> {ts: z}
    funding_z_at(times, zs, t) -> Optional[float]      the z-score at or before t
    funding_filter_blocks(z, side, threshold) -> bool  the live-desk entry-filter decision

The research signal (D4): per coin, score the trailing `lookback_h`-hour mean
funding rate against that SAME coin's own trailing `dist_days`-day
distribution of that rolling mean -- a z-score, lookahead-safe by
construction (every mean and every distribution statistic at time `t` uses
only funding rows settled at or before `t`). D4/D5 found the SHORT side
alone profitable on 40 crypto majors: z >= 2.0 (a crowded long paying
extreme funding) fades to a reversal short, 5-day hold, net +4.6%/event at
25bps, both OOS halves positive, null p=0.0006. The LONG side (z <= -2.0,
fading a crowded short) was cleanly refuted there (p~0.5-0.7), and
separately C5 found tokenized-equity funding structurally charges longs, so
there is no "fade the crowded short" edge expected on xyz instruments either.

As a FILTER on an existing desk (not a new entry signal of its own):
`funding_filter_blocks` reads the crowded side as a reason to hold off
adding to the side the market is about to turn against -- refuse a new LONG
while z is at or above the threshold, a new SHORT while z is at or below
its negative. It never closes a position already open and never flips a
side; `desk_tick_sim.Params.funding_z_filter` (None = off, the default,
every existing golden test byte-identical) is the only thing that reads it.

Every window here is a plain integer count of ROWS, not a time span: a
missing hour's row (API hiccup) silently shortens the window instead of
reaching further into the past to compensate. That is the conservative
direction (a shorter lookback dilutes rather than amplifies an extreme), and
it matches `fetch_funding_cached`'s raw, ungapped-by-default hourly rows.
"""
from __future__ import annotations

import bisect
from typing import Dict, List, Optional, Sequence, Tuple

HOUR_MS = 3_600_000
DAY_MS = 86_400_000

# D4's own numbers: 24h trailing funding, scored against its own trailing
# 30-day distribution of that same rolling mean.
DEFAULT_LOOKBACK_H = 24
DEFAULT_DIST_DAYS = 30

# Below this, a window is treated as zero-variance (see funding_zscore_series).
_MIN_STD = 1e-9


def funding_zscore_series(funding_sorted: Sequence[Tuple[int, float]], lookback_h: int = DEFAULT_LOOKBACK_H,
                          dist_days: int = DEFAULT_DIST_DAYS) -> Dict[int, float]:
    """z-score of the trailing `lookback_h`-row mean funding rate, at every
    timestamp in `funding_sorted` (sorted, deduplicated (time_ms, rate)
    pairs -- one per settled hour), against the distribution of that SAME
    rolling mean over the preceding `dist_days` days (a time window, by
    timestamp, so a gap in the input never widens it). A point is left out
    of the result until both windows are warmed up: `lookback_h` funding
    rows behind it, and at least `dist_days` days of history (by timestamp)
    behind the distribution window. A zero-variance window (every rolling
    mean identical) is also left out -- an undefined z, not an infinite
    one. O(n): both windows are maintained incrementally, oldest-first.
    Pure."""
    times = [t for t, _ in funding_sorted]
    rates = [r for _, r in funding_sorted]
    n = len(rates)
    dist_ms = dist_days * DAY_MS

    # trailing `lookback_h`-row mean, by count (hourly rows with no gaps in
    # practice; see module docstring for the gap behaviour)
    mean_lb: List[Optional[float]] = [None] * n
    running = 0.0
    for i in range(n):
        running += rates[i]
        if i >= lookback_h:
            running -= rates[i - lookback_h]
        if i >= lookback_h - 1:
            mean_lb[i] = running / lookback_h

    out: Dict[int, float] = {}
    left = 0
    sum_m = 0.0
    sumsq_m = 0.0
    count = 0
    for i in range(n):
        if mean_lb[i] is not None:
            sum_m += mean_lb[i]
            sumsq_m += mean_lb[i] * mean_lb[i]
            count += 1
        cutoff = times[i] - dist_ms
        while left <= i and times[left] <= cutoff:
            if mean_lb[left] is not None:
                sum_m -= mean_lb[left]
                sumsq_m -= mean_lb[left] * mean_lb[left]
                count -= 1
            left += 1
        if mean_lb[i] is None or count < 2 or times[i] - times[0] < dist_ms:
            continue    # not warmed up: fewer than dist_days of history exist yet
        mean_h = sum_m / count
        var_h = max(0.0, sumsq_m / count - mean_h * mean_h)
        std_h = var_h ** 0.5
        # A literally-constant funding rate is not a real market (and
        # running-sum floating point drift never lands on an exact 0), so
        # this floor is an absolute one, far below any real funding rate's
        # variance (funding rates run ~1e-6..1e-3) -- not a relative
        # fraction of std_h, which a real zero would still pass.
        if std_h <= _MIN_STD:
            continue
        out[times[i]] = (mean_lb[i] - mean_h) / std_h
    return out


def funding_z_at(times_sorted: Sequence[int], zs: Sequence[float], t: int) -> Optional[float]:
    """The most recent z-score at or before `t` (bisect over `times_sorted`,
    ascending, paired 1:1 with `zs`). None before the first one, or when
    `times_sorted` is empty. Pure."""
    lo = bisect.bisect_right(times_sorted, t) - 1
    if lo < 0:
        return None
    return zs[lo]


FUNDING_FILTER_NAME = "funding_z"


def funding_filter_blocks(z: Optional[float], side: str, threshold: float) -> bool:
    """True when a new `side` entry is refused: a LONG while z >= threshold
    (a crowded-long funding spike, the fade-to-short signal in D4/D5), a
    SHORT while z <= -threshold (crowded short). `z=None` (warm-up, or no
    funding history at all) never blocks -- the filter is additive risk
    management, not a gate the sim cannot run without. Pure."""
    if z is None or side not in ("long", "short"):
        return False
    return z >= threshold if side == "long" else z <= -threshold


__all__ = ["funding_zscore_series", "funding_z_at", "funding_filter_blocks", "FUNDING_FILTER_NAME",
           "DEFAULT_LOOKBACK_H", "DEFAULT_DIST_DAYS"]
