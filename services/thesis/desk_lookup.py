"""Tiny lookups shared across services/thesis's own modules: resolving a coin
or pair label back to its analytics snapshot. Kept in its own module (instead
of living in, say, prompt.py) so prompt.py, validator.py, assemble.py,
render.py and fallback.py can each import it without creating a circular
import between any pair of them.
"""
from __future__ import annotations

from typing import Optional

from contracts.strategies.models import DeskAnalytics, DeskSpec, InstrumentSnapshot, PairSnapshot


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
