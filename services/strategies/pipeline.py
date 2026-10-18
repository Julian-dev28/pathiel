"""The refresh pipeline: analytics -> intel -> token gate -> thesis -> journal.

    refresh(desk_id, force=False) -> RefreshJob

Analytics is always rebuilt (it's cheap-ish and deterministic — no reason to
serve a stale read). Intel and thesis are resolved by IMPORT PATH
(`services.intel.api:build_packet`, `services.thesis.api:write_thesis`) so
this module has zero hard import-time dependency on either service — both
are being built in parallel by other agents and may not exist yet in this
worktree. A missing or failing provider degrades the refresh (state="done",
message explains what was skipped) rather than raising; only a failure in
THIS service's own analytics is treated as a hard "error" RefreshJob.

`needs_rewrite()` is the token gate: skip the (billable, slow) thesis
provider unless something actually changed since the last machine thesis.
A change of a desk's primary instrument (oil: WTI to Brent, 2026-10-19) is
one of those changes: the cached read and the last thesis were priced off
the old primary, so neither is served or built on (`analytics_matches_spec`,
`primary_changed`).
Every refresh — written, skipped, or errored — appends one row to
`$PATHIEL_STATE_DIR/strategies/usage.jsonl`, so cost per thesis is visible.
"""
from __future__ import annotations

import importlib
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional, Tuple

from contracts.strategies.models import (
    DeskAnalytics, DeskSpec, IntelPacket, RefreshJob, Thesis, Usage,
)
from pathiel.agents.atomic_io import append_json_line, read_json, write_json_atomic

from services.strategies import analytics as analytics_mod
from services.strategies import journal
from services.strategies.desks import get_spec

logger = logging.getLogger(__name__)

INTEL_PROVIDER_PATH = "services.intel.api:build_packet"
THESIS_PROVIDER_PATH = "services.thesis.api:write_thesis"

_FALLBACK_STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".state")


def _state_dir() -> str:
    return os.environ.get("PATHIEL_STATE_DIR") or _FALLBACK_STATE_DIR


def _desk_dir(desk_id: str) -> str:
    return os.path.join(_state_dir(), "strategies", desk_id)


def _analytics_cache_path(desk_id: str) -> str:
    return os.path.join(_desk_dir(desk_id), "analytics", "latest.json")


def _intel_cache_path(desk_id: str) -> str:
    return os.path.join(_desk_dir(desk_id), "intel", "latest.json")


def _usage_log_path() -> str:
    return os.path.join(_state_dir(), "strategies", "usage.jsonl")


# ── desk primaries: what a cached read or a thesis was priced off ──────────

def primary_coins(spec: DeskSpec) -> List[str]:
    """The desk's primary coins, in spec order: the instruments the leverage
    ladder and the forward stats are built for, and the call is priced off."""
    return [i.coin for i in spec.instruments if i.role == "primary"]


def analytics_matches_spec(spec: DeskSpec, result: DeskAnalytics) -> bool:
    """False when `result` was built while the desk had other primaries.
    Ladder rungs and forward stats are only ever built for primaries, so a
    rung or forward row on a coin that is not a primary now proves the read
    is from before the change. A primary ADDED since is not detected (every
    old row is still a primary): that read is incomplete until the next
    rebuild, not wrong."""
    built = {r.coin for r in result.ladder} | {f.coin for f in result.forward}
    return built <= set(primary_coins(spec))


def primary_changed(spec: DeskSpec, prev: Thesis) -> bool:
    """Whether `prev` was written while the desk had other primaries.
    Theses written since 2026-10-19 carry the answer (primary_coins). Older
    ones do not, so for those a long or short call on a coin that is not a
    primary now is read as written for the old desk. That can misfire once,
    on an old call that chose a secondary coin on purpose; the rewrite is
    stamped, so it never fires twice for the same desk."""
    now = primary_coins(spec)
    if prev.primary_coins:
        return sorted(prev.primary_coins) != sorted(now)
    return prev.call.side in ("long", "short") and prev.call.instrument not in now


# ── analytics / intel cache (read by facade.get_brief / list_cards) ─────────

def save_analytics(desk_id: str, result: DeskAnalytics) -> None:
    write_json_atomic(_analytics_cache_path(desk_id), result.model_dump(mode="json"))


def load_cached_analytics(desk_id: str) -> Optional[DeskAnalytics]:
    """The cached read, or None when there is none or it was built for other
    primaries than the desk has now: that cache counts as no cache, so it is
    rebuilt rather than served with the old primary's ladder."""
    raw = read_json(_analytics_cache_path(desk_id))
    if not raw:
        return None
    result = DeskAnalytics.model_validate(raw)
    spec = get_spec(desk_id)
    if spec is not None and not analytics_matches_spec(spec, result):
        logger.info(f"[strategies.pipeline] {desk_id}: cached analytics were built for other "
                    f"primaries than {primary_coins(spec)}; treating them as missing")
        return None
    return result


def cached_analytics_age_s(desk_id: str, *, now: Optional[float] = None) -> Optional[float]:
    """Seconds since the analytics cache was last WRITTEN (file mtime), or
    None when there is none. The write time, not DeskAnalytics.as_of, is
    what says how long ago this process last paid for a rebuild."""
    try:
        mtime = os.path.getmtime(_analytics_cache_path(desk_id))
    except OSError:
        return None
    return max(0.0, (time.time() if now is None else now) - mtime)


def save_intel(desk_id: str, packet: IntelPacket) -> None:
    write_json_atomic(_intel_cache_path(desk_id), packet.model_dump(mode="json"))


def load_cached_intel(desk_id: str) -> Optional[IntelPacket]:
    raw = read_json(_intel_cache_path(desk_id))
    return IntelPacket.model_validate(raw) if raw else None


def build_and_cache_analytics(spec: DeskSpec, **kwargs: Any) -> DeskAnalytics:
    """The one path to a fresh DeskAnalytics — used by both refresh() and
    facade.get_brief(), so there is exactly one way this number gets built."""
    result = analytics_mod.build_analytics(spec, **kwargs)
    save_analytics(spec.id, result)
    return result


# ── provider resolution (intel / thesis — built by other agents) ────────────

def _resolve_provider(path: str) -> Any:
    module_path, _, attr = path.partition(":")
    module = importlib.import_module(module_path)
    return getattr(module, attr)


def _load_intel(spec: DeskSpec) -> Tuple[Optional[IntelPacket], Optional[str]]:
    try:
        build_packet = _resolve_provider(INTEL_PROVIDER_PATH)
    except (ImportError, ModuleNotFoundError, AttributeError) as exc:
        return None, f"intel provider unavailable ({INTEL_PROVIDER_PATH}): {exc}"
    try:
        return build_packet(spec), None
    except Exception as exc:  # noqa: BLE001 - a provider bug degrades the refresh, doesn't crash it
        logger.warning(f"[strategies.pipeline] intel provider raised for {spec.id}: {exc}")
        return None, f"intel provider raised: {exc}"


def _load_thesis(spec: DeskSpec, result: DeskAnalytics, intel: Optional[IntelPacket],
                 prev: Optional[Thesis]) -> Tuple[Optional[Thesis], Optional[str]]:
    try:
        write_thesis = _resolve_provider(THESIS_PROVIDER_PATH)
    except (ImportError, ModuleNotFoundError, AttributeError) as exc:
        return None, f"thesis provider unavailable ({THESIS_PROVIDER_PATH}): {exc}"
    try:
        return write_thesis(spec, result, intel, prev), None
    except Exception as exc:  # noqa: BLE001 - same degrade-not-crash rule as intel
        logger.warning(f"[strategies.pipeline] thesis provider raised for {spec.id}: {exc}")
        return None, f"thesis provider raised: {exc}"


# ── token gate ─────────────────────────────────────────────────────────────

def needs_rewrite(spec: DeskSpec, result: DeskAnalytics, intel: Optional[IntelPacket],
                  prev: Optional[Thesis], *, now: Optional[datetime] = None,
                  force: bool = False) -> Tuple[bool, str]:
    """Whether to spend a (billable, slow) call on a new thesis. Checked in
    this order — the first match wins:

        1. force                             -> True,  "forced"
        2. no prior machine thesis           -> True,  "no_prior_thesis"
        3. desk's primaries changed since prior (primary_changed) -> True, "primary_changed"
        4. prior thesis expired              -> True,  "expired"
        5. refresh_min_interval_min not yet elapsed since prior -> False, "min_interval_not_elapsed"
           (rate limit wins even over a material move below — it only
           guards the two triggers after it; the three above always win)
        6. call instrument moved >= material_move_pct since prior entry -> True, "material_move"
        7. a headline >= min_headline_relevance arrived after prior.created_at -> True, "high_relevance_headline"
        8. otherwise                         -> False, "no_trigger"
    """
    now = now or datetime.now(timezone.utc)
    if force:
        return True, "forced"
    if prev is None:
        return True, "no_prior_thesis"
    if primary_changed(spec, prev):
        return True, "primary_changed"
    if now >= prev.expires_at:
        return True, "expired"

    elapsed_min = (now - prev.created_at).total_seconds() / 60.0
    if elapsed_min < spec.token_budget.refresh_min_interval_min:
        return False, "min_interval_not_elapsed"

    primary = next((i for i in result.instruments if i.coin == prev.call.instrument), None)
    if primary is not None and prev.call.entry_px:
        move_pct = abs(primary.mid / prev.call.entry_px - 1.0) * 100.0
        if move_pct >= spec.token_budget.material_move_pct:
            return True, "material_move"

    if intel is not None:
        for h in intel.headlines:
            if (h.relevance >= spec.token_budget.min_headline_relevance
                   and h.published_at is not None and h.published_at > prev.created_at):
                return True, "high_relevance_headline"

    return False, "no_trigger"


def _log_usage(desk_id: str, decision: str, reason: str, usage: Optional[Usage]) -> None:
    append_json_line(_usage_log_path(), {
        "ts": datetime.now(timezone.utc).isoformat(), "desk": desk_id, "decision": decision,
        "reason": reason, "input_tokens": usage.input_tokens if usage else None,
        "output_tokens": usage.output_tokens if usage else None,
        "duration_s": usage.duration_s if usage else None,
    })


# ── entrypoint ─────────────────────────────────────────────────────────────

# Plain-English stage labels, reported through refresh(on_stage=...) so a UI
# polling the job can say what is happening instead of a bare spinner.
STAGE_MARKET = "reading market data"
STAGE_INTEL = "pulling news, policy and odds"
STAGE_GATE = "checking whether anything changed"
STAGE_THESIS = "writing the thesis (about a minute)"


def refresh(desk_id: str, force: bool = False,
            on_stage: Optional[Callable[[str], None]] = None) -> RefreshJob:
    job_id = uuid.uuid4().hex
    started = datetime.now(timezone.utc)

    def stage(label: str) -> None:
        if on_stage is not None:
            try:
                on_stage(label)
            except Exception:  # noqa: BLE001 - a progress hook must never break a refresh
                logger.debug("[strategies.pipeline] on_stage hook raised", exc_info=True)

    spec = get_spec(desk_id)
    if spec is None:
        return RefreshJob(id=job_id, desk_id=desk_id, state="error", started_at=started,
                          finished_at=datetime.now(timezone.utc), message=f"unknown desk '{desk_id}'")

    t0 = time.monotonic()
    stage(STAGE_MARKET)
    try:
        # candle_max_age_s=0: a thesis is written against the market NOW, so
        # the stored daily series always gets its tail re-fetched here (one
        # small request per instrument), unlike a brief, which may serve a
        # few-minute-old copy.
        result = build_and_cache_analytics(spec, candle_max_age_s=0)
    except Exception as exc:
        logger.exception(f"[strategies.pipeline] analytics build failed for {desk_id}")
        _log_usage(desk_id, "error", "analytics_failed", Usage(duration_s=time.monotonic() - t0))
        return RefreshJob(id=job_id, desk_id=desk_id, state="error", started_at=started,
                          finished_at=datetime.now(timezone.utc),
                          message=f"analytics build failed: {exc}")

    stage(STAGE_INTEL)
    intel, intel_note = _load_intel(spec)
    if intel is not None:
        save_intel(desk_id, intel)

    stage(STAGE_GATE)
    prev = journal.latest_thesis(desk_id, author="machine")
    decision, reason = needs_rewrite(spec, result, intel, prev, force=force)
    notes = [n for n in (intel_note,) if n]

    if not decision:
        usage = Usage(skipped_reason=reason, duration_s=time.monotonic() - t0)
        _log_usage(desk_id, "skipped", reason, usage)
        message = "; ".join([reason] + notes)
        return RefreshJob(id=job_id, desk_id=desk_id, state="skipped", started_at=started,
                          finished_at=datetime.now(timezone.utc), message=message, usage=usage)

    stage(STAGE_THESIS)
    # A thesis written for other primaries is not a base to update: the
    # incremental prompt would measure the new primary's price against the
    # old call's entry (Brent at 107 vs a WTI entry at 98.87), and a failed
    # call would fall back to re-serving the old call. Write from scratch.
    base = None if prev is not None and primary_changed(spec, prev) else prev
    thesis, thesis_note = _load_thesis(spec, result, intel, base)
    if thesis_note:
        notes.append(thesis_note)

    if thesis is None:
        usage = Usage(skipped_reason=reason, duration_s=time.monotonic() - t0)
        _log_usage(desk_id, "degraded", reason, usage)
        message = "; ".join(notes) if notes else "thesis provider unavailable"
        return RefreshJob(id=job_id, desk_id=desk_id, state="done", started_at=started,
                          finished_at=datetime.now(timezone.utc), message=message, usage=usage)

    thesis = thesis.model_copy(update={"primary_coins": primary_coins(spec)})
    journal.save_thesis(thesis)
    usage = thesis.usage or Usage()
    if usage.duration_s is None:
        usage = usage.model_copy(update={"duration_s": time.monotonic() - t0})
    _log_usage(desk_id, "written", reason, usage)
    return RefreshJob(id=job_id, desk_id=desk_id, state="done", started_at=started,
                      finished_at=datetime.now(timezone.utc),
                      message=f"thesis written ({reason})", usage=usage)
