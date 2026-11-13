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

import logging
from functools import lru_cache
from typing import Callable, Dict, List, Optional

from contracts import runtime
from contracts.strategies.models import DeskSpec, Instrument

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


logger = logging.getLogger(__name__)

# Rungs a public install is offered when its profile cap leaves fewer than two
# of the desk's own rungs (the default 3x cap leaves none of 5/10/20).
_PUBLIC_LOW_RUNGS = (2.0, 3.0)


def profile_leverage_cap() -> Optional[float]:
    """The saved risk profile's max leverage, or the highest any auto tier
    allows when it is unset (the same cap the MCP set_leverage gate uses).
    The public default (3x) when the profile cannot be read."""
    try:
        from pathiel.agents.config_store import read_risk_profile
        from services.risk.presets import resolve_profile
        profile = read_risk_profile()
        if profile.max_leverage is not None:
            return float(profile.max_leverage)
        cap = resolve_profile(profile, 0.0)[1].max_leverage
        return float(cap) if cap is not None else None
    except Exception as e:   # noqa: BLE001 - a broken profile read falls back to the safe cap
        from services.risk.presets import public_default_profile
        logger.warning(f"[desks] risk profile unreadable, using the public cap: {e}")
        return float(public_default_profile().max_leverage)


def ladder_for_cap(ladder: List[float], cap: Optional[float]) -> List[float]:
    """The rungs at or below `cap`, with 2x and 3x added when fewer than two
    survive. Never empty: a cap under 2x gives that cap, floored at 1x."""
    if cap is None:
        return list(ladder)
    keep = sorted({float(r) for r in ladder if float(r) <= cap})
    if len(keep) < 2:
        keep = sorted(set(keep) | {r for r in _PUBLIC_LOW_RUNGS if r <= cap})
    return keep or [max(1.0, float(int(cap)))]


def _for_install(spec: DeskSpec) -> DeskSpec:
    """The spec as this install sees it. The operator's install: the cached
    spec itself, untouched. A public install: a copy whose leverage ladder
    stops at the risk profile's max_leverage, so the thesis prompt, its
    validator, the analytics ladder and the brief never offer a leverage the
    profile refuses (the oil ladder is 5/10/20; the public cap is 3x)."""
    if not runtime.is_public():
        return spec
    ladder = ladder_for_cap(list(spec.leverage_ladder), profile_leverage_cap())
    if ladder == [float(r) for r in spec.leverage_ladder]:
        return spec
    return spec.model_copy(update={"leverage_ladder": ladder}, deep=True)


def substitutions(spec: DeskSpec) -> List[Dict[str, str]]:
    """The testnet substitutes this process applies to `spec`, one dict per
    swapped instrument: coin, label, status (what the venue said about
    coin), substitute, substitute_label. [] on mainnet, with no venue read.

    Rule: on testnet, an instrument with a testnet_substitute is swapped
    unless the venue says its own coin is listed there. A failed listing
    read swaps too: the substitute is verified on testnet, the original is
    not, and a desk that flipped between the two on read errors would keep
    rewriting its thesis."""
    from services.strategies import listing
    if not listing.on_testnet():
        return []
    out: List[Dict[str, str]] = []
    for inst in spec.instruments:
        sub = inst.testnet_substitute
        if sub is None:
            continue
        status = listing.coin_status(inst.coin)
        if status == "listed":
            continue
        out.append({"coin": inst.coin, "label": inst.label, "status": status,
                    "substitute": sub.coin, "substitute_label": sub.label})
    return out


def substitution_note(spec: "DeskSpec | None") -> Optional[str]:
    """One plain sentence per swap for the brief, or None (always None on
    mainnet). Takes the registry spec (get_spec output is already swapped)."""
    if spec is None:
        return None
    swaps = substitutions(spec)
    if not swaps:
        return None
    parts = []
    for s in swaps:
        why = {"delisted": "delisted on testnet", "not_listed": "not listed on testnet"}.get(
            s["status"], "not confirmed as listed on testnet")
        parts.append(f"Testnet: this desk trades {s['substitute_label']} ({s['substitute']}) as the "
                     f"stand-in for {s['label']} ({s['coin']}), which is {why}. Prices, levels and "
                     f"sizes here are {s['substitute_label']}, not {s['label']}; mainnet trades "
                     f"{s['label']}.")
    return " ".join(parts)


def _for_network(spec: DeskSpec) -> DeskSpec:
    """The spec as this network sees it. Mainnet: the spec itself, untouched,
    with no venue read. Testnet: each instrument named by substitutions()
    becomes its substitute (same role); an instrument that already trades the
    substitute coin is dropped so the coin appears once; a pair with a
    swapped leg is dropped (Brent-WTI would be WTI-WTI); the tagline says
    what stands in for what."""
    swaps = substitutions(spec)
    if not swaps:
        return spec
    by_coin = {s["coin"]: s for s in swaps}
    sub_coins = {s["substitute"] for s in swaps}
    instruments = []
    for inst in spec.instruments:
        s = by_coin.get(inst.coin)
        if s is not None:
            instruments.append(Instrument(coin=s["substitute"], label=s["substitute_label"], role=inst.role))
        elif inst.coin not in sub_coins:
            instruments.append(inst)
    pairs = [p for p in spec.pairs
             if p.long not in by_coin and p.short not in by_coin and p.long != p.short]
    tagline = spec.tagline + " " + " ".join(
        f"On testnet {s['substitute_label']} ({s['substitute']}) stands in for {s['label']} "
        f"({s['coin']}), which is not tradable there." for s in swaps)
    return spec.model_copy(update={"instruments": instruments, "pairs": pairs, "tagline": tagline},
                           deep=True)


def list_specs() -> List[DeskSpec]:
    """Every registered desk's validated DeskSpec, in registry order."""
    return [_for_install(_for_network(_cached_spec(desk_id))) for desk_id in _BUILDERS]


def get_spec(desk_id: str) -> "DeskSpec | None":
    """The desk's DeskSpec, or None if desk_id isn't registered. On a public
    install the leverage ladder is cut to the risk profile's cap. On testnet
    an unlisted instrument is replaced by its verified testnet substitute."""
    builder = _BUILDERS.get(desk_id)
    return _for_install(_for_network(_cached_spec(desk_id))) if builder else None


def registry_spec(desk_id: str) -> "DeskSpec | None":
    """The desk as its module defines it: no network swap, no install cut."""
    return _cached_spec(desk_id) if desk_id in _BUILDERS else None


def require_spec(desk_id: str) -> DeskSpec:
    """Like get_spec, but raises UnknownDeskError instead of returning None —
    for call sites (API routes, CLI) that should 404/exit rather than carry
    a None forward."""
    spec = get_spec(desk_id)
    if spec is None:
        raise UnknownDeskError(desk_id)
    return spec
