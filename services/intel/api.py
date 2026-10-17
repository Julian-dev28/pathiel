"""services/intel entry point.

    build_packet(spec: DeskSpec, now: datetime | None = None) -> IntelPacket

Zero LLM calls — deterministic fetch, parse, dedup, score. Every network
source runs concurrently in its own thread, each wrapped in a TTL file
cache (services/intel/cache.py) keyed by desk + source, so a desk that was
just refreshed answers from disk in well under 100ms. A hard wall-clock
deadline (`FETCH_DEADLINE_S`) bounds the cold-build path to the brief's
"under 15s cold" target regardless of how badly a single source misbehaves
— a source still running past the deadline is recorded in
`sources_failed` as `<key>:timeout_budget_exceeded` and its result is
simply not waited on; this function never raises and never blocks past
the deadline plus a small fixed amount of in-memory work (dedup, scoring,
render), which is the actual performance contract, not "every source
finishes."

This module never calls an LLM — see CLAUDE.md's latent/deterministic
split: everything here is fetch + parse + a pure scoring function, so it
belongs in code, not in a prompt. The thesis writer downstream
(services/thesis, per docs/overhaul/SWARM_RULES.md section 6) is the only
latent step in the whole desk pipeline and this packet is its entire view
of the world — which is also why `sources_failed` is never silently
empty: a thesis that doesn't know a source died can't account for it.
"""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from contracts.strategies.models import (
    Catalyst, DeskSpec, IntelPacket, MarketOdds, PolicyItem, PositioningItem,
)
from services.intel import cache, env
from services.intel.render import estimate_tokens, render_compact  # re-exported, see below
from services.intel.sources import catalysts, news, policy, positioning, predictions

logger = logging.getLogger(__name__)

FETCH_DEADLINE_S = 12.0
MAX_WORKERS = 24

_TTL_S = {
    "news": 600.0,          # 10 min — headlines move fast
    "policy": 14_400.0,     # 4h — regs/bills move slowly
    "predictions": 600.0,   # 10 min — odds move fast
    "positioning": 43_200.0,  # 12h — COT only updates weekly
    "catalysts_live": 900.0,  # 15 min — NHC storms update every few hours;
                               # unlocks/earnings are far slower but share
                               # the shortest TTL rather than adding a 4th
                               # category for two rarely-changing checks.
}

_JsonList = List[Dict[str, Any]]


def _fn_key(fn: Callable) -> str:
    mod = fn.__module__.rsplit(".", 1)[-1]
    return f"{mod}.{fn.__name__}"


def _pyd_to_jsonable(items: Sequence) -> _JsonList:
    return [item.model_dump(mode="json") for item in items]


def _pyd_from_jsonable(model):
    def _f(raw: _JsonList) -> List:
        return [model.model_validate(r) for r in raw]
    return _f


def _news_to_jsonable(items: Sequence[Dict[str, Any]]) -> _JsonList:
    out = []
    for r in items:
        pub = r.get("published_at")
        out.append({**r, "published_at": pub.isoformat() if isinstance(pub, datetime) else None})
    return out


def _news_from_jsonable(raw: _JsonList) -> List[Dict[str, Any]]:
    out = []
    for r in raw:
        pub = r.get("published_at")
        out.append({**r, "published_at": datetime.fromisoformat(pub) if pub else None})
    return out


# category -> (ttl_key, to_jsonable, from_jsonable)
_CODECS = {
    "news": ("news", _news_to_jsonable, _news_from_jsonable),
    "policy": ("policy", _pyd_to_jsonable, _pyd_from_jsonable(PolicyItem)),
    "predictions": ("predictions", _pyd_to_jsonable, _pyd_from_jsonable(MarketOdds)),
    "positioning": ("positioning", _pyd_to_jsonable, _pyd_from_jsonable(PositioningItem)),
    "catalysts_live": ("catalysts_live", _pyd_to_jsonable, _pyd_from_jsonable(Catalyst)),
}


def _submit(executor: ThreadPoolExecutor, desk_dir: str, category: str, fn: Callable,
            args: tuple) -> Tuple[Future, str]:
    """`desk_dir` must already be a resolved, concrete path — resolved
    ONCE in build_packet's main thread before any job is submitted, never
    looked up lazily inside a worker. See cache.py's module docstring for
    why: a job this function submits can keep running after
    `build_packet` has already returned (the deadline-driven
    `executor.shutdown(wait=False)` abandons stragglers rather than
    blocking on them), so anything a worker thread reads from the
    environment at call time could have already changed or been torn
    down by whatever context submitted it — a real failure mode this
    exact function caught during testing, not a hypothetical one."""
    ttl_key, to_jsonable, from_jsonable = _CODECS[category]
    key = _fn_key(fn)
    ttl_s = _TTL_S[ttl_key]

    def job() -> Tuple[List, Optional[str]]:
        def fetch_jsonable() -> Tuple[Optional[_JsonList], Optional[str]]:
            items, err = fn(*args)
            return (to_jsonable(items) if items is not None else None), err
        raw, err = cache.get_or_fetch(desk_dir, key, ttl_s, fetch_jsonable)
        items = from_jsonable(raw) if raw is not None else []
        return items, err

    return executor.submit(job), key


def build_packet(spec: DeskSpec, now: Optional[datetime] = None) -> IntelPacket:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    failed: List[str] = []
    raw_news: _JsonList = []
    policy_items: List[PolicyItem] = []
    odds: List[MarketOdds] = []
    positioning_items: List[PositioningItem] = []
    live_catalysts: List[Catalyst] = []

    # Resolved ONCE, synchronously, in this (the caller's) thread — see
    # _submit's docstring for why every worker closes over this plain
    # string instead of re-reading $PATHIEL_STATE_DIR itself.
    desk_dir = env.intel_dir(spec.id)

    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    jobs: List[Tuple[str, str, Future]] = []
    try:
        for fn in news.ATOMIC_FETCHERS:
            fut, key = _submit(executor, desk_dir, "news", fn, (spec,))
            jobs.append(("news", key, fut))
        for fn in policy.ATOMIC_FETCHERS:
            fut, key = _submit(executor, desk_dir, "policy", fn, (spec,))
            jobs.append(("policy", key, fut))
        for fn in predictions.ATOMIC_FETCHERS:
            fut, key = _submit(executor, desk_dir, "predictions", fn, (spec,))
            jobs.append(("predictions", key, fut))
        for fn in positioning.ATOMIC_FETCHERS:
            fut, key = _submit(executor, desk_dir, "positioning", fn, (spec,))
            jobs.append(("positioning", key, fut))
        for fn in catalysts.ATOMIC_FETCHERS:
            fut, key = _submit(executor, desk_dir, "catalysts_live", fn, (spec, now))
            jobs.append(("catalysts_live", key, fut))

        all_futures = [fut for _cat, _key, fut in jobs]
        done, not_done = wait(all_futures, timeout=FETCH_DEADLINE_S)
    finally:
        # Don't block this call on a straggler — shutdown(wait=False) lets
        # build_packet return on time; the thread itself still finishes
        # (bounded by http_client's own timeout) and is simply not waited
        # on. See module docstring.
        executor.shutdown(wait=False)

    fut_info = {fut: (cat, key) for cat, key, fut in jobs}
    for fut in not_done:
        _cat, key = fut_info[fut]
        failed.append(f"{key}:timeout_budget_exceeded")

    for fut in done:
        cat, key = fut_info[fut]
        try:
            items, err = fut.result()
        except Exception as exc:  # never raise to the caller, see module docstring
            items, err = [], f"{key}:unexpected:{type(exc).__name__}"
            logger.exception(f"[intel.api] {key} raised")
        if err:
            failed.append(err)
        if not items:
            continue
        if cat == "news":
            raw_news.extend(items)
        elif cat == "policy":
            policy_items.extend(items)
        elif cat == "predictions":
            odds.extend(items)
        elif cat == "positioning":
            positioning_items.extend(items)
        elif cat == "catalysts_live":
            live_catalysts.extend(items)

    headlines = news.score_and_dedup(raw_news, spec, now)
    policy_items = policy.dedup_policy(policy_items)
    catalyst_items = catalysts.calendar_catalysts(spec, now) + live_catalysts
    catalyst_items.sort(key=lambda c: c.at)

    packet = IntelPacket(
        desk_id=spec.id, as_of=now, headlines=headlines, policy=policy_items,
        catalysts=catalyst_items, odds=odds, positioning=positioning_items,
        token_estimate=0, sources_failed=failed,
    )
    rendered = render_compact(packet, spec.token_budget.max_input_tokens)
    packet.token_estimate = estimate_tokens(rendered)
    return packet


__all__ = ["build_packet", "render_compact", "estimate_tokens"]
