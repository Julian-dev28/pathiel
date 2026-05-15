"""Hyperliquid-specific low-level types.

Raw API response shapes used for msgpack encoding and HL exchange interactions.
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel


class HLMeta(BaseModel):
    """Raw perp meta response shape.

    {type: 'meta'} returns:
    { universe: [{ name, szDecimals, maxLeverage, minNtl? }] }
    """
    universe: List[dict[str, Any]]


class HLSpotMeta(BaseModel):
    """Raw spot meta response shape.

    {type: 'spotMeta'} returns:
    { universe: [{ name, szDecimals?, tokens?, index }], tokens: [{ name, szDecimals? }] }
    """
    universe: List[dict[str, Any]]
    tokens: List[dict[str, Any]]


class HLOrderResponse(BaseModel):
    """Response from HL exchange order endpoint."""
    ok: bool
    order_id: Optional[str] = None
    error: Optional[str] = None


class HLTriggerOrderResponse(BaseModel):
    """Response from HL exchange trigger order endpoint."""
    ok: bool
    order_id: Optional[str] = None
    error: Optional[str] = None


class HLCancelResponse(BaseModel):
    """Response from HL exchange cancel endpoint."""
    ok: bool
    error: Optional[str] = None
