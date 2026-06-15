"""Risk-first position sizing — the Turtle "N" / Larry Hite / Ed Seykota school.

PURE FUNCTIONS ONLY. No network, no config reads, no side effects — so they are
trivially testable and carry zero live impact until a caller wires them in.

The production sizing today (executor.py) is:
    notional = equity * equity_fraction * leverage * conviction_mult
which is volatility-BLIND: a high-ATR 10x memecoin and a low-ATR 3x major sized
by the same formula carry wildly different dollar-risk-to-stop by accident
(Phase-1/Phase-2 audit finding). These helpers replace that with EQUAL DOLLAR
RISK PER TRADE: size so that, if the stop is hit, every trade loses the same
fixed fraction of equity regardless of the instrument's volatility or leverage.

Nothing here is enforced until the executor calls it (gated, default-off).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizingResult:
    notional_usd: float        # position notional to open ($)
    implied_leverage: float    # notional / equity (a consequence, not an input)
    risk_usd: float            # $ that will be lost if the stop is hit
    stop_distance_frac: float  # fractional price move from entry to stop
    clamped_by: str            # "" | "notional_cap" | "max_leverage" | "zero" — why size was reduced


def atr_equal_risk_notional(
    *,
    equity: float,
    risk_per_trade_pct: float,   # fraction of equity to risk if stopped (e.g. 0.01 = 1%)
    atr_abs: float,              # ATR in price units (e.g. get_hl_atr("4h",14,coin))
    entry_px: float,
    sl_atr_mult: float,          # stop distance = sl_atr_mult * ATR (matches the live backup SL)
    max_trade_notional_usd: float = 0.0,  # hard per-trade $ ceiling (0 = none)
    coin_max_leverage: int = 0,           # exchange per-coin max lev (0 = unknown/none)
    config_max_leverage: int = 0,         # operator leverage cap (0 = none)
) -> SizingResult:
    """Turtle-"N" equal-dollar-risk sizing.

    Solve  notional * stop_distance_frac == risk_per_trade_pct * equity
    so the loss-at-stop is a FIXED fraction of equity for every trade, then
    clamp by the per-trade notional ceiling and the max-leverage caps. Leverage
    is an OUTPUT (notional/equity), never an input — you size the bet to the risk,
    you don't pick leverage and discover the risk.

    Returns a SizingResult with notional 0 (clamped_by="zero") when inputs are
    degenerate (non-positive equity/atr/entry) — caller must treat 0 as "do not
    trade", never as "use a default".
    """
    if equity <= 0 or atr_abs <= 0 or entry_px <= 0 or risk_per_trade_pct <= 0 or sl_atr_mult <= 0:
        return SizingResult(0.0, 0.0, 0.0, 0.0, "zero")

    stop_distance_frac = (sl_atr_mult * atr_abs) / entry_px
    if stop_distance_frac <= 0:
        return SizingResult(0.0, 0.0, 0.0, 0.0, "zero")

    risk_usd_target = risk_per_trade_pct * equity
    notional = risk_usd_target / stop_distance_frac
    clamped_by = ""

    # Cap by max leverage (notional cannot exceed lev * equity). Use the tighter
    # of the operator cap and the exchange per-coin cap when both are present.
    lev_caps = [c for c in (coin_max_leverage, config_max_leverage) if c and c > 0]
    if lev_caps:
        max_notional_by_lev = min(lev_caps) * equity
        if notional > max_notional_by_lev:
            notional = max_notional_by_lev
            clamped_by = "max_leverage"

    # Hard per-trade $ ceiling.
    if max_trade_notional_usd and max_trade_notional_usd > 0 and notional > max_trade_notional_usd:
        notional = max_trade_notional_usd
        clamped_by = "notional_cap"

    implied_leverage = notional / equity if equity > 0 else 0.0
    # Realized risk reflects the (possibly clamped) notional, not the target.
    risk_usd = notional * stop_distance_frac
    return SizingResult(notional, implied_leverage, risk_usd, stop_distance_frac, clamped_by)


def risk_of_ruin(
    *,
    win_rate: float,           # 0..1
    payoff_ratio: float,       # avg win / avg loss (R multiple)
    risk_per_trade_pct: float, # fraction of equity risked per trade
    ruin_fraction: float = 1.0,  # drawdown that counts as "ruin" (1.0 = lose everything)
) -> float:
    """Approximate probability of eventual ruin (0..1) for a fixed-fractional bettor.

    Uses the standard gambler's-ruin per-unit approximation: model each trade as
    risking one "unit" (= risk_per_trade_pct of equity) to win `payoff_ratio`
    units with probability `win_rate`. The per-unit ruin probability is
        r = ((1 - edge_ratio) / (1 + edge_ratio)) ** units_to_ruin
    where edge_ratio derives from the edge per unit risked, and units_to_ruin is
    how many consecutive-units of capital exist before `ruin_fraction` is gone.
    This is an estimate for the dashboard, not a guarantee — its job is to make
    "this risk-per-trade is insane" visible BEFORE the account proves it.

    Returns 1.0 (certain ruin) for a non-positive edge, 0.0 for inputs that can't
    lose. Clamps to [0,1].
    """
    win_rate = max(0.0, min(1.0, win_rate))
    if payoff_ratio <= 0 or risk_per_trade_pct <= 0:
        return 1.0
    loss_rate = 1.0 - win_rate
    # Expected value per unit risked (in R). <=0 edge => ruin is certain over time.
    edge_per_unit = win_rate * payoff_ratio - loss_rate
    if edge_per_unit <= 0:
        return 1.0
    # Advantage 'a' normalized to total action per unit risked.
    a = edge_per_unit / (win_rate * payoff_ratio + loss_rate)
    a = max(0.0, min(0.999999, a))
    # Capital measured in units of risk before hitting the ruin threshold.
    units_to_ruin = max(1.0, (ruin_fraction) / risk_per_trade_pct)
    base = (1.0 - a) / (1.0 + a)
    ror = base ** units_to_ruin
    return max(0.0, min(1.0, ror))
