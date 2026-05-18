"""Core data types shared across the agent."""

from __future__ import annotations

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
