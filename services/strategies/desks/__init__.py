"""Desk registry. One module per desk (oil, gas, btc, top5, ai, metals, indices),
each exporting `build_spec() -> DeskSpec`. This module is the single place
that knows the full list; nothing else should import a desk module directly.

Adding a desk: write `services/strategies/desks/<id>.py` with a
`build_spec() -> DeskSpec` function (copy an existing one — oil.py has the
most comments on *why*), then add it to `_BUILDERS` below. `python -m
services.strategies list` and the gate tests (test_desks.py) will pick it up
automatically — the test suite iterates this registry, it does not hardcode
desk ids.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Callable, Dict, List

from contracts.strategies.models import DeskSpec

from services.strategies.desks import ai, btc, gas, indices, metals, oil, top5

# id -> builder. Order here is the order `list_desks()` returns, which is the
# order the /desks overview page renders — oil first, it's the flagship.
_BUILDERS: Dict[str, Callable[[], DeskSpec]] = {
    oil.DESK_ID: oil.build_spec,
    gas.DESK_ID: gas.build_spec,
    btc.DESK_ID: btc.build_spec,
    top5.DESK_ID: top5.build_spec,
    ai.DESK_ID: ai.build_spec,
    metals.DESK_ID: metals.build_spec,
    indices.DESK_ID: indices.build_spec,
}


class UnknownDeskError(KeyError):
    """Raised by get_spec()/require_spec() for an id not in the registry."""


@lru_cache(maxsize=None)
def _cached_spec(desk_id: str) -> DeskSpec:
    # DeskSpec construction is pure config (no I/O), so caching it is safe
    # and saves re-validating the same pydantic model on every call. Tests
    # that need a fresh build (e.g. mutating a builder) should call the
    # desk module's build_spec() directly instead of going through here.
    return _BUILDERS[desk_id]()


def list_desk_ids() -> List[str]:
    """Registered desk ids, in registry order."""
    return list(_BUILDERS.keys())


def list_specs() -> List[DeskSpec]:
    """Every registered desk's validated DeskSpec, in registry order."""
    return [_cached_spec(desk_id) for desk_id in _BUILDERS]


def get_spec(desk_id: str) -> "DeskSpec | None":
    """The desk's DeskSpec, or None if desk_id isn't registered."""
    builder = _BUILDERS.get(desk_id)
    return _cached_spec(desk_id) if builder else None


def require_spec(desk_id: str) -> DeskSpec:
    """Like get_spec, but raises UnknownDeskError instead of returning None —
    for call sites (API routes, CLI) that should 404/exit rather than carry
    a None forward."""
    spec = get_spec(desk_id)
    if spec is None:
        raise UnknownDeskError(desk_id)
    return spec
