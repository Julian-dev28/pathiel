"""Tiny lookups shared across services/thesis's own modules: resolving a coin
or pair label back to its analytics snapshot. Kept in its own module (instead
of living in, say, prompt.py) so prompt.py, validator.py, assemble.py,
render.py and fallback.py can each import it without creating a circular
import between any pair of them.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from contracts.strategies.models import (
    DeskAnalytics, DeskSpec, ForwardStats, InstrumentSnapshot, InstrumentStructure, PairSnapshot,
    PriceExtreme,
)

# An all-time extreme set within this many days of DeskAnalytics.as_of is
# PRICE DISCOVERY, not a real ceiling/floor (2026-10-31, root cause: the
# live 30-day silver test, docs/strategy/SILVER_THESIS_TEST_2026-10-31.md).
# Hyperliquid's xyz:SILVER history only starts 2025-12-26, so during
# October's sell-off silver kept printing a new venue low every few days
# (54.817, then 53.053, then 52.694, then 51.856) - each one was briefly
# "the all-time low" by definition, not a level that had ever held. The
# stale-extreme hard cap (correct for Brent, whose all-time high is 8
# months old and the operator explicitly wants capped) turned 10 of 30
# thesis days flat against a market that kept falling, and on 2026-10-30 it
# forced the call flat at 51.97 right before silver fell to 49.3.
#
# A STALE extreme (older than this window) keeps today's exact hard-cap
# behaviour - see check_targets_reachable in services/thesis/validator.py
# and _render_structure in services/thesis/prompt.py.
PRICE_DISCOVERY_WINDOW_DAYS = 14


def is_fresh_extreme(extreme: Optional[PriceExtreme], as_of: datetime) -> bool:
    """True if ``extreme`` was set within PRICE_DISCOVERY_WINDOW_DAYS of
    ``as_of`` - the venue is still in price discovery on that side, so the
    extreme is not a hard ceiling/floor yet. False (the STALE, today's
    existing-behaviour case) for a None extreme or one older than the
    window."""
    if extreme is None:
        return False
    return (as_of - extreme.at) < timedelta(days=PRICE_DISCOVERY_WINDOW_DAYS)


def primary_coin(spec: DeskSpec) -> str:
    for inst in spec.instruments:
        if inst.role == "primary":
            return inst.coin
    # DeskSpec._one_primary already guarantees one exists; this is defensive
    # only, for a spec built by hand in a test without going through it.
    return spec.instruments[0].coin


def find_instrument(coin: str, analytics: DeskAnalytics) -> Optional[InstrumentSnapshot]:
    for snap in analytics.instruments:
        if snap.coin == coin:
            return snap
    return None


def primary_instrument(spec: DeskSpec, analytics: DeskAnalytics) -> Optional[InstrumentSnapshot]:
    return find_instrument(primary_coin(spec), analytics)


def find_pair(label: str, analytics: DeskAnalytics) -> Optional[PairSnapshot]:
    for pair in analytics.pairs:
        if pair.label == label:
            return pair
    return None


def find_structure(coin: str, analytics: DeskAnalytics) -> Optional[InstrumentStructure]:
    for st in analytics.structure:
        if st.coin == coin:
            return st
    return None


def find_forward(coin: str, analytics: DeskAnalytics) -> Optional[ForwardStats]:
    for fwd in analytics.forward:
        if fwd.coin == coin:
            return fwd
    return None


def find_ladder_rung(leverage: float, analytics: DeskAnalytics, *, coin: Optional[str] = None,
                     side: Optional[str] = None):
    """The LadderRung matching ``leverage`` (and ``coin`` / ``side`` if
    given). The ladder holds a long AND a short rung per leverage, with
    different verdicts, so callers that know the call's side must pass it.
    Exact float equality is intentional: the ladder's leverage values come
    from DeskSpec.leverage_ladder, the same literal floats the LLM is shown."""
    for rung in analytics.ladder:
        if (rung.leverage == leverage and (coin is None or rung.coin == coin)
                and (side is None or rung.side == side)):
            return rung
    return None


def ladder_leverages(analytics: DeskAnalytics) -> list:
    """Distinct leverages in ladder order (each appears once per side)."""
    out = []
    for rung in analytics.ladder:
        if rung.leverage not in out:
            out.append(rung.leverage)
    return out
