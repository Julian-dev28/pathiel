"""Risk contract, v1: sizing that scales from a $10 account to a $100k one.

The user sets a handful of plain choices (RiskProfile). Every number the
executor needs is DERIVED from those choices and current equity by one pure
function in services/risk (derive_limits). No global "minimum equity" switch
blocks all trading: each trade is checked on its own (BookFeasibility), and
the answer always comes with a sentence a person can act on.

Venue facts the derivation must respect: Hyperliquid's minimum order is
$10 notional, and each coin has its own max leverage.

Routes (services/risk/api.py):
  GET  /api/risk/profile   -> RiskProfile
  PUT  /api/risk/profile   -> RiskProfile        body: RiskProfile
  GET  /api/risk/limits    -> DerivedLimits      (active account's equity)
  POST /api/risk/preview   -> DerivedLimits      body: PreviewIn (any equity, any profile)
  POST /api/risk/size      -> TradeSize          body: TradeSizeIn (active account's equity)

Function surface (services/risk/size.py, used by the MCP server):
  size_trade(equity, profile, books, entry_px=, stop_px=, ...) -> TradeSize
    How big ONE specific trade (entry, stop, optional leverage and targets)
    should be for this account under the same rules as the books.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.1.0"   # 1.1.0 (2026-10-23): risk/daily/bump caps widened to 100 (operator opts out of risk caps)

Preset = Literal["auto", "starter", "small", "standard", "pro", "custom"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RiskProfile(_M):
    """The only settings a user has to understand. "auto" picks the preset
    from equity; any explicit field overrides the preset's value."""
    preset: Preset = "auto"
    risk_per_trade_pct: Optional[float] = Field(default=None, gt=0, le=100)  # % of equity lost if the stop hits; 100 = no cap
    daily_loss_limit_pct: Optional[float] = Field(default=None, gt=0, le=100) # trading pauses for the day past this; 100 = never
    max_leverage: Optional[float] = Field(default=None, ge=1, le=50)
    max_open_positions: Optional[int] = Field(default=None, ge=1, le=50)     # None = derived from equity
    # Small accounts: Hyperliquid's $10 minimum can force more risk than
    # risk_per_trade_pct allows. Allowed only up to max_bumped_risk_pct.
    allow_min_order_bump: Optional[bool] = None
    max_bumped_risk_pct: Optional[float] = Field(default=None, gt=0, le=100)


class BookFeasibility(_M):
    book: str
    enabled: bool
    can_trade: bool
    notional_usd: Optional[float] = None
    leverage: Optional[float] = None
    margin_usd: Optional[float] = None
    stop_pct: Optional[float] = None
    risk_usd: Optional[float] = None
    risk_pct: Optional[float] = None                  # of equity
    bumped_to_min: bool = False                       # sized up to the $10 venue minimum
    needs_equity_usd: Optional[float] = None          # smallest equity at which this book can trade
    reason: str                                       # one plain sentence, always present


class DerivedLimits(_M):
    equity_usd: float
    preset_applied: Preset                            # never "auto": the preset auto resolved to
    profile: RiskProfile                              # fully resolved, every field set
    risk_per_trade_usd: float
    daily_loss_limit_usd: float                       # positive number of dollars
    max_open_positions: int
    min_order_notional_usd: float = 10.0
    min_equity_to_trade_usd: float                    # below this nothing can trade
    can_trade: bool
    books: List[BookFeasibility] = []
    headline: str                                     # one sentence for the top of the page
    notes: List[str] = []


class PreviewIn(_M):
    equity_usd: float = Field(ge=0)
    profile: RiskProfile = RiskProfile()


class TargetPayoff(_M):
    px: float
    gain_usd: float                                   # if price reaches px, at this size
    r_multiple: float                                 # gain / risk_usd


class TradeSize(_M):
    """One trade, sized. Every money field is None when can_trade is False."""
    side: Literal["long", "short"]                    # inferred: stop below entry = long
    entry_px: float
    stop_px: float
    stop_pct: float                                   # distance entry -> stop, % of entry
    can_trade: bool
    size: Optional[float] = None                      # coin units, rounded to the venue's size step
    notional_usd: Optional[float] = None
    leverage: Optional[float] = None                  # applied: min(requested, coin max, profile max)
    leverage_capped_by: Optional[Literal["profile", "coin"]] = None
    margin_usd: Optional[float] = None
    risk_usd: Optional[float] = None                  # lost if the stop fills
    risk_pct: Optional[float] = None                  # of equity
    bumped_to_min: bool = False                       # sized up to the venue minimum
    targets: List[TargetPayoff] = []
    reason: str                                       # one plain sentence, always present
    warnings: List[str] = []                          # e.g. best target pays under 1R


class TradeSizeIn(_M):
    """POST /api/risk/size body: one trade to size for the active account."""
    coin: str
    entry_px: float = Field(gt=0)
    stop_px: float = Field(gt=0)
    leverage: Optional[float] = Field(default=None, gt=0, le=100)
    targets: List[float] = []

