"""Deterministic, no-LLM scoring for the thesis replay eval
(services.strategies.evals.thesis_replay). Pure: same call + same candles
always produce the same score, no network, no randomness.

Profit and loss over 141 historical calls is too noisy to compare model A
vs model B on, so every call (the original stored one, or a replayed one
from another model) is graded on two sharper scores, from Hyperliquid
public candles after the thesis's created_at:

    direction_24h       was the call's side right 24h later -- the close
                         vs a reference price at created_at? hit / miss /
                         void (no side).
    target_before_stop  from the call's entry (or the reference price if
                         no entry trigger fires), did the first target
                         trade before the stop within the thesis horizon?
                         hit / stop / neither / void.

Both scores reuse services.strategies.grader's own bar primitives (`Bar`,
`_clip`, `_direction_sign`, `_first_fill`) instead of reimplementing entry-
fill or stop/target-touch logic a second time -- same "stop checked first on
a same-bar tie" conservatism, same 1h-bar OHLC semantics the live grader
already uses and already has tests for.

`void` covers three cases: a flat call (no side) or a pair call (not
scored here -- needs the long/short spread construction grader.py does for
grade_thesis, out of scope for this eval); missing candle coverage (the
fetched window does not reach back to created_at -- see `has_coverage`, the
check the live grader does not need because it always fetches "back from
now", but a short-window fetcher like the 1m/5m candleSnapshot rolls from
now and silently returns a truncated window for an old thesis); and not
enough elapsed history yet to reach the 24h mark or the call's own horizon.

`reference price` (both scores, when there is no fill yet) is the close of
the first 1h bar at/after created_at -- the same fallback
`services.strategies.grader.grade_thesis` already uses for "entry = bars[0]
.close" when a call has no entry_px, so this eval's convention matches the
live grader's instead of inventing a second one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from contracts.strategies.models import Call
from pathiel.models.types import Candle

from services.review.stats import wilson_ci
from services.strategies.grader import Bar, _clip, _direction_sign, _first_fill

DEFAULT_COVERAGE_SLACK_H = 2.0       # a fetched window's first bar must open within this
                                      # many hours of created_at to count as "covers it"
DEFAULT_DIRECTION_HORIZON_H = 24.0   # direction_24h's fixed window, independent of the
                                      # thesis's own (7-day) horizon

DirectionOutcome = Literal["hit", "miss", "void"]
TargetStopOutcome = Literal["hit", "stop", "neither", "void"]

VOID_NO_SIDE = "no_side"
VOID_PAIR_NOT_SCORED = "pair_not_scored"
VOID_NO_COVERAGE = "no_coverage"
VOID_WINDOW_TOO_SHORT = "window_too_short"
VOID_MISSING_STOP_OR_TARGET = "missing_stop_or_target"


@dataclass(frozen=True)
class DirectionScore:
    outcome: DirectionOutcome
    reason: Optional[str] = None
    ref_px: Optional[float] = None
    px_after: Optional[float] = None


@dataclass(frozen=True)
class TargetStopScore:
    outcome: TargetStopOutcome
    reason: Optional[str] = None
    entry_used: Optional[float] = None
    resolved_at: Optional[str] = None   # isoformat, so the dataclass stays trivially JSON-able


@dataclass(frozen=True)
class RateSummary:
    """A hit rate over non-void outcomes, with a Wilson 95% interval
    (services.review.stats.wilson_ci -- the same small-n-honest interval
    the trade-review harness already uses, not reimplemented here).
    `n`/`k` exclude void outcomes; `counts` has every outcome label seen,
    void included, for the leaderboard's own detail columns."""
    n: int
    k: int
    void: int
    rate: Optional[float]
    ci_lo: Optional[float]
    ci_hi: Optional[float]
    counts: Dict[str, int]


def bars_from_candles(candles: Sequence[Candle], start: datetime, end: datetime) -> List[Bar]:
    """Candle -> Bar, clipped to [start, end] by the bar's OPEN time (same
    inclusive convention as grader._clip) and sorted oldest first. Pure:
    no network, candles are already in hand."""
    rows = [(c.t, c.h, c.l, c.c) for c in candles]
    return sorted(_clip(rows, start, end), key=lambda b: b.t)


def has_coverage(bars: Sequence[Bar], start: datetime, *, slack_h: float = DEFAULT_COVERAGE_SLACK_H) -> bool:
    """Whether `bars` (oldest first) actually reaches back to `start`: its
    first bar must open at or within `slack_h` hours of `start`. Catches a
    short-window fetcher (1m/5m candleSnapshot rolls from NOW) silently
    serving bars that start well after `start` for an old thesis, instead
    of grading on a truncated window as if it were complete."""
    return bool(bars) and bars[0].t <= start + timedelta(hours=slack_h)


def _unscoreable_side(side: str) -> Optional[str]:
    if side == "flat":
        return VOID_NO_SIDE
    if side == "pair":
        return VOID_PAIR_NOT_SCORED
    return None


def direction_24h(call: Call, created_at: datetime, bars: Sequence[Bar], *,
                  horizon_h: float = DEFAULT_DIRECTION_HORIZON_H,
                  coverage_slack_h: float = DEFAULT_COVERAGE_SLACK_H) -> DirectionScore:
    """`bars` must already cover (at least) [created_at, created_at +
    horizon_h] -- callers fetch that window; this function only grades
    it. hit iff the direction-adjusted move is strictly positive (a tie at
    exactly 0 counts as miss, matching grader.Grade.direction_hit's own
    `ret_pct > 0.0` convention)."""
    reason = _unscoreable_side(call.side)
    if reason:
        return DirectionScore("void", reason=reason)
    sign = _direction_sign(call.side)
    if not has_coverage(bars, created_at, slack_h=coverage_slack_h):
        return DirectionScore("void", reason=VOID_NO_COVERAGE)

    ref_px = bars[0].close
    cutoff = created_at + timedelta(hours=horizon_h)
    at_or_before_cutoff = [b for b in bars if b.t <= cutoff]
    if not at_or_before_cutoff or at_or_before_cutoff[-1].t < cutoff - timedelta(hours=coverage_slack_h):
        return DirectionScore("void", reason=VOID_WINDOW_TOO_SHORT, ref_px=ref_px)

    px_after = at_or_before_cutoff[-1].close
    outcome: DirectionOutcome = "hit" if (px_after - ref_px) * sign > 0.0 else "miss"
    return DirectionScore(outcome, ref_px=ref_px, px_after=px_after)


def target_before_stop(call: Call, created_at: datetime, bars: Sequence[Bar], *,
                       coverage_slack_h: float = DEFAULT_COVERAGE_SLACK_H) -> TargetStopScore:
    """`bars` must already cover [created_at, min(expires_at, now)] -- the
    thesis's own horizon, clipped to now if it has not fully elapsed yet.
    Entry resolution: if `call.entry_px` is set and a later bar's low/high
    touches it (grader._first_fill), start walking from that bar with
    entry_used = call.entry_px; otherwise (no entry_px, or it never fills
    inside the window) start from bar 0 with entry_used = bars[0].close --
    "the reference price", same fallback direction_24h uses. From the
    start bar on, a bar touching both the stop and the target the same
    hour resolves as the stop first (grader.grade_thesis's own
    unprovable-you-avoided-it conservatism)."""
    reason = _unscoreable_side(call.side)
    if reason:
        return TargetStopScore("void", reason=reason)
    if call.stop_px is None or not call.targets:
        return TargetStopScore("void", reason=VOID_MISSING_STOP_OR_TARGET)
    if not has_coverage(bars, created_at, slack_h=coverage_slack_h):
        return TargetStopScore("void", reason=VOID_NO_COVERAGE)

    sign = _direction_sign(call.side)
    stop = call.stop_px
    target = call.targets[0]

    start_i = 0
    entry_used = bars[0].close
    if call.entry_px is not None:
        fill_i = _first_fill(bars, call.entry_px, sign)
        if fill_i is not None:
            start_i, entry_used = fill_i, call.entry_px

    for b in bars[start_i:]:
        stop_hit = (b.low <= stop) if sign > 0 else (b.high >= stop)
        target_hit = (b.high >= target) if sign > 0 else (b.low <= target)
        if stop_hit:
            return TargetStopScore("stop", entry_used=entry_used, resolved_at=b.t.isoformat())
        if target_hit:
            return TargetStopScore("hit", entry_used=entry_used, resolved_at=b.t.isoformat())
    return TargetStopScore("neither", entry_used=entry_used)


def score_call(call: Call, created_at: datetime, expires_at: datetime, candles: Sequence[Candle], *,
              now: Optional[datetime] = None, coverage_slack_h: float = DEFAULT_COVERAGE_SLACK_H,
              direction_horizon_h: float = DEFAULT_DIRECTION_HORIZON_H,
              ) -> Tuple[DirectionScore, TargetStopScore]:
    """Both scores for one call, from a flat list of candles already
    fetched to cover (at least) the window either score needs -- callers
    are responsible for fetching enough; this slices what each score
    actually looks at. `now` bounds target_before_stop to "as of now" for a
    thesis whose horizon has not fully elapsed yet (default: wall clock)."""
    from datetime import timezone
    now = now or datetime.now(timezone.utc)

    reason = _unscoreable_side(call.side)
    if reason:
        return DirectionScore("void", reason=reason), TargetStopScore("void", reason=reason)

    dir_end = created_at + timedelta(hours=direction_horizon_h + coverage_slack_h)
    dir_bars = bars_from_candles(candles, created_at, dir_end)
    d = direction_24h(call, created_at, dir_bars, horizon_h=direction_horizon_h,
                      coverage_slack_h=coverage_slack_h)

    ts_end = min(expires_at, now)
    ts_bars = bars_from_candles(candles, created_at, ts_end)
    t = target_before_stop(call, created_at, ts_bars, coverage_slack_h=coverage_slack_h)
    return d, t


def summarize_outcomes(outcomes: Sequence[str], *, hit_label: str = "hit",
                       void_label: str = "void") -> RateSummary:
    """Hit rate + Wilson 95% CI over non-void outcomes. n=0 (every outcome
    void, or an empty list) reports rate/ci as None, never 0/0."""
    counts: Dict[str, int] = {}
    for o in outcomes:
        counts[o] = counts.get(o, 0) + 1
    void = counts.get(void_label, 0)
    n = len(outcomes) - void
    k = counts.get(hit_label, 0)
    if n <= 0:
        return RateSummary(n=0, k=0, void=void, rate=None, ci_lo=None, ci_hi=None, counts=counts)
    lo, hi = wilson_ci(k, n)
    return RateSummary(n=n, k=k, void=void, rate=k / n, ci_lo=lo, ci_hi=hi, counts=counts)
