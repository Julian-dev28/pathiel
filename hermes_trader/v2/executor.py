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
from hermes_trader.v2 import ledger as v2_ledger
from hermes_trader.v2 import risk
from hermes_trader.v2.books import Intent
from hermes_trader.v2.dsl_exit import ExitPolicy, deregister_position, register_position

logger = logging.getLogger(__name__)

_LIVE_ENV = "HERMES_V2_LIVE"
BACKUP_SL_MAX_FRAC_OF_LIQ = 0.60
_SL_RETRY_SLEEP_S = 2.0

# Maker-first entries (VERIFIED_TRADERS.md §3.1): structured winners run 49%
# median maker notional at 1.38bps avg fee vs our 100% taker at 3.07bps, and
# both surviving v2 entry books are non-urgent by construction (3-5d holds).
# Entries rest post-only (ALO) at the touch; the 60s exit sub-cycle crosses
# via the existing IOC path after `maker_wait_min` unfilled.
ENTRY_MAKER_FIRST_DEFAULT = True
MAKER_WAIT_MIN_DEFAULT = 30.0
_PENDING_MAKERS_PATH = state_file(".v2_pending_makers.json")

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


# ── Maker-first plumbing ──────────────────────────────────────────────────────

def _load_pending() -> Dict[str, Dict[str, Any]]:
    try:
        import json
        raw = json.load(open(_PENDING_MAKERS_PATH))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_pending(pending: Dict[str, Dict[str, Any]]) -> None:
    try:
        import json
        with open(_PENDING_MAKERS_PATH, "w") as fh:
            json.dump(pending, fh)
    except Exception:
        pass


def _oid(p: Dict[str, Any]) -> Any:
    """Order id for api calls: numeric string -> int (HL), else pass through."""
    o = p.get("oid")
    try:
        return int(o)
    except (TypeError, ValueError):
        return o


def slippage_bps(side: str, ref_px: float, fill_px: float) -> Optional[float]:
    """Signed entry slippage vs the signal reference price, in bps.
    POSITIVE = paid worse than the signal ref (long filled higher / short lower)."""
    if ref_px <= 0 or fill_px <= 0:
        return None
    d = (fill_px - ref_px) / ref_px
    return round((d if side == "long" else -d) * 10_000.0, 2)


def _record_fill_meta(book: str, coin: str, side: str, signal_bar_t: int, *,
                      ref_px: float, fill_px: float, fill_path: str,
                      maker_sz: float = 0.0, taker_sz: float = 0.0,
                      oid: Any = None, wait_ms: int = 0) -> None:
    """Ungradeable meta row (horizon 0) tying the FILL to its signal so the
    maker-first change grades itself: fill path + slippage vs signal ref px
    land in the same v2_<book>.jsonl as the signal rows."""
    v2_ledger.record(
        book, coin=coin, side="meta_fill", signal_bar_t=int(signal_bar_t or 0),
        entry_ref_px=float(fill_px or 0.0), horizon_days=0.0, stop_pct=0.0,
        meta={"fill_path": fill_path, "ref_px": float(ref_px or 0.0),
              "fill_px": float(fill_px or 0.0),
              "slip_bps": slippage_bps(side, float(ref_px or 0.0), float(fill_px or 0.0)),
              "entry_side": side, "maker_sz": round(float(maker_sz), 8),
              "taker_sz": round(float(taker_sz), 8), "oid": oid,
              "wait_ms": int(wait_ms)})


def _protect_and_register(api: Any, coin: str, side: str, entry_px: float,
                          filled: float, stop_pct: float, hold_days: float,
                          leverage: int, max_frac: float) -> Dict[str, Any]:
    """Shared tail of every successful entry: backup on-exchange SL (the rail
    that works while the loop is dark) + DSL registration."""
    is_buy = side == "long"
    sl_px, sl_capped = backup_sl_price(entry_px, stop_pct, is_buy, leverage, max_frac)
    sl_res = api.place_hl_trigger_order(is_buy, filled, sl_px, "sl", coin)
    if not sl_res.get("ok"):
        time.sleep(_SL_RETRY_SLEEP_S)   # transient 429 class — one cheap retry
        sl_res = api.place_hl_trigger_order(is_buy, filled, sl_px, "sl", coin)
    sl_missing = not sl_res.get("ok")
    if sl_missing:
        logger.error(f"[v2-exec] backup SL FAILED twice for {coin} — position "
                     f"has NO server-side stop (DSL loop is sole protection)")
    else:
        actual_pct = abs(entry_px - sl_px) / entry_px * 100.0 if entry_px > 0 else 0.0
        logger.info(f"[v2-exec] backup SL {coin} @ {sl_px} "
                    f"({actual_pct:.1f}% spot{' [liq-capped]' if sl_capped else ''})")
    register_position(coin, side, entry_px,
                      policy=book_exit_policy(stop_pct, hold_days, leverage),
                      leverage=leverage)
    return {"backup_sl_px": sl_px, "backup_sl_capped": sl_capped,
            "sl_missing": sl_missing}


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
    if intent.coin in _load_pending():
        # A maker entry is already resting for this coin (claim held by the
        # original attempt) — the 60s sub-cycle owns its fate, not a re-signal.
        return _refused("maker_pending", intent)

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

        # Maker-first: rest a post-only ALO at the touch; the 60s exit
        # sub-cycle (check_pending_makers) finalizes the fill or crosses via
        # the IOC path after `maker_wait_min`. Falls straight through to IOC
        # when disabled, unsupported by the api, or ALO-rejected (would cross).
        entry_cfg = (cfg.get("entry") or {})
        maker_first = bool(entry_cfg.get("maker_first", ENTRY_MAKER_FIRST_DEFAULT))
        if maker_first and hasattr(api, "place_hl_maker_order"):
            mres = api.place_hl_maker_order(is_buy, size, mid, intent.coin)
            if mres.get("ok") and mres.get("order_id") and "avg_px" not in mres:
                pending = _load_pending()
                pending[intent.coin] = {
                    "book": intent.book, "coin": intent.coin, "side": intent.side,
                    "oid": mres["order_id"],
                    "size": float(mres.get("size") or size),
                    "limit_px": float(mres.get("limit_px") or 0.0) or mid,
                    "ref_px": float(intent.entry_ref_px or 0.0) or mid,
                    "signal_bar_t": int(intent.signal_bar_t or 0),
                    "stop_pct": float(intent.stop_pct),
                    "hold_days": float(intent.hold_days),
                    "leverage": leverage,
                    "notional_usd": float(intent.notional_usd),
                    "max_frac_of_liq": float(cfg.get("backup_sl_max_frac_of_liq",
                                                     BACKUP_SL_MAX_FRAC_OF_LIQ) or 0.0),
                    "placed_ms": int(time.time() * 1000),
                    "wait_min": float(entry_cfg.get("maker_wait_min",
                                                    MAKER_WAIT_MIN_DEFAULT)),
                }
                _save_pending(pending)
                logger.info(f"[v2-exec] maker-first {intent.coin} {intent.side}: ALO "
                            f"resting @ {pending[intent.coin]['limit_px']} "
                            f"oid={mres['order_id']} (cross fallback in "
                            f"{pending[intent.coin]['wait_min']:.0f}m)")
                return {"executed": False, "reason": "maker_pending",
                        "pending_maker": True, "book": intent.book,
                        "coin": intent.coin, "side": intent.side,
                        "order_id": mres["order_id"],
                        "limit_px": pending[intent.coin]["limit_px"]}
            logger.info(f"[v2-exec] maker-first {intent.coin}: ALO did not rest "
                        f"({mres.get('error', 'no oid')}) — crossing via IOC")

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

    max_frac = float(cfg.get("backup_sl_max_frac_of_liq", BACKUP_SL_MAX_FRAC_OF_LIQ) or 0.0)
    prot = _protect_and_register(api, intent.coin, intent.side, entry_px, filled,
                                 intent.stop_pct, intent.hold_days, leverage, max_frac)
    ref_px = float(intent.entry_ref_px or 0.0) or mid
    _record_fill_meta(intent.book, intent.coin, intent.side, intent.signal_bar_t,
                      ref_px=ref_px, fill_px=entry_px, fill_path="taker",
                      taker_sz=filled, oid=res.get("order_id"))
    return {
        "executed": True, "book": intent.book, "coin": intent.coin,
        "side": intent.side, "entry_px": entry_px, "size_usd": abs(filled) * entry_px,
        "order_id": res.get("order_id"), "fill_path": "taker", **prot,
    }


def check_pending_makers(cfg: Optional[Dict[str, Any]] = None, *,
                         order_api: Any = None,
                         now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    """60s exit-sub-cycle sweep of resting maker entries.

    filled          -> backup SL + DSL register + meta_fill row (fill_path maker)
    open, in window -> leave it resting
    open, expired   -> cancel, then cross the unfilled remainder via the IOC
                       path (fill_path taker, or mixed on a partial maker fill)
    dead, no fill   -> release the book's claim + meta_fill row (fill_path none)

    Shadow wall intact: refuses before touching any api; an EMPTY pending file
    returns without any api access at all. A failed cancel NEVER proceeds to
    cross (the resting order may have just filled — next sweep resolves it).
    """
    if not live_enabled():
        return []
    pending = _load_pending()
    if not pending:
        return []
    api = order_api if order_api is not None else _order_api()
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    cfg = cfg or {}
    out: List[Dict[str, Any]] = []
    changed = False
    for coin, p in list(pending.items()):
        try:
            st = api.order_fill_status(_oid(p), coin) or {}
        except Exception as exc:
            logger.warning(f"[v2-exec] maker status check failed for {coin}: {exc}")
            continue
        status = str(st.get("status") or "unknown")
        filled_sz = float(st.get("filled_sz") or 0.0)
        size = float(p.get("size") or 0.0)
        wait_ms = float(p.get("wait_min") or MAKER_WAIT_MIN_DEFAULT) * 60_000.0
        expired = now - int(p.get("placed_ms") or 0) >= wait_ms

        if status == "filled":
            out.append(_finalize_maker_entry(api, p, entry_px=float(p["limit_px"]),
                                             maker_sz=size or filled_sz, taker_sz=0.0,
                                             taker_px=0.0, now_ms=now))
            del pending[coin]
            changed = True
            continue
        if status in ("open", "unknown") and not expired:
            continue
        if status in ("open", "unknown"):
            cancel = {}
            try:
                cancel = api.cancel_orders(_oid(p), coin) or {}
            except Exception as exc:
                cancel = {"ok": False, "error": str(exc)}
            if not cancel.get("ok"):
                logger.warning(f"[v2-exec] maker cancel failed for {coin} "
                               f"oid={p['oid']}: {cancel.get('error')} — retrying next sweep")
                continue
            try:                       # pick up any fill between poll and cancel
                st2 = api.order_fill_status(_oid(p), coin) or {}
                filled_sz = max(filled_sz, float(st2.get("filled_sz") or 0.0))
            except Exception:
                pass

        # Order is dead (canceled/rejected, or cancelled by us just now).
        maker_sz = min(filled_sz, size) if size > 0 else filled_sz
        remaining = max(0.0, size - maker_sz)
        taker_sz, taker_px = 0.0, 0.0
        if remaining > 0:
            mid = float(api.get_hl_price(coin) or 0.0)
            min_ntl = float(api.min_entry_notional_usd(coin, mid) or 0.0) if mid > 0 else 0.0
            if mid > 0 and remaining * mid >= min_ntl:
                res = api.place_hl_order(p["side"] == "long", remaining, mid, coin)
                if res.get("ok"):
                    taker_px = float(res.get("avg_px") or 0.0) or mid
                    taker_sz = float(res.get("total_sz") or 0.0) or remaining
                else:
                    logger.warning(f"[v2-exec] maker fallback IOC failed for {coin}: "
                                   f"{res.get('error', 'unknown')}")
        total = maker_sz + taker_sz
        if total <= 0:
            claims = claims_registry()
            claims.release(coin, p["book"])
            claims.save()
            _record_fill_meta(p["book"], coin, p["side"], int(p.get("signal_bar_t") or 0),
                              ref_px=float(p.get("ref_px") or 0.0), fill_px=0.0,
                              fill_path="none", oid=p.get("oid"),
                              wait_ms=now - int(p.get("placed_ms") or 0))
            logger.info(f"[v2-exec] maker-first {coin} expired unfilled "
                        f"({status}) — claim released, no position")
            out.append({"executed": False, "filled": False, "book": p["book"],
                        "coin": coin, "side": p["side"], "fill_path": "none",
                        "reason": f"maker_dead_{status}"})
        else:
            entry_px = ((maker_sz * float(p["limit_px"])) + (taker_sz * taker_px)) / total
            out.append(_finalize_maker_entry(api, p, entry_px=entry_px,
                                             maker_sz=maker_sz, taker_sz=taker_sz,
                                             taker_px=taker_px, now_ms=now))
        del pending[coin]
        changed = True
    if changed:
        _save_pending(pending)
    return out


def _finalize_maker_entry(api: Any, p: Dict[str, Any], *, entry_px: float,
                          maker_sz: float, taker_sz: float, taker_px: float,
                          now_ms: int) -> Dict[str, Any]:
    """A pending maker entry became a position: protect it, register the DSL
    tracker, and write the self-grading meta_fill row."""
    coin, side, book = p["coin"], p["side"], p["book"]
    filled = maker_sz + taker_sz
    prot = _protect_and_register(api, coin, side, entry_px, filled,
                                 float(p["stop_pct"]), float(p["hold_days"]),
                                 int(p.get("leverage") or 1),
                                 float(p.get("max_frac_of_liq",
                                             BACKUP_SL_MAX_FRAC_OF_LIQ) or 0.0))
    fill_path = "maker" if taker_sz <= 0 else ("taker" if maker_sz <= 0 else "mixed")
    ref_px = float(p.get("ref_px") or 0.0)
    _record_fill_meta(book, coin, side, int(p.get("signal_bar_t") or 0),
                      ref_px=ref_px, fill_px=entry_px, fill_path=fill_path,
                      maker_sz=maker_sz, taker_sz=taker_sz, oid=p.get("oid"),
                      wait_ms=now_ms - int(p.get("placed_ms") or 0))
    logger.info(f"[v2-exec] maker-first fill {coin} {side}: {fill_path} @ {entry_px} "
                f"(ref {ref_px}, slip {slippage_bps(side, ref_px, entry_px)}bps)")
    return {"executed": True, "filled": True, "book": book, "coin": coin,
            "side": side, "entry_px": entry_px, "size_usd": abs(filled) * entry_px,
            "fill_path": fill_path, "order_id": p.get("oid"), **prot}


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
