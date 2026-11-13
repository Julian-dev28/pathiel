"""Deterministic assembly: ThesisDraft (the LLM's latent JSON) + spec +
analytics + intel -> a full, contract-valid Thesis.

Every field here is either copied straight from the draft (the LLM's own
authored content) or computed with no model call at all - ids, timestamps,
expiry, catalysts, leverage verdicts, resolved sources, payload hashes,
usage. This is the "code fills everything deterministic" half of the
contract's own rule; validator.py is what stands between the draft and this
function ever running on untrusted content.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from contracts.strategies.models import (
    DeskAnalytics, DeskSpec, IntelPacket, LeverageView, PayloadRef, SourceRef, Thesis, Usage,
)
from services.thesis.hashing import hash_payload
from services.thesis.llm_schema import ThesisDraft


def make_thesis_id(desk_id: str, now: datetime) -> str:
    return f"{desk_id}-{now.strftime('%Y%m%dT%H%M')}-m"


def build_leverage_view(analytics: DeskAnalytics, leverage_reasons, call) -> List[LeverageView]:
    """One LeverageView per ladder leverage, for the side of the call (long
    when the call is flat or a pair). leverage + verdict come from the
    matching ladder rung, never from the LLM; reason is matched by leverage,
    not by position."""
    from services.thesis import desk_lookup

    side = call.side if call.side in ("long", "short") else "long"
    coins = {r.coin for r in analytics.ladder}
    coin = call.instrument if call.instrument in coins else None
    by_lev = {}
    for lr in leverage_reasons:
        by_lev.setdefault(float(lr.leverage), lr.reason)
    out: List[LeverageView] = []
    for lev in desk_lookup.ladder_leverages(analytics):
        rung = (desk_lookup.find_ladder_rung(lev, analytics, coin=coin, side=side)
                or desk_lookup.find_ladder_rung(lev, analytics, side=side)
                or desk_lookup.find_ladder_rung(lev, analytics))
        out.append(LeverageView(leverage=lev, verdict=rung.verdict,
                                reason=by_lev.get(float(lev), "no reason provided")))
    return out


def resolve_sources(draft: ThesisDraft, intel: Optional[IntelPacket]) -> List[SourceRef]:
    """Only headlines/policy/odds carry a real url (SourceRef.url is
    required) - catalysts and positioning are legitimate citations
    (validator.check_cited_ids_exist allows them) but have nothing to link
    to, so they are not included in the renderable Sources list."""
    if intel is None:
        return []
    linkable: dict[str, tuple[str, str]] = {}
    for h in intel.headlines:
        linkable[h.id] = (h.title, h.url)
    for p in intel.policy:
        linkable[p.id] = (p.title, p.url)
    for o in intel.odds:
        linkable[o.id] = (o.question, o.url)

    cited_ids: List[str] = []
    seen = set()
    for pt in (*draft.bull, *draft.bear):
        for sid in pt.source_ids:
            if sid not in seen:
                seen.add(sid)
                cited_ids.append(sid)

    out: List[SourceRef] = []
    for sid in cited_ids:
        if sid in linkable:
            title, url = linkable[sid]
            out.append(SourceRef(id=sid, title=title, url=url))
    return out


def assemble_thesis(
    *,
    spec: DeskSpec,
    analytics: DeskAnalytics,
    intel: Optional[IntelPacket],
    draft: ThesisDraft,
    now: datetime,
    usage: Usage,
) -> Thesis:
    return Thesis(
        id=make_thesis_id(spec.id, now),
        desk_id=spec.id,
        author="machine",
        created_at=now,
        expires_at=now + timedelta(days=spec.horizon_days),
        horizon_days=spec.horizon_days,
        call=draft.call,
        scenarios=draft.scenarios,
        bull=draft.bull,
        bear=draft.bear,
        catalysts=list(intel.catalysts) if intel is not None else [],
        leverage_view=build_leverage_view(analytics, draft.leverage_reasons, draft.call),
        lessons=draft.lessons,
        what_changes_my_mind=draft.what_changes_my_mind,
        sources=resolve_sources(draft, intel),
        analytics_ref=PayloadRef(as_of=analytics.as_of, sha256=hash_payload(analytics)),
        intel_ref=PayloadRef(as_of=intel.as_of, sha256=hash_payload(intel)) if intel is not None else None,
        usage=usage,
        grade=None,
    )
