"""Hyperliquid resource adapter — our OWN market-data source.

Clean-room principle: every OHLC/price series this API serves is backed by
Hyperliquid, an exchange we connect to directly. We never proxy a competitor's
API. This module is the single upstream seam for HL candle data; routers call
these coroutines and never touch the raw client.

The underlying client (`hermes_trader.client.hl_client.fetch_hl_candles`) is
synchronous and handles auth/SSL/rate-limiting/retries itself. It is wrapped in
`asyncio.to_thread(...)` so the async request path never blocks the event loop.
"""
from __future__ import annotations

import asyncio
import logging

from hermes_trader.client.hl_client import fetch_hl_candles

logger = logging.getLogger(__name__)


async def get_ohlc(coin: str, interval: str = "1d", limit: int = 100) -> list[dict]:
    """Return `limit` OHLCV bars for `coin` at `interval`, oldest-first.

    Each bar is `{"t":int_ms,"o":float,"h":float,"l":float,"c":float,"v":float}`.
    Returns `[]` on any failure or empty history — never raises — so callers can
    treat "no data" and "upstream hiccup" uniformly (the client already retries
    transient 429/timeouts before returning empty).
    """
    try:
        # fetch_hl_candles' third positional arg is `count` (bar count).
        candles = await asyncio.to_thread(fetch_hl_candles, coin, interval, limit)
    except Exception as e:  # defensive: client is designed not to raise, but seam stays total
        logger.warning("[hl_resource] get_ohlc(%s,%s,%s) failed: %s", coin, interval, limit, e)
        return []

    out: list[dict] = []
    for c in candles or []:
        try:
            out.append({
                "t": int(c.t),
                "o": float(c.o),
                "h": float(c.h),
                "l": float(c.l),
                "c": float(c.c),
                "v": float(c.v),
            })
        except (AttributeError, TypeError, ValueError) as e:
            logger.warning("[hl_resource] skipping malformed candle for %s: %s", coin, e)
            continue
    return out


async def get_price(coin: str) -> float | None:
    """Latest close for `coin` (the most recent 1d bar's close).

    Returns `None` when no bar is available rather than raising or guessing.
    """
    bars = await get_ohlc(coin, interval="1d", limit=1)
    if not bars:
        # Fall back to a slightly wider window in case the newest bar was blanked.
        bars = await get_ohlc(coin, interval="1d", limit=2)
    if not bars:
        return None
    return bars[-1]["c"]
