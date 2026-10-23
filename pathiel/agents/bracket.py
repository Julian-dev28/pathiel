"""Bracket trades: one entry with an exact stop and take-profit, placed as
asked. The operator's direct-trade path (Claude places it via the MCP
`place_bracket` tool). Also carries `close_position`, the MCP `close_position`
tool's handler — moved here 2026-10-23 (wave 2: the autonomous scan/research/
execute engine in `executor.py` is retired; Claude trades through this module
and the MCP server now, so `close_position_market` is the one piece of
`executor.py` that still has a live caller).

Everything here is deterministic: the R gate, the side, the one-position rule
and the post-close sweep. The judgment (which entry, which stop) comes in as
arguments, never from in here.

Exchange calls go through `ex`, the pathiel.client.exchange module by
default, so tests pass a fake.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ── minimum tradable equity ──────────────────────────────────────────────────
# Hyperliquid rejects orders under roughly $10 notional, so this is the absolute
# floor below which no order can clear the exchange at all.
#
# Moved out of `executor.py` 2026-10-23 (wave 2) alongside `close_position`:
# `pathiel/dashboard.py`'s risk panel calls `min_tradable_equity` directly as
# its fallback when the risk service can't load, and `scripts/profit_audit.py`
# calls it too — both real, surviving callers that have nothing to do with
# the retired autonomous engine.
MIN_TRADABLE_EQUITY_USD = 25.0


def book_margin_requirement(config: Optional[Dict[str, Any]] = None) -> float:
    """Equity needed for EVERY enabled book to hold a position simultaneously.

    Sum of each book's margin (notional / leverage), grossed up by the
    free-margin floor the executor used to enforce. With every per-book
    config block retired (headless desk, 2026-10-23), this sums to 0 for any
    config in practice today — kept as a derivation, not a constant, so a
    reintroduced per-entry block with `enabled`/`notional_usd`/`shadow_only`
    keys is still counted correctly rather than silently ignored.

    A book is a block carrying `enabled`, `notional_usd` AND `shadow_only`.
    That third key is the discriminator, and it is load-bearing: `enabled` +
    `notional_usd` alone also matches gate RELAXATIONS like the retired
    `thin_short_relax`, which carried a notional it applied to but opened no
    position of its own.
    """
    cfg = config or {}
    try:
        free_floor = float(cfg.get("min_available_margin_pct", 0.10) or 0.0)
    except (TypeError, ValueError):
        free_floor = 0.10
    if not 0.0 <= free_floor < 1.0:
        free_floor = 0.10

    total = 0.0
    for block in cfg.values():
        if not isinstance(block, dict):
            continue
        if not {"enabled", "notional_usd", "shadow_only"} <= block.keys():
            continue
        if not block.get("enabled", False):
            continue
        try:
            notional = float(block.get("notional_usd") or 0)
            leverage = max(1, int(block.get("leverage", 1) or 1))
        except (TypeError, ValueError):
            continue
        total += notional / leverage
    return total / (1.0 - free_floor) if total > 0 else 0.0


def min_tradable_equity(config: Optional[Dict[str, Any]] = None) -> float:
    """The floor below which trading refuses to run.

    Precedence:
      1. an explicit `min_tradable_equity_usd` — deliberate override for a
         small test account
      2. what the enabled book set actually costs, so the system refuses to run
         a crippled subset of itself
      3. MIN_TRADABLE_EQUITY_USD, the exchange-minimum backstop

    A negative or non-numeric override falls back rather than disabling the
    floor: a typo must not silently unlock trading on a dust account.
    """
    cfg = config or {}
    if "min_tradable_equity_usd" in cfg:
        try:
            v = float(cfg["min_tradable_equity_usd"])
            if v >= 0:
                return v
        except (TypeError, ValueError):
            pass
        return MIN_TRADABLE_EQUITY_USD
    return max(MIN_TRADABLE_EQUITY_USD, book_margin_requirement(cfg))


DEFAULT_MIN_R = 1.5
# The stop may use at most this share of the distance to liquidation. At 20x
# on Brent liquidation sits ~2.5% away and one average hour moves 2.7%; the
# only 20x trades that survived were the ones whose stop sat far inside that
# distance (operator's record, 2026-10-23). Half is the rule.
DEFAULT_MAX_STOP_FRAC_OF_LIQ = 0.5


def liq_estimate(entry_px: float, leverage: float, max_leverage: float, is_long: bool) -> float:
    """Hyperliquid isolated-margin liquidation price:
    liq = entry - side * (entry/leverage - mmr*entry) / (1 - mmr*side), mmr = 1/(2*max_leverage).
    Checked against the venue's own number on 2026-10-23: 104.41 computed vs 104.43 shown
    (20x Brent long from 107.16); the gap is fees and funding."""
    side = 1.0 if is_long else -1.0
    mmr = 1.0 / (2.0 * max_leverage)
    return entry_px - side * (entry_px / leverage - mmr * entry_px) / (1.0 - mmr * side)


def max_leverage_for_stop(entry_px: float, stop_px: float, max_leverage: float,
                          max_stop_frac: float = DEFAULT_MAX_STOP_FRAC_OF_LIQ) -> int:
    """Highest whole leverage at which this stop still uses no more than
    `max_stop_frac` of the liquidation distance. Closed form of liq_estimate:
    the liquidation distance as a fraction of entry is (1/L - mmr)/(1 - mmr*side),
    so L <= 1 / (stop_frac/max_stop_frac * (1 - mmr*side) + mmr)."""
    is_long = stop_px < entry_px
    side = 1.0 if is_long else -1.0
    mmr = 1.0 / (2.0 * max_leverage)
    s = abs(entry_px - stop_px) / entry_px
    if s <= 0:
        return 1
    lev = 1.0 / ((s / max_stop_frac) * (1.0 - mmr * side) + mmr)
    return max(1, min(int(max_leverage), int(lev + 1e-9)))


def check_leverage(entry_px: float, stop_px: float, leverage: float, coin_max_leverage: float,
                   max_stop_frac: float = DEFAULT_MAX_STOP_FRAC_OF_LIQ) -> Dict[str, Any]:
    """The 20x rule: the stop must sit inside `max_stop_frac` of the way to
    liquidation, so a stop fires long before the venue does. Returns the
    liquidation price, both distances, and when it fails, the highest
    leverage that would pass with this stop."""
    if min(entry_px, stop_px, leverage, coin_max_leverage) <= 0 or entry_px == stop_px:
        return {"ok": False, "reason": "entry, stop, leverage and coin max leverage must be positive"}
    is_long = stop_px < entry_px
    liq = liq_estimate(entry_px, leverage, coin_max_leverage, is_long)
    liq_dist = abs(entry_px - liq)
    stop_dist = abs(entry_px - stop_px)
    frac = stop_dist / liq_dist if liq_dist > 0 else float("inf")
    out: Dict[str, Any] = {
        "ok": frac <= max_stop_frac + 1e-9, "leverage": leverage, "liq_px": round(liq, 6),
        "liq_dist_pct": round(liq_dist / entry_px * 100, 3), "stop_dist_pct": round(stop_dist / entry_px * 100, 3),
        "stop_frac_of_liq": round(frac, 3), "max_stop_frac": max_stop_frac,
        "max_leverage_ok": max_leverage_for_stop(entry_px, stop_px, coin_max_leverage, max_stop_frac),
    }
    if not out["ok"]:
        out["reason"] = (f"stop uses {frac:.0%} of the liquidation distance at {leverage:g}x "
                         f"(max {max_stop_frac:.0%}); this stop allows at most {out['max_leverage_ok']}x")
    return out


def check_levels(entry_px: float, stop_px: float, tp_px: float,
                 min_r: float = DEFAULT_MIN_R) -> Dict[str, Any]:
    """Side and reward:risk from the three prices. ok False with a plain
    reason when they don't form a valid bracket or pay under `min_r`."""
    if min(entry_px, stop_px, tp_px) <= 0:
        return {"ok": False, "reason": "prices must be positive"}
    if stop_px < entry_px < tp_px:
        side = "long"
    elif tp_px < entry_px < stop_px:
        side = "short"
    else:
        return {"ok": False, "reason": "entry must sit between stop and target"}
    r = abs(tp_px - entry_px) / abs(entry_px - stop_px)
    out = {"ok": r + 1e-9 >= min_r, "side": side, "r_multiple": round(r, 2), "min_r": min_r}
    if not out["ok"]:
        out["reason"] = f"pays {r:.2f}R, under the {min_r}R minimum"
    return out


def place_bracket(coin: str, *, entry_px: Optional[float], stop_px: float, tp_px: float,
                  size: float, leverage: int, min_r: float = DEFAULT_MIN_R,
                  coin_max_leverage: Optional[float] = None,
                  max_stop_frac: float = DEFAULT_MAX_STOP_FRAC_OF_LIQ,
                  dry_run: bool = False, ex: Any = None) -> Dict[str, Any]:
    """Place one bracket. entry_px None = market now (IOC), checked against
    the live mid; otherwise a resting limit with stop and target attached.

    Refuses under min_r, refuses when the stop sits too close to liquidation
    for this leverage (check_leverage; needs coin_max_leverage), and refuses
    when the coin already has a position or a resting entry, so a repeated
    call (a loop tick, a retry) can never stack a second trade.
    """
    if ex is None:
        from pathiel.client import exchange as ex   # noqa: N813
    market = entry_px is None
    ref_px = float(ex.get_hl_price(coin)) if market else float(entry_px)
    gate = check_levels(ref_px, stop_px, tp_px, min_r)
    base = {"coin": coin, "mode": "market" if market else "limit", "entry_px": ref_px,
            "stop_px": stop_px, "tp_px": tp_px, "size": size, "leverage": leverage, **gate}
    if not gate["ok"]:
        return {**base, "placed": False}
    if coin_max_leverage:
        lev_gate = check_leverage(ref_px, stop_px, leverage, coin_max_leverage, max_stop_frac)
        base["liq"] = {k: lev_gate[k] for k in ("liq_px", "liq_dist_pct", "stop_dist_pct",
                                                 "stop_frac_of_liq", "max_leverage_ok") if k in lev_gate}
        if not lev_gate["ok"]:
            return {**base, "ok": False, "placed": False, "reason": lev_gate["reason"]}
    if size <= 0:
        return {**base, "ok": False, "placed": False, "reason": "size must be positive"}

    user = ex.resolve_user_address()
    pos = ex._position_size_on_coin(user, coin)
    if pos is None:
        return {**base, "ok": False, "placed": False, "reason": "position read failed; not trading blind"}
    if pos != 0:
        return {**base, "ok": False, "placed": False, "reason": f"already in a position ({pos}) on {coin}"}
    entries = [o for o in ex._resting_orders_for_coin(user, coin) if not o.get("reduceOnly")]
    if entries:
        return {**base, "ok": False, "placed": False,
                "reason": f"{len(entries)} entry order(s) already resting on {coin}; cancel first"}
    if dry_run:
        return {**base, "placed": False, "dry_run": True}

    ex.cancel_stranded_reduce_only_orders(coin)   # old triggers must not hit the new trade
    lev = ex.set_leverage(coin, int(leverage), is_cross=False, strict=True)
    if not lev.get("ok"):
        return {**base, "ok": False, "placed": False, "reason": f"set_leverage failed: {lev.get('error')}"}
    is_buy = gate["side"] == "long"

    if not market:
        res = ex.place_hl_bracket_order(coin, is_buy, size, float(entry_px), stop_px, tp_px)
        return {**base, "ok": bool(res.get("ok")), "placed": bool(res.get("entry", {}).get("ok")),
                "result": res}

    entry = ex.place_hl_order(is_buy, size, ref_px, coin)
    filled = float(entry.get("total_sz") or 0)
    if not entry.get("ok") or filled <= 0:
        return {**base, "ok": False, "placed": False, "result": {"entry": entry}}
    sl = ex.place_hl_trigger_order(is_buy, filled, stop_px, "sl", coin)
    tp = ex.place_hl_trigger_order(is_buy, filled, tp_px, "tp", coin)
    out = {**base, "ok": bool(sl.get("ok")), "placed": True, "filled_sz": filled,
           "avg_px": entry.get("avg_px"), "result": {"entry": entry, "sl": sl, "tp": tp}}
    if not sl.get("ok"):
        out["reason"] = "FILLED WITHOUT A STOP: place one now"
    return out


def sweep(coin: str, ex: Any = None) -> Dict[str, Any]:
    """One tick of housekeeping for `coin`: cancel reduce-only orders left on a
    flat coin (the sibling of a stop or target that fired), and report whether
    an open position has a stop. Never opens or closes a position."""
    if ex is None:
        from pathiel.client import exchange as ex   # noqa: N813
    user = ex.resolve_user_address()
    pos = ex._position_size_on_coin(user, coin)
    if pos is None:
        return {"coin": coin, "ok": False, "reason": "position read failed"}
    orders = ex._resting_orders_for_coin(user, coin)
    if pos == 0:
        entries = [o for o in orders if not o.get("reduceOnly")]
        attached_ids = ex.attached_child_oids(orders)
        attached = [o for o in orders if o.get("reduceOnly") and int(o.get("oid") or 0) in attached_ids]
        # The stop/target a resting entry carries (normalTpsl children) are
        # protection, not leftovers; cancel_stranded_* skips them by oid and
        # cancels only the reduce-only rows no entry claims.
        n = ex.cancel_stranded_reduce_only_orders(coin)
        return {"coin": coin, "ok": True, "position": 0.0, "cancelled_stranded": n,
                "resting_entries": [{"oid": o.get("oid"), "px": o.get("limitPx"), "sz": o.get("sz")}
                                    for o in entries],
                **({"attached_to_entry": [{"type": o.get("orderType"), "px": o.get("triggerPx"), "sz": o.get("sz")}
                                          for o in attached]} if attached else {})}
    stops = [o for o in orders if o.get("reduceOnly") and "Stop" in str(o.get("orderType", ""))]
    tps = [o for o in orders if o.get("reduceOnly") and "Take Profit" in str(o.get("orderType", ""))]
    covered = sum(float(o.get("sz") or 0) for o in stops)
    return {"coin": coin, "ok": covered + 1e-9 >= abs(pos), "position": pos,
            "stop_px": [o.get("triggerPx") for o in stops], "tp_px": [o.get("triggerPx") for o in tps],
            "stop_covers": covered,
            **({} if covered + 1e-9 >= abs(pos) else {"reason": "POSITION NOT FULLY COVERED BY A STOP"})}


def close_position(coin: str) -> Dict[str, Any]:
    """Market-close any open perp position for `coin`.

    Moved out of `pathiel/agents/executor.py` 2026-10-23 (wave 2), trimmed to
    its live-money essentials: the retired executor's DSL tracker lookup
    (`dsl_exit`), trade-outcome ledger (`memory.record_close`) and loss-
    cooldown arm (`memory.set_loss_cooldown`) all read or wrote state that no
    surviving module produces or consumes any more — Claude is the only
    trader on the wallet, there is no DSL exit loop re-registering trackers,
    and no win-rate/payoff panel reads the outcome ledger. Carrying that dead
    bookkeeping forward would just be a second, pointless place state could
    go stale. Leverage for the realized-PnL% calc now comes straight off the
    live Hyperliquid position (`position.leverage`) instead of a tracker
    guess captured at entry time — strictly more accurate, since it is the
    venue's own number at the moment of the close.

    Returns `entry_px`, `fill_px`, `spot_pct`, `realized_pnl_pct` (leveraged,
    net of taker fees) and `fees_pct` whenever the close fills with a
    parseable avgPx.
    """
    from pathiel.client.exchange import (
        cancel_open_orders_for_coin,
        cancel_stranded_reduce_only_orders,
        get_hl_price,
        place_hl_order,
    )
    from pathiel.client.hl_client import fetch_account_state, resolve_user_address

    user = resolve_user_address()
    if not user:
        return {"ok": False, "coin": coin, "error": "no_user_address"}

    # include_hip3=True so we can resolve HIP-3 positions (xyz:MU, vntl:*, ...).
    # Without this every close call for a HIP-3 position would fall into the
    # `already_flat` branch even when the position is real.
    state = fetch_account_state(user, include_hip3=True)
    pos = next(
        (p for p in state.get("asset_positions", [])
         if p.get("position", {}).get("coin") == coin),
        None,
    )
    if not pos:
        # Already flat. It closed outside this path (an exchange-side stop or
        # TP filled, a manual close) — clear any sibling reduce-only order so
        # stale brackets don't pile up. Re-checks flatness on the coin's own
        # dex first; reduce-only orders only.
        cancel_stranded_reduce_only_orders(coin)
        return {"ok": True, "coin": coin, "noop": "already_flat"}

    try:
        szi = float(pos["position"].get("szi", "0") or 0)
        entry_px = float(pos["position"].get("entryPx") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "coin": coin, "error": "bad_szi"}
    if szi == 0:
        cancel_stranded_reduce_only_orders(coin)   # same as already_flat above
        return {"ok": True, "coin": coin, "noop": "zero_szi"}

    is_long = szi > 0
    side = "long" if is_long else "short"
    mid_price = get_hl_price(coin)
    if mid_price <= 0:
        return {"ok": False, "coin": coin, "error": f"invalid_price_for_{coin}"}

    # HL stores leverage as {"value": N, "type": "cross"|"isolated"}; a bare
    # int on older/synthesized records. This is the venue's live number, not
    # a cached guess from entry time.
    leverage_obj = pos["position"].get("leverage")
    if isinstance(leverage_obj, dict):
        leverage = int(leverage_obj.get("value", 1) or 1)
    else:
        leverage = int(leverage_obj or 1)

    # reduce_only: a close must only FLATTEN. Without it, the $10-min size floor in
    # place_hl_order overshoots a sub-$10 position and flips it to the opposite side
    # (the BIRD short<->long churn loop). reduce_only makes HL ignore the excess.
    res = place_hl_order(is_buy=not is_long, size=abs(szi), mid_price=mid_price, coin=coin,
                         reduce_only=True)
    out: Dict[str, Any] = {**res, "coin": coin, "side": side,
                            "entry_px": entry_px, "leverage": leverage}

    if res.get("ok"):
        # Cancel the now-stranded reduce-only SL/TP trigger bracket so stale
        # orders don't pile up and reject a future reduce-only order on this coin.
        cancel_open_orders_for_coin(coin)
        fill_px = res.get("avg_px")
        if fill_px and entry_px > 0:
            # Spot move from the perspective of the position: long earns when
            # mark rises, short earns when mark falls.
            if is_long:
                spot_pct = (fill_px - entry_px) / entry_px * 100
            else:
                spot_pct = (entry_px - fill_px) / entry_px * 100
            # 2 round-trip taker fills at 2.5bps x leverage
            fees_pct = 0.025 * 2 * leverage
            out["fill_px"] = fill_px
            out["spot_pct"] = round(spot_pct, 4)
            out["realized_pnl_pct"] = round(spot_pct * leverage - fees_pct, 4)
            out["fees_pct"] = round(fees_pct, 4)
    return out
