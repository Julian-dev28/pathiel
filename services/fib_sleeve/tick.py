"""One tick of the fib sleeve. Deterministic: no LLM, the rule is mechanical.

    run_tick(cfg, live=False, now_ms=..., deps=...) -> the decision (a dict)

A tick reads the sub-account (position, resting orders, equity, fills) and
Brent candles, then does at most ONE thing:

  flat, nothing resting   rest the limit at the fib level with its stop and
                          target attached (`bracket.place_bracket`, so the
                          2R floor, the leverage rule and the one-position
                          check are the desk's own), sized from the
                          sub-account's equity
  flat, entry resting     keep it while ITS OWN swing mode still names that
                          level (a resting entry from the other mode, left
                          over from a `cfg.swing` change, is managed under
                          that mode, not cancelled on the flip alone);
                          cancel it when the swing changed, the level is
                          spent, the weekend opened or the kill switch fired
  in a position           move the stop to the entry once price covered 75%
                          of the way to target (`bracket.set_stop`, tighter
                          only); close it at the weekend open, or if no stop
                          covers it

`live=False` (the dry run) performs every read, computes the same decision
and returns it under the same keys with `"mode": "dry_run"`: the exchange
facade it is given refuses every signing call and the store refuses every
write. `cfg.enabled` False: a live tick returns "disabled" before any read.

Everything a tick touches comes in through `Deps`, so the gate tests run
the whole tick on fakes.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from pathiel.models.types import Candle
from services.fib_sleeve import ledger, rule
from services.fib_sleeve.config import SleeveConfig, sleeve_dir
from services.fib_sleeve.store import Store
from services.strategies.weekend import weekend_closed

DAY_MS = 86_400_000
DRY_RUN_LOOKBACK_MS = 30 * DAY_MS   # fills a dry run reads when the sleeve has no start yet


@dataclass
class Deps:
    ex: Any                                                   # the exchange, bound to the sub-account
    bracket: Any                                              # pathiel.agents.bracket
    fetch_candles: Callable[[str, str, int], List[Candle]]
    fetch_fills: Callable[[str, int], List[Dict[str, Any]]]
    equity: Callable[[str, str], Dict[str, Any]]              # subaccount.account_equity
    role: Callable[[str], Dict[str, Any]]                     # subaccount.account_role
    master: Callable[[], str]                                 # the signing account's address
    store: Store


def default_deps(cfg: SleeveConfig, live: bool, state_dir: Optional[str] = None) -> Deps:
    """The real thing. Dry run: a read-only exchange facade and a read-only
    store, so no code path can sign or write."""
    from pathiel.agents import bracket
    from pathiel.client import hl_client, subaccount
    return Deps(ex=subaccount.AccountExchange(cfg.account, read_only=not live), bracket=bracket,
                fetch_candles=hl_client.fetch_hl_candles, fetch_fills=hl_client.fetch_user_fills_by_time,
                equity=subaccount.account_equity, role=subaccount.account_role,
                master=hl_client.resolve_user_address,
                store=Store(sleeve_dir(state_dir), read_only=not live))


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def _px(o: Dict[str, Any], key: str) -> float:
    try:
        return float(o.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _rows(orders: List[Dict[str, Any]], label: str) -> List[Dict[str, Any]]:
    return [o for o in orders if o.get("reduceOnly") and label in str(o.get("orderType", ""))]


def _close(ex: Any, coin: str, pos: float) -> Dict[str, Any]:
    """Flatten with a reduce-only IOC, then cancel what is left resting."""
    mid = float(ex.get_hl_price(coin))
    res = ex.place_hl_order(is_buy=pos < 0, size=abs(pos), mid_price=mid, coin=coin, reduce_only=True)
    out = {"ok": bool(res.get("ok")), "result": res}
    if res.get("ok"):
        out["cancelled"] = ex.cancel_open_orders_for_coin(coin)
    return out


def run_tick(cfg: SleeveConfig, *, live: bool, now_ms: int, deps: Deps) -> Dict[str, Any]:
    d: Dict[str, Any] = {"desk": cfg.desk_id, "coin": cfg.coin, "account": cfg.account,
                         "variant": cfg.variant_name, "mode": "live" if live else "dry_run",
                         "enabled": cfg.enabled, "utc": _iso(now_ms), "ts_ms": now_ms}

    def done(action: str, why: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
        d["action"] = action
        if why:
            d["why"] = why
        d.update(extra)
        if live and action != "disabled":
            deps.store.append_log(d)
        return d

    if live and not cfg.enabled:
        return done("disabled", "enabled is false: nothing read, nothing placed")
    if not cfg.enabled:
        d["note"] = "enabled is false: a live tick would do nothing; this is what an enabled one would do"

    ex, coin, user = deps.ex, cfg.coin, cfg.account
    state = deps.store.load()

    # ── reads ────────────────────────────────────────────────────────────
    pos = ex._position_size_on_coin(user, coin)
    if pos is None:
        return done("error", "position read failed; not trading blind")
    orders = ex._resting_orders_for_coin(user, coin)
    eq = deps.equity(user, coin)
    if not eq.get("ok"):
        return done("error", f"equity read failed: {eq.get('error')}")
    entries = [o for o in orders if not o.get("reduceOnly")]
    flat = pos == 0
    d["account_state"] = {"equity": round(eq["equity"], 4), "mode": eq.get("mode"), "source": eq.get("source"),
                          "position": pos, "resting_entries": len(entries), "resting_orders": len(orders)}

    if live:
        role, master = deps.role(user), str(deps.master() or "").lower()
        if role.get("role") != "subAccount" or not master or role.get("master") != master:
            return done("error", f"{user} is not a sub-account of the signing account {master or '(none)'}: "
                                 f"venue says {role}")
        if not state.get("epoch_ms"):
            if not flat or entries:
                return done("error", "first live tick needs a flat sub-account with nothing resting")
            state = {"epoch_ms": now_ms, "starting_equity_usd": cfg.starting_equity_usd or eq["equity"],
                     "kill": None}
            deps.store.save(state)
            d["started"] = {"epoch_utc": _iso(now_ms), "starting_equity_usd": state["starting_equity_usd"]}

    # ── the record and the kill switch ───────────────────────────────────
    epoch = int(state.get("epoch_ms") or now_ms - DRY_RUN_LOOKBACK_MS)
    trades, open_trade = ledger.round_trips(deps.fetch_fills(user, epoch), coin)
    start_eq = cfg.starting_equity_usd or state.get("starting_equity_usd") or (eq["equity"] if flat else None)
    d["record"] = {**ledger.summary(trades), "starting_equity_usd": start_eq, "since_utc": _iso(epoch)}
    kill = state.get("kill")
    if not kill:
        why = ledger.kill_reason(cfg, trades, start_eq, eq["equity"], flat)
        if why:
            kill = {"reason": why, "at_ms": now_ms, "at_utc": _iso(now_ms)}
            d["kill_switch_tripped"] = True
            if live:
                deps.store.save({**state, "kill": kill})
    d["kill"] = kill

    hours = rule.closed(deps.fetch_candles(coin, "1h", rule.HOURS_WANTED), rule.HOUR_MS, now_ms)
    all15 = sorted(deps.fetch_candles(coin, "15m", rule.BARS_WANTED), key=lambda b: b.t)
    bars = rule.closed(all15, rule.FIFTEEN_MS, now_ms)
    if len(hours) < 2 * 14 or not all15:
        return done("error", "candle read failed or too short; not trading blind")
    price = all15[-1].c
    d["price"] = price

    # ── in a position ────────────────────────────────────────────────────
    if not flat:
        is_long = pos > 0
        stops, tps = _rows(orders, "Stop"), _rows(orders, "Take Profit")
        covered = sum(deps.bracket.order_cover(o, pos) for o in stops)
        row = next((p for p in eq.get("positions") or [] if p.get("coin") in ledger.coin_names(coin)), {})
        stop_px = [_px(o, "triggerPx") for o in stops if _px(o, "triggerPx") > 0]
        cur_stop = (max(stop_px) if is_long else min(stop_px)) if stop_px else None
        d["position"] = {"side": "long" if is_long else "short", "size": abs(pos),
                         "entry_px": row.get("entry_px"), "stop_px": cur_stop,
                         "tp_px": [_px(o, "triggerPx") for o in tps], "stop_covers": covered}
        reason = ("weekend" if weekend_closed(now_ms)
                  else "no stop covers the position" if covered + 1e-9 < abs(pos) else None)
        if reason:
            res = _close(ex, coin, pos) if live else {"dry_run": True}
            return done("close", reason, result=res)
        fill_px = float(row.get("entry_px") or 0)
        target = _px(tps[0], "triggerPx") if tps else 0.0
        if fill_px > 0 and target > 0 and open_trade:
            fill_bar = open_trade["open_ms"] - open_trade["open_ms"] % rule.FIFTEEN_MS
            new = rule.ratchet_stop(cfg, is_long, fill_px, target, cur_stop, [b for b in bars if b.t > fill_bar])
            if new is not None:
                res = deps.bracket.set_stop(coin, new, ex=ex) if live else {"dry_run": True}
                return done("ratchet", f"price covered {cfg.ratchet[2:]}% of the way to target: stop to the entry",
                            stop_px=new, result=res)
        return done("hold", "position open, stop and target resting")

    # ── flat ─────────────────────────────────────────────────────────────
    max_lev = float(ex.get_max_leverage(coin) or 0) or None
    plan = rule.plan_entry(cfg, hours, bars, price, now_ms, max_lev, resting=bool(entries))
    d["plan"] = plan
    if entries:
        resting_px = _px(entries[0], "limitPx")
        resting_long = str(entries[0].get("side")) == "B"
        entry_plan = plan
        if not rule.resting_matches(plan, resting_px, resting_long, cfg.tick_size):
            # cfg.swing may have changed since this entry was placed. Manage
            # an entry from the OTHER mode under ITS OWN rule until it
            # resolves (fills, is spent, hits the weekend...) rather than
            # cancelling it on the mode flip alone: the active mode only
            # governs a brand new entry.
            alt = rule.other_swing_mode(cfg.swing)
            if alt is not None:
                alt_plan = rule.plan_entry(replace(cfg, swing=alt), hours, bars, price, now_ms, max_lev,
                                           resting=True)
                if rule.resting_matches(alt_plan, resting_px, resting_long, cfg.tick_size):
                    entry_plan = alt_plan
                    d["resting_entry_swing_mode"] = alt
        stale = (f"kill switch: {kill['reason']}" if kill
                 else entry_plan.get("reason") if not entry_plan["ok"]
                 else "swing_changed" if not rule.resting_matches(entry_plan, resting_px, resting_long,
                                                                   cfg.tick_size)
                 else None)
        d["resting_entry"] = {"px": resting_px, "side": "long" if resting_long else "short",
                              "oid": entries[0].get("oid")}
        if not stale:
            return done("resting", "the entry rests at the level the rule names")
        res: Dict[str, Any] = {"dry_run": True}
        if live:
            res = {"cancelled": [ex.cancel_orders(int(o["oid"]), coin) for o in entries],
                   "stranded_cancelled": ex.cancel_stranded_reduce_only_orders(coin)}
        return done("cancel_entry", stale, result=res)
    if kill:
        return done("killed", f"kill switch: {kill['reason']}; no new entry until rearmed")
    foreign = [p["coin"] for p in eq.get("positions") or [] if p.get("coin") not in ledger.coin_names(coin)]
    if foreign:
        return done("blocked", f"the sub-account holds another position ({', '.join(foreign)}); one at a time")
    if not plan["ok"]:
        return done("no_order", plan.get("reason"))
    if live:
        ex.cancel_stranded_reduce_only_orders(coin)   # the sibling of a stop or target that fired
    sz_dec = int(ex.get_coin_index(coin)[1])
    size = rule.size_for(cfg, eq["equity"], plan["leverage"], plan["entry"], plan["stop"], sz_dec)
    d["size"] = size
    res = deps.bracket.place_bracket(
        coin, entry_px=round(plan["entry"], 4), stop_px=round(plan["stop"], 4), tp_px=round(plan["target"], 4),
        size=size["size"], leverage=int(plan["leverage"]), min_r=cfg.min_r, coin_max_leverage=max_lev,
        target_offset_frac=0.0,   # the tested target is the extension itself, no 0.5% offset
        dry_run=not live, ex=ex)
    if res.get("placed") and not res.get("ok"):
        # the entry went in but a leg was refused: never leave an entry without its stop
        cleanup = ex.cancel_open_orders_for_coin(coin)
        return done("place_incomplete", f"a leg was refused, entry cancelled: {res.get('result')}",
                    bracket=res, cancelled=cleanup)
    if not res.get("ok") or not (res.get("placed") or res.get("dry_run")):
        return done("refused", str(res.get("reason") or res.get("result")), bracket=res)
    return done("place" if live else "would_place",
                f"{plan['side']} limit {res['entry_px']:g}, stop {res['stop_px']:g}, target {res['tp_px']:g}, "
                f"{res.get('r_multiple')}R, size {size['size']:g} at {plan['leverage']}x", bracket=res)
