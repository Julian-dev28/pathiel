"""Which slice of the account each active desk trades with.

Operator, 2026-10-31: silver joins oil as a live desk, "keep the use only 10%
of our equity on it", then "oil 95% silver 5%". Agent config:

    "active_desks": ["oil", "metals"],
    "desk_equity_share": {"oil": 0.95, "metals": 0.05}

A desk sizes every trade off `equity x its share` and holds at most one
position (its own slot), so oil and silver can be open at the same time
without one starving the other of margin. With no `desk_equity_share` saved
the old rule stands: one desk, the whole equity, share 1.0.

Fail closed: shares that do not add up (a value outside (0, 1], or a sum
over 1) or an active desk with no share when shares are configured make
`share_for_coin` raise, and place_bracket refuses rather than size blind.
Pure apart from the desk registry (no I/O): the caller passes the config.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from services.strategies.desks import get_spec

CONFIG_KEY = "desk_equity_share"
_SUM_TOLERANCE = 1e-9


class DeskShareError(ValueError):
    """The saved shares cannot be used to size a trade."""


def active_desks(config: Mapping[str, Any]) -> List[str]:
    raw = config.get("active_desks") or ["oil"]
    return [d for d in raw if isinstance(d, str) and get_spec(d) is not None]


def desk_shares(config: Mapping[str, Any]) -> Dict[str, float]:
    """{desk_id: share} for the ACTIVE desks, validated. Empty dict when no
    shares are saved (the legacy one-desk, whole-equity rule)."""
    raw = config.get(CONFIG_KEY)
    if not raw:
        return {}
    if not isinstance(raw, Mapping):
        raise DeskShareError(f"{CONFIG_KEY} must be a {{desk: fraction}} map, got {type(raw).__name__}")
    out: Dict[str, float] = {}
    for desk in active_desks(config):
        if desk not in raw:
            raise DeskShareError(f"active desk {desk!r} has no {CONFIG_KEY} entry")
        try:
            share = float(raw[desk])
        except (TypeError, ValueError):
            raise DeskShareError(f"{CONFIG_KEY}[{desk!r}] is not a number: {raw[desk]!r}") from None
        if not 0.0 < share <= 1.0:
            raise DeskShareError(f"{CONFIG_KEY}[{desk!r}] = {share:g} is outside (0, 1]")
        out[desk] = share
    total = sum(out.values())
    if total > 1.0 + _SUM_TOLERANCE:
        raise DeskShareError(f"{CONFIG_KEY} adds up to {total:g} of equity, over 1")
    return out


def desk_coins(desk_id: str) -> List[str]:
    spec = get_spec(desk_id)
    return [i.coin for i in spec.instruments] if spec else []


def desk_for_coin(coin: str, config: Mapping[str, Any]) -> Optional[str]:
    """The active desk that trades `coin`: a desk whose PRIMARY is the coin
    wins over one that only lists it, then registry order of active_desks."""
    desks = active_desks(config)
    for desk in desks:
        spec = get_spec(desk)
        if any(i.coin == coin and i.role == "primary" for i in spec.instruments):
            return desk
    for desk in desks:
        if coin in desk_coins(desk):
            return desk
    return None


def share_for_coin(coin: str, config: Mapping[str, Any]) -> Tuple[Optional[str], float]:
    """(desk, share of equity) a trade on `coin` sizes with. (desk, 1.0)
    when no shares are saved. Raises DeskShareError when shares are saved
    but bad, or the coin belongs to no active desk."""
    shares = desk_shares(config)
    desk = desk_for_coin(coin, config)
    if not shares:
        return desk, 1.0
    if desk is None:
        raise DeskShareError(f"{coin} is on no active desk, so it has no equity share")
    return desk, shares[desk]


def slots(config: Mapping[str, Any], profile_slots: int) -> int:
    """How many positions the account may hold at once: one per desk with a
    share, else the risk profile's max_open_positions (the legacy rule)."""
    shares = desk_shares(config)
    return len(shares) if shares else max(1, int(profile_slots or 1))


__all__ = ["CONFIG_KEY", "DeskShareError", "active_desks", "desk_shares", "desk_coins",
           "desk_for_coin", "share_for_coin", "slots"]
