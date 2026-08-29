"""Which markets this system is allowed to look at, in one place.

2026-08-29 (operator decision): the tradable universe is restricted to majors.
The reason is capacity, not edge. The activity audit put 94% of missed moves
down to capital saturation, and the illiquid tail is where slippage eats a
thesis before the thesis is wrong. Majors buy clean fills, honest backtests,
and enough depth that sizing stops being the binding constraint.

Stated plainly so nobody mistakes it for alpha: this is a UNIVERSE
RESTRICTION, not a strategy. Majors are the most efficient, most arbitraged
markets that exist. Restricting to them removes a leak. It does not create an
edge, and no result measured here should be read as if it did.

Matching is by BARE ticker — the HIP-3 dex prefix is stripped — so one entry
covers `GOLD`, `xyz:GOLD`, and `km:GOLD` alike. This mirrors
market_regime.classify_asset, which resolves the same way and for the same
reason: HIP-3 venues are mixed, and the prefix says where a market is listed,
not what it is.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Set

# The default majors universe. Bare tickers; every listing venue's prefix for
# the same underlying resolves here.
MAJORS: List[str] = [
    # crypto majors — the only crypto with real depth on HL
    "BTC", "ETH", "SOL", "BNB", "XRP",
    # commodities (HIP-3 tokenized). Several tickers per underlying because
    # venues disagree: oil is CL on one dex and USOIL on another.
    "GOLD", "SILVER", "COPPER",
    "OIL", "USOIL", "WTI", "BRENT", "BRENTOIL", "CL",
    "NATGAS", "GAS", "NGAS",
    # broad indices
    "SP500", "US500", "USTECH", "XYZ100",
    # mega-cap single names
    "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA",
]


def bare_ticker(coin: str) -> str:
    """`xyz:GOLD` -> `GOLD`. A bare ticker is returned unchanged, uppercased."""
    return (coin or "").split(":", 1)[-1].upper()


def in_allowlist(coin: str, allowlist: Optional[Iterable[str]]) -> bool:
    """True when `coin` is allowed.

    An empty or absent allowlist means "no restriction" — that is the historical
    meaning of the config key and callers depend on it.

    A coin matches on either its full name or its bare ticker, so an operator
    who has pinned an exact `xyz:GOLD` in their live config keeps working while
    a plain `GOLD` entry covers every venue.
    """
    allowed: Set[str] = {str(c).upper() for c in (allowlist or []) if c}
    if not allowed:
        return True
    return (coin or "").upper() in allowed or bare_ticker(coin) in allowed


def filter_markets(markets: Iterable[dict], allowlist: Optional[Iterable[str]]) -> List[dict]:
    """Filter a universe payload down to the allowlist. Empty list = unrestricted."""
    allowed: Set[str] = {str(c).upper() for c in (allowlist or []) if c}
    if not allowed:
        return list(markets)
    return [m for m in markets if in_allowlist(m.get("coin", ""), allowed)]
