"""Capital rotation — the Phase-1 fix for the #1 cause of missed moves.

Phase-1 audit finding: 94% of missed big movers die at the 300% notional cap or
max_concurrent — i.e. the book is FULL, not the signal absent. Capital is
allocated first-come-first-served with no ranking, so a strong fresh signal can
never displace a weak, stale position. This module decides when it should.

PURE FUNCTION — no network, no side effects, fully testable. The caller (executor,
in SHADOW mode first) logs the decision; only when shadow_mode is off does it act.

Principle blend of the greats:
  - ride winners (Seykota/Livermore): NEVER evict a position that's still working
    (roe >= protect_winner_roe_pct) — let the trend run.
  - cut what isn't working (Dennis/Turtles): the eviction target is the WEAKEST
    non-winner (lowest ROE), and only after it's had min_hold_minutes to prove out.
  - opportunity cost (Druckenmiller: concentrate capital in the best ideas): only a
    genuinely strong fresh signal (composite >= min_candidate_composite) justifies
    paying the round-trip fees to rotate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class RotationDecision:
    should_rotate: bool
    evict_coin: Optional[str]      # position to close, if rotating
    evict_roe_pct: float
    reason: str                    # human-readable rationale (logged in shadow + live)


def decide_rotation(
    *,
    candidate_coin: str,
    candidate_composite: float,
    blocked_reasons: List[str],
    open_positions: List[Dict[str, Any]],   # each: {coin, roe_pct, age_minutes}
    min_candidate_composite: float,
    min_hold_minutes: float,
    protect_winner_roe_pct: float,
) -> RotationDecision:
    """Return whether to rotate the weakest non-winner out for `candidate_coin`.

    Only fires when the candidate was blocked PURELY by capital constraints
    (equity_risk / max_concurrent) — never overrides a real risk veto (regime,
    cooldown, liquidity, news, opposite-guard, confidence...).
    """
    NO = RotationDecision(False, None, 0.0, "")

    # 1. Only capital-saturation blocks are rotatable. If ANY other gate vetoed
    #    the trade, rotation must not resurrect it.
    capital_markers = ("exceed 300% of equity", "max positions reached")
    if not blocked_reasons:
        return NO
    if not all(any(m in r for m in capital_markers) for r in blocked_reasons):
        return RotationDecision(False, None, 0.0,
                                "blocked by a non-capital gate — not rotatable")

    # 2. The fresh signal must be strong enough to justify the round-trip.
    if candidate_composite < min_candidate_composite:
        return RotationDecision(False, None, 0.0,
                                f"candidate composite {candidate_composite:.0f} < "
                                f"{min_candidate_composite:.0f} — not worth a rotation")

    # 3. Don't churn into the same coin we'd evict; find eligible evictees:
    #    not strongly winning (ride winners) AND past the min hold (anti-churn).
    eligible = [
        p for p in open_positions
        if p.get("coin") and p.get("coin") != candidate_coin
        and float(p.get("roe_pct", 0.0)) < protect_winner_roe_pct
        and float(p.get("age_minutes", 0.0)) >= min_hold_minutes
    ]
    if not eligible:
        return RotationDecision(False, None, 0.0,
                                "no eligible evictee (all winners, too young, or none)")

    # 4. Evict the WEAKEST non-winner (lowest ROE).
    weakest = min(eligible, key=lambda p: float(p.get("roe_pct", 0.0)))
    evict_roe = float(weakest.get("roe_pct", 0.0))
    return RotationDecision(
        True, weakest["coin"], evict_roe,
        f"rotate: evict {weakest['coin']} (roe {evict_roe:+.1f}%, "
        f"age {float(weakest.get('age_minutes',0)):.0f}m) → {candidate_coin} "
        f"(composite {candidate_composite:.0f})",
    )
