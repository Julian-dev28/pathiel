"""Grade theses, point-in-time, from hourly candles. Deterministic: same
thesis + same candles always produce the same Grade.

A call is a bracket: an entry limit, a stop and a first target, good for
`horizon_days`. `grade_thesis` walks the 1h bars from `created_at`:

    entry fills   the first bar whose low touches the entry (long) or whose
                  high touches it (short) -- a limit at or through the
                  market fills on the first bar, a dip/rally limit waits
    stop / target the first later bar (the fill bar included) whose low/high
                  touches one; a bar touching both counts as the STOP (the
                  worse, unprovable-you-avoided-it outcome)
    horizon       nothing touched by `expires_at`: "expired" when filled,
                  "untriggered" when the entry never filled

so a call resolves THE HOUR its stop or target is touched, not a week
later. Before 2026-11-13 `grade_all_due` only looked at `expires_at`
(the oil desk's horizon is 7 days) and the Desk page showed 32 calls as
"pending" whose stops had been hit days earlier.

`grade_thesis` resolves the thesis's Call to an hourly price series -- a
single coin for side in (long, short, flat), or a synthetic long-minus-short
spread series (aligned by candle timestamp, same technique as
analytics.build_pair_snapshot; closes only, so its "bars" are flat) when
side == "pair" and the desk's PairSpec is available. `grade_all_due` is the
idempotent sweep: every desk, every ungraded thesis, graded once it has
resolved or expired and saved back via the journal -- calling it again finds
nothing left to grade and does nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from contracts.strategies.models import (Call, CallResult, CallSide, DeskSpec, Grade, Scenario, Thesis,
                                         TrackRecord)
from pathiel.models.types import Candle

from services.strategies import journal
from services.strategies.desks import get_spec

HourlyFetcher = Callable[[str, str, int], Sequence[Candle]]

# Hyperliquid candleSnapshot serves at most this many bars per request; a
# thesis older than that (in hours) cannot be regraded from the venue.
MAX_CANDLES_PER_FETCH = 5000


def default_hourly_fetcher(coin: str, interval: str, count: int) -> Sequence[Candle]:
    from pathiel.client.hl_client import fetch_hl_candles
    return fetch_hl_candles(coin, interval, min(count, MAX_CANDLES_PER_FETCH))


def _direction_sign(side: CallSide) -> Optional[float]:
    """+1 long/pair, -1 short, None for flat (no direction to grade)."""
    if side in ("long", "pair"):
        return 1.0
    if side == "short":
        return -1.0
    return None


def _hours_needed(start: datetime, now: datetime) -> int:
    """How many 1h bars a fetcher that counts BACK FROM NOW must return to
    cover [start, now]. Before 2026-11-13 this counted to the window's end,
    which only covered the window when grading happened the hour it closed;
    a regrade a week later fetched bars that missed the window entirely."""
    span_hours = max(1, int((now - start).total_seconds() // 3600))
    return span_hours + 48  # buffer so the window's edges are covered even with gaps


@dataclass(frozen=True)
class Bar:
    t: datetime
    high: float
    low: float
    close: float


def _clip(rows: List[Tuple[int, float, float, float]], start: datetime, end: datetime) -> List[Bar]:
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    return [Bar(datetime.fromtimestamp(t / 1000.0, tz=timezone.utc), h, lo, c)
           for t, h, lo, c in rows if start_ms <= t <= end_ms]


def _price_bars(call: Call, spec: Optional[DeskSpec], hourly_fetcher: HourlyFetcher,
                start: datetime, end: datetime, now: datetime) -> List[Bar]:
    count = _hours_needed(start, now)
    pair = None
    if call.side == "pair" and spec is not None:
        pair = next((p for p in spec.pairs if p.label == call.instrument), None)

    if pair is not None:
        long_c = hourly_fetcher(pair.long, "1h", count)
        short_c = hourly_fetcher(pair.short, "1h", count)
        by_t = {c.t: c.c for c in short_c}
        # a spread has no intrabar high/low; its bars are the close, flat
        rows = [(c.t, c.c - by_t[c.t], c.c - by_t[c.t], c.c - by_t[c.t]) for c in long_c if c.t in by_t]
    else:
        rows = [(c.t, c.h, c.l, c.c) for c in hourly_fetcher(call.instrument, "1h", count)]

    series = _clip(rows, start, end)
    return sorted(series, key=lambda r: r.t)


def find_scenario_bucket(scenarios: Sequence[Scenario], raw_final_ret: float) -> Optional[Scenario]:
    """The scenario bucket the realized RAW return actually fell in (raw,
    not direction-adjusted — Scenario.move_lo_pct/move_hi_pct are price-move
    buckets per the model's own doc, matching how the thesis author wrote
    them even for a short call). If the realized return falls outside every
    bucket (an authoring gap), the nearest bucket by distance to its closest
    edge. None for an empty scenario list. Public: also used by
    services.thesis.scorecard to report which bucket actually happened,
    alongside this same grading."""
    if not scenarios:
        return None
    bucket = next((s for s in scenarios if s.move_lo_pct <= raw_final_ret < s.move_hi_pct), None)
    if bucket is not None:
        return bucket

    def edge_dist(s: Scenario) -> float:
        if raw_final_ret < s.move_lo_pct:
            return s.move_lo_pct - raw_final_ret
        return raw_final_ret - s.move_hi_pct
    return min(scenarios, key=edge_dist)


def _brier(scenarios: Sequence[Scenario], raw_final_ret: Optional[float]) -> Optional[float]:
    """Multiclass Brier score: sum((p_i - y_i)^2) over scenario buckets,
    y_i=1 for the bucket the realized raw return actually fell in (see
    find_scenario_bucket). Range [0, 2] for a single observation: 0 is a
    perfect call, 2 is maximally wrong (confident in the wrong bucket).
    """
    if not scenarios or raw_final_ret is None:
        return None
    bucket = find_scenario_bucket(scenarios, raw_final_ret)
    return sum((s.probability - (1.0 if s is bucket else 0.0)) ** 2 for s in scenarios)


def _first_fill(bars: Sequence[Bar], entry: float, sign: float) -> Optional[int]:
    """Index of the bar that fills a limit at `entry`: the first bar whose
    low reaches down to it (long) or whose high reaches up to it (short).
    A limit already at or through the market fills on the first bar."""
    for i, b in enumerate(bars):
        if (sign > 0 and b.low <= entry) or (sign < 0 and b.high >= entry):
            return i
    return None


def _excursions(seg: Sequence[Bar], entry: float, sign: float) -> Tuple[float, float]:
    """(mae_pct, mfe_pct) over `seg`, direction-adjusted: for a long the
    favourable side is the bar high and the adverse side the low; a short
    flips both."""
    if not seg or not entry:
        return 0.0, 0.0
    fav = [((b.high if sign > 0 else b.low) / entry - 1.0) * sign for b in seg]
    adv = [((b.low if sign > 0 else b.high) / entry - 1.0) * sign for b in seg]
    return min(min(adv), 0.0), max(max(fav), 0.0)


def grade_thesis(thesis: Thesis, *, hourly_fetcher: Optional[HourlyFetcher] = None,
                 spec: Optional[DeskSpec] = None, now: Optional[datetime] = None) -> Grade:
    """Point-in-time grade over [created_at, min(expires_at, now)].

    `result` is set the moment the call resolves: "win"/"loss" as soon as a
    bar touches the first target/the stop after the entry filled, "expired"/
    "untriggered" once the horizon has passed. A directional call that has
    neither resolved nor expired comes back with result None -- a mid-flight
    read (ret_pct at the latest close), which `grade_all_due` does NOT save.
    A flat call has nothing to resolve: result stays None, ret_pct 0.0.

    Brier is scored on the raw return at the horizon, so it is None for a
    call graded before its horizon; the scorecard's no-lookahead filter
    (services.thesis.scorecard.eligible_theses) already requires
    expires_at <= as_of, so an early grade never reaches a prompt."""
    hourly_fetcher = hourly_fetcher or default_hourly_fetcher
    now = now or datetime.now(timezone.utc)
    end = min(thesis.expires_at, now)
    window_closed = thesis.expires_at <= now
    call = thesis.call

    bars = _price_bars(call, spec, hourly_fetcher, thesis.created_at, end, now)
    sign = _direction_sign(call.side)

    if not bars:
        return Grade(graded_at=now, outcome_px=call.entry_px or 0.0, ret_pct=0.0,
                     direction_hit=None, hit_target_first=None, hit_stop_first=None,
                     brier=None, mae_pct=0.0, mfe_pct=0.0,
                     notes=["no candle data available in [created_at, expires_at]; ungradeable"])

    entry = call.entry_px if call.entry_px else bars[0].close
    last_close = bars[-1].close
    raw_horizon_ret = last_close / entry - 1.0 if entry else 0.0
    brier = _brier(thesis.scenarios, raw_horizon_ret) if (thesis.scenarios and window_closed) else None
    notes: List[str] = []

    # ── flat: nothing to fill, nothing to resolve ──────────────────────────
    if sign is None:
        mae, mfe = _excursions(bars, entry, 1.0)
        return Grade(graded_at=now, outcome_px=last_close, ret_pct=0.0, direction_hit=None,
                     hit_target_first=None, hit_stop_first=None, brier=brier,
                     mae_pct=mae, mfe_pct=mfe, notes=notes)

    # ── entry fill ─────────────────────────────────────────────────────────
    fill_i = 0 if not call.entry_px else _first_fill(bars, entry, sign)
    if fill_i is None:
        if not window_closed:
            return Grade(graded_at=now, outcome_px=last_close, ret_pct=0.0, direction_hit=None,
                         hit_target_first=None, hit_stop_first=None, brier=None,
                         mae_pct=0.0, mfe_pct=0.0, notes=["entry not filled yet"])
        return Grade(graded_at=now, outcome_px=last_close, ret_pct=0.0, direction_hit=None,
                     hit_target_first=None, hit_stop_first=None, brier=brier,
                     mae_pct=0.0, mfe_pct=0.0, result="untriggered", resolved_at=bars[-1].t,
                     notes=["entry never filled inside the horizon"])
    triggered_at = bars[fill_i].t

    # ── stop / target, from the fill bar on ────────────────────────────────
    hit_target_first: Optional[bool] = None
    hit_stop_first: Optional[bool] = None
    result: Optional[CallResult] = None
    resolved_i: Optional[int] = None
    stop = call.stop_px
    target = call.targets[0] if call.targets else None
    if stop is not None and target is not None:
        hit_target_first = False
        hit_stop_first = False
        for i in range(fill_i, len(bars)):
            b = bars[i]
            stop_hit = (b.low <= stop) if sign > 0 else (b.high >= stop)
            target_hit = (b.high >= target) if sign > 0 else (b.low <= target)
            if stop_hit:
                # Stop checked first: a bar that touches both on the same
                # hourly bar is the worse, unprovable-you-avoided-it outcome
                # — same conservatism as the ladder's liq-before-stop ordering.
                hit_stop_first, result, resolved_i = True, "loss", i
                break
            if target_hit:
                hit_target_first, result, resolved_i = True, "win", i
                break

    if result == "win":
        outcome_px = float(target)
    elif result == "loss":
        outcome_px = float(stop)
    else:
        outcome_px = last_close
        if window_closed:
            result, resolved_i = "expired", len(bars) - 1
        else:
            notes.append("open: neither stop nor target touched yet")

    raw_ret = outcome_px / entry - 1.0 if entry else 0.0
    ret_pct = raw_ret * sign
    seg = bars[fill_i:(resolved_i + 1) if resolved_i is not None else len(bars)]
    mae, mfe = _excursions(seg, entry, sign)

    return Grade(graded_at=now, outcome_px=outcome_px, ret_pct=ret_pct, direction_hit=ret_pct > 0.0,
                hit_target_first=hit_target_first, hit_stop_first=hit_stop_first, brier=brier,
                mae_pct=mae, mfe_pct=mfe, notes=notes, result=result, triggered_at=triggered_at,
                resolved_at=bars[resolved_i].t if resolved_i is not None else None)


def is_final(grade: Grade, thesis: Thesis, now: datetime) -> bool:
    """Whether `grade` is the last word on `thesis`: it resolved (win, loss,
    expired, untriggered) or the horizon has passed (flat calls, and the
    ungradeable no-candles case, settle at the horizon)."""
    return grade.result is not None or thesis.expires_at <= now


def _ungradeable(grade: Optional[Grade]) -> bool:
    return grade is not None and any("ungradeable" in n for n in grade.notes)


class _SweepFetcher:
    """One venue read per (coin, interval) per sweep. Every thesis asks for
    the bars back to its own created_at; the first ask for a coin fetches
    `min_count` (enough for the oldest due thesis on the desk) and later
    asks are served from that list -- `_clip` trims each to its window.
    32 pending oil calls are one Brent read, not 32."""

    def __init__(self, inner: HourlyFetcher, min_count: int) -> None:
        self._inner = inner
        self._min_count = min_count
        self._cache: Dict[Tuple[str, str], Tuple[int, List[Candle]]] = {}

    def __call__(self, coin: str, interval: str, count: int) -> Sequence[Candle]:
        key = (coin, interval)
        hit = self._cache.get(key)
        if hit is None or hit[0] < count:
            n = max(count, self._min_count)
            hit = (n, list(self._inner(coin, interval, n)))
            self._cache[key] = hit
        return hit[1]


def grade_all_due(*, hourly_fetcher: Optional[HourlyFetcher] = None,
                  now: Optional[datetime] = None, regrade: bool = False) -> int:
    """Grade every ungraded thesis across every desk that has a journal on
    disk, saving the grade once it is final (`is_final`): a resolved call
    the hour it resolves, everything else at its horizon. Idempotent — a
    thesis with .grade already set is skipped, so calling this twice in a
    row grades 0 the second time. An open, unresolved call is read but not
    saved; the next sweep reads it again.

    `regrade=True` (the 2026-11-13 backfill, `python -m services.strategies
    grade --regrade`) re-reads EVERY thesis under today's rules and
    overwrites its grade, except that a real grade is never replaced by an
    ungradeable one (no candles that far back). Returns the number of
    grades written."""
    now = now or datetime.now(timezone.utc)
    inner = hourly_fetcher or default_hourly_fetcher
    graded = 0
    for desk_id in journal.all_desk_ids():
        spec = get_spec(desk_id)
        due = [t for t in journal.list_theses(desk_id)
               if (regrade or t.grade is None) and t.created_at <= now]
        if not due:
            continue
        fetcher = _SweepFetcher(inner, max(_hours_needed(t.created_at, now) for t in due))
        for thesis in due:
            grade = grade_thesis(thesis, hourly_fetcher=fetcher, spec=spec, now=now)
            if not is_final(grade, thesis, now):
                continue
            if regrade and thesis.grade is not None and _ungradeable(grade) and not _ungradeable(thesis.grade):
                continue
            journal.save_thesis(thesis.model_copy(update={"grade": grade}))
            graded += 1
    return graded


def _stats(theses: Sequence[Thesis]) -> Dict[str, object]:
    graded = [t for t in theses if t.grade is not None]
    directions = [t.grade.direction_hit for t in graded if t.grade.direction_hit is not None]
    briers = [t.grade.brier for t in graded if t.grade.brier is not None]
    rets = [t.grade.ret_pct for t in graded]
    results: Dict[str, int] = {}
    for t in graded:
        if t.grade.result is not None:
            results[t.grade.result] = results.get(t.grade.result, 0) + 1
    return {
        "n_graded": len(graded),
        "direction_hit_rate": (sum(directions) / len(directions)) if directions else None,
        "mean_brier": (sum(briers) / len(briers)) if briers else None,
        "mean_ret_pct": (sum(rets) / len(rets)) if rets else None,
        "results": results,
    }


def aggregate_track_record(theses: Sequence[Thesis]) -> TrackRecord:
    """Overall + by-author roll-up. `by_author`'s per-author dict has the
    same keys as TrackRecord's own top-level fields (minus by_author
    itself) — see contracts/strategies/models.py's doc comment on the field.
    """
    overall = _stats(theses)
    by_author: Dict[str, Dict[str, object]] = {}
    for author in ("machine", "user"):
        subset = [t for t in theses if t.author == author]
        if subset:
            by_author[author] = _stats(subset)
    return TrackRecord(**overall, by_author=by_author)


def track_record_for_desk(desk_id: str) -> TrackRecord:
    return aggregate_track_record(journal.list_theses(desk_id))
