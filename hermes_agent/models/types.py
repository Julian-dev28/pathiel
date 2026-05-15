"""Core data types used across the agent.

Converts TypeScript interfaces from lib/types.ts to Pydantic BaseModel classes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class AgentVerdict(str, Enum):
    """Agent decision verdict."""
    PASS = "PASS"
    LONG = "LONG"
    SHORT = "SHORT"
    CLOSE = "CLOSE"


class TASignal(str, Enum):
    """Technical analysis signal strength."""
    CONFIRMED = "CONFIRMED"
    WEAK = "WEAK"
    REJECTED = "REJECTED"


class MarketCategory(str, Enum):
    """Market category."""
    crypto = "crypto"
    equity = "equity"
    commodity = "commodity"


class Candle(BaseModel):
    """OHLCV candle."""
    t: int  # timestamp
    o: float  # open
    h: float  # high
    l: float  # low
    c: float  # close
    v: float  # volume

    def __getitem__(self, key: str) -> float:
        """Allow dict-style access: c['t'], c['c'], etc."""
        return getattr(self, key)


class HLAccount(BaseModel):
    """Hyperliquid account snapshot."""
    equity: float
    spot_usdc: float = 0.0
    total_equity: float = 0.0
    total_ntl: float = 0.0
    position: Optional[HLPosition] = None


class HLPosition(BaseModel):
    """Hyperliquid position on a single asset."""
    side: str  # 'long' or 'short'
    size_btc: float  # absolute position size
    entry_px: float
    unrealized_pnl: float
    leverage: float = 5.0


class HLCandleRow(BaseModel):
    """Raw candle from HL API (string values)."""
    t: int
    o: str
    h: str
    l: str
    c: str
    v: str


class HLExchangeResponse(BaseModel):
    """Response from HL exchange endpoint."""
    status: str
    response: Optional[Any] = None


class HLMetaResponse(BaseModel):
    """Response to {type: 'meta'} info call."""
    universe: list[dict[str, Any]] = Field(default_factory=list)


class HLSpotMetaResponse(BaseModel):
    """Response to {type: 'spotMeta'} info call."""
    universe: list[dict[str, Any]] = Field(default_factory=list)
    tokens: list[dict[str, Any]] = Field(default_factory=list)


class HLClearinghouseState(BaseModel):
    """Response to {type: 'clearinghouseState'} info call."""
    margin_summary: Optional[dict[str, Any]] = None
    asset_positions: list[dict[str, Any]] = Field(default_factory=list)


class HLSpotClearinghouseState(BaseModel):
    """Response to {type: 'spotClearinghouseState'} info call."""
    balances: list[dict[str, Any]] = Field(default_factory=list)


class HLMarket(BaseModel):
    """A tradeable market on Hyperliquid (perp or spot)."""
    coin: str
    type: str  # 'perp' or 'spot'
    category: str  # 'crypto', 'equity', 'commodity'
    sz_decimals: int = 5
    max_leverage: int = 1
    min_notional: Optional[float] = None


class AgentConfig(BaseModel):
    """Agent config stored in .agent-config.json."""
    mode: str = "OFF"  # 'OFF' or 'LIVE'
    min_ai_confidence: float = 0.8
    max_concurrent: int = 3
    max_trade_notional_usd: float = 200.0
    max_daily_loss_usd: float = -100.0
    min_market_volume_usd: float = 5_000_000.0
    max_total_notional_pct: float = 0.3
    cooldown_min: float = 60.0
    coin_allowlist: list[str] = Field(default_factory=list)
    coin_blocklist: list[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True
