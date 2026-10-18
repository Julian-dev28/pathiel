"""The desk-core facade — the ONLY surface other agents should import. Every
function here has a stable signature other services (desk-ui, and anyone
else) code against directly:

    list_cards() -> list[DeskCard]
    get_brief(desk_id) -> DeskBrief
    refresh(desk_id, force=False) -> RefreshJob          (synchronous)
    submit_user_thesis(desk_id, body: UserThesisIn) -> Thesis
    track_record(desk_id) -> TrackRecord
    grade_due() -> int

`list_cards()` is cheap: it reads whatever analytics/intel/thesis were last
cached (pipeline.load_cached_analytics / load_cached_intel) plus the journal,
no live network on the calling thread.

`get_brief()` is stale-while-revalidate. Measured 2026-10-18 against the
dashboard server's Hyperliquid budget, a live rebuild on every page view took
22s to 122s per desk. Now:

    cache younger than max_age_s      -> served as-is
    older, and wait=False (the web)   -> served at once, rebuilt by the
                                         background worker, brief.refreshing=True
    no cache, or wait=True (MCP, CLI) -> rebuilt inline (fast: candles come
                                         from services/strategies/candle_store)

Rebuilds run on ONE background worker, one desk at a time, so six stale
desks never stampede the rate budget. Intel and thesis still come from
whatever the last refresh() cached/wrote: fetching intel and calling the
(billable) thesis provider are refresh()'s job, not brief's.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable, Deque, List, Optional, Tuple

from contracts.strategies.models import (
    Catalyst, DeskAnalytics, DeskBrief, DeskCard, DeskSpec, IntelPacket, Point, RefreshJob,
    Thesis, TrackRecord, Usage, UserThesisIn,
)

from services.strategies import desks, grader, journal, pipeline

logger = logging.getLogger(__name__)

# ── analytics freshness ─────────────────────────────────────────────────────

# The dashboard server shares ~2 weight/s of Hyperliquid budget with every
# other page; one full rebuild of all six desks is ~27 candle requests (~540
# weight, ~4.5 min of that budget). Hence: a brief older than 15 min is
# served and rebuilt in the background, the overview only nudges desks older
# than an hour, and startup warms only desks with NO cache at all.
BRIEF_MAX_AGE_S = 900.0     # a brief serves cached analytics this young without a rebuild
CARD_MAX_AGE_S = 3600.0     # the overview queues a background rebuild past this age
STALE_AFTER_S = 3 * 3600.0  # past this age the brief says so in `stale`, not just `refreshing`


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _background_enabled() -> bool:
    """PATHIEL_DESK_BACKGROUND_REFRESH=0 turns the worker off (the test
    session does, so no gate test ever starts a thread that hits the venue)."""
    return os.environ.get("PATHIEL_DESK_BACKGROUND_REFRESH", "1") != "0"


# One worker drains a queue of desk ids, oldest first, urgent (a brief
# someone is looking at) jumps the line. _start_worker is a seam: tests
# replace it with `lambda fn: fn()` to drain synchronously.
_LOCK = threading.Lock()
_PENDING: Deque[str] = deque()
_ACTIVE: Optional[str] = None
_WORKER_RUNNING = False


def _start_worker(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="desk-analytics", daemon=True).start()


def _drain() -> None:
    global _ACTIVE, _WORKER_RUNNING
    while True:
        with _LOCK:
            if not _PENDING:
                _ACTIVE = None
                _WORKER_RUNNING = False
                return
            desk_id = _PENDING.popleft()
            _ACTIVE = desk_id
        spec = desks.get_spec(desk_id)
        if spec is None:
            continue
        t0 = time.monotonic()
        try:
            pipeline.build_and_cache_analytics(spec)
            logger.info(f"[strategies.facade] {desk_id}: analytics rebuilt in the background "
                        f"in {time.monotonic() - t0:.1f}s")
        except Exception as exc:  # noqa: BLE001 - one desk failing must not stop the queue
            logger.warning(f"[strategies.facade] {desk_id}: background analytics rebuild failed "
                           f"after {time.monotonic() - t0:.1f}s: {exc}")


def revalidate(desk_id: str, *, urgent: bool = False) -> bool:
    """Queue a background analytics rebuild for one desk. Idempotent: a desk
    already queued or being rebuilt is not queued twice (urgent moves it to
    the front). Returns True when a rebuild for this desk is queued or
    running after the call, False when the worker is disabled."""
    global _WORKER_RUNNING
    if not _background_enabled():
        return False
    start = False
    with _LOCK:
        if desk_id != _ACTIVE:
            if desk_id in _PENDING:
                if urgent:
                    _PENDING.remove(desk_id)
                    _PENDING.appendleft(desk_id)
            elif urgent:
                _PENDING.appendleft(desk_id)
            else:
                _PENDING.append(desk_id)
        if not _WORKER_RUNNING:
            _WORKER_RUNNING = True
            start = True
    if start:
        _start_worker(_drain)
    return True


def is_refreshing(desk_id: str) -> bool:
    with _LOCK:
        return desk_id == _ACTIVE or desk_id in _PENDING


def warm_all(*, max_age_s: Optional[float] = None, only_missing: bool = False) -> List[str]:
    """Queue a rebuild for every desk whose analytics cache is missing or
    (unless only_missing) older than `max_age_s`, in registry order (oil
    first). Returns the queued desk ids."""
    max_age = _env_float("PATHIEL_DESK_BRIEF_MAX_AGE_S", BRIEF_MAX_AGE_S) if max_age_s is None else max_age_s
    queued = []
    for spec in desks.list_specs():
        age = pipeline.cached_analytics_age_s(spec.id)
        if age is None or (not only_missing and age > max_age):
            if revalidate(spec.id):
                queued.append(spec.id)
    return queued


def start_warmup() -> List[str]:
    """Server-startup hook: build analytics for desks that have NONE yet, in
    the background, so no first page view waits on a cold build. Desks with
    an old cache are left to stale-while-revalidate: rebuilding all six on
    every restart spent ~4.5 min of the server's venue budget and made every
    other page wait behind it. PATHIEL_DESK_WARMUP=0 skips it."""
    if os.environ.get("PATHIEL_DESK_WARMUP", "1") == "0":
        return []
    queued = warm_all(only_missing=True)
    if queued:
        logger.info(f"[strategies.facade] warming desk analytics in the background: {queued}")
    return queued


def _next_catalyst(intel: Optional[IntelPacket], now: datetime) -> Optional[Catalyst]:
    if not intel or not intel.catalysts:
        return None
    upcoming = [c for c in intel.catalysts if c.at > now]
    return min(upcoming, key=lambda c: c.at) if upcoming else None


def _primary_instrument(spec: DeskSpec):
    return next((i for i in spec.instruments if i.role == "primary"), spec.instruments[0])


def list_cards() -> List[DeskCard]:
    """One DeskCard per registered desk, from cached state only — safe to
    call often (an overview page polling this should never trigger a live
    HL read)."""
    now = datetime.now(timezone.utc)
    cards: List[DeskCard] = []
    for spec in desks.list_specs():
        age = pipeline.cached_analytics_age_s(spec.id)
        if age is None or age > _env_float("PATHIEL_DESK_CARD_MAX_AGE_S", CARD_MAX_AGE_S):
            revalidate(spec.id)   # background, single worker; this call never waits on it
        result = pipeline.load_cached_analytics(spec.id)
        intel = pipeline.load_cached_intel(spec.id)
        thesis = journal.latest_thesis(spec.id)
        track = grader.track_record_for_desk(spec.id)
        primary = _primary_instrument(spec)
        snap = next((s for s in result.instruments if s.coin == primary.coin), None) if result else None

        cards.append(DeskCard(
            id=spec.id, name=spec.name, tagline=spec.tagline,
            primary_coin=primary.coin, primary_label=primary.label,
            mid=snap.mid if snap else None, ret_1d=snap.ret_1d if snap else None,
            one_liner=thesis.call.one_liner if thesis else None,
            call_side=thesis.call.side if thesis else None,
            thesis_age_min=((now - thesis.created_at).total_seconds() / 60.0) if thesis else None,
            next_catalyst=_next_catalyst(intel, now),
            track_record=track,
        ))
    return cards


def _analytics_for_brief(spec: DeskSpec, max_age_s: float,
                         wait: bool) -> Tuple[Optional[DeskAnalytics], bool, List[str]]:
    """(analytics, refreshing, stale notes) per the module docstring's table."""
    notes: List[str] = []
    age = pipeline.cached_analytics_age_s(spec.id)
    cached = pipeline.load_cached_analytics(spec.id) if age is not None else None
    if cached is not None and age <= max_age_s:
        return cached, is_refreshing(spec.id), notes
    if cached is not None and not wait:
        refreshing = revalidate(spec.id, urgent=True)
        if age > STALE_AFTER_S:
            notes.append(f"analytics: {age / 60:.0f} min old")
        return cached, refreshing, notes
    try:
        return pipeline.build_and_cache_analytics(spec), False, notes
    except Exception as exc:  # noqa: BLE001 - serve what we have rather than fail the brief
        logger.warning(f"[strategies.facade] {spec.id}: analytics rebuild failed: {exc}")
        if cached is not None:
            notes.append(f"analytics: rebuild failed, showing a {age / 60:.0f} min old copy")
            return cached, False, notes
        notes.append("analytics: unavailable right now (venue did not answer)")
        return None, False, notes


def get_brief(desk_id: str, *, max_age_s: Optional[float] = None, wait: bool = False) -> DeskBrief:
    """Full desk brief, stale-while-revalidate (see module docstring).
    `wait=True` rebuilds inline instead of serving an old copy, for callers
    with no page to update later (MCP, CLI). Raises desks.UnknownDeskError
    for an unregistered desk_id; the API layer turns that into a 404."""
    spec = desks.require_spec(desk_id)
    now = datetime.now(timezone.utc)
    max_age = _env_float("PATHIEL_DESK_BRIEF_MAX_AGE_S", BRIEF_MAX_AGE_S) if max_age_s is None else max_age_s

    result, refreshing, stale = _analytics_for_brief(spec, max_age, wait)
    intel = pipeline.load_cached_intel(desk_id)
    history = journal.list_theses(desk_id)
    thesis = history[0] if history else None
    track = grader.aggregate_track_record(history)

    if intel is None:
        stale.append("intel: never fetched (run refresh)")
    if thesis is None:
        stale.append("thesis: none yet (run refresh)")
    elif thesis.expires_at <= now:
        stale.append("thesis: expired")

    return DeskBrief(spec=spec, analytics=result, intel=intel, thesis=thesis,
                     history=history, track_record=track, stale=stale, refreshing=refreshing)


def refresh(desk_id: str, force: bool = False,
            on_stage: Optional[Callable[[str], None]] = None) -> RefreshJob:
    """Synchronous refresh: analytics -> intel -> token gate -> thesis ->
    journal. See pipeline.refresh for the full step-by-step. The HTTP layer
    (api.py) is responsible for running this off the request thread; this
    function blocks until done. `on_stage` gets a plain-English label as
    each step starts (pipeline.STAGE_*)."""
    return pipeline.refresh(desk_id, force=force, on_stage=on_stage)


def submit_user_thesis(desk_id: str, body: UserThesisIn) -> Thesis:
    """Julien's own call, journaled alongside the machine's so the track
    record can compare them. `body.notes` (free text, no field of its own on
    Thesis) is folded into `bull` as a single labeled Point — the closest
    honest fit in the contract; see services/strategies/README.md."""
    spec = desks.require_spec(desk_id)
    now = datetime.now(timezone.utc)
    bull = [Point(text=f"user note: {body.notes}")] if body.notes else []
    thesis = Thesis(
        id=f"{desk_id}-{now:%Y%m%dT%H%M%S}-u-{uuid.uuid4().hex[:6]}",
        desk_id=desk_id, author="user", created_at=now,
        expires_at=now + timedelta(days=spec.horizon_days), horizon_days=spec.horizon_days,
        call=body.call, scenarios=body.scenarios, bull=bull,
        usage=Usage(skipped_reason="user_submitted"),
    )
    journal.save_thesis(thesis)
    return thesis


def track_record(desk_id: str) -> TrackRecord:
    return grader.track_record_for_desk(desk_id)


def grade_due() -> int:
    """Grade every expired, ungraded thesis across every desk. Idempotent —
    see grader.grade_all_due."""
    return grader.grade_all_due()
