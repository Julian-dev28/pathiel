"""The two ways write_thesis() ends when the LLM path cannot be trusted.

write_thesis()'s return type is bare ``Thesis``, not ``Thesis | None`` - the
contract never lets a caller get nothing back, even when every LLM call
failed and there is no ``prev`` to fall back to. These two functions are
what fills that gap:

- ``reject_with_prev``: a repair round that still fails, or an LLM call that
  raised, when ``prev`` exists - "reject and return prev with a reason" per
  the brief. Returns a copy of ``prev`` (never mutates the caller's object)
  with ``usage.skipped_reason`` set, so the caller can tell a fresh miss
  from a genuinely fresh thesis without inspecting timestamps.
- ``build_fallback_thesis``: the same situation when ``prev`` is ``None``.
  A flat, zero-confidence, zero-conviction call built from analytics alone -
  no LLM, cannot fail, always a valid Thesis. Its ``expires_at`` is
  deliberately short (1 hour, not the desk's usual horizon) so the next
  refresh retries soon instead of treating a dead gateway as a week-long
  flat stance.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from contracts.strategies.models import (
    Call, DeskAnalytics, DeskSpec, IntelPacket, LeverageView, PayloadRef, Thesis, Usage,
)
from services.thesis import desk_lookup
from services.thesis.hashing import hash_payload

_FALLBACK_TTL = timedelta(hours=1)


def reject_with_prev(prev: Thesis, reason: str) -> Thesis:
    return prev.model_copy(update={"usage": prev.usage.model_copy(update={"skipped_reason": reason})})


def build_fallback_thesis(
    spec: DeskSpec,
    analytics: DeskAnalytics,
    intel: IntelPacket | None,
    reason: str,
    now: datetime,
    *,
    usage: Usage | None = None,
) -> Thesis:
    primary = desk_lookup.primary_instrument(spec, analytics)
    instrument = primary.coin if primary is not None else desk_lookup.primary_coin(spec)
    one_liner = f"No fresh call: {reason}"
    call = Call(
        side="flat",
        instrument=instrument,
        one_liner=one_liner[:160],
        entry_px=None,
        entry_zone=None,
        stop_px=None,
        targets=[],
        leverage=None,
        confidence=0.0,
        invalidation="N/A - thesis generation unavailable; refresh again once the LLM gateway is healthy.",
    )
    leverage_view = [
        LeverageView(leverage=rung.leverage, verdict=rung.verdict, reason="no reason available (LLM call failed)")
        for rung in analytics.ladder if rung.side == "long"
    ]
    return Thesis(
        id=_make_id(spec.id, now),
        desk_id=spec.id,
        author="machine",
        created_at=now,
        expires_at=now + _FALLBACK_TTL,
        horizon_days=spec.horizon_days,
        call=call,
        scenarios=[],
        bull=[],
        bear=[],
        catalysts=list(intel.catalysts) if intel is not None else [],
        leverage_view=leverage_view,
        lessons=[],
        what_changes_my_mind=[],
        sources=[],
        analytics_ref=PayloadRef(as_of=analytics.as_of, sha256=hash_payload(analytics)),
        intel_ref=PayloadRef(as_of=intel.as_of, sha256=hash_payload(intel)) if intel is not None else None,
        usage=(usage.model_copy(update={"skipped_reason": reason}) if usage is not None
               else Usage(skipped_reason=reason)),
        grade=None,
    )


def _make_id(desk_id: str, now: datetime) -> str:
    return f"{desk_id}-{now.strftime('%Y%m%dT%H%M')}-m"


__all__ = ["reject_with_prev", "build_fallback_thesis"]
