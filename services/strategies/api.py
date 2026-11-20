"""HTTP routes for the desks facility — every route from the bottom of
contracts/strategies/models.py, implemented against services.strategies.facade
only. No business logic lives here beyond background-job bookkeeping for
POST .../refresh: that call returns immediately with a queued RefreshJob and
runs facade.refresh() (synchronous, potentially several seconds) on a
background thread, updating an in-process job registry the client polls via
GET .../jobs/{job_id}.

HTML route (/desks/{desk_id}) reads pathiel/templates/desk.html AT REQUEST
TIME, not at import time. Missing -> a minimal inline placeholder, never a
500. The /desks grid list page (desks.html) was removed 2026-10-23 — see
desk_page()'s docstring.
"""
from __future__ import annotations

import html
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import pathiel
from contracts.strategies.models import DeskBrief, DeskCard, RefreshJob, Thesis, UserThesisIn

from services.strategies import desk_leverage, desk_log, desks, entry_card, facade, journal, levels
from services.strategies.call_line import call_line
from services.strategies import next_trade as next_trade_mod

logger = logging.getLogger(__name__)

router = APIRouter()

_TEMPLATES_DIR = Path(pathiel.__file__).parent / "templates"

# The leverage the Levels card's live-entry row is priced at: the same
# default GET /next and the Desk page's "Next trade" box start on
# (oil_desk.js DEFAULT_LEVERAGE) -- the leverage-fit rule can change the
# ACTUAL leverage used (next_trade's own "leverage" field), this is only
# the ceiling a reader has not yet picked a different rung for.
DEFAULT_ENTRY_LEVERAGE = 20.0

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


def _require_operator_or_local(request: Request) -> None:
    """Gate for the routes that WRITE desk state or spend a model call.

    Open until 2026-10-25 (security review, docs/security/SUPPLY_CHAIN_2026-10-25.md
    finding P0-1). POST /theses saves a thesis, and the newest thesis of ANY
    author is the call /api/desks/{id} and the MCP desk_brief serve to the
    trader, so an anonymous POST rewrote the live desk's call: from the
    internet on a deployment, and from any web page the operator had open
    while CORS was "*". POST /refresh spent one model call per click. The
    loop log is Claude's own trade journal. Same gate as accounts and risk:
    local mode, else the deployment's operator."""
    from services.auth.integration import require_operator_or_local
    require_operator_or_local(request)


def _unknown_desk(desk_id: str) -> HTTPException:
    known = ", ".join(desks.list_desk_ids())
    return HTTPException(status_code=404, detail=f"unknown desk '{desk_id}'; known: {known}")


# ── JSON routes ────────────────────────────────────────────────────────────

@router.get("/api/desks", response_model=List[DeskCard])
def get_desks() -> List[DeskCard]:
    return facade.list_cards()


# Registered BEFORE the "/api/desks/{desk_id}" route below: FastAPI matches
# routes in registration order, and {desk_id} is a wildcard that would
# otherwise swallow the literal path "active" as a desk id (404ing it).
@router.get("/api/desks/active")
def get_active_desks() -> Dict[str, object]:
    """Which desk(s) the landing page should render, from agent config's
    `active_desks` (default `["oil"]`). Filters out any id that isn't a
    real registered desk (a stale/typo'd config entry must not 404 the
    page); falls back to `["oil"]` if that leaves nothing. One entry -> no
    switcher; more than one -> the page shows a small tab row.

    `active_desks` and `default_desk` are kept exactly as before (an older
    cached page reads only those two). `desks` is the richer per-desk list
    a 2026-10-31+ page reads for the switcher label and the "sizes off X%
    of equity" note: id, display name, primary coin, and this desk's slice
    of equity (services/strategies/desk_share.desk_shares) -- null when no
    `desk_equity_share` is saved (the legacy whole-equity rule). A bad
    `desk_equity_share` config (DeskShareError) is reported as a top-level
    `share_error` string rather than 500ing the page; every desk's
    `equity_share` is null in that case.

    `leverage_cap` (2026-11-20) is this desk's own leverage ceiling, fully
    resolved to a number (services/strategies/desk_leverage.effective_cap):
    sp500 50x and hype 10x regardless of the account's own profile, oil (no
    entry of its own there) the account's saved risk profile cap. The Desk
    page's "Next trade" leverage picker and the call card's "the desk's
    rule" line both read this -- before it existed every desk showed oil's
    own 20x line."""
    from pathiel.agents.config_store import read_agent_config
    try:
        config = read_agent_config()
    except Exception as e:  # noqa: BLE001 - a config read miss must not 500 the page
        logger.warning(f"[strategies.api] read_agent_config() failed: {e}")
        config = {}
    configured = list(config.get("active_desks") or [])
    known = set(desks.list_desk_ids())
    active = [d for d in configured if d in known] or ["oil"]

    from services.strategies import desk_share
    share_error: Optional[str] = None
    try:
        shares = desk_share.desk_shares(config)
    except desk_share.DeskShareError as e:
        shares = {}
        share_error = str(e)

    desk_entries: List[Dict[str, object]] = []
    for d in active:
        spec = desks.require_spec(d)
        primary = next((i for i in spec.instruments if i.role == "primary"), spec.instruments[0])
        desk_entries.append({"id": d, "name": spec.name, "primary_coin": primary.coin,
                             "equity_share": shares.get(d),
                             # This desk's own leverage cap (desk_leverage.effective_cap: sp500 50x,
                             # hype 10x, oil the account's saved profile cap) -- 2026-11-20 bug report:
                             # the Desk page hardcoded 20x (oil's number) for every desk's leverage
                             # picker and call-card line, including HYPE's 10x.
                             "leverage_cap": desk_leverage.effective_cap(primary.coin, config)})

    out: Dict[str, object] = {"active_desks": active, "default_desk": active[0], "desks": desk_entries}
    if share_error:
        out["share_error"] = share_error
    return out


def _quick_markdown(brief: DeskBrief) -> Optional[str]:
    """render_markdown(thesis, analytics, "quick") when there is a thesis to
    render, else None (a desk with no call yet has nothing to render; the
    page shows its own empty state for that, same as /desks/{id})."""
    if brief.thesis is None or brief.analytics is None:
        return None
    try:
        from services.thesis.render import render_markdown
        return render_markdown(brief.thesis, brief.analytics, "quick")
    except Exception as e:  # noqa: BLE001 - a render miss must not 500 the whole brief
        logger.warning(f"[strategies.api] quick markdown render failed for {brief.spec.id}: {e}")
        return None


def _quick_fields(brief: DeskBrief) -> Optional[dict]:
    """quick_fields(thesis) for the Desk page's call card; None without a
    thesis or when the render misses (same contract as _quick_markdown)."""
    if brief.thesis is None or brief.analytics is None:
        return None
    try:
        from services.thesis.render import quick_fields
        return quick_fields(brief.thesis)
    except Exception as e:  # noqa: BLE001 - a render miss must not 500 the whole brief
        logger.warning(f"[strategies.api] quick fields render failed for {brief.spec.id}: {e}")
        return None


@router.get("/api/desks/{desk_id}", response_model=DeskBrief)
def get_desk_brief(desk_id: str) -> DeskBrief:
    try:
        brief = facade.get_brief(desk_id)
    except desks.UnknownDeskError:
        raise _unknown_desk(desk_id)
    return brief.model_copy(update={"refresh_job": running_job(desk_id),
                                    "quick_markdown": _quick_markdown(brief),
                                    "quick_fields": _quick_fields(brief)})


@router.post("/api/desks/{desk_id}/refresh", response_model=RefreshJob)
def post_refresh(request: Request, desk_id: str, body: RefreshBody = RefreshBody()) -> RefreshJob:
    _require_operator_or_local(request)
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
def post_thesis(request: Request, desk_id: str, body: UserThesisIn) -> Thesis:
    _require_operator_or_local(request)
    try:
        return facade.submit_user_thesis(desk_id, body)
    except desks.UnknownDeskError:
        raise _unknown_desk(desk_id)


# ── Desk dashboard: 20x line, candle levels, loop log ───────────────────────
#
# docs/overhaul/DESK_UI_2026-10-23.md: the product is a generic desk UI that
# expands to other desks once oil is mastered; oil is the only ACTIVE desk
# today (agent config `active_desks`, default ["oil"], see
# get_active_desks() above), not the identity. Every route below is
# desk_id-parameterized, never oil-hardcoded.

@router.get("/api/desks/{desk_id}/line")
def get_call_line(desk_id: str, leverage: float = Query(20, gt=0, le=100)) -> Dict[str, object]:
    """The 20x line for this desk's current call, recomputed at `leverage`
    (the dashboard's 5/10/20 selector). See services/strategies/call_line.py
    for the math; pathiel/agents/bracket.py for the underlying rule. Never
    500s on a desk with no thesis yet or incomplete market data — answers
    {"ok": False, "reason": "..."} instead."""
    try:
        brief = facade.get_brief(desk_id)
    except desks.UnknownDeskError:
        raise _unknown_desk(desk_id)
    if brief.thesis is None:
        return {"ok": False, "reason": "no thesis yet — call refresh_desk"}

    from pathiel.client.exchange import get_max_leverage
    coin = brief.thesis.call.instrument
    try:
        coin_max_leverage: Optional[float] = float(get_max_leverage(coin))
    except Exception as e:  # noqa: BLE001 - a venue-metadata miss must not 500 the line
        logger.warning(f"[strategies.api] get_max_leverage({coin!r}) failed: {e}")
        coin_max_leverage = None
    return call_line(brief.thesis.call, coin_max_leverage, leverage)


@router.get("/api/desks/{desk_id}/next")
def get_next_trade(desk_id: str, leverage: float = Query(20, gt=0, le=100)) -> Dict[str, object]:
    """The desk's next trade by the tick's own rules (services/strategies/
    next_trade.py): would the tick buy at the mid now, else at what price,
    with the stop, target, R and the leverage rule there. The thesis gives
    the side and the never-chase ceiling; the 24h range gives the levels.
    Read-only; answers {"ok": False, "reason"} rather than a 500."""
    try:
        brief = facade.get_brief(desk_id)
    except desks.UnknownDeskError:
        raise _unknown_desk(desk_id)
    return desk_next_trade(desk_id, leverage, brief=brief)


def desk_next_trade(desk_id: str, leverage: float, brief=None) -> Dict[str, object]:
    """Body of GET /api/desks/{id}/next, callable without HTTP (the MCP
    desk_brief shows it next to the thesis since 2026-11-02, so a thesis
    price the tick will not trade at is never mistaken for the entry).
    Raises desks.UnknownDeskError for an unknown desk."""
    if brief is None:
        brief = facade.get_brief(desk_id)
    if brief.thesis is None:
        return {"ok": False, "source": "desk_rules", "reason": "no thesis yet — call refresh_desk"}
    call = brief.thesis.call
    coin = call.instrument or _primary_coin(desk_id)
    try:
        lv = levels.summarize_coin(coin)
    except Exception as e:  # noqa: BLE001 - no live levels means no answer, never a guess
        # The exception's text stays in the log, not the answer: it can carry
        # URLs and internals (CodeQL py/stack-trace-exposure).
        logger.warning(f"[strategies.api] live levels for {coin!r} failed: {e!r}")
        return {"ok": False, "source": "desk_rules",
                "reason": f"could not read live levels for {coin} ({type(e).__name__})"}
    r24 = lv.get("range_24h") or {}
    if lv.get("mid") is None or r24.get("low") is None or r24.get("high") is None:
        return {"ok": False, "source": "desk_rules", "reason": f"not enough closed candles for {coin}'s 24h range"}
    from pathiel.client.exchange import get_max_leverage
    try:
        coin_max: Optional[float] = float(get_max_leverage(coin))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[strategies.api] get_max_leverage({coin!r}) failed: {e}")
        coin_max = None
    buf = next_trade_mod.stop_buffer_for(coin, lv.get("atr14_1h"), lv.get("mid"))
    if buf is None:
        return {"ok": False, "source": "desk_rules", "reason": f"no ATR for {coin} to size the stop buffer"}
    out = next_trade_mod.next_trade(call.side, float(lv["mid"]), float(r24["low"]), float(r24["high"]),
                                    thesis_entry=call.entry_px,
                                    # the coin's desk cap when it has one (the S&P: 50x), else as asked
                                    leverage=desk_leverage.entry_leverage(coin, leverage),
                                    coin_max_leverage=coin_max, stop_buffer=buf,
                                    leverage_fit=next_trade_mod.leverage_fit_for(coin),
                                    offset_frac=next_trade_mod.target_offset_frac_for(coin),
                                    breakout=lv.get("breakout_24h") if next_trade_mod.breakout_for(coin) else None,
                                    thesis_age_min=next_trade_mod.thesis_age_minutes(brief.thesis.created_at),
                                    entry_delay_min=next_trade_mod.entry_delay_for(coin),
                                    # 2026-11-20: the same closes_1h / trend_gate_span gap as the watcher
                                    # (scripts/trigger_watch.py's evaluate()) -- without these this route
                                    # (the Desk page and the MCP desk_brief) never applied HYPE's 120h
                                    # trend gate either.
                                    closes_1h=lv.get("closes_1h"),
                                    trend_gate_span=next_trade_mod.trend_gate_span_for(coin))
    out["coin"] = coin
    return out


def _primary_coin(desk_id: str) -> str:
    spec = desks.require_spec(desk_id)
    primary = next((i for i in spec.instruments if i.role == "primary"), spec.instruments[0])
    return primary.coin


@router.get("/api/desks/{desk_id}/levels")
def get_desk_levels(desk_id: str) -> Dict[str, object]:
    """Candle-derived levels for this desk's primary instrument: mid, last
    closed 1h candle, 8h/24h/48h range, ATR14, where the mid sits in the
    24h range, PLUS (2026-11-19) an `entry` field: the thesis's own entry,
    the live entry next_trade() will actually act on (stop/target/R/
    status), and the mid's distance to it. See services/strategies/
    levels.py::summarize_coin and services/strategies/entry_card.py."""
    try:
        coin = _primary_coin(desk_id)
    except desks.UnknownDeskError:
        raise _unknown_desk(desk_id)
    try:
        out = levels.summarize_coin(coin)
    except Exception as e:  # noqa: BLE001 - report the failure, never a 500 with no detail
        raise HTTPException(status_code=502, detail=f"could not read live levels for {coin}: {e}")
    out["entry"] = _levels_entry(desk_id, coin, out)
    return out


def _levels_entry(desk_id: str, coin: str, lv: Dict[str, object]) -> Dict[str, object]:
    """entry_card.build() for the Levels card, off the SAME next_trade()
    and event_blackout() calls GET /next and the tick use -- never a
    second, re-derived stop/target/R calculation. A thesis, venue-leverage
    or blackout read that fails degrades to entry_card's own "no thesis"/
    "waiting" shape instead of 500ing the whole Levels card (every other
    piece of it already follows that rule)."""
    try:
        brief = facade.get_brief(desk_id)
    except Exception as e:  # noqa: BLE001 - desk_id is already validated by the caller
        logger.warning(f"[strategies.api] brief read for {desk_id}'s entry card failed: {e!r}")
        return entry_card.build(mid=lv.get("mid"), atr14=lv.get("atr14_1h"), side=None,
                                thesis_entry_px=None, next_trade_result=None, blackout=None)

    side: Optional[str] = None
    thesis_entry_px: Optional[float] = None
    nt: Optional[Dict[str, object]] = None
    if brief.thesis is not None:
        call = brief.thesis.call
        side = call.side if call.side in ("long", "short") else None
        thesis_entry_px = call.entry_px
        if side:
            try:
                nt = desk_next_trade(desk_id, DEFAULT_ENTRY_LEVERAGE, brief=brief)
            except Exception as e:  # noqa: BLE001 - a next_trade miss must not 500 the Levels card
                logger.warning(f"[strategies.api] next_trade for {desk_id}'s entry card failed: {e!r}")
                nt = {"ok": False, "reason": "could not compute the live entry"}

    blackout: Optional[Dict[str, object]] = None
    if side:
        from services.strategies.event_blackout import event_blackout
        try:
            blackout = event_blackout(coin, side=side)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[strategies.api] event_blackout for {coin!r} failed: {e!r}")

    return entry_card.build(mid=lv.get("mid"), atr14=lv.get("atr14_1h"), side=side,
                            thesis_entry_px=thesis_entry_px, next_trade_result=nt, blackout=blackout)


@router.get("/api/desks/{desk_id}/log")
def get_desk_log(request: Request, desk_id: str,
                 n: int = Query(30, ge=1, le=500),
                 all_lines: bool = Query(False, alias="all")) -> Dict[str, object]:
    """The last `n` lines of this desk's loop progress log (env
    PATHIEL_<DESK>_LOG, e.g. PATHIEL_OIL_LOG, default /tmp/oil-loop/progress.log
    for desk_id "oil" and /tmp/<desk>-loop/progress.log otherwise). An empty
    `lines` list — including when the file does not exist yet, or this desk
    has no loop at all — means "no ticks yet", not an error.

    Default (`all` unset/false): only this desk's own lines (a `DESK-TICK
    <desk>` tick line or a `WATCH <desk>` watcher-wake line; see
    desk_log.filter_desk_lines) — every other desk's lines, and any line
    the shared file picked up that names no desk at all (the oil-only
    paper-breakout sim's `PAPER-A6 ...` lines, 2026-11-20 bug report), are
    dropped. `?all=1`: the raw tail exactly as written, for the Desk
    page's "show all lines" toggle."""
    _require_operator_or_local(request)
    if desks.get_spec(desk_id) is None:
        raise _unknown_desk(desk_id)
    return {"lines": desk_log.read_desk_log(desk_id, n, include_all=all_lines)}


# ── HTML routes ────────────────────────────────────────────────────────────

_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate"}


def _render_or_placeholder(filename: str, title: str, body: str) -> HTMLResponse:
    path = _TEMPLATES_DIR / filename
    if path.exists():
        # Same no-cache policy as every other dashboard page: a stale cached
        # page would pair new JS with old markup after a deploy.
        return HTMLResponse(path.read_text(encoding="utf-8"), headers=_NO_CACHE)
    title, body = html.escape(title), html.escape(body)   # desk_id reaches both (CodeQL py/reflective-xss)
    placeholder = (
        f"<!doctype html><html><head><title>{title}</title></head><body>"
        f"<h1>{title}</h1><p>{body}</p>"
        f"<p>JSON: <a href=\"/api/desks\">/api/desks</a></p></body></html>"
    )
    return HTMLResponse(placeholder, headers=_NO_CACHE)


# The /desks grid list page (desks.html) was removed 2026-10-23 — the Desk
# UI overhaul made the active desk's own thesis page ("Thesis" in the
# 3-tab nav: Desk / Thesis / Account, singular /desk/{id} to read
# distinctly from the /api/desks/... collection routes) the only desk page
# the UI ever links to; the other desk specs (btc, top5, ai, metals,
# indices) stay registered in services/strategies/desks/*.py and still
# answer at /desk/{desk_id} and /api/desks/{desk_id} for a direct link or a
# script, they are just not reachable from any nav unless marked active
# (agent config active_desks).
@router.get("/desk/{desk_id}", response_class=HTMLResponse)
def desk_page(desk_id: str) -> HTMLResponse:
    if desks.get_spec(desk_id) is None:
        raise _unknown_desk(desk_id)
    return _render_or_placeholder(
        "desk.html", f"Desk: {desk_id}",
        f"UI pending (owned by the desk-ui agent). Brief data is live at /api/desks/{desk_id}.")
