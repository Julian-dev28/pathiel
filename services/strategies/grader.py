"""Grade expired theses, point-in-time, from hourly candles. Deterministic:
same thesis + same candles always produce the same Grade.

`grade_thesis` resolves the thesis's Call to an hourly price series — a
single coin for side in (long, short, flat), or a synthetic long-minus-short
spread series (aligned by candle timestamp, same technique as
analytics.build_pair_snapshot) when side == "pair" and the desk's PairSpec is
available. `grade_all_due` is the idempotent sweep: every desk, every
expired-and-ungraded thesis, graded once and saved back via the journal —
calling it again finds nothing left to grade and does nothing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from contracts.strategies.models import Call, CallSide, DeskSpec, Grade, Scenario, Thesis, TrackRecord
from pathiel.models.types import Candle

from services.strategies import journal
from services.strategies.desks import get_spec

HourlyFetcher = Callable[[str, str, int], Sequence[Candle]]


def default_hourly_fetcher(coin: str, interval: str, count: int) -> Sequence[Candle]:
    from pathiel.client.hl_client import fetch_hl_candles
    return fetch_hl_candles(coin, interval, count)


def _direction_sign(side: CallSide) -> Optional[float]:
    """+1 long/pair, -1 short, None for flat (no direction to grade)."""
    if side in ("long", "pair"):
        return 1.0
    if side == "short":
        return -1.0
    return None


def _hours_needed(start: datetime, end: datetime) -> int:
    span_hours = max(1, int((end - start).total_seconds() // 3600))
    return span_hours + 48  # buffer so the window's edges are covered even with gaps


def _clip(rows: List[Tuple[int, float]], start: datetime, end: datetime) -> List[Tuple[datetime, float]]:
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    return [(datetime.fromtimestamp(t / 1000.0, tz=timezone.utc), px)
           for t, px in rows if start_ms <= t <= end_ms]


def _price_series(call: Call, spec: Optional[DeskSpec], hourly_fetcher: HourlyFetcher,
                  start: datetime, end: datetime) -> List[Tuple[datetime, float]]:
    count = _hours_needed(start, end)
    pair = None
    if call.side == "pair" and spec is not None:
        pair = next((p for p in spec.pairs if p.label == call.instrument), None)

    if pair is not None:
        long_c = hourly_fetcher(pair.long, "1h", count)
        short_c = hourly_fetcher(pair.short, "1h", count)
        by_t = {c.t: c.c for c in short_c}
        rows = [(c.t, c.c - by_t[c.t]) for c in long_c if c.t in by_t]
    else:
        rows = [(c.t, c.c) for c in hourly_fetcher(call.instrument, "1h", count)]

    series = _clip(rows, start, end)
    return sorted(series, key=lambda r: r[0])


def _brier(scenarios: Sequence[Scenario], raw_final_ret: Optional[float]) -> Optional[float]:
    """Multiclass Brier score: sum((p_i - y_i)^2) over scenario buckets,
    y_i=1 for the bucket the realized raw return actually fell in (raw, not
    direction-adjusted — Scenario.move_lo_pct/move_hi_pct are price-move
    buckets per the model's own doc, matching how the thesis author wrote
    them even for a short call). Range [0, 2] for a single observation: 0 is
    a perfect call, 2 is maximally wrong (confident in the wrong bucket).
    If the realized return falls outside every bucket (an authoring gap),
    scored against the nearest bucket by distance to its closest edge.
    """
    if not scenarios or raw_final_ret is None:
        return None
    bucket = next((s for s in scenarios if s.move_lo_pct <= raw_final_ret < s.move_hi_pct), None)
    if bucket is None:
        def edge_dist(s: Scenario) -> float:
            if raw_final_ret < s.move_lo_pct:
                return s.move_lo_pct - raw_final_ret
            return raw_final_ret - s.move_hi_pct
        bucket = min(scenarios, key=edge_dist)
    return sum((s.probability - (1.0 if s is bucket else 0.0)) ** 2 for s in scenarios)


def grade_thesis(thesis: Thesis, *, hourly_fetcher: Optional[HourlyFetcher] = None,
                 spec: Optional[DeskSpec] = None, now: Optional[datetime] = None) -> Grade:
    """Point-in-time grade as of min(thesis.expires_at, now). Call this on
    theses that have actually expired; grading one early just means `end`
    silently equals `now` and you get a mid-flight read, not a final one."""
    hourly_fetcher = hourly_fetcher or default_hourly_fetcher
    now = now or datetime.now(timezone.utc)
    end = min(thesis.expires_at, now)

    series = _price_series(thesis.call, spec, hourly_fetcher, thesis.created_at, end)
    sign = _direction_sign(thesis.call.side)

    if not series:
        return Grade(graded_at=now, outcome_px=thesis.call.entry_px or 0.0, ret_pct=0.0,
                     direction_hit=None, hit_target_first=None, hit_stop_first=None,
                     brier=None, mae_pct=0.0, mfe_pct=0.0,
                     notes=["no candle data available in [created_at, expires_at]; ungradeable"])

    entry = thesis.call.entry_px if thesis.call.entry_px else series[0][1]
    outcome_px = series[-1][1]
    raw_final_ret = outcome_px / entry - 1.0 if entry else 0.0
    ret_pct = raw_final_ret * sign if sign is not None else 0.0
    direction_hit = (ret_pct > 0.0) if sign is not None else None

    path_rets = [((px / entry - 1.0) * (sign if sign is not None else 1.0)) for _, px in series] \
        if entry else [0.0]
    mfe_pct = max(path_rets)
    mae_pct = min(path_rets)

    hit_target_first: Optional[bool] = None
    hit_stop_first: Optional[bool] = None
    if sign is not None and thesis.call.stop_px is not None and thesis.call.targets:
        target = thesis.call.targets[0]
        stop = thesis.call.stop_px
        hit_target_first = False
        hit_stop_first = False
        for _, px in series:
            target_hit = (px >= target) if sign > 0 else (px <= target)
            stop_hit = (px <= stop) if sign > 0 else (px >= stop)
            if stop_hit:
                # Stop checked first: a bar that crosses both on the same hourly
                # bar is the worse, unprovable-you-avoided-it outcome — same
                # conservatism as the ladder's liq-before-stop ordering.
                hit_stop_first = True
                break
            if target_hit:
                hit_target_first = True
                break

    brier = _brier(thesis.scenarios, raw_final_ret) if thesis.scenarios else None

    return Grade(graded_at=now, outcome_px=outcome_px, ret_pct=ret_pct, direction_hit=direction_hit,
                hit_target_first=hit_target_first, hit_stop_first=hit_stop_first, brier=brier,
                mae_pct=mae_pct, mfe_pct=mfe_pct, notes=[])


def grade_all_due(*, hourly_fetcher: Optional[HourlyFetcher] = None,
                  now: Optional[datetime] = None) -> int:
    """Grade every expired, ungraded thesis across every desk that has a
    journal on disk. Idempotent — a thesis with .grade already set is
    skipped, so calling this twice in a row grades 0 the second time."""
    now = now or datetime.now(timezone.utc)
    graded = 0
    for desk_id in journal.all_desk_ids():
        spec = get_spec(desk_id)
        for thesis in journal.list_theses(desk_id):
            if thesis.grade is not None or thesis.expires_at > now:
                continue
            grade = grade_thesis(thesis, hourly_fetcher=hourly_fetcher, spec=spec, now=now)
            journal.save_thesis(thesis.model_copy(update={"grade": grade}))
            graded += 1
    return graded


def _stats(theses: Sequence[Thesis]) -> Dict[str, Optional[float]]:
    graded = [t for t in theses if t.grade is not None]
    directions = [t.grade.direction_hit for t in graded if t.grade.direction_hit is not None]
    briers = [t.grade.brier for t in graded if t.grade.brier is not None]
    rets = [t.grade.ret_pct for t in graded]
    return {
        "n_graded": len(graded),
        "direction_hit_rate": (sum(directions) / len(directions)) if directions else None,
        "mean_brier": (sum(briers) / len(briers)) if briers else None,
        "mean_ret_pct": (sum(rets) / len(rets)) if rets else None,
    }


def aggregate_track_record(theses: Sequence[Thesis]) -> TrackRecord:
    """Overall + by-author roll-up. `by_author`'s per-author dict has the
    same keys as TrackRecord's own top-level fields (minus by_author
    itself) — see contracts/strategies/models.py's doc comment on the field.
    """
    overall = _stats(theses)
    by_author: Dict[str, Dict[str, Optional[float]]] = {}
    for author in ("machine", "user"):
        subset = [t for t in theses if t.author == author]
        if subset:
            by_author[author] = _stats(subset)
    return TrackRecord(**overall, by_author=by_author)


def track_record_for_desk(desk_id: str) -> TrackRecord:
    return aggregate_track_record(journal.list_theses(desk_id))
