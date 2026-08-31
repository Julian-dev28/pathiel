"""Market-data endpoints — UW-compatible paths, backed by OUR resources.

Every route here is authenticated and scoped to ``"market"`` (``require_scope``
resolves the Bearer key to a Principal and checks the plan grants the scope).
Paths mirror the Unusual Whales ``/api/stock/{ticker}/...`` contract so an
existing UW client can point at us unchanged, but the data is sourced from
Hyperliquid and our own first-party signals — never proxied from UW.

The options-flow route is the honest seam: when the licensed upstream slot is
empty it returns 501, never a fabricated number.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from ..auth import require_scope
from ..schemas import NetFlowPoint, OHLCBar, envelope
from ..services import market_service

router = APIRouter(prefix="/api", tags=["market"])

# HL-supported candle intervals. Validated here so the router gives a precise
# 422 instead of forwarding a nonsense interval to the resource.
ALLOWED_INTERVALS: frozenset[str] = frozenset(
    {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w", "1M"}
)

# Ticker grammar covers crypto ("BTC", "kPEPE") and HIP-3 equity perps
# ("xyz:AAPL"). Anchored so nothing weirder than [alnum : . _ -] reaches a resource.
_TICKER_PATTERN = r"^[A-Za-z0-9:._-]+$"
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


@router.get("/stock/{ticker}/ohlc", summary="OHLCV candles")
async def stock_ohlc(
    ticker: str = Path(
        ...,
        min_length=1,
        max_length=32,
        pattern=_TICKER_PATTERN,
        description='Symbol: "BTC" (crypto) or "xyz:AAPL" (HIP-3 equity perp).',
    ),
    interval: str = Query(
        "1d",
        description="Candle size, e.g. 1m/5m/1h/1d. See ALLOWED_INTERVALS.",
    ),
    limit: int = Query(100, ge=1, le=1000, description="Number of bars, oldest-first."),
    _principal=Depends(require_scope("market")),
):
    """Return up to ``limit`` OHLCV bars for ``ticker`` at ``interval``.

    Empty history yields ``{"data": []}`` (not an error) — the resource treats a
    listing with no candles and a transient upstream hiccup identically.
    """
    if interval not in ALLOWED_INTERVALS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unsupported interval '{interval}'; allowed: {sorted(ALLOWED_INTERVALS)}",
        )
    bars = await market_service.ohlc(ticker, interval, limit)
    return envelope([OHLCBar(**b) for b in bars])


@router.get("/stock/{ticker}/net-flow", summary="Net options flow")
async def stock_net_flow(
    ticker: str = Path(
        ...,
        min_length=1,
        max_length=32,
        pattern=_TICKER_PATTERN,
        description="Symbol to fetch net options flow for.",
    ),
    date: str | None = Query(
        None,
        pattern=_DATE_PATTERN,
        description="Session date YYYY-MM-DD; omit for the latest session.",
    ),
    _principal=Depends(require_scope("market")),
):
    """Net options flow for ``ticker``.

    Returns 501 when the licensed options-flow upstream slot is empty — the
    adapter is honest about missing data rather than inventing it.
    """
    point = await market_service.net_flow(ticker, date)
    if point is None:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            detail="options-flow upstream not configured — licensed data adapter slot is empty",
        )
    return envelope(NetFlowPoint(**point))


@router.get("/stock/{ticker}/momentum", summary="First-party momentum signal")
async def stock_momentum(
    ticker: str = Path(
        ...,
        min_length=1,
        max_length=32,
        pattern=_TICKER_PATTERN,
        description="Symbol to score, e.g. \"BTC\" or \"xyz:AAPL\".",
    ),
    lookback: int = Query(7, ge=1, le=365, description="Lookback window in bars."),
    _principal=Depends(require_scope("market")),
):
    """Our own momentum signal over ``lookback`` bars — a first-party resource,
    no licensed dependency, so it always returns a payload."""
    signal = await market_service.momentum(ticker, lookback)
    return envelope(signal)
