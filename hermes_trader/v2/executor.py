"""v2 executor — entry + on-exchange backup SL + claims. LIVE is opt-in by env.

SHADOW SAFETY (the load-bearing design): every path that could place an exchange
order goes through `_order_api()`, which refuses to even IMPORT the order layer
unless the process was started with HERMES_V2_LIVE=1. `execute_intent`,
`close_coin` and `flatten_all` all check `live_enabled()` FIRST and return a
refusal before any injected/imported order api is touched — so in shadow mode an
order is structurally unreachable, not merely skipped (gate-tested).

Live semantics are a lean port of v1 agents/executor.maybe_execute for the
strategy-book path:
  - risk gates (v2.risk.entry_gates) before any order
  - claims registry: one book owns one coin, ever
  - IOC entry via place_hl_order, then an on-exchange reduce-only trigger SL
    immediately after the fill, capped by backup_sl_max_frac_of_liq (0.60) —
    the only rail that works while the loop is dark (the Mac slept 194.8h/15d)
  - DSL registration with the book's VALIDATED stop-or-horizon policy
    (protect_pct 9999 never arms the trail; exit is stop or horizon close)
  - reduce-only close semantics so a sub-$10 residual can never flip a position
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Set

from hermes_trader.agents.rebalancer_owned import ClaimsRegistry, state_file
from hermes_trader.v2 import risk
from hermes_trader.v2.books import Intent
from hermes_trader.v2.dsl_exit import ExitPolicy, deregister_position, register_position

logger = logging.getLogger(__name__)

_LIVE_ENV = "HERMES_V2_LIVE"
BACKUP_SL_MAX_FRAC_OF_LIQ = 0.60
_SL_RETRY_SLEEP_S = 2.0

# ── v2 claims registry ────────────────────────────────────────────────────────
# TRAP (keep documented AT the definition, spec §3 rail 4): a new v2 book MUST
# join this frozenset or every claim it makes is denied and any persisted claim
# it holds is scrubbed as a stale owner.
#
# v2 deliberately keeps its OWN claims file (.v2_claims.json), NOT v1's
# .rebalancer_claims.json: v1's registry self-heals by deleting claims from any
# book outside ITS active set, so sharing the file would let the v1 loop scrub
# v2's claims mid-flight (and vice versa after the Phase-4 teardown).
V2_ACTIVE_CLAIM_BOOKS = frozenset({"extreme_fade", "funding_spike_short", "xs_momentum"})
_CLAIMS_PATH = state_file(".v2_claims.json")

_claims_singleton: Optional[ClaimsRegistry] = None


def claims_registry(path: Optional[str] = None) -> ClaimsRegistry:
    """Shared v2 ClaimsRegistry (reuses the v1 class + semantics verbatim)."""
    global _claims_singleton
    if path is not None:
        return ClaimsRegistry(path, active_books=set(V2_ACTIVE_CLAIM_BOOKS)).load()
    if _claims_singleton is None:
        _claims_singleton = ClaimsRegistry(_CLAIMS_PATH,
                                           active_books=set(V2_ACTIVE_CLAIM_BOOKS))
    return _claims_singleton.load()


def reset_claims_singleton() -> None:
    """Test hook: drop the cached registry so redirected paths take effect."""
    global _claims_singleton
    _claims_singleton = None


# ── Shadow gate ───────────────────────────────────────────────────────────────

class ShadowModeViolation(RuntimeError):
    """Raised when the order layer is requested without HERMES_V2_LIVE=1."""


def live_enabled() -> bool:
    return os.environ.get(_LIVE_ENV) == "1"


def _order_api():
    """The ONLY door to order placement. Refuses to import the exchange layer
    unless the process explicitly opted into live trading via HERMES_V2_LIVE=1."""
    if not live_enabled():
        raise ShadowModeViolation(
            f"order path requested in shadow mode (set {_LIVE_ENV}=1 to go live)")
    from hermes_trader.client import exchange
    return exchange


def _refused(reason: str, intent: Optional[Intent] = None, **extra: Any) -> Dict[str, Any]:
    out = {"executed": False, "reason": reason, **extra}
    if intent is not None:
        out["book"] = intent.book
        out["coin"] = intent.coin
    return out


# ── Backup on-exchange stop ───────────────────────────────────────────────────

def backup_sl_price(entry_px: float, stop_pct: float, is_long: bool,
                    leverage: int, max_frac_of_liq: float = BACKUP_SL_MAX_FRAC_OF_LIQ
                    ) -> tuple[float, bool]:
    """Trigger price for the server-side disaster stop (v1 executor port).

    Distance = stop_pct of entry, capped inside the approximate liquidation
    buffer: max_frac_of_liq / leverage of entry (e.g. a 20% stop at 12x becomes
    a 5% trigger — the exchange flattens us well before liquidation even if the
    process is dead). Returns (price, capped?).
    """
    dist = entry_px * float(stop_pct) / 100.0
    capped = False
    if leverage > 0 and max_frac_of_liq > 0:
        max_dist = entry_px * (float(max_frac_of_liq) / float(leverage))
        if 0 < max_dist < dist:
            dist = max_dist
            capped = True
    px = max(0.0, entry_px - dist) if is_long else entry_px + dist
    return px, capped


def book_exit_policy(stop_pct: float, hold_days: float, leverage: int = 1) -> ExitPolicy:
    """The VALIDATED book exit shape: hard stop or horizon close, NO trail.

    protect_pct=9999 never arms phase 2 (exactly the structure every surviving
    book was validated with — extreme_fade_live._fade_analysis port)."""
    lev = max(1, int(leverage))
    return ExitPolicy(
        max_loss_pct=float(stop_pct),
        max_loss_roe_pct=float(stop_pct) * lev,
        protect_pct=9999.0,
        retrace_threshold=0.5,
        hard_timeout_minutes=float(hold_days) * 1440.0,
        breakeven_trigger_pct=0.0,
        breakeven_lock_pct=0.0,
        stale_flat_timeout_minutes=0.0,
        consecutive_breaches_required=1,
        atr_stop_enabled=False,
        noise_band_enabled=False,
    )


# ── Entry ─────────────────────────────────────────────────────────────────────

def execute_intent(intent: Intent, *, cfg: Dict[str, Any], equity: float,
                   available: float, held_coins: Set[str],
                   total_open_notional: float, day_volume_usd: float,
                   daily_pnl: float, order_api: Any = None) -> Dict[str, Any]:
    """Open one book intent through gates → claim → order → backup SL → DSL.

    Shadow mode: refuses IMMEDIATELY (before gates, claims, or any api access) —
    order placement is impossible without HERMES_V2_LIVE=1 regardless of what
    `order_api` a caller injects.
    """
    if not live_enabled():
        return _refused("shadow_mode_order_blocked", intent)

    reasons = risk.entry_gates(
        side=intent.side, notional_usd=intent.notional_usd,
        day_volume_usd=day_volume_usd, equity=equity, available=available,
        total_open_notional=total_open_notional, daily_pnl=daily_pnl,
        risk_cfg=(cfg.get("risk") or {}),
    )
    if reasons:
        return _refused("risk_gates", intent, blocked_by=reasons)
    if intent.coin in held_coins:
        return _refused("already_held", intent)

    claims = claims_registry()
    if not claims.claim(intent.coin, intent.book):
        return _refused(f"claimed_by_{claims.owner_of(intent.coin)}", intent)
    claims.save()

    try:
        api = order_api if order_api is not None else _order_api()
        mid = float(api.get_hl_price(intent.coin) or 0.0)
        if mid <= 0:
            raise ValueError(f"invalid_price_for_{intent.coin}")
        min_notional = float(api.min_entry_notional_usd(intent.coin, mid) or 0.0)
        if min_notional > 0 and intent.notional_usd < min_notional:
            raise ValueError(f"below_min_order_notional (${intent.notional_usd:.2f} "
                             f"< ${min_notional:.2f})")
        size = float(api.entry_size_for_notional(intent.coin, intent.notional_usd, mid) or 0.0)
        if size <= 0:
            raise ValueError("entry_size_zero")
        leverage = max(1, min(int(intent.leverage or 1),
                              int(api.get_max_leverage(intent.coin) or 1)))
        api.set_leverage(intent.coin, leverage)
        is_buy = intent.side == "long"
        res = api.place_hl_order(is_buy, size, mid, intent.coin)
        if not res.get("ok"):
            raise ValueError(f"order_failed: {res.get('error', 'unknown')}")
    except ShadowModeViolation:
        claims.release(intent.coin, intent.book)
        claims.save()
        raise
    except Exception as exc:
        claims.release(intent.coin, intent.book)
        claims.save()
        return _refused(str(exc), intent)

    entry_px = float(res.get("avg_px") or 0.0) or mid
    filled = float(res.get("total_sz") or 0.0) or size

    # Backup on-exchange stop — the rail that works while the loop is dark.
    max_frac = float(cfg.get("backup_sl_max_frac_of_liq", BACKUP_SL_MAX_FRAC_OF_LIQ) or 0.0)
    sl_px, sl_capped = backup_sl_price(entry_px, intent.stop_pct, is_buy, leverage, max_frac)
    sl_res = api.place_hl_trigger_order(is_buy, filled, sl_px, "sl", intent.coin)
    if not sl_res.get("ok"):
        time.sleep(_SL_RETRY_SLEEP_S)   # transient 429 class — one cheap retry
        sl_res = api.place_hl_trigger_order(is_buy, filled, sl_px, "sl", intent.coin)
    sl_missing = not sl_res.get("ok")
    if sl_missing:
        logger.error(f"[v2-exec] backup SL FAILED twice for {intent.coin} — position "
                     f"has NO server-side stop (DSL loop is sole protection)")
    else:
        actual_pct = abs(entry_px - sl_px) / entry_px * 100.0 if entry_px > 0 else 0.0
        logger.info(f"[v2-exec] backup SL {intent.coin} @ {sl_px} "
                    f"({actual_pct:.1f}% spot{' [liq-capped]' if sl_capped else ''})")

    register_position(intent.coin, intent.side, entry_px,
                      policy=book_exit_policy(intent.stop_pct, intent.hold_days, leverage),
                      leverage=leverage)
    return {
        "executed": True, "book": intent.book, "coin": intent.coin,
        "side": intent.side, "entry_px": entry_px, "size_usd": abs(filled) * entry_px,
        "order_id": res.get("order_id"), "backup_sl_px": sl_px,
        "backup_sl_capped": sl_capped, "sl_missing": sl_missing,
    }


# ── Close ─────────────────────────────────────────────────────────────────────

def close_coin(coin: str, *, book: Optional[str] = None,
               order_api: Any = None, account_state: Optional[Dict[str, Any]] = None
               ) -> Dict[str, Any]:
    """Reduce-only market close of `coin` (lean port of v1 close_position_market).

    reduce_only means a sub-$10 residual can never flip the position (the BIRD
    churn loop). Deregisters the DSL tracker + cancels the stranded SL bracket
    + releases the book claim on success.
    """
    if not live_enabled():
        return {"ok": False, "coin": coin, "reason": "shadow_mode_order_blocked"}

    api = order_api if order_api is not None else _order_api()
    if account_state is None:
        from hermes_trader.client.hl_client import fetch_account_state, resolve_user_address
        user = resolve_user_address()
        if not user:
            return {"ok": False, "coin": coin, "error": "no_user_address"}
        account_state = fetch_account_state(user, include_hip3=True) or {}

    pos = next((p for p in account_state.get("asset_positions", [])
                if p.get("position", {}).get("coin") == coin), None)
    claims = claims_registry()
    if not pos:
        deregister_position(coin, "long")
        deregister_position(coin, "short")
        if book:
            claims.release(coin, book)
            claims.save()
        return {"ok": True, "coin": coin, "noop": "already_flat"}
    try:
        szi = float(pos["position"].get("szi", "0") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "coin": coin, "error": "bad_szi"}
    if szi == 0:
        deregister_position(coin, "long")
        deregister_position(coin, "short")
        return {"ok": True, "coin": coin, "noop": "zero_szi"}

    is_long = szi > 0
    side = "long" if is_long else "short"
    mid = float(api.get_hl_price(coin) or 0.0)
    if mid <= 0:
        return {"ok": False, "coin": coin, "error": f"invalid_price_for_{coin}"}
    res = api.place_hl_order(is_buy=not is_long, size=abs(szi), mid_price=mid,
                             coin=coin, reduce_only=True)
    out = {**res, "coin": coin, "side": side}
    if res.get("ok"):
        deregister_position(coin, side)
        api.cancel_open_orders_for_coin(coin)
        if book:
            claims.release(coin, book)
        else:
            owner = claims.owner_of(coin)
            if owner:
                claims.release(coin, owner)
        claims.save()
    return out


def flatten_all(account_state: Dict[str, Any], *, order_api: Any = None) -> List[Dict[str, Any]]:
    """Kill-switch flatten: reduce-only close every open position (equity>0 guarded
    upstream by risk.kill_switch's degraded-read branch)."""
    if not live_enabled():
        return [{"ok": False, "reason": "shadow_mode_order_blocked"}]
    results = []
    for p in account_state.get("asset_positions", []) or []:
        coin = p.get("position", {}).get("coin")
        if coin:
            results.append(close_coin(coin, order_api=order_api,
                                      account_state=account_state))
    return results
