"""Four presets, hand-tuned once, documented here. Everything else is derived.

Julien's ADHD brief: a handful of plain choices, everything else derived and
explained. The plain choice is which of these four tiers you're in — "auto"
makes even that choice for you, from equity alone. Nobody hand-tunes a
per-book notional_usd again; derive.py turns these five numbers plus current
equity into every book's size, every time, the same way.

Why four, and why these bands
    STARTER   $0      - $1,000     one position, bump allowed, highest risk%.
              Below ~$1k, Hyperliquid's $10 order minimum is the binding
              constraint, not "how much should I risk" — the whole point of
              the bump allowance is to let a $15 account clear that minimum
              without faking the risk number. One position at a time because
              that's what the ADHD brief asked for at this size: one decision,
              not a juggling act.

              max_bumped_risk_pct=15.0 is deliberately generous, not an
              arbitrary round number: book_params.py's own FLOOR_STOP_PCT
              (15%) is the widest stop a book falls back to when it sets no
              override, and the real exchange minimum is $10.50
              (pathiel.client.exchange.MIN_ORDER_USD). Solving
              needs_equity_usd for that worst-realistic case against this
              cap gives $10.50 x 0.15 x 100 / 15 = $10.50 — i.e. a brand
              new, zero-configuration account can bump-trade the instant it
              clears the exchange's OWN minimum, which is the literal
              "$10 account" Julien asked for, with no migration and no
              manual tuning required. A tighter-stop book (the 6% most
              currently-live books actually use) stays far under this
              ceiling in practice; it only gets approached by the widest
              fallback stop at the smallest realistic equity.
    SMALL     $1,000  - $10,000    a few positions, bump still allowed (the
              $10 floor is usually clear by here, but not always on a wide-stop
              book), tighter risk% than starter because the $ amounts start to
              matter.
    STANDARD  $10,000 - $50,000    several positions, bump OFF — at this size
              the $10 exchange minimum is never the constraint, so "allow a
              bigger risk% to hit the minimum" has nothing to fix; leaving it
              on would just be a dead knob.
    PRO       $50,000 +            most positions, lowest risk%, bump OFF,
              lowest leverage cap. Capital preservation matters more than
              expression once the dollar amounts are this large.

The numbers move in ONE direction as equity grows: risk_per_trade_pct down,
daily_loss_limit_pct down, max_leverage down, position-count ceiling up. There
is no tier where a bigger account is allowed to take MORE risk per trade or
MORE leverage than a smaller one — that direction has no justification and
services/risk/tests/test_derive.py's property tests enforce it (monotonicity
of the feasible-book set and of max_open_positions as equity rises).

PROFIT_AUDIT.md section 1/2: liquidations were 92% of the last 60 days' net
loss, caused by leverage wide enough that the position's OWN stop sat past the
exchange's liquidation price. These presets' max_leverage numbers are chosen
so that a book's typical stop (10-20%) comfortably clears
BACKUP_SL_MAX_FRAC_OF_LIQ (book_params.py, 0.60) at the preset's leverage cap:
e.g. starter's 10x -> widest honored stop 6%; the existing
stop_honoring_leverage_cap in executor.py still does the per-trade clamp, this
just keeps the PRESET ceiling from fighting it on every book.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from contracts.risk.models import RiskProfile

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class PresetDef:
    name: str
    equity_min_usd: float
    equity_max_usd: Optional[float]      # None = no upper bound (pro)
    risk_per_trade_pct: float
    daily_loss_limit_pct: float
    max_leverage: float
    allow_min_order_bump: bool
    max_bumped_risk_pct: float
    max_positions_cap: int               # the preset-tier ceiling fed into derive_limits
    blurb: str                           # one plain sentence, used by the README and the API


# Ordered low-equity to high-equity; resolve_preset_name walks this in order.
_ORDER: Tuple[str, ...] = ("starter", "small", "standard", "pro")

PRESETS: Dict[str, PresetDef] = {
    "starter": PresetDef(
        name="starter", equity_min_usd=0.0, equity_max_usd=1_000.0,
        risk_per_trade_pct=3.0, daily_loss_limit_pct=8.0, max_leverage=10.0,
        allow_min_order_bump=True, max_bumped_risk_pct=15.0, max_positions_cap=1,
        blurb="One position at a time. Risks up to 3% a trade (as much as 15% "
              "if the $10 exchange minimum forces it on a wide-stop book), "
              "stops for the day after an 8% loss.",
    ),
    "small": PresetDef(
        name="small", equity_min_usd=1_000.0, equity_max_usd=10_000.0,
        risk_per_trade_pct=2.0, daily_loss_limit_pct=6.0, max_leverage=8.0,
        allow_min_order_bump=True, max_bumped_risk_pct=4.0, max_positions_cap=3,
        blurb="Up to 3 positions at once. Risks up to 2% a trade (4% if "
              "bumped to the exchange minimum), stops for the day after 6%.",
    ),
    "standard": PresetDef(
        name="standard", equity_min_usd=10_000.0, equity_max_usd=50_000.0,
        risk_per_trade_pct=1.5, daily_loss_limit_pct=5.0, max_leverage=6.0,
        allow_min_order_bump=False, max_bumped_risk_pct=2.0, max_positions_cap=6,
        blurb="Up to 6 positions at once. Risks up to 1.5% a trade, stops for "
              "the day after 5%. The $10 minimum never needs a bump at this size.",
    ),
    "pro": PresetDef(
        name="pro", equity_min_usd=50_000.0, equity_max_usd=None,
        risk_per_trade_pct=1.0, daily_loss_limit_pct=4.0, max_leverage=5.0,
        allow_min_order_bump=False, max_bumped_risk_pct=1.0, max_positions_cap=10,
        blurb="Up to 10 positions at once. Risks up to 1% a trade, stops for "
              "the day after 4%. Capital preservation over expression.",
    ),
}

# "custom" (RiskProfile.preset == "custom"): the user has opted all the way out
# of a preset. Any field they still left unset falls back to the SAFEST
# (pro-tier) number for that field, never a looser one — a half-filled custom
# profile fails toward caution, not toward risk. See resolve_profile().
_CUSTOM_FALLBACK = PRESETS["pro"]


def resolve_preset_name(equity_usd: float) -> str:
    """Which preset "auto" picks at this equity. Never "auto" or "custom"."""
    for name in _ORDER:
        p = PRESETS[name]
        if p.equity_max_usd is None or equity_usd < p.equity_max_usd:
            return name
    return _ORDER[-1]  # unreachable (pro's equity_max_usd is None) — defensive only


def resolve_profile(profile: RiskProfile, equity_usd: float) -> Tuple[str, RiskProfile]:
    """Fill every preset-sourced field profile.py left as None.

    Returns (concrete_preset_name, resolved_profile). The resolved profile's
    `max_open_positions` is NOT decided here — it stays whatever the caller
    passed (None or explicit); derive_limits() does the equity/margin/loss-
    budget derivation for that one field, since it needs the risk budget and
    daily loss limit in dollars, which this function does not compute.

    Any field already set on `profile` (not None) is an explicit user choice
    and is carried through untouched — "any explicit field overrides the
    preset's value" (contracts/risk/models.py RiskProfile docstring).
    """
    if profile.preset == "custom":
        name = "custom"
        base = _CUSTOM_FALLBACK
    elif profile.preset == "auto":
        name = resolve_preset_name(equity_usd)
        base = PRESETS[name]
    else:
        name = profile.preset
        base = PRESETS[name]

    resolved = profile.model_copy(update={
        "preset": name,
        "risk_per_trade_pct": (profile.risk_per_trade_pct if profile.risk_per_trade_pct is not None
                               else base.risk_per_trade_pct),
        "daily_loss_limit_pct": (profile.daily_loss_limit_pct if profile.daily_loss_limit_pct is not None
                                 else base.daily_loss_limit_pct),
        "max_leverage": (profile.max_leverage if profile.max_leverage is not None
                         else base.max_leverage),
        "allow_min_order_bump": (profile.allow_min_order_bump if profile.allow_min_order_bump is not None
                                 else base.allow_min_order_bump),
        "max_bumped_risk_pct": (profile.max_bumped_risk_pct if profile.max_bumped_risk_pct is not None
                                else base.max_bumped_risk_pct),
    })
    return name, resolved


def positions_cap_for(preset_name: str) -> int:
    """The preset-tier position-count ceiling derive_limits() clamps against.

    "custom" uses the same safe (pro-tier) ceiling as its other fallbacks.
    """
    if preset_name == "custom":
        return _CUSTOM_FALLBACK.max_positions_cap
    return PRESETS[preset_name].max_positions_cap
