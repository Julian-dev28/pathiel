"""derive_limits: equity + a RiskProfile + the book roster -> every number the
executor needs. PURE and deterministic — no network, no file I/O, no clock
reads, no randomness. Same inputs, same output, forever.

THE SHAPE OF A BOOK
Two kinds, told apart by which fields a `BookSpec` carries:

  risk-scaled   (`stop_pct` set) — most books. Size is derived:
                    notional = risk_budget_usd / stop_distance
                then capped by the profile's max leverage, the book's own
                (per-coin) leverage ceiling, and a margin share — see
                `_price_risk_scaled_book`. Bumped to the venue's $10 minimum
                only when the profile allows it AND the bumped risk% stays at
                or under `max_bumped_risk_pct`.
  fixed/structural (`fixed_min_equity_usd` set) — e.g. drawdown_ladder, whose
                economics (five resting rungs, 1x) aren't a risk-per-trade
                question at all. It either has enough equity to place one
                rung at Hyperliquid's minimum, or it doesn't. No risk sizing
                applies; `needs_equity_usd` is the fixed number.

WHY `needs_equity_usd` NEVER DEPENDS ON CURRENT EQUITY
Every book's feasibility threshold (_needs_equity_usd for risk-scaled books,
`fixed_min_equity_usd` for structural ones) is a pure function of the
RESOLVED PROFILE and the book's own parameters — never of the equity being
evaluated. `can_trade` is then a plain `equity_usd >= needs_equity_usd`
comparison. This is what makes "more equity never shrinks the feasible-book
set" true by construction rather than by luck: the threshold a book must
clear doesn't move depending on which equity you're asking about, so a higher
equity can only match more thresholds, never fewer. The leverage/margin-share
caps below are display-only refinements on top of an already-True can_trade;
they can shrink the SIZE shown, never flip a book from tradeable to not (see
the floor at the venue minimum in `_price_risk_scaled_book`).

MARGIN SHARE
`max_open_positions` slots could all be open at once, so one book cannot be
sized as if it owned 100% of usable margin — it gets `usable_margin /
max_open_positions`. This only matters for already-large notionals (the
$10-minimum floor below is what actually binds at low equity); see
services/risk/README.md for a worked $10k example where it does bind.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from contracts.risk.models import BookFeasibility, DerivedLimits, RiskProfile
from services.risk import presets

CONTRACT_VERSION = "1.0.0"


def equity_risk_budget_usd(equity_usd: float, risk_per_trade_pct: float) -> float:
    """Dollars at stake if the stop hits: equity x risk_per_trade_pct (a
    percent, e.g. 3.0 means 3%). The one-line formula every risk-scaled trade
    sizes against.

    Moved in from `pathiel.agents.sizing` 2026-10-23 (wave 2): that module's
    other two functions (`atr_equal_risk_notional`, `risk_of_ruin`) had no
    caller left once the offline backtest harnesses that used them
    (`scripts/backtest_logged.py`, `scripts/strategy_grid_search.py`) were
    retired, so the whole module went rather than keeping it alive for this
    one formula shared with nothing else.

    Returns 0.0 for non-positive inputs — a degenerate risk budget must read
    as "size nothing", never as a negative or NaN dollar figure a caller
    might subtract or divide by.
    """
    if equity_usd <= 0 or risk_per_trade_pct <= 0:
        return 0.0
    return equity_usd * risk_per_trade_pct / 100.0

_EPS = 1e-9


@dataclass(frozen=True)
class VenueRules:
    """Venue facts derive_limits must respect. Not part of the external
    contract (contracts/risk/models.py) — an internal input the caller builds.

    `min_order_notional_usd` defaults to Hyperliquid's documented $10 floor.
    Live callers (pathiel/agents/executor.py, services/risk/api.py) pass the
    real exchange constant instead: `pathiel.client.exchange.MIN_ORDER_USD`
    is $10.50, padded above the literal $10 so Hyperliquid's own coin-size
    rounding can never land a few cents under the true minimum. Passing that
    padded number here means "can_trade" always agrees with what the
    exchange will actually accept.
    """
    min_order_notional_usd: float = 10.0
    free_margin_floor_frac: float = 0.10   # mirrors executor.py's min_available_margin_pct default


@dataclass(frozen=True)
class BookSpec:
    """One book's sizing inputs. The caller (executor.py / api.py) builds the
    list from the live book registry; derive_limits treats it as data.

    Exactly one of (`stop_pct`, `fixed_min_equity_usd`) should be set — see
    the module docstring. Setting neither reports the book as untradeable
    with a reason saying so, rather than guessing a stop.
    """
    name: str
    enabled: bool = True
    stop_pct: Optional[float] = None
    max_leverage: Optional[float] = None          # per-book/per-coin cap; None = profile cap only
    fixed_min_equity_usd: Optional[float] = None
    fixed_description: str = ""


def _floor_eps(x: float, eps: float = _EPS) -> int:
    """Floor with a tiny epsilon so float noise (2.9999999999) doesn't floor
    to one less than the exact-math answer (3)."""
    return int(math.floor(x + eps))


def _needs_equity_usd(stop_pct: float, risk_per_trade_pct: float, allow_bump: bool,
                      max_bumped_risk_pct: float, min_notional_usd: float) -> float:
    """Smallest equity at which this book clears Hyperliquid's order minimum.

    organic: equity large enough that the risk-sized notional (equity x
        risk_per_trade_pct / stop_distance) reaches min_notional_usd on its
        own, no bump needed.
    bumped: equity large enough that bumping straight to min_notional_usd
        keeps the resulting risk% at or under max_bumped_risk_pct. Always
        <= organic when allowed (that is the entire point of the bump: reach
        the exchange floor at a lower equity than the organic path needs, by
        accepting a temporarily higher risk% that itself shrinks as equity
        grows past this threshold).

    Pure function of the book's own stop and the resolved profile — not of
    any particular current equity. See the module docstring for why that
    matters.
    """
    stop_frac = stop_pct / 100.0
    if stop_frac <= 0 or risk_per_trade_pct <= 0:
        return float("inf")
    organic = min_notional_usd * stop_frac * 100.0 / risk_per_trade_pct
    if not allow_bump or max_bumped_risk_pct <= 0:
        return organic
    bumped = min_notional_usd * stop_frac * 100.0 / max_bumped_risk_pct
    return min(organic, bumped)


def _derive_max_open_positions(equity_usd: float, risk_per_trade_usd: float,
                               daily_loss_limit_usd: float, max_leverage: float,
                               positions_cap: int, venue: VenueRules) -> int:
    """How many positions this equity, margin and daily-loss limit support at
    once — the smallest of three independent ceilings, floored at 1 (the
    contract requires max_open_positions >= 1; see RiskProfile.max_open_positions):

      structural    how many $-minimum positions could usable margin even
                    cover at the profile's leverage cap. Grows with equity.
      loss_budget   if every open slot stopped out THE SAME DAY, that total
                    loss must not exceed the daily loss limit:
                    floor(daily_loss_limit_usd / risk_per_trade_usd). Equity
                    cancels out of this ratio (both scale with equity), so
                    within one preset tier it is a true constant; it only
                    moves at a preset boundary, and the four presets'
                    (daily_loss_limit_pct / risk_per_trade_pct) ratios are
                    chosen non-decreasing (2, 3, 3, 4) specifically so this
                    never drops as equity crosses a tier.
      positions_cap the preset-tier ceiling (presets.py) — the plain-choice
                    simplicity bound ("one decision, not a juggling act" at
                    starter tier).
    """
    usable_margin = equity_usd * (1.0 - venue.free_margin_floor_frac)
    min_margin_per_slot = (venue.min_order_notional_usd / max_leverage) if max_leverage > 0 \
        else venue.min_order_notional_usd
    structural_slots = _floor_eps(usable_margin / min_margin_per_slot) if min_margin_per_slot > 0 else 0
    loss_budget_slots = _floor_eps(daily_loss_limit_usd / risk_per_trade_usd) if risk_per_trade_usd > 0 \
        else positions_cap
    return max(1, min(structural_slots, loss_budget_slots, positions_cap))


def _price_fixed_book(book: BookSpec, equity_usd: float) -> BookFeasibility:
    needed = float(book.fixed_min_equity_usd or 0.0)
    desc = book.fixed_description or "a fixed structural minimum"
    if equity_usd + _EPS >= needed:
        return BookFeasibility(
            book=book.name, enabled=True, can_trade=True,
            reason=f"Equity clears the ${needed:,.2f} floor: {desc}.",
        )
    return BookFeasibility(
        book=book.name, enabled=True, can_trade=False,
        needs_equity_usd=round(needed, 2),
        reason=(f"Needs ${needed:,.2f} of equity: {desc} "
                f"(add ${needed - equity_usd:,.2f})."),
    )


def _price_risk_scaled_book(book: BookSpec, equity_usd: float, resolved: RiskProfile,
                            risk_per_trade_usd: float, max_open_positions: int,
                            venue: VenueRules) -> BookFeasibility:
    stop_pct = float(book.stop_pct or 0.0)
    stop_frac = stop_pct / 100.0
    leverage_caps = [v for v in (resolved.max_leverage, book.max_leverage) if v and v > 0]
    leverage_cap = min(leverage_caps) if leverage_caps else 0.0

    needs = _needs_equity_usd(
        stop_pct, float(resolved.risk_per_trade_pct or 0.0), bool(resolved.allow_min_order_bump),
        float(resolved.max_bumped_risk_pct or 0.0), venue.min_order_notional_usd,
    )
    if equity_usd + _EPS < needs:
        shortfall = max(0.0, needs - equity_usd)
        bump_note = "" if resolved.allow_min_order_bump else " (bumping to the minimum is off for this profile)"
        return BookFeasibility(
            book=book.name, enabled=True, can_trade=False,
            stop_pct=stop_pct, needs_equity_usd=round(needs, 2),
            reason=(f"Hyperliquid's ${venue.min_order_notional_usd:,.2f} minimum order would "
                    f"risk too much of ${equity_usd:,.2f} at this book's {stop_pct:.1f}% stop{bump_note}. "
                    f"Add ${shortfall:,.2f} to trade this book."),
        )

    # risk_per_trade_usd is already equity_risk_budget_usd(equity_usd,
    # resolved.risk_per_trade_pct), computed once in derive_limits and shared
    # across every book in the roster — not recomputed per book.
    target_notional = (risk_per_trade_usd / stop_frac) if stop_frac > 0 else 0.0

    usable_margin = equity_usd * (1.0 - venue.free_margin_floor_frac)
    margin_share = usable_margin / max(1, max_open_positions)
    cap_leverage = (leverage_cap * equity_usd) if leverage_cap > 0 else target_notional
    cap_margin_share = (leverage_cap * margin_share) if leverage_cap > 0 else target_notional

    notional = min(target_notional, cap_leverage, cap_margin_share)
    bumped = notional < venue.min_order_notional_usd - _EPS
    notional = max(notional, venue.min_order_notional_usd)   # a tradeable book never shows below the venue floor

    margin_usd = (notional / leverage_cap) if leverage_cap > 0 else notional
    risk_usd = notional * stop_frac
    risk_pct = (risk_usd / equity_usd * 100.0) if equity_usd > 0 else 0.0

    if bumped:
        reason = (f"Sized up to Hyperliquid's ${venue.min_order_notional_usd:,.2f} minimum: "
                  f"risks ${risk_usd:,.2f} ({risk_pct:.1f}%) if the stop hits.")
    else:
        reason = (f"Fits: ${notional:,.2f} notional at {leverage_cap:.1f}x, "
                  f"risks ${risk_usd:,.2f} ({risk_pct:.1f}%) if the stop hits.")

    return BookFeasibility(
        book=book.name, enabled=True, can_trade=True,
        notional_usd=round(notional, 2), leverage=round(leverage_cap, 2),
        margin_usd=round(margin_usd, 2), stop_pct=stop_pct,
        risk_usd=round(risk_usd, 2), risk_pct=round(risk_pct, 2),
        bumped_to_min=bumped, needs_equity_usd=None, reason=reason,
    )


def _headline(can_trade: bool, equity_usd: float, min_equity_to_trade_usd: float,
             max_open_positions: int, risk_per_trade_usd: float,
             books: Optional[List[BookFeasibility]] = None) -> str:
    if can_trade:
        plural = "s" if max_open_positions != 1 else ""
        risks = sorted(b.risk_usd for b in (books or []) if b.can_trade and b.risk_usd)
        if risks and abs(risks[-1] - risks[0]) >= 0.01:
            cost = f"a stop-out costs ${risks[0]:,.2f} to ${risks[-1]:,.2f}"
        elif risks:
            cost = f"a stop-out costs about ${risks[0]:,.2f}"
        else:
            cost = f"risking about ${risk_per_trade_usd:,.2f} per trade"
        return f"You can trade {max_open_positions} position{plural} at a time; {cost}."
    if min_equity_to_trade_usd <= 0 or min_equity_to_trade_usd == float("inf"):
        return "No book is configured to trade yet."
    shortfall = max(0.0, min_equity_to_trade_usd - equity_usd)
    return f"Add ${shortfall:,.2f} more to place your first trade."


def _notes(resolved: RiskProfile, daily_loss_limit_usd: float, books: List[BookFeasibility]) -> List[str]:
    notes = [f"Daily pause after -${daily_loss_limit_usd:,.2f}."]
    bumped = [b.book for b in books if b.bumped_to_min]
    if bumped:
        # The exact dollar figure is already in each bumped book's own
        # `reason` row (it has the real venue minimum); this note adds the
        # risk-% context without repeating a number it would have to
        # duplicate (and could get wrong if the venue minimum isn't $10).
        cap = resolved.max_bumped_risk_pct
        notes.append(
            f"{', '.join(bumped)} sized up to the exchange minimum"
            + (f" (risk allowed up to {cap:.1f}% for this)." if cap is not None else ".")
        )
    blocked_fixed = [b for b in books if not b.can_trade and b.needs_equity_usd and b.stop_pct is None]
    for b in blocked_fixed:
        notes.append(f"{b.book} needs ${b.needs_equity_usd:,.2f} of equity to place its first order.")
    return notes


def derive_limits(equity_usd: float, profile: RiskProfile, books: List[BookSpec],
                  venue: Optional[VenueRules] = None) -> DerivedLimits:
    """Equity + profile + the book roster -> DerivedLimits. PURE. See the
    module docstring for the two book shapes and why feasibility is
    equity-threshold-monotonic by construction."""
    if equity_usd < 0:
        raise ValueError("equity_usd must be >= 0")
    venue = venue or VenueRules()

    preset_name, resolved = presets.resolve_profile(profile, equity_usd)
    risk_per_trade_usd = equity_risk_budget_usd(equity_usd, float(resolved.risk_per_trade_pct or 0.0))
    daily_loss_limit_usd = equity_usd * float(resolved.daily_loss_limit_pct or 0.0) / 100.0
    coherence_notes: List[str] = []
    # Coherence 1 (integrator, 2026-10-17): a trade's risk budget never exceeds
    # the daily pause. Live: a migrated config risked $2.40 per trade against
    # a $1.84 daily pause, so one budget-sized stop-out would end the day early.
    if daily_loss_limit_usd > 0 and risk_per_trade_usd > daily_loss_limit_usd + 1e-9:
        risk_per_trade_usd = daily_loss_limit_usd
        coherence_notes.append(
            f"Risk per trade capped at your daily pause (${daily_loss_limit_usd:,.2f}).")

    if profile.max_open_positions is not None:
        max_open = max(1, min(50, int(profile.max_open_positions)))
    else:
        cap = presets.positions_cap_for(preset_name)
        max_open = _derive_max_open_positions(
            equity_usd, risk_per_trade_usd, daily_loss_limit_usd,
            float(resolved.max_leverage or 0.0), cap, venue,
        )
    resolved = resolved.model_copy(update={"max_open_positions": max_open})

    def _price_all(max_open_now: int):
        return _price_books(books, equity_usd, resolved, risk_per_trade_usd, max_open_now, venue)

    rows, thresholds = _price_all(max_open)
    # Coherence 2 and 3. The minimum order can force one trade to risk more
    # than the pct-based daily pause (at $15, Hyperliquid's ~$10.50 minimum
    # with a 15% stop risks $1.57 vs an 8% pause of $1.22). Then the pause is
    # raised to that one trade, or the account could never trade at all, and
    # the enforced pct is raised with it so the executor agrees. Positions
    # are capped so every open stop-out together fits inside the pause.
    for _ in range(4):
        risks = [r.risk_usd for r in rows if r.can_trade and r.risk_usd]
        worst = max(risks, default=0.0)
        if worst > daily_loss_limit_usd + 1e-9 and equity_usd > 0:
            daily_loss_limit_usd = worst
            resolved = resolved.model_copy(update={
                "daily_loss_limit_pct": round(min(50.0, worst / equity_usd * 100.0), 4)})
            coherence_notes.append(
                f"Daily pause raised to ${worst:,.2f}: one minimum-size trade must fit in a day.")
        if worst > 0 and daily_loss_limit_usd > 0:
            fit = max(1, _floor_eps(daily_loss_limit_usd / worst))
            if fit < max_open:
                coherence_notes.append(
                    f"Capped at {fit} open position{'s' if fit != 1 else ''} so that every "
                    f"open stop-out together stays within the ${daily_loss_limit_usd:,.2f} daily pause.")
                max_open = fit
                resolved = resolved.model_copy(update={"max_open_positions": max_open})
                rows, thresholds = _price_all(max_open)
                continue
        break

    min_equity_to_trade = min(thresholds) if thresholds else float("inf")
    can_trade = any(r.can_trade for r in rows)
    min_equity_display = round(min_equity_to_trade, 2) if min_equity_to_trade != float("inf") else 0.0

    return DerivedLimits(
        equity_usd=equity_usd, preset_applied=preset_name, profile=resolved,
        risk_per_trade_usd=round(risk_per_trade_usd, 2),
        daily_loss_limit_usd=round(daily_loss_limit_usd, 2),
        max_open_positions=max_open,
        min_order_notional_usd=venue.min_order_notional_usd,
        min_equity_to_trade_usd=min_equity_display,
        can_trade=can_trade, books=rows,
        headline=_headline(can_trade, equity_usd, min_equity_to_trade, max_open, risk_per_trade_usd, rows),
        notes=_notes(resolved, daily_loss_limit_usd, rows) + coherence_notes,
    )


def _price_books(books, equity_usd, resolved, risk_per_trade_usd, max_open, venue):
    rows: List[BookFeasibility] = []
    thresholds: List[float] = []
    for b in books:
        if not b.enabled:
            rows.append(BookFeasibility(book=b.name, enabled=False, can_trade=False,
                                        reason="Book is disabled."))
            continue
        if b.fixed_min_equity_usd is not None:
            rows.append(_price_fixed_book(b, equity_usd))
            thresholds.append(float(b.fixed_min_equity_usd))
        elif b.stop_pct is not None:
            rows.append(_price_risk_scaled_book(
                b, equity_usd, resolved, risk_per_trade_usd, max_open, venue))
            thresholds.append(_needs_equity_usd(
                float(b.stop_pct), float(resolved.risk_per_trade_pct or 0.0),
                bool(resolved.allow_min_order_bump), float(resolved.max_bumped_risk_pct or 0.0),
                venue.min_order_notional_usd,
            ))
        else:
            rows.append(BookFeasibility(
                book=b.name, enabled=True, can_trade=False,
                reason="Book has no sizing rule configured (needs a stop_pct or a fixed minimum)."))
    return rows, thresholds


def book_feasibility(limits: DerivedLimits, book_name: str) -> Optional[BookFeasibility]:
    """Look up one book's row out of an already-derived DerivedLimits. Used by
    the executor wiring, which only cares about the one book it is about to
    trade."""
    return next((b for b in limits.books if b.book == book_name), None)
