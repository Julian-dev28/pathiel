"""Core data types shared across the agent."""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel


class Candle(BaseModel):
    """OHLCV candle."""
    t: int  # timestamp (ms)
    o: float  # open
    h: float  # high
    l: float  # low
    c: float  # close
    v: float  # volume

    def __getitem__(self, key: str) -> float:
        """Allow dict-style access: candle['c'], candle['t'], etc."""
        return getattr(self, key)


class TriggerHit(TypedDict, total=False):
    """Result of a single trigger check from `indicators.triggers`, consumed
    by the perception scan + composite scoring.

    `direction` ("up"/"down") is set by the IMPULSE triggers (pctMoveSpike,
    breakout, momentumBurst, shockDay) so the runner gate can tell a rally
    from a crash — before 2026-07-10 a +12% rally and a -12% crash produced
    the identical composite score and a down-impulse counted as LONG
    structure (the SMSN selloff-buy class)."""
    name: str
    score: float
    reason: str
    fired: bool
    direction: str
