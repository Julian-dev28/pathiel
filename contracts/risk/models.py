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
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0.0"

Preset = Literal["auto", "starter", "small", "standard", "pro", "custom"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RiskProfile(_M):
    """The only settings a user has to understand. "auto" picks the preset
    from equity; any explicit field overrides the preset's value."""
    preset: Preset = "auto"
    risk_per_trade_pct: Optional[float] = Field(default=None, gt=0, le=25)   # % of equity lost if the stop hits
    daily_loss_limit_pct: Optional[float] = Field(default=None, gt=0, le=50) # trading pauses for the day past this
    max_leverage: Optional[float] = Field(default=None, ge=1, le=50)
    max_open_positions: Optional[int] = Field(default=None, ge=1, le=50)     # None = derived from equity
    # Small accounts: Hyperliquid's $10 minimum can force more risk than
    # risk_per_trade_pct allows. Allowed only up to max_bumped_risk_pct.
    allow_min_order_bump: Optional[bool] = None
    max_bumped_risk_pct: Optional[float] = Field(default=None, gt=0, le=25)


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
