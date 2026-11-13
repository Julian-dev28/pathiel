"""Core data types shared across pathiel.

2026-10-25: trimmed to `Candle`. The research-era TypedDicts that used to live
here (`TriggerHit`, `BookAnalysis` and its override blocks, the shadow-ledger
`GradeResult` family, `WebSearchTelemetry`) described the retired autonomous
engine's perception -> research -> execute records; nothing imported them.
"""

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
