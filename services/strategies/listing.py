"""Is a coin tradable on the network this process runs on? Testnet only.

Testnet lists fewer markets than mainnet (2026-11-12: xyz:BRENTOIL is
delisted there, xyz:ARM and most equities are missing). Two callers ask:

  * services/strategies/desks: swap a desk instrument for its verified
    testnet substitute (the oil desk trades xyz:CL as the Brent stand-in).
  * services/strategies/analytics: skip the candle read for a coin the venue
    does not list. hl_client.fetch_hl_candles retries a non-list reply six
    times with backoff (0.5+1+2+4+8+8 s), and an unlisted coin's reply is
    never a list, so one such coin held desk_brief for about 27 s and pushed
    it into the 30 s MCP handler deadline.

On mainnet every function answers without a read and without importing the
venue client, so the operator's path is unchanged.

The answer is pathiel.client.exchange.coin_listing ('listed', 'delisted',
'not_listed', 'unknown'), looked up at call time (tests monkeypatch it) and
cached per (network, coin): LISTED_TTL_S for a venue answer, UNKNOWN_TTL_S
for a failed read so it is tried again soon.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Tuple

from contracts import runtime

LISTED_TTL_S = 600.0
UNKNOWN_TTL_S = 60.0
NOT_TRADABLE = ("delisted", "not_listed")

_CACHE: Dict[Tuple[str, str], Tuple[float, str]] = {}
_LOCK = threading.Lock()
# One venue read at a time: analytics asks for every desk coin from a thread
# pool at once, and a cold cache would otherwise send the same perpDexs and
# meta reads from each thread. After the first, the venue client's own meta
# cache answers the rest without a request.
_READ_LOCK = threading.Lock()


def on_testnet() -> bool:
    return runtime.network() == "testnet"


def coin_status(coin: str) -> str:
    """coin_listing(coin) on this network, cached. 'listed' on mainnet with no
    read at all: the mainnet desks are verified listed, and the operator's
    path must not gain a venue read."""
    if not on_testnet():
        return "listed"
    key = (runtime.network(), coin)
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit is not None and now < hit[0]:
        return hit[1]
    with _READ_LOCK:
        with _LOCK:
            hit = _CACHE.get(key)
        if hit is not None and time.monotonic() < hit[0]:
            return hit[1]           # another thread read it while this one waited
        try:
            from pathiel.client import exchange
            status = str(exchange.coin_listing(coin))
        except Exception:   # noqa: BLE001 - an import or read failure is an unknown, never a raise
            status = "unknown"
        ttl = UNKNOWN_TTL_S if status == "unknown" else LISTED_TTL_S
        with _LOCK:
            _CACHE[key] = (time.monotonic() + ttl, status)
    return status


def not_tradable_here(coin: str) -> bool:
    """True only when the venue said so (delisted or not listed). A failed
    read is not proof: the caller goes ahead as before."""
    return coin_status(coin) in NOT_TRADABLE


def reset() -> None:
    with _LOCK:
        _CACHE.clear()


__all__ = ["LISTED_TTL_S", "UNKNOWN_TTL_S", "NOT_TRADABLE", "on_testnet", "coin_status",
           "not_tradable_here", "reset"]
