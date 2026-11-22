"""Coin/desk resolution for the research MCP, v1: exactly the five coins the
build brief names -- BRENTOIL (xyz:BRENTOIL), HYPE, BTC, ETH and SP500
(xyz:SP500).

Deliberately does NOT go through services.strategies.desks.get_spec() /
list_specs() / require_spec(): those apply install-kind and network
adjustments (a public install's leverage-ladder cut, a testnet coin swap --
see services/strategies/desks/__init__.py's _for_install/_for_network) that
exist for the TRADING server's own install, not for a stateless research
answer. Calling them would also risk pulling in pathiel.agents.config_store
/ services.risk.presets (the risk-profile read behind profile_leverage_cap())
for no benefit here. Instead this module calls each desk MODULE's own
build_spec() directly -- pure, no I/O, no env read, no network -- so a
research_brief always describes the desk as designed, the same for every
caller regardless of how this process happens to be installed.

ETH has no standalone desk in the registry; its research draws on the top5
desk's DeskSpec (the only one that lists ETH as an instrument), with a note
in research_brief's output. desk_id_for("ETH") returning "top5" means the
latest-stored-thesis lookup reads top5's journal (not ETH-specific; top5 is
rarely active and often has no stored thesis at all -- research_brief says
so rather than guessing).
"""
from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from contracts.strategies.models import DeskSpec
from services.strategies.desks import btc as _btc_desk
from services.strategies.desks import hype as _hype_desk
from services.strategies.desks import oil as _oil_desk
from services.strategies.desks import sp500 as _sp500_desk
from services.strategies.desks import top5 as _top5_desk


class UnsupportedCoinError(ValueError):
    """Raised for a coin this v1 server does not cover yet."""


# canonical HL coin name -> (desk id its research comes from, that desk
# module's build_spec). Order here is the order SUPPORTED_COINS lists them.
_COIN_DESK: Dict[str, Tuple[str, Callable[[], DeskSpec]]] = {
    "xyz:BRENTOIL": ("oil", _oil_desk.build_spec),
    "HYPE": ("hype", _hype_desk.build_spec),
    "BTC": ("btc", _btc_desk.build_spec),
    "ETH": ("top5", _top5_desk.build_spec),
    "xyz:SP500": ("sp500", _sp500_desk.build_spec),
}

SUPPORTED_COINS: List[str] = list(_COIN_DESK)

# accepted spellings -> canonical HL coin name. Matched case-insensitively
# (canonical_coin upper()s the input before the lookup).
_ALIASES: Dict[str, str] = {
    "BRENTOIL": "xyz:BRENTOIL", "XYZ:BRENTOIL": "xyz:BRENTOIL",
    "BRENT": "xyz:BRENTOIL", "OIL": "xyz:BRENTOIL", "CRUDE": "xyz:BRENTOIL",
    "HYPE": "HYPE", "HYPERLIQUID": "HYPE",
    "BTC": "BTC", "BITCOIN": "BTC",
    "ETH": "ETH", "ETHEREUM": "ETH",
    "SP500": "xyz:SP500", "XYZ:SP500": "xyz:SP500", "S&P500": "xyz:SP500",
    "S&P 500": "xyz:SP500", "SPX": "xyz:SP500", "SP 500": "xyz:SP500",
}


def canonical_coin(raw: str) -> str:
    """The HL coin name `raw` resolves to. Raises UnsupportedCoinError (with
    the supported list in the message) for anything else -- a research_brief
    on an unsupported coin fails with a clear, actionable error, never a
    silent empty result."""
    key = (raw or "").strip().upper()
    canon = _ALIASES.get(key)
    if canon is None:
        raise UnsupportedCoinError(
            f"{raw!r} is not a coin this server covers yet. Supported: "
            f"{', '.join(SUPPORTED_COINS)}.")
    return canon


def desk_id_for(coin: str) -> str:
    """The desk id this coin's research (catalysts, odds queries, the
    stored-thesis lookup) comes from. Raises UnsupportedCoinError."""
    desk_id, _ = _COIN_DESK[canonical_coin(coin)]
    return desk_id


def desk_spec_for(coin: str) -> DeskSpec:
    """The DeskSpec this coin's research is drawn from -- a fresh build every
    call (pure, cheap; no caching needed, see the module docstring). Raises
    UnsupportedCoinError."""
    _, build_spec = _COIN_DESK[canonical_coin(coin)]
    return build_spec()
