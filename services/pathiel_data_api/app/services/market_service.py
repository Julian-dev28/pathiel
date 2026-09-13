"""Market-data business logic — the thin seam between routers and resources.

Routers stay declarative (params, auth, envelope) and never touch a resource
directly; they call these coroutines. Each coroutine calls one of OUR resources
(Hyperliquid candles, our first-party signals, the licensed options-flow adapter
slot) and shapes the raw result into the plain dicts the response schemas
validate.

Clean-room principle: the shapes returned here match the Unusual Whales API
*contract* (field names a UW client expects), but every value is sourced from a
resource we own. We never proxy a competitor's API.
"""
from __future__ import annotations

from ..resources import hl_resource, signal_resource


def _shape_bar(bar: dict) -> dict:
    """Map an HL candle ``{t,o,h,l,c,v}`` to the UW-compatible OHLCBar shape.

    ``t`` is milliseconds since epoch; ``o/h/l/c`` are prices, ``v`` is volume.
    ``.get`` keeps the mapping total so a malformed bar degrades to ``None``
    fields rather than raising inside the request path.
    """
    return {
        "t": bar.get("t"), "o": bar.get("o"), "h": bar.get("h"),
        "l": bar.get("l"), "c": bar.get("c"), "v": bar.get("v"),
    }


async def ohlc(ticker: str, interval: str, limit: int) -> list[dict]:
    """OHLCV bars for ``ticker`` at ``interval``, oldest-first, UW-shaped.

    ``ticker`` passes straight through to the HL resource: ``"BTC"`` for a crypto
    perp, ``"xyz:AAPL"`` for a HIP-3 equity perp. Returns ``[]`` when there is no
    history — the resource retries transient upstream errors and returns empty
    rather than raising, so callers treat "no data" and "hiccup" uniformly.
    """
    bars = await hl_resource.get_ohlc(ticker, interval=interval, limit=limit)
    return [_shape_bar(b) for b in bars]


async def net_flow(ticker: str, date: str | None) -> dict | None:
    """Net options flow for ``ticker`` on ``date`` (default: latest session).

    Returns ``None`` when the licensed options-flow upstream is not configured:
    the resource signals an empty adapter slot with ``None`` and we propagate it
    unchanged, so the router can answer an honest 501 instead of fabricating a
    number. Otherwise returns the resource's net-flow dict.
    """
    return await signal_resource.net_flow(ticker, date=date)


async def momentum(coin: str, lookback: int) -> dict:
    """Our first-party momentum signal for ``coin`` over ``lookback`` bars.

    This is OUR resource, not a UW re-serve — there is no licensed dependency, so
    it always returns a dict (never ``None``).
    """
    return await signal_resource.our_momentum_signal(coin, lookback=lookback)
