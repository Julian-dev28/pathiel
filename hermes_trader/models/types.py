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


class TriggerHitExtras(TypedDict, total=False):
    """Optional structured keys on a fired trigger. B.44 fade-family state
    fix 2026-10-14: the decision-voice state carries side-aware fields for
    mfiExtreme/rsiDivergence (the side-less names in triggers_fired measured
    as danger-tokens for the decision model — B.44 gauntlet). Perception and
    composite scoring ignore these keys; only decision_voice.build_state
    reads them."""
    side: str            # "long" | "short" — the trade side the signal suggests;
                         # breakout uses "up" | "down" — the break DIRECTION
                         # (t_a99fdac2), not a suggested trade side
    kind: str            # mfiExtreme: "oversold" | "overbought"
    mfi_value: int
    threshold: int
    flavour: str         # rsiDivergence: "bullish" | "bearish"
    extreme_pct: float   # rsiDivergence: signed price-extreme % beyond prior
    pct_break: float     # breakout: % beyond the range edge (t_a99fdac2)
    shape: str           # vwapDeviation: "fade" | "retest"
    z: float             # vwapDeviation: signed stretch sigma
    age: int             # vwapDeviation: anchor age in bars


class TriggerHit(TriggerHitExtras):
    """Result of a single trigger check from `indicators.triggers`, consumed
    by the perception scan + composite scoring."""
    name: str
    score: float
    reason: str
    fired: bool
