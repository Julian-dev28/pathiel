"""copy_trade — mirror one Hyperliquid wallet, with a leverage multiplier.

LIVE BY OPERATOR OVERRIDE 2026-09-21, AGAINST ITS OWN BACKTEST
--------------------------------------------------------------
W-CP1 (research/alpha_swarm/findings/W-CP1_copy_0xe282.md) backtested exactly
this rule on the leader's full history and refuted it: every start month from
2025-03 to 2025-10 ends liquidated at every multiplier from 0.5x to 3x, and the
one window that survives (2025-11 on) loses to random entry times, p 0.79-0.85.
The operator built it anyway. That is recorded here so nobody mistakes it for a
validated book.

THE RULE
--------
  leader    `copy_trade.leader` in config (default 0xe282...df29), main + xyz dex.
  copy      only positions the leader opens from flat after the book first
            sees him. What he already holds at that moment is ignored until he
            goes flat on it.
  open/add  our notional = leverage_mult x (his added notional / his equity)
                           x our equity x sleeve_frac
            so on the sleeve our exposure is leverage_mult times his.
  reduce    the same fraction of our position he took off his; flat when he
            is flat.
  leverage  the exchange setting for a copied coin is his setting x
            leverage_mult, capped at the coin's max.
  stop      none. The leader never stops out and the copy follows his exits.

His equity is perp account value on every queried dex plus spot USDC: he runs
unified margin, so his spot USDC is his collateral (W-CP1).

WHAT IT SHARES WITH drawdown_ladder
-----------------------------------
Copied coins are invisible to the hard daily-loss flatten, the DSL tracker and
the slot count (scripts/trading_loop.py), because a stop the leader does not
have would break the mirror. The daily-loss GATE still blocks new opens and
adds. `mtm_decision` demotes the book at DEMOTE_DRAWDOWN of its sleeve;
demotion stops new copies and keeps mirroring exits of the ones open.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Any, Callable, Dict, List, Optional, Set

from pathiel.agents.atomic_io import append_json_line, read_json, write_json_atomic
from pathiel.agents.drawdown_ladder_live import book_drawdown
from pathiel.agents.rebalancer_owned import get_claims_registry, state_file

logger = logging.getLogger(__name__)

_BOOK_NAME = "copy_trade"

DEFAULT_LEADER = "0xe2823659be02e0f48a4660e4da008b5e1abfdf29"
DEXES = ("", "xyz")
DEFAULT_SLEEVE_FRAC = 0.25
DEFAULT_MULT = 1.0
MAX_MULT = 3.0                      # the largest multiplier W-CP1 tested
DEMOTE_DRAWDOWN = -0.5
_MIN_ORDER_USD = 10.5               # pathiel.client.exchange.MIN_ORDER_USD
_DAY_MS = 86_400_000

_STATE = state_file(".copy_trade.json")
_EQUITY_LOG = state_file(".copy_trade_equity.jsonl")


class LiveVenue:
    """Exchange calls, lazily imported like drawdown_ladder's."""

    def leader_state(self, user: str) -> Optional[Dict[str, Any]]:
        """{"equity", "positions": {coin: {szi, px, lev}}}, or None if any
        read failed. A failed read must never look like a flat leader."""
        from pathiel.client.hl_client import _http_post
        positions: Dict[str, Dict[str, float]] = {}
        equity = 0.0
        for dex in DEXES:
            st = _http_post("/info", {"type": "clearinghouseState", "user": user, "dex": dex},
                            timeout=10)
            if not isinstance(st, dict) or "assetPositions" not in st:
                return None
            equity += float((st.get("marginSummary") or {}).get("accountValue") or 0)
            for ap in st["assetPositions"]:
                p = ap.get("position") or {}
                szi = float(p.get("szi") or 0)
                if not szi:
                    continue
                coin = p["coin"] if (not dex or ":" in p["coin"]) else f"{dex}:{p['coin']}"
                positions[coin] = {"szi": szi,
                                   "px": float(p.get("positionValue") or 0) / abs(szi),
                                   "lev": float((p.get("leverage") or {}).get("value") or 1)}
        spot = _http_post("/info", {"type": "spotClearinghouseState", "user": user}, timeout=10)
        if not isinstance(spot, dict) or "balances" not in spot:
            return None
        equity += sum(float(b.get("total") or 0) for b in spot["balances"] if b.get("coin") == "USDC")
        return {"equity": equity, "positions": positions}

    def mid(self, coin: str) -> float:
        from pathiel.client.exchange import get_hl_price
        return get_hl_price(coin)

    def max_leverage(self, coin: str) -> int:
        from pathiel.client.exchange import get_max_leverage
        return get_max_leverage(coin)

    def set_leverage(self, coin: str, leverage: int) -> bool:
        from pathiel.client.exchange import set_leverage
        return bool(set_leverage(coin, leverage).get("ok"))

    def market(self, coin: str, is_buy: bool, size: float, mid: float,
               reduce_only: bool = False) -> Dict[str, Any]:
        from pathiel.client.exchange import place_hl_order
        return place_hl_order(is_buy, size, mid, coin, reduce_only=reduce_only)

    def size_for(self, coin: str, notional: float, mid: float) -> float:
        from pathiel.client.exchange import entry_size_for_notional
        return entry_size_for_notional(coin, notional, mid)

    def close(self, coin: str) -> bool:
        from pathiel.agents.executor import close_position_market
        return bool(close_position_market(coin).get("ok"))


# ── pure pieces ──────────────────────────────────────────────────────────────

def copy_notional(his_added_ntl: float, his_equity: float, our_equity: float,
                  sleeve_frac: float, mult: float) -> float:
    """Dollar size of our add for his add of `his_added_ntl`."""
    if his_equity <= 0:
        return 0.0
    return mult * his_added_ntl / his_equity * our_equity * sleeve_frac


def copy_leverage(his_lev: float, mult: float, max_lev: int) -> int:
    return max(1, min(int(max_lev), math.ceil(his_lev * mult)))


def owned_coins(path: str = _STATE) -> Set[str]:
    state = read_json(path, default=None)
    if not isinstance(state, dict):
        return set()
    return set((state.get("copies") or {}).keys())


def mtm_decision(rows: List[Dict[str, Any]], live: Optional[bool]) -> Dict[str, Any]:
    """The grader's call, on mark-to-market book equity. Closed copies are not
    graded: with no stop, a copy closes when the leader takes profit."""
    dd = book_drawdown(rows)
    if dd is None:
        return {"book": _BOOK_NAME, "n": 0, "verdict": "PENDING", "action": "none",
                "why": "no mark-to-market history yet"}
    if dd <= DEMOTE_DRAWDOWN:
        return {"book": _BOOK_NAME, "n": len(rows), "verdict": "REFUTED",
                "action": "demote" if live else "none",
                "why": f"mark-to-market drawdown {dd:.1%} of sleeve is past {DEMOTE_DRAWDOWN:.0%}"}
    return {"book": _BOOK_NAME, "n": len(rows), "verdict": "LIVE", "action": "none",
            "why": f"drawdown {dd:.1%} of sleeve, inside {DEMOTE_DRAWDOWN:.0%}"}


def load_equity_log(path: str = _EQUITY_LOG) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                logger.warning("[%s] unparseable equity log line skipped", _BOOK_NAME)
    return rows


# ── the run ──────────────────────────────────────────────────────────────────

def _fresh(leader: str) -> Dict[str, Any]:
    return {"leader": leader, "last": None, "ignored": [], "copies": {}, "realized_cum": 0.0}


def maybe_run(config: Dict[str, Any],
              positions: Optional[List[Dict[str, Any]]],
              equity: float,
              dex_available: Dict[str, float],
              daily_pnl: float,
              daily_loss_limit: float,
              allow_entries: bool = True,
              now_ms: Optional[int] = None,
              venue: Any = None,
              log_event: Callable[[Dict[str, Any]], None] = lambda e: None,
              state_path: str = _STATE,
              equity_log_path: str = _EQUITY_LOG) -> Dict[str, Any]:
    """Diff the leader's positions against last cycle and mirror the change.

    Exits are mirrored whenever the reads are good, even disabled, demoted or
    in OFF mode: a copy already on the exchange must still follow him out.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    venue = venue or LiveVenue()
    out: Dict[str, Any] = {"book": _BOOK_NAME, "opened": [], "added": [], "reduced": [],
                           "closed": [], "skipped": {}}
    if equity <= 0:
        out["skipped"]["all"] = "degraded account read"
        return out

    cfg = config.get(_BOOK_NAME) or {}
    leader = str(cfg.get("leader") or DEFAULT_LEADER).lower()
    mult = min(MAX_MULT, max(0.0, float(cfg.get("leverage_mult", DEFAULT_MULT) or 0.0)))
    sleeve_frac = float(cfg.get("sleeve_frac", DEFAULT_SLEEVE_FRAC) or 0.0)
    enabled = bool(cfg.get("enabled", False)) and not bool(cfg.get("shadow_only", False))

    state = read_json(state_path, default=None)
    if not isinstance(state, dict) or state.get("leader") != leader:
        state = _fresh(leader)
    lead = venue.leader_state(leader)
    if lead is None:
        out["skipped"]["all"] = "leader read failed"
        return out
    now_pos = lead["positions"]
    if state["last"] is None:
        # First sight of this leader: everything he holds predates the copy.
        state["last"] = {c: p["szi"] for c, p in now_pos.items()}
        state["ignored"] = sorted(now_pos)
        write_json_atomic(state_path, state, indent=1)
        log_event({"event": "copy_trade", "action": "baseline", "leader": leader,
                   "ignored": state["ignored"]})
        return out

    ours = {}
    for p in positions or []:
        pos = p.get("position") or {}
        if pos.get("coin"):
            ours[pos["coin"]] = pos
    claims = get_claims_registry()
    entries_ok = enabled and allow_entries and daily_pnl > daily_loss_limit
    ignored = set(state["ignored"])
    last = state["last"]
    retry: Set[str] = set()              # failed exits: keep `last` so the diff repeats

    for coin in sorted(set(last) | set(now_pos)):
        prev = float(last.get(coin) or 0.0)
        cur = now_pos.get(coin, {}).get("szi", 0.0)
        if prev == cur:
            continue
        flipped = prev and cur and (prev > 0) != (cur > 0)

        if coin in ignored:
            if cur and not flipped:
                continue
            ignored.discard(coin)
            if not cur:
                continue
            prev = 0.0                       # a flip is a fresh open

        copy = state["copies"].get(coin)
        mine = ours.get(coin)
        if copy and not mine:
            # Our copy is gone (liquidated, closed by hand): stop mirroring
            # this coin until the leader is flat on it again.
            state["copies"].pop(coin)
            claims.release(coin, _BOOK_NAME)
            if cur:
                ignored.add(coin)
            out["skipped"][coin] = "our copy vanished"
            continue

        if copy and (not cur or flipped):
            _record_realized(state, mine, venue, coin)
            if venue.close(coin):
                state["copies"].pop(coin)
                claims.release(coin, _BOOK_NAME)
                out["closed"].append(coin)
                log_event({"event": "copy_trade", "action": "close", "coin": coin})
            else:
                out["skipped"][coin] = "close failed, retrying next cycle"
                retry.add(coin)
                continue
            if not flipped:
                continue
            copy, prev = None, 0.0

        if copy and abs(cur) < abs(prev):
            frac = (abs(prev) - abs(cur)) / abs(prev)
            szi = float(mine["szi"])
            mid = venue.mid(coin)
            if mid <= 0:
                out["skipped"][coin] = "no mid"
                retry.add(coin)
                continue
            size = abs(szi) * frac
            if (abs(szi) - size) * mid < _MIN_ORDER_USD:
                _record_realized(state, mine, venue, coin)
                ok = venue.close(coin)
                if ok:
                    state["copies"].pop(coin)
                    claims.release(coin, _BOOK_NAME)
            else:
                ok = venue.market(coin, szi < 0, venue.size_for(coin, size * mid, mid), mid,
                                  reduce_only=True).get("ok")
                if ok:
                    state["realized_cum"] += (size if szi > 0 else -size) * (
                        mid - float(mine.get("entryPx") or mid))
            if not ok:
                out["skipped"][coin] = "reduce failed, retrying next cycle"
                retry.add(coin)
                continue
            out["reduced"].append(coin)
            log_event({"event": "copy_trade", "action": "reduce", "coin": coin,
                       "frac": round(frac, 4)})
        elif abs(cur) > abs(prev):
            if not entries_ok:
                if not copy and cur:
                    ignored.add(coin)        # missed the open: sit this trip out
                out["skipped"][coin] = "entries blocked"
            else:
                _add(coin, cur, prev, now_pos[coin], lead["equity"], equity, sleeve_frac,
                     mult, copy, dex_available, state, claims, venue, log_event, out, now_ms,
                     ignored)
    state["last"] = {c: p["szi"] for c, p in now_pos.items()}
    for coin in retry:
        if coin in last:
            state["last"][coin] = last[coin]
    state["ignored"] = sorted(ignored)
    claims.save()
    _snapshot(state, ours, equity, sleeve_frac, now_ms, equity_log_path)
    write_json_atomic(state_path, state, indent=1)
    return out


def _add(coin, cur, prev, lp, his_equity, our_equity, sleeve_frac, mult, copy,
         dex_available, state, claims, venue, log_event, out, now_ms, ignored):
    ntl = copy_notional((abs(cur) - abs(prev)) * lp["px"], his_equity, our_equity,
                        sleeve_frac, mult)
    if not copy:
        owner = claims.owner_of(coin)
        if owner not in (None, _BOOK_NAME):
            ignored.add(coin)
            out["skipped"][coin] = f"claimed by {owner}"
            return
    lev = copy_leverage(lp["lev"], mult, venue.max_leverage(coin))
    dex = coin.split(":", 1)[0] if ":" in coin else ""
    room = max(0.0, float(dex_available.get(dex, 0.0))) * lev * 0.95
    ntl = min(ntl, room)
    if ntl < _MIN_ORDER_USD:
        if not copy:
            ignored.add(coin)
        out["skipped"][coin] = f"copy ${ntl:.2f} under the ${_MIN_ORDER_USD} minimum"
        return
    mid = venue.mid(coin)
    if mid <= 0:
        out["skipped"][coin] = "no mid"
        return
    if not copy:
        if not claims.claim(coin, _BOOK_NAME):
            ignored.add(coin)
            out["skipped"][coin] = "claim refused"
            return
        if not venue.set_leverage(coin, lev):
            claims.release(coin, _BOOK_NAME)
            ignored.add(coin)
            out["skipped"][coin] = "set_leverage failed"
            return
    res = venue.market(coin, cur > 0, venue.size_for(coin, ntl, mid), mid)
    if not res.get("ok"):
        if not copy:
            claims.release(coin, _BOOK_NAME)
            ignored.add(coin)
        out["skipped"][coin] = f"order rejected: {res.get('error')}"
        return
    dex_available[dex] = float(dex_available.get(dex, 0.0)) - ntl / lev
    if copy:
        out["added"].append(coin)
    else:
        state["copies"][coin] = {"opened_ms": now_ms, "leverage": lev}
        out["opened"].append(coin)
    log_event({"event": "copy_trade", "action": "add" if copy else "open", "coin": coin,
               "side": "long" if cur > 0 else "short", "notional": round(ntl, 2),
               "leverage": lev, "mult": mult})


def _record_realized(state, mine, venue, coin):
    """Estimate of what a full close realizes, at mid. The grader reads the
    book's drawdown, not this number's cents."""
    if not mine:
        return
    mid = venue.mid(coin)
    if mid > 0:
        state["realized_cum"] += float(mine["szi"]) * (mid - float(mine.get("entryPx") or mid))


def _snapshot(state, ours, equity, sleeve_frac, now_ms, path):
    day = now_ms // _DAY_MS
    if state.get("snap_day") == day:
        return
    unreal = sum(float((ours.get(c) or {}).get("unrealizedPnl") or 0) for c in state["copies"])
    append_json_line(path, {"t": now_ms, "pnl": round(state["realized_cum"] + unreal, 4),
                            "sleeve": round(equity * sleeve_frac, 4)})
    state["snap_day"] = day
