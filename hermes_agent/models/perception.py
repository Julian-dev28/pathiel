"""Perception model from the scan engine.

Converts TypeScript Perception type from lib/agent/perception.ts.
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel


class TriggerHit(BaseModel):
    """A single trigger result."""
    name: str
    score: float
    reason: str
    fired: bool


class Perception(BaseModel):
    """Result of a market scan — a triggered candidate."""
    id: str
    coin: str
    type: str  # 'perp' or 'spot'
    fired_at: int  # Date.now() in ms
    mid: float
    triggers: List[TriggerHit]
    composite_score: float
    ta_signal: Optional[str] = None
    ta_score: Optional[float] = None
    ta_trend_4h: Optional[str] = None
    ta_rsi_4h: Optional[float] = None
    ta_atr_4pct: Optional[float] = None
    ta_reason: Optional[str] = None
