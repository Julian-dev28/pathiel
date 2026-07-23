"""Pydantic response/request models for the Hermes Data API.

Clean-room-compatible with the Unusual Whales (UW) public API contract: we serve
OUR OWN resources (Hyperliquid crypto, xyz tokenized equities, our computed
signals) inside the same response envelope shape UW uses, so an existing UW client
can point at us with minimal changes.

Envelope contract
-----------------
Success bodies wrap the payload in ``{"data": <payload>}`` where ``<payload>`` is
either a list of records or a single record. See ``DataEnvelope`` / ``envelope()``.

Errors follow RFC 7807 ``application/problem+json`` — see ``Problem``.

Field-name compatibility vs. types
-----------------------------------
UW returns money/quantity fields as JSON strings (e.g. ``net_call_premium:
"2234.00"``). We keep the UW *field names* but expose clean numeric types. Pydantic
runs in lax mode, so a UW string like ``"2234.00"`` still validates into a ``float``
on ingest — wire-compatible in, clean types out.

Dual pydantic support
----------------------
Works on pydantic v2 (primary) and v1 (fallback). The only version-sensitive piece
is the generic envelope base class, handled below.
"""
from __future__ import annotations

from typing import Any, Generic, List, Optional, TypeVar, Union

import pydantic
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Version shim
# ---------------------------------------------------------------------------
_PYDANTIC_V2 = pydantic.VERSION.startswith("2")

T = TypeVar("T")

__all__ = [
    "DataEnvelope",
    "envelope",
    "OHLCBar",
    "NetFlowPoint",
    "FlowAlert",
    "Problem",
    "problem",
    "HealthResponse",
]


# ---------------------------------------------------------------------------
# Generic success envelope: {"data": <payload>}
# ---------------------------------------------------------------------------
# Generics are awkward across pydantic versions, so callers should prefer the
# `envelope()` helper for building responses. `DataEnvelope[T]` exists as a typed
# model for OpenAPI docs and for callers that want validation.
if _PYDANTIC_V2:

    class DataEnvelope(BaseModel, Generic[T]):
        """UW-style success envelope. `data` is a single record or a list of them."""

        data: Union[List[T], T]

else:  # pragma: no cover - exercised only on pydantic v1
    try:
        from pydantic.generics import GenericModel as _GenericModel

        class DataEnvelope(_GenericModel, Generic[T]):  # type: ignore[no-redef]
            """UW-style success envelope. `data` is a single record or a list of them."""

            data: Union[List[T], T]

    except Exception:  # very old pydantic without generics support

        class DataEnvelope(BaseModel):  # type: ignore[no-redef]
            """UW-style success envelope (untyped fallback)."""

            data: Any


def envelope(payload: Any) -> dict:
    """Wrap any JSON-serializable payload in the UW success envelope.

    >>> envelope([1, 2, 3])
    {'data': [1, 2, 3]}
    >>> envelope({"status": "ok"})
    {'data': {'status': 'ok'}}
    """
    return {"data": payload}


# ---------------------------------------------------------------------------
# Candle / OHLC — crypto + tokenized-equity chart endpoints
# ---------------------------------------------------------------------------
class OHLCBar(BaseModel):
    """A single OHLCV candle. `t` is the bar-open time in epoch milliseconds."""

    t: int = Field(..., description="Bar open time, epoch milliseconds (UTC).")
    o: float = Field(..., description="Open price.")
    h: float = Field(..., description="High price.")
    l: float = Field(..., description="Low price.")
    c: float = Field(..., description="Close price.")
    v: float = Field(..., description="Volume over the bar, in base units.")


# ---------------------------------------------------------------------------
# Net premium flow — mirrors UW `net-prem-ticks`
# ---------------------------------------------------------------------------
class NetFlowPoint(BaseModel):
    """One net-premium tick. Mirrors UW's net-prem shape (field names preserved).

    UW sends the premium/volume figures as strings; we expose floats (pydantic
    coerces the UW strings on ingest).
    """

    date: str = Field(..., description="Trading day, YYYY-MM-DD.")
    ticker: str = Field(..., description="Underlying symbol.")
    net_call_premium: float = Field(..., description="Net call premium for the tick.")
    net_put_premium: float = Field(..., description="Net put premium for the tick.")
    net_premium: float = Field(
        ..., description="Net premium (calls minus puts) for the tick."
    )
    net_call_volume: float = Field(..., description="Net call volume for the tick.")
    net_put_volume: float = Field(..., description="Net put volume for the tick.")


# ---------------------------------------------------------------------------
# Flow alerts — mirrors UW `flow-alerts`
# ---------------------------------------------------------------------------
class FlowAlert(BaseModel):
    """A flow alert. Mirrors UW's flow-alerts shape (field names preserved)."""

    ticker: str = Field(..., description="Underlying symbol.")
    type: str = Field(..., description='Contract side, e.g. "call" or "put".')
    strike: float = Field(..., description="Option strike price.")
    expiry: str = Field(..., description="Contract expiry date, YYYY-MM-DD.")
    total_premium: float = Field(..., description="Total premium traded in the alert.")
    volume: int = Field(..., description="Contracts traded.")
    open_interest: int = Field(..., description="Open interest on the contract.")
    has_sweep: bool = Field(..., description="Whether the alert includes a sweep.")
    created_at: str = Field(..., description="Alert creation time, ISO-8601 UTC.")


# ---------------------------------------------------------------------------
# RFC 7807 problem+json error body
# ---------------------------------------------------------------------------
class Problem(BaseModel):
    """RFC 7807 `application/problem+json` error body.

    Served with the matching HTTP status and `Content-Type: application/problem+json`.
    """

    type: str = Field(
        default="about:blank",
        description="A URI identifying the problem type. Defaults to 'about:blank'.",
    )
    title: str = Field(..., description="Short, human-readable summary of the problem.")
    status: int = Field(..., description="HTTP status code for this occurrence.")
    detail: Optional[str] = Field(
        default=None, description="Human-readable explanation specific to this occurrence."
    )
    instance: Optional[str] = Field(
        default=None, description="URI reference identifying this specific occurrence."
    )


def problem(
    status: int,
    title: str,
    detail: Optional[str] = None,
    type: str = "about:blank",
    instance: Optional[str] = None,
) -> dict:
    """Build an RFC 7807 problem+json body as a plain dict.

    >>> problem(404, "Not Found", detail="no such ticker")["status"]
    404
    """
    body: dict = {"type": type, "title": title, "status": status}
    if detail is not None:
        body["detail"] = detail
    if instance is not None:
        body["instance"] = instance
    return body


# ---------------------------------------------------------------------------
# Health / liveness
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    """Liveness/readiness payload. Not wrapped in the data envelope."""

    status: str = Field(..., description='Service health, e.g. "ok".')
    service: str = Field(..., description="Service name.")
    version: str = Field(..., description="Deployed version string.")
    env: str = Field(..., description="Deployment environment, e.g. dev|staging|prod.")


# ---------------------------------------------------------------------------
# Smoke check (single-file constraint: no separate test file created).
# Run: python -m services.hermes_data_api.app.schemas
# ---------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    def _dump(m: BaseModel) -> dict:
        return m.model_dump() if hasattr(m, "model_dump") else m.dict()

    bar = OHLCBar(t=1, o=1, h=2, l=0.5, c=1.5, v=100)
    assert _dump(bar) == {"t": 1, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100.0}

    # UW-style string inputs coerce into clean numeric types.
    pt = NetFlowPoint(
        date="2025-03-21",
        ticker="AAPL",
        net_call_premium="2234.00",
        net_put_premium="-11106.00",
        net_premium="-8872.00",
        net_call_volume="640",
        net_put_volume="-137",
    )
    assert _dump(pt)["net_call_premium"] == 2234.0

    alert = FlowAlert(
        ticker="MSFT",
        type="call",
        strike="375",
        expiry="2023-12-22",
        total_premium="186705",
        volume=2442,
        open_interest=7913,
        has_sweep=True,
        created_at="2023-12-12T16:35:52.168490Z",
    )
    assert _dump(alert)["strike"] == 375.0

    assert envelope([1, 2, 3]) == {"data": [1, 2, 3]}
    assert problem(404, "Not Found", detail="no such ticker")["status"] == 404

    env = DataEnvelope[OHLCBar](data=[bar])
    assert len(_dump(env)["data"]) == 1

    hr = HealthResponse(status="ok", service="hermes-data-api", version="0.1.0", env="dev")
    assert _dump(hr)["status"] == "ok"

    print(f"schemas.py smoke OK (pydantic {pydantic.VERSION})")
