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
cached by a refresh (pipeline.load_cached_analytics / load_cached_intel) plus
the journal, no live network. `get_brief()` builds a FRESH DeskAnalytics
(live HL data, <10s for oil — see tests/test_online_oil.py) because that is
literally what "brief oil" promises; intel and thesis still come from
whatever the last refresh() cached/wrote, since fetching intel and calling
the (billable) thesis provider are refresh()'s job, not brief's.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from contracts.strategies.models import (
    Catalyst, DeskBrief, DeskCard, DeskSpec, IntelPacket, Point, RefreshJob, Thesis,
    TrackRecord, Usage, UserThesisIn,
)

from services.strategies import desks, grader, journal, pipeline


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


def get_brief(desk_id: str) -> DeskBrief:
    """Full desk brief. Builds DeskAnalytics live (see module docstring);
    raises desks.UnknownDeskError for an unregistered desk_id — the API
    layer turns that into a 404."""
    spec = desks.require_spec(desk_id)
    now = datetime.now(timezone.utc)

    result = pipeline.build_and_cache_analytics(spec)
    intel = pipeline.load_cached_intel(desk_id)
    history = journal.list_theses(desk_id)
    thesis = history[0] if history else None
    track = grader.aggregate_track_record(history)

    stale: List[str] = []
    if intel is None:
        stale.append("intel: never fetched (run refresh)")
    if thesis is None:
        stale.append("thesis: none yet (run refresh)")
    elif thesis.expires_at <= now:
        stale.append("thesis: expired")

    return DeskBrief(spec=spec, analytics=result, intel=intel, thesis=thesis,
                     history=history, track_record=track, stale=stale)


def refresh(desk_id: str, force: bool = False) -> RefreshJob:
    """Synchronous refresh: analytics -> intel -> token gate -> thesis ->
    journal. See pipeline.refresh for the full step-by-step. The HTTP layer
    (api.py) is responsible for running this off the request thread; this
    function blocks until done."""
    return pipeline.refresh(desk_id, force=force)


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
