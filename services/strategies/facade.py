"""The desk-core facade — the ONLY surface other agents should import. Every
function here has a stable signature other services (desk-ui, and anyone
else) code against directly:

    list_cards() -> list[DeskCard]
    get_brief(desk_id) -> DeskBrief
    refresh(desk_id, force=False) -> RefreshJob          (synchronous)
    submit_user_thesis(desk_id, body: UserThesisIn) -> Thesis
    track_record(desk_id) -> TrackRecord          (thesis grading + trade grading, combined)
    grade_due() -> int                            (thesis grading only)
    grade_due_trades(desk_id=None) -> int          (trade grading only; run AFTER grade_due())

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

from pathiel.client.rate_limit import background_priority
from services.strategies import desks, grader, journal, pipeline, trades as trade_grading

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
            # Background priority: a page view or a clicked refresh goes
            # first (2026-10-19: a clicked BTC refresh sat behind this queue).
            with background_priority():
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
    """Queue a rebuild for every desk whose analytics cache is missing, was
    built for other primaries (pipeline.load_cached_analytics treats that as
    missing), or (unless only_missing) is older than `max_age_s`, in
    registry order (oil first). Returns the queued desk ids."""
    max_age = _env_float("PATHIEL_DESK_BRIEF_MAX_AGE_S", BRIEF_MAX_AGE_S) if max_age_s is None else max_age_s
    queued = []
    for spec in desks.list_specs():
        age = pipeline.cached_analytics_age_s(spec.id)
        missing = age is None or pipeline.load_cached_analytics(spec.id) is None
        if missing or (not only_missing and age > max_age):
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
        result = pipeline.load_cached_analytics(spec.id)   # None if built for other primaries
        if result is None or age is None or age > _env_float("PATHIEL_DESK_CARD_MAX_AGE_S", CARD_MAX_AGE_S):
            revalidate(spec.id)   # background, single worker; this call never waits on it
        intel = pipeline.load_cached_intel(spec.id)
        thesis = journal.latest_thesis(spec.id)
        track = trade_grading.track_record_for_desk(spec.id)
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
    if age is None and not wait:
        # A desk with NO cache file at all yet — never built, ever, on this
        # state dir — used to fall through to the inline build below
        # regardless of `wait`, on the theory that it is "fast: candles come
        # from services/strategies/candle_store" (this function's own
        # module docstring). Measured against a cold candle_store 2026-10-23
        # (docs/overhaul/DESK_UI_2026-10-23.md, Polish section) that is not
        # true the first time any desk is ever opened, or right after a
        # restart before start_warmup()'s background pass finishes: ai took
        # 24s, top5 took 9s, oil/gas/btc 0.1-0.5s — and the web route
        # (services/strategies/api.py's get_desk_brief/get_call_line) calls
        # get_brief() with wait=False, so that whole build ran synchronously
        # inside the one request a browser's Thesis-page fetch was waiting
        # on: a blank page for as long as the venue took to answer every
        # instrument. `wait=True` (MCP, CLI — scripts/pathiel-mcp-server.py,
        # services/strategies/__main__.py) has no page coming back later to
        # update, so it still builds inline below; the web path never blocks
        # on a desk's FIRST build again. A cache that exists but was built
        # for a different primary instrument (age is not None, cached is
        # None) is a narrower, rarer case — actively wrong data, not merely
        # absent — and keeps the inline rebuild below so a stale wrong-coin
        # ladder is never served even once.
        refreshing = revalidate(spec.id, urgent=True)
        notes.append("analytics: first build queued in the background — reload in a few seconds"
                     if refreshing else "analytics: unavailable right now (background refresh is off)")
        return None, refreshing, notes
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
    track = trade_grading.enrich_track_record(grader.aggregate_track_record(history), desk_id)

    if intel is None:
        stale.append("intel: never fetched (run refresh)")
    elif intel.sources_failed:
        # A missing free API key (EIA_API_KEY/FRED_API_KEY/CONGRESS_API_KEY)
        # skips its source cleanly and lands here as "...API_KEY_missing" —
        # visible before this fix only inside intel.sources_failed itself,
        # never in the one list __main__.py's CLI and the MCP brief both
        # print. See scripts/intel_keys.py for which keys are missing and
        # where to get each.
        missing_keys = [f for f in intel.sources_failed if "API_KEY_missing" in f]
        if missing_keys:
            stale.append(f"intel: missing API key(s) — {', '.join(missing_keys)} "
                         f"(run scripts/intel_keys.py)")
        other = [f for f in intel.sources_failed if f not in missing_keys]
        if other:
            stale.append(f"intel: {len(other)} source(s) failed this refresh "
                         f"(see intel.sources_failed)")
    if thesis is None:
        stale.append("thesis: none yet (run refresh)")
    elif thesis.expires_at <= now:
        stale.append("thesis: expired")
    elif thesis.author == "machine" and pipeline.primary_changed(spec, thesis):
        was = ", ".join(thesis.primary_coins) or thesis.call.instrument
        stale.append(f"thesis: written for {was}; this desk now prices off "
                     f"{', '.join(pipeline.primary_coins(spec))} (run refresh)")

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
    return trade_grading.track_record_for_desk(desk_id)


def grade_due() -> int:
    """Grade every expired, ungraded thesis across every desk. Idempotent —
    see grader.grade_all_due. Thesis grading only; call grade_due_trades()
    after this (same sweep) so a thesis that just expired is graded before
    its trades are computed — the server's background job and the
    desk_grade MCP tool both do this in that order."""
    return grader.grade_all_due()


def grade_due_trades(desk_id: Optional[str] = None) -> int:
    """Grade every registered account's trades against every graded-or-
    expired thesis that has no trades computed yet, across every desk (or
    just `desk_id`). Idempotent — see services.strategies.trades.grade_due."""
    return trade_grading.grade_due(desk_id=desk_id)
