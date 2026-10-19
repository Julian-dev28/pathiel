"""HTTP routes for the desks facility — every route from the bottom of
contracts/strategies/models.py, implemented against services.strategies.facade
only. No business logic lives here beyond background-job bookkeeping for
POST .../refresh: that call returns immediately with a queued RefreshJob and
runs facade.refresh() (synchronous, potentially several seconds) on a
background thread, updating an in-process job registry the client polls via
GET .../jobs/{job_id}.

HTML routes (/desks, /desks/{desk_id}) read pathiel/templates/desks.html and
desk.html AT REQUEST TIME, not at import time — those files are the desk-ui
agent's to write and may not exist yet in this worktree. Missing -> a
minimal inline placeholder, never a 500.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import pathiel
from contracts.strategies.models import DeskBrief, DeskCard, RefreshJob, Thesis, UserThesisIn

from services.strategies import desks, facade, journal

logger = logging.getLogger(__name__)

router = APIRouter()

_TEMPLATES_DIR = Path(pathiel.__file__).parent / "templates"

# In-process background-job registry for POST /refresh. One FastAPI process
# per SWARM_RULES (dev servers are single-port, single-process), so a plain
# dict + lock is the right amount of machinery — no external queue needed.
_JOBS: Dict[str, RefreshJob] = {}
_JOBS_LOCK = threading.Lock()
# A finished job is kept this long so a polling page (desks.js follows one for
# up to 10 minutes) and a reload still find its outcome, then dropped: without
# this the registry grew by one entry per click for the life of the process.
_FINISHED_JOB_TTL_S = 3600.0


def _prune_finished_jobs(now: datetime) -> None:
    """Drop finished jobs older than _FINISHED_JOB_TTL_S. Caller holds _JOBS_LOCK."""
    def age_s(job: RefreshJob) -> float:
        at = job.finished_at or job.started_at
        if at.tzinfo is None:            # a naive stamp must not crash every later click
            at = at.replace(tzinfo=timezone.utc)
        return (now - at).total_seconds()

    for job_id in [k for k, j in _JOBS.items()
                   if j.state not in ("queued", "running") and age_s(j) > _FINISHED_JOB_TTL_S]:
        del _JOBS[job_id]


def running_job(desk_id: str) -> Optional[RefreshJob]:
    """The refresh queued or running for this desk, if any. At most one: a
    second click (or a reloaded page clicking again) joins it rather than
    starting another market read and another model call."""
    with _JOBS_LOCK:
        live = [j for j in _JOBS.values()
                if j.desk_id == desk_id and j.state in ("queued", "running")]
    return max(live, key=lambda j: j.started_at) if live else None


class RefreshBody(BaseModel):
    force: bool = False


def _unknown_desk(desk_id: str) -> HTTPException:
    known = ", ".join(desks.list_desk_ids())
    return HTTPException(status_code=404, detail=f"unknown desk '{desk_id}'; known: {known}")


# ── JSON routes ────────────────────────────────────────────────────────────

@router.get("/api/desks", response_model=List[DeskCard])
def get_desks() -> List[DeskCard]:
    return facade.list_cards()


@router.get("/api/desks/{desk_id}", response_model=DeskBrief)
def get_desk_brief(desk_id: str) -> DeskBrief:
    try:
        brief = facade.get_brief(desk_id)
    except desks.UnknownDeskError:
        raise _unknown_desk(desk_id)
    return brief.model_copy(update={"refresh_job": running_job(desk_id)})


@router.post("/api/desks/{desk_id}/refresh", response_model=RefreshJob)
def post_refresh(desk_id: str, body: RefreshBody = RefreshBody()) -> RefreshJob:
    if desks.get_spec(desk_id) is None:
        raise _unknown_desk(desk_id)

    job_id = uuid.uuid4().hex
    started = datetime.now(timezone.utc)
    job = RefreshJob(id=job_id, desk_id=desk_id, state="queued", started_at=started)
    with _JOBS_LOCK:
        _prune_finished_jobs(started)
        # One refresh per desk at a time; check and register under one lock
        # so two clicks a millisecond apart cannot both start one.
        live = [j for j in _JOBS.values()
                if j.desk_id == desk_id and j.state in ("queued", "running")]
        if live:
            return max(live, key=lambda j: j.started_at)
        _JOBS[job_id] = job

    def _stage(label: str) -> None:
        with _JOBS_LOCK:
            current = _JOBS.get(job_id)
            if current is not None and current.state == "running":
                _JOBS[job_id] = current.model_copy(update={"message": label})

    def _run() -> None:
        with _JOBS_LOCK:
            _JOBS[job_id] = job.model_copy(update={"state": "running"})
        logger.info(f"[strategies.api] {desk_id}: refresh started (force={body.force})")
        try:
            result = facade.refresh(desk_id, force=body.force, on_stage=_stage)
            with _JOBS_LOCK:
                _JOBS[job_id] = result.model_copy(update={"id": job_id, "started_at": started})
            # The trace a "nothing happened" report needs (2026-10-19: a BTC
            # refresh left no line anywhere saying whether it ran).
            took = (datetime.now(timezone.utc) - started).total_seconds()
            logger.info(f"[strategies.api] {desk_id}: refresh {result.state} in {took:.0f}s: "
                        f"{result.message}")
        except Exception as exc:  # noqa: BLE001 - record the crash on the job, don't just vanish it
            logger.exception(f"[strategies.api] background refresh crashed for {desk_id}")
            with _JOBS_LOCK:
                _JOBS[job_id] = job.model_copy(update={
                    "state": "error", "message": str(exc), "finished_at": datetime.now(timezone.utc),
                })

    threading.Thread(target=_run, name=f"desk-refresh-{desk_id}", daemon=True).start()
    return job


@router.get("/api/desks/jobs/{job_id}", response_model=RefreshJob)
def get_job(job_id: str) -> RefreshJob:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job '{job_id}'")
    return job


@router.get("/api/desks/{desk_id}/theses", response_model=List[Thesis])
def get_theses(desk_id: str) -> List[Thesis]:
    if desks.get_spec(desk_id) is None:
        raise _unknown_desk(desk_id)
    return journal.list_theses(desk_id)


@router.post("/api/desks/{desk_id}/theses", response_model=Thesis)
def post_thesis(desk_id: str, body: UserThesisIn) -> Thesis:
    try:
        return facade.submit_user_thesis(desk_id, body)
    except desks.UnknownDeskError:
        raise _unknown_desk(desk_id)


# ── HTML routes ────────────────────────────────────────────────────────────

_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate"}


def _render_or_placeholder(filename: str, title: str, body: str) -> HTMLResponse:
    path = _TEMPLATES_DIR / filename
    if path.exists():
        # Same no-cache policy as every other dashboard page: a stale cached
        # page would pair new JS with old markup after a deploy.
        return HTMLResponse(path.read_text(encoding="utf-8"), headers=_NO_CACHE)
    placeholder = (
        f"<!doctype html><html><head><title>{title}</title></head><body>"
        f"<h1>{title}</h1><p>{body}</p>"
        f"<p>JSON: <a href=\"/api/desks\">/api/desks</a></p></body></html>"
    )
    return HTMLResponse(placeholder, headers=_NO_CACHE)


@router.get("/desks", response_class=HTMLResponse)
def desks_page() -> HTMLResponse:
    return _render_or_placeholder(
        "desks.html", "Desks",
        "UI pending (owned by the desk-ui agent). Overview data is live at /api/desks.")


@router.get("/desks/{desk_id}", response_class=HTMLResponse)
def desk_page(desk_id: str) -> HTMLResponse:
    if desks.get_spec(desk_id) is None:
        raise _unknown_desk(desk_id)
    return _render_or_placeholder(
        "desk.html", f"Desk: {desk_id}",
        f"UI pending (owned by the desk-ui agent). Brief data is live at /api/desks/{desk_id}.")
