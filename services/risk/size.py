"""size_trade: how big ONE specific trade should be for this account.

derive_limits answers "what can this account trade" for the configured
books. A person (or an agent through MCP) looking at a desk thesis asks a
narrower question: "WTI long at 98.87, stop 88.22, 5x: how much?" This
function answers it with the same rules, so a manual trade is sized exactly
like the bot would size a book with that stop:

  risk budget   = derive_limits(...).risk_per_trade_usd (profile % of equity,
                  coherence-capped by the daily pause)
  notional      = risk budget / stop distance, capped by leverage x margin share
  leverage      = min(requested, coin max, profile max)
  venue minimum = bumped up to it when the profile allows, else can't trade

PURE: no I/O. The caller supplies equity, profile, the live book roster
(services.risk.books.all_book_specs) and the venue rules.

Where it breaks: `sz_decimals` rounding can move notional a few cents; the
minimum-order check runs AFTER rounding so the answer never shows a size the
venue would reject. Liquidation price is NOT computed here (it needs the
coin's margin table, which the MCP server already owns).
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

from contracts.risk.models import RiskProfile, TargetPayoff, TradeSize
from services.risk.derive import BookSpec, VenueRules, _price_risk_scaled_book, derive_limits


def _round_size(size: float, sz_decimals: Optional[int], entry_px: float, min_notional: float) -> float:
    """Round down to the venue's size step, then up one step if that fell
    under the minimum order. No step known: unrounded."""
    if sz_decimals is None or sz_decimals < 0:
        return size
    step = 10.0 ** -sz_decimals
    down = math.floor(size / step + 1e-9) * step
    if down * entry_px + 1e-9 < min_notional:
        down = math.ceil(min_notional / entry_px / step - 1e-9) * step
    return round(down, sz_decimals)


def size_trade(equity_usd: float, profile: RiskProfile, books: Sequence[BookSpec], *,
               entry_px: float, stop_px: float, leverage: Optional[float] = None,
               coin_max_leverage: Optional[float] = None, targets: Sequence[float] = (),
               venue: Optional[VenueRules] = None, sz_decimals: Optional[int] = None) -> TradeSize:
    venue = venue or VenueRules()
    side = "long" if stop_px < entry_px else "short"
    if entry_px <= 0 or stop_px <= 0 or entry_px == stop_px:
        return TradeSize(side=side, entry_px=entry_px, stop_px=stop_px, stop_pct=0.0,
                         can_trade=False, reason="Entry and stop must be positive and different.")
    stop_pct = abs(entry_px - stop_px) / entry_px * 100.0
    if equity_usd <= 0:
        return TradeSize(side=side, entry_px=entry_px, stop_px=stop_px, stop_pct=round(stop_pct, 3),
                         can_trade=False, reason="This account has no equity to size against.")

    limits = derive_limits(equity_usd, profile, list(books), venue)
    asked = [v for v in (leverage, coin_max_leverage) if v and v > 0]
    row = _price_risk_scaled_book(
        BookSpec(name="trade", stop_pct=stop_pct, max_leverage=min(asked) if asked else None),
        equity_usd, limits.profile, limits.risk_per_trade_usd, limits.max_open_positions, venue)

    if not row.can_trade or not row.notional_usd:
        return TradeSize(side=side, entry_px=entry_px, stop_px=stop_px, stop_pct=round(stop_pct, 3),
                         can_trade=False, reason=row.reason)

    applied = float(row.leverage or 0.0)
    capped_by = None
    profile_max = float(limits.profile.max_leverage or 0.0)
    if leverage and applied + 1e-9 < leverage:
        capped_by = "coin" if coin_max_leverage and applied >= coin_max_leverage - 1e-9 \
            and (not profile_max or coin_max_leverage <= profile_max) else "profile"

    size = _round_size(row.notional_usd / entry_px, sz_decimals, entry_px,
                       venue.min_order_notional_usd)
    notional = size * entry_px
    risk_usd = notional * stop_pct / 100.0
    margin = notional / applied if applied > 0 else notional
    sign = 1.0 if side == "long" else -1.0
    payoffs: List[TargetPayoff] = []
    for px in targets:
        if px and px > 0:
            gain = notional * (px / entry_px - 1.0) * sign
            payoffs.append(TargetPayoff(px=px, gain_usd=round(gain, 2),
                                        r_multiple=round(gain / risk_usd, 2) if risk_usd else 0.0))

    warnings: List[str] = []
    best_r = max((t.r_multiple for t in payoffs), default=None)
    if best_r is not None and best_r < 1.0:
        warnings.append(f"Best target pays {best_r:.2f}R: the stop costs more than the "
                        "targets pay (1R = the loss at the stop).")

    lev_note = f" (capped by your {capped_by} max)" if capped_by else ""
    reason = (f"{side.capitalize()} ${notional:,.2f} at {applied:g}x{lev_note}: "
              f"${margin:,.2f} margin, loses ${risk_usd:,.2f} ({risk_usd / equity_usd * 100:.1f}%) "
              f"if the stop at {stop_px:g} fills.")
    if row.bumped_to_min:
        reason += f" Sized up to the ${venue.min_order_notional_usd:,.2f} venue minimum."
    return TradeSize(
        side=side, entry_px=entry_px, stop_px=stop_px, stop_pct=round(stop_pct, 3), can_trade=True,
        size=size, notional_usd=round(notional, 2), leverage=applied, leverage_capped_by=capped_by,
        margin_usd=round(margin, 2), risk_usd=round(risk_usd, 2),
        risk_pct=round(risk_usd / equity_usd * 100.0, 2), bumped_to_min=bool(row.bumped_to_min),
        targets=payoffs, reason=reason, warnings=warnings,
    )
