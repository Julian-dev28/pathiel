"""The "20x line": whether a desk's current call's stop sits inside
pathiel/agents/bracket.py's leverage rule at a chosen leverage, computed
fresh from the call's OWN entry/stop/targets.

Deliberately NOT the same number as `DeskAnalytics.ladder` (contracts/
strategies/models.py's `LadderRung`): the ladder prices a full-equity
position at the desk's configured `leverage_ladder` rungs for empirical
historical odds (P(liq), EV); this is the operator's own actual bracket
(the one the oil-tick loop placed, or is about to) checked against the
2026-10-23 rule in pathiel/agents/bracket.py, recomputable at any leverage
the dashboard's selector (5/10/20) asks for.

Backs `GET /api/desks/{desk_id}/line`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from contracts.strategies.models import Call


def _entry_price(call: Call) -> Optional[float]:
    if call.entry_px is not None:
        return call.entry_px
    if call.entry_zone:
        return (call.entry_zone[0] + call.entry_zone[-1]) / 2.0
    return None


def call_line(call: Call, coin_max_leverage: Optional[float], leverage: float) -> Dict[str, Any]:
    """liq px, stop distance as a share of the liquidation distance, the
    leverage-rule verdict at `leverage`, the R gate against the first
    target, and the R multiple to every target - all from this call's own
    entry/stop/targets. `ok=False` with a plain `reason` for a call this
    cannot be computed for (flat/pair side, no entry or stop yet, or no
    venue max-leverage reading) - never a crash or a made-up number."""
    if call.side not in ("long", "short"):
        return {"ok": False, "reason": f"no leverage line for a {call.side} call"}
    entry_px = _entry_price(call)
    if entry_px is None or call.stop_px is None:
        return {"ok": False, "reason": "the call has no entry/stop price yet"}
    if not coin_max_leverage:
        return {"ok": False, "reason": f"venue max leverage for {call.instrument} is unavailable right now"}

    from pathiel.agents.bracket import check_leverage, check_levels

    lev = check_leverage(entry_px, call.stop_px, leverage, coin_max_leverage)
    if "max_leverage_ok" not in lev:
        # check_leverage's early-exit shape (entry==stop, or a non-positive
        # input): no liq/stop-distance fields to report, just the reason.
        return {"ok": False, "reason": lev.get("reason", "invalid entry/stop/leverage")}

    r_to_targets: List[Dict[str, float]] = []
    denom = abs(entry_px - call.stop_px)
    if denom > 0:
        r_to_targets = [{"px": t, "r_multiple": round(abs(t - entry_px) / denom, 2)} for t in call.targets]

    first_tp = call.targets[0] if call.targets else None
    r_gate = check_levels(entry_px, call.stop_px, first_tp) if first_tp else None

    verdict = (f"{leverage:g}x OK" if lev.get("ok")
              else f"no {leverage:g}x: max {lev.get('max_leverage_ok')}x")

    return {
        "ok": bool(lev.get("ok")), "side": call.side, "leverage": leverage,
        "entry_px": entry_px, "stop_px": call.stop_px, "targets": list(call.targets),
        "liq_px": lev.get("liq_px"), "liq_dist_pct": lev.get("liq_dist_pct"),
        "stop_dist_pct": lev.get("stop_dist_pct"), "stop_frac_of_liq": lev.get("stop_frac_of_liq"),
        "max_leverage_ok": lev.get("max_leverage_ok"), "verdict": verdict,
        "r_gate": r_gate, "r_to_targets": r_to_targets,
    }


__all__ = ["call_line"]
