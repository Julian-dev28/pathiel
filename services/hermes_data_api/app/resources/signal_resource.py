"""Signal resources — first-party computed signals + licensed-data adapter slots.

Two kinds of resource live here, and the distinction is the whole point:

1. Signals we compute ourselves from our OWN upstream data (Hyperliquid candles
   via `hl_resource`). `our_momentum_signal` is a real example — trailing return
   from HL closes. We can serve these because we own the inputs and the math.

2. Licensed-data adapter SLOTS. `net_flow` is options-flow data that requires the
   OPRA options tape, a licensed feed we do not have. We do NOT reproduce a
   competitor's proprietary tape or fabricate numbers. The slot returns `None`
   until/unless we wire a licensed upstream, so the router answers 501 honestly.
"""
from __future__ import annotations

import logging

from ..config import get_settings
from . import hl_resource

logger = logging.getLogger(__name__)


async def net_flow(ticker: str, date: str | None = None) -> dict | None:
    """Options net premium flow for `ticker` — LICENSED-DATA ADAPTER SLOT.

    This is options-flow data sourced from the OPRA options tape, a licensed feed
    we do not currently hold. Per the clean-room principle we do NOT reproduce any
    competitor's proprietary options tape and we NEVER fabricate flow numbers.

    Behaviour:
      - `settings.options_flow_upstream` empty  -> return `None`
        (the router turns this into an honest HTTP 501 "adapter not configured").
      - `settings.options_flow_upstream` set     -> our OWN licensed feed is wired;
        fetch from it and return the normalized flow dict.

    When/if we license our own options-flow feed, plug it in below. Example wiring
    (kept commented so no external call is made until a real upstream exists):

        # import httpx
        # async with httpx.AsyncClient(timeout=10) as client:
        #     resp = await client.get(
        #         settings.options_flow_upstream,          # OUR licensed feed's base URL
        #         params={"ticker": ticker, "date": date},
        #     )
        #     resp.raise_for_status()
        #     data = resp.json()
        # return {
        #     "ticker": ticker,
        #     "date": date,
        #     "net_call_premium": float(data["net_call_premium"]),
        #     "net_put_premium": float(data["net_put_premium"]),
        #     "net_flow": float(data["net_call_premium"]) - float(data["net_put_premium"]),
        # }
    """
    settings = get_settings()
    if not settings.options_flow_upstream:
        # No licensed feed configured. Signal "not configured" — never fabricate.
        return None

    # A licensed upstream IS configured but the adapter is not yet implemented.
    # Fail loud rather than silently returning fake/empty flow.
    raise NotImplementedError(
        "options_flow_upstream is set but the licensed-feed adapter is not wired; "
        "implement the httpx call shown in this function's docstring."
    )


async def our_momentum_signal(coin: str, lookback: int = 7) -> dict:
    """First-party momentum signal: trailing `lookback`-day return of `coin`.

    Computed entirely from OUR OWN Hyperliquid candles (`hl_resource.get_ohlc`).
    `trailing_return` = last_close / close_`lookback`_bars_ago - 1, as a decimal
    fraction (0.05 == +5%). Returns `trailing_return` = 0.0 when there is not
    enough history to compute it, rather than raising.
    """
    if lookback < 1:
        lookback = 1

    # Need `lookback + 1` bars: the bar `lookback` days ago and the latest bar.
    bars = await hl_resource.get_ohlc(coin, interval="1d", limit=lookback + 1)

    trailing_return = 0.0
    if len(bars) >= lookback + 1:
        past_close = bars[-(lookback + 1)]["c"]
        last_close = bars[-1]["c"]
        if past_close > 0:
            trailing_return = last_close / past_close - 1.0
    else:
        logger.info(
            "[signal_resource] our_momentum_signal(%s, lookback=%s): only %d bars, "
            "returning trailing_return=0.0", coin, lookback, len(bars))

    return {"coin": coin, "lookback": lookback, "trailing_return": float(trailing_return)}
