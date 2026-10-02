"""drawdown_ladder — buy a 21% drawdown in the crypto majors, average down, no stop.

LIVE by operator instruction 2026-09-14, after W-WH2 validated the rule on BTC,
ETH, SOL, BNB and XRP at 1x. See research/alpha_swarm/findings/
W-WH2_scale_in_ladder.md for the backtest and W-WH1_wallet_0xe282.md for the
wallet the rule was read off.

THE RULE, FROZEN IN THE PRE-REGISTRATION
-----------------------------------------
  trigger   a closed daily bar at or under 0.789 x the highest high of the
            30 closed bars before it
  entry     rung 1 at market; rungs 2-5 as resting GTC limit buys, each 7.17%
            under the one before, all placed at entry
  size      rung notional = sleeve / 25, sleeve = 50% of account equity
  exit      one reduce-only GTC limit for the whole position at average entry
            x 1.0514, re-priced after every rung fill
  stop      none

  6y backtest: 2.933x, max DD -43.5%, halves 1.516x / 1.429x, random-entry
  null p 0.0015. Buy-and-hold of the same coins: 8.37x. This is a defensive
  long with a timing edge over random entry, not alpha over holding.

WHAT IS DELIBERATELY HARD-CODED
-------------------------------
Every rule parameter and the leverage are constants, not config. The leverage
cliff sits between 2x (survived, -81% DD) and 2.5x (liquidated 2022-06-15), and
a tunable number one typo from that cliff is not a risk control. Config carries
only `enabled`, `shadow_only` (the grader's demote switch) and `sleeve_frac`
(the operator's capital split, 2026-09-14).

THE THREE CARVE-OUTS THIS BOOK NEEDS, EACH AN OPERATOR DECISION 2026-09-14
--------------------------------------------------------------------------
1. No backup stop. It never goes through the executor. With the executor's
   60% backup stop the backtest lands on the random-entry median (1.874x vs
   1.867x), so the stop IS the difference between the rule and no rule.
2. The hard daily-loss flatten skips this book's positions (`book_positions`
   in scripts/trading_loop.py). The daily-loss GATE still blocks new ladders.
   At a 50% sleeve the backtest crosses the -12.1% daily kill on zero days.
3. It trades the main-dex crypto majors while every other book is HIP-3 only.

WHAT KILLS IT
-------------
A market that bleeds for years without a +5% bounce off average entry fills
all five rungs early and marks every ladder at full loss. At 1x that is a
drawdown, not a liquidation. `mtm_decision` demotes the book when its
mark-to-market drawdown passes the backtest's -43.5% of sleeve.

Closed-ladder win rate is 99% by construction (a ladder only closes at its
target). Nothing may grade this book on closed trades.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from pathiel.agents.atomic_io import append_json_line, read_json, write_json_atomic
from pathiel.agents.rebalancer_owned import get_claims_registry, state_file

logger = logging.getLogger(__name__)

_BOOK_NAME = "drawdown_ladder"

# ── the rule (W-WH2 FROZEN; tests/test_drawdown_ladder.py pins these to it) ──
UNIVERSE = ("BTC", "ETH", "SOL", "BNB", "XRP")
TRIGGER_DD = -0.211
RUNGS = 5
GAP = -0.0717
TP = 0.0514
LOOKBACK = 30
LEVERAGE = 1

DEFAULT_SLEEVE_FRAC = 0.5
DEMOTE_DRAWDOWN = -0.435            # W-WH2 six-year max drawdown, A majors 1x
ENTRY_WINDOW_MS = 6 * 3_600_000     # enter within 6h of the daily close, or skip the day
RETRY_MS = 3_600_000                # back-off before re-placing a rejected order
_DAY_MS = 86_400_000
_MIN_ORDER_USD = 10.5               # pathiel.client.exchange.MIN_ORDER_USD, without its import
_DEAD = ("canceled", "rejected", "marginCanceled", "reduceOnlyCanceled",
         "selfTradeCanceled", "siblingFilledCanceled", "delistedCanceled",
         "liquidatedCanceled", "scheduledCancel", "unknown")

_STATE = state_file(".drawdown_ladder.json")
_EQUITY_LOG = state_file(".drawdown_ladder_equity.jsonl")


# ── the exchange, as the book sees it ────────────────────────────────────────

class LiveVenue:
    """The exchange calls this book makes. Imports are lazy so the grader can
    import `mtm_decision` without loading a signing stack."""

    def daily_bars(self, coin: str, count: int) -> List[List[float]]:
        from pathiel.client.hl_client import fetch_hl_candles
        return [[c.t, c.o, c.h, c.l, c.c] for c in fetch_hl_candles(coin, "1d", count)]

    def set_leverage(self, coin: str, leverage: int) -> bool:
        from pathiel.client.exchange import set_leverage
        return bool(set_leverage(coin, leverage).get("ok"))

    def market_buy(self, coin: str, notional: float) -> Dict[str, Any]:
        from pathiel.client.exchange import entry_size_for_notional, get_hl_price, place_hl_order
        mid = get_hl_price(coin)
        if mid <= 0:
            return {"ok": False, "error": f"no mid for {coin}"}
        return place_hl_order(True, entry_size_for_notional(coin, notional, mid), mid, coin)

    def limit(self, coin: str, is_buy: bool, size: float, px: float,
              reduce_only: bool = False) -> Dict[str, Any]:
        from pathiel.client.exchange import place_hl_limit_order
        return place_hl_limit_order(coin, is_buy, size, px, reduce_only=reduce_only)

    def size_for(self, coin: str, notional: float, px: float) -> float:
        from pathiel.client.exchange import entry_size_for_notional
        return entry_size_for_notional(coin, notional, px)

    def cancel(self, coin: str, oid: int) -> bool:
        from pathiel.client.exchange import cancel_orders
        return bool(cancel_orders(int(oid), coin).get("ok"))

    def order_status(self, oid: int) -> str:
        from pathiel.client.exchange import fetch_order_status
        return fetch_order_status(int(oid))

    def close(self, coin: str) -> bool:
        from pathiel.agents.executor import close_position_market
        return bool(close_position_market(coin).get("ok"))

    def realized_since(self, coin: str, start_ms: int) -> Optional[float]:
        """Closed PnL net of fees plus funding for `coin` since `start_ms`, or
        None if any page failed. Paged: HL caps fills at 2000 and funding at
        500 rows a call, and a ladder can stay open for two years."""
        from pathiel.client.hl_client import _http_post, resolve_user_address
        user = resolve_user_address()
        now = int(time.time() * 1000)
        pnl = 0.0
        for kind in ("userFillsByTime", "userFunding"):
            start, seen = int(start_ms), set()
            while True:
                rows = _http_post("/info", {"type": kind, "user": user,
                                            "startTime": start, "endTime": now}, timeout=15)
                if not isinstance(rows, list):
                    return None
                fresh = [r for r in rows if json.dumps(r, sort_keys=True) not in seen]
                seen.update(json.dumps(r, sort_keys=True) for r in fresh)
                for r in fresh:
                    if kind == "userFillsByTime" and r.get("coin") == coin:
                        pnl += float(r.get("closedPnl") or 0) - float(r.get("fee") or 0)
                    elif kind == "userFunding" and (r.get("delta") or {}).get("coin") == coin:
                        pnl += float(r["delta"].get("usdc") or 0)
                if not fresh or len(rows) < 500:
                    break
                start = max(int(r["time"]) for r in rows)
        return pnl


# ── pure pieces ──────────────────────────────────────────────────────────────

def rung_prices(first_px: float) -> List[float]:
    """Rung i sits (1 + GAP)^(i-1) under the first fill."""
    return [first_px * (1 + GAP) ** i for i in range(RUNGS)]


def rung_notional(equity: float, sleeve_frac: float) -> float:
    return LEVERAGE * equity * sleeve_frac / (RUNGS * len(UNIVERSE))


def min_equity_to_trade(sleeve_frac: float) -> float:
    """Account equity at which one rung clears the exchange minimum."""
    return _MIN_ORDER_USD * RUNGS * len(UNIVERSE) / (LEVERAGE * sleeve_frac)


def trigger(bars: List[List[float]], day_start_ms: int) -> Optional[Dict[str, float]]:
    """Evaluate the closed bar that ended at `day_start_ms`.

    Returns None when the history cannot answer (fewer than 31 closed bars, or
    the latest closed bar is not yesterday's): a stale candle is not a quiet
    market. Otherwise {fire, close, prior_high, level}.
    """
    closed = [b for b in bars if b[0] < day_start_ms]
    if len(closed) < LOOKBACK + 1 or closed[-1][0] != day_start_ms - _DAY_MS:
        return None
    prior_high = max(b[2] for b in closed[-LOOKBACK - 1:-1])
    close = closed[-1][4]
    level = (1 + TRIGGER_DD) * prior_high
    return {"fire": close <= level, "close": close, "prior_high": prior_high, "level": level}


def book_positions(positions: Optional[Iterable[Dict[str, Any]]],
                   ladder_coins: Set[str]) -> List[Dict[str, Any]]:
    """Positions every OTHER safety system may act on: all but this book's.

    The hard daily-loss flatten, the DSL tracker and the slots count read this
    instead of the raw list. Each of them would otherwise put a stop on a
    ladder, and the stop is the one thing W-WH2 showed erases the edge."""
    return [p for p in (positions or [])
            if (p.get("position") or {}).get("coin") not in ladder_coins]


def owned_coins(path: str = _STATE) -> Set[str]:
    state = read_json(path, default=None)
    if not isinstance(state, dict):
        return set()
    return set((state.get("ladders") or {}).keys())


def book_drawdown(rows: List[Dict[str, Any]]) -> Optional[float]:
    """Worst mark-to-market drawdown of the book as a fraction of its sleeve.

    Each row is one daily snapshot {pnl, sleeve}: cumulative realized plus open
    unrealized PnL in USD, and the sleeve's dollar size that day."""
    peak = None
    worst = None
    for r in rows:
        try:
            pnl, sleeve = float(r["pnl"]), float(r["sleeve"])
        except (KeyError, TypeError, ValueError):
            continue
        if sleeve <= 0:
            continue
        peak = pnl if peak is None else max(peak, pnl)
        dd = (pnl - peak) / sleeve
        worst = dd if worst is None else min(worst, dd)
    return worst


def mtm_decision(rows: List[Dict[str, Any]], live: Optional[bool]) -> Dict[str, Any]:
    """The grader's call for this book, on mark-to-market equity only."""
    dd = book_drawdown(rows)
    if dd is None:
        return {"book": _BOOK_NAME, "n": 0, "verdict": "PENDING", "action": "none",
                "why": "no mark-to-market history yet"}
    if dd <= DEMOTE_DRAWDOWN:
        return {"book": _BOOK_NAME, "n": len(rows), "verdict": "REFUTED",
                "action": "demote" if live else "none",
                "why": (f"mark-to-market drawdown {dd:.1%} of sleeve is past the "
                        f"backtest's worst {DEMOTE_DRAWDOWN:.1%}")}
    return {"book": _BOOK_NAME, "n": len(rows), "verdict": "LIVE", "action": "none",
            "why": f"drawdown {dd:.1%} of sleeve, inside {DEMOTE_DRAWDOWN:.1%}"}


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


# ── state ────────────────────────────────────────────────────────────────────

def _load(path: str) -> Optional[Dict[str, Any]]:
    """None means the file exists and cannot be read. A missing file is a cold
    start. Losing track of open ladders is the one failure this book cannot
    shrug off, so an unreadable file is quarantined and entries stop."""
    if not os.path.exists(path):
        return {"ladders": {}, "eval_day": {}, "realized_cum": 0.0}
    state = read_json(path, default=None)
    if not isinstance(state, dict) or not isinstance(state.get("ladders"), dict):
        bad = f"{path}.corrupt-{int(time.time())}"
        try:
            os.replace(path, bad)
        except OSError:
            pass
        logger.error("[%s] state file unreadable, quarantined to %s. Open ladders "
                     "keep their resting orders on the exchange but are no longer "
                     "managed, and no new ladder opens until this is resolved.",
                     _BOOK_NAME, bad)
        return None
    state.setdefault("eval_day", {})
    state.setdefault("realized_cum", 0.0)
    return state


def _utc_day(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


# ── the run ──────────────────────────────────────────────────────────────────

def maybe_run(config: Dict[str, Any],
              positions: Optional[List[Dict[str, Any]]],
              equity: float,
              main_available: float,
              daily_pnl: float,
              daily_loss_limit: float,
              allow_entries: bool = True,
              now_ms: Optional[int] = None,
              venue: Any = None,
              log_event: Callable[[Dict[str, Any]], None] = lambda e: None,
              state_path: str = _STATE,
              equity_log_path: str = _EQUITY_LOG) -> Dict[str, Any]:
    """Manage open ladders every cycle; open new ones once per UTC day.

    Management runs whenever the account read is good, even when the book is
    disabled, demoted, or the loop is in OFF mode: a ladder already on the
    exchange still needs its take-profit re-priced and its rungs cancelled
    when it closes.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    venue = venue or LiveVenue()
    out: Dict[str, Any] = {"book": _BOOK_NAME, "managed": 0, "opened": [], "closed": [],
                           "skipped": {}}
    if equity <= 0:
        # Every degraded read in _sync_account_state returns equity 0 and an
        # empty position list. A ladder read as flat off that would be closed.
        out["skipped"]["all"] = "degraded account read"
        return out

    state = _load(state_path)
    if state is None:
        out["skipped"]["all"] = "state unreadable"
        return out

    cfg = config.get(_BOOK_NAME) or {}
    live = {}
    for p in positions or []:
        pos = p.get("position") or {}
        if pos.get("coin") in UNIVERSE:
            live[pos["coin"]] = pos
    claims = get_claims_registry()

    for coin in list(state["ladders"]):
        _manage(coin, state, live.get(coin), venue, now_ms, claims, log_event, out)
        out["managed"] += 1
    claims.save()
    write_json_atomic(state_path, state, indent=1)

    sleeve_frac = float(cfg.get("sleeve_frac", DEFAULT_SLEEVE_FRAC) or 0.0)
    _snapshot_equity(state, live, equity, sleeve_frac, now_ms, equity_log_path)
    write_json_atomic(state_path, state, indent=1)

    enabled = bool(cfg.get("enabled", False)) and not bool(cfg.get("shadow_only", False))
    if not (enabled and allow_entries):
        return out

    day_start = now_ms // _DAY_MS * _DAY_MS
    day = _utc_day(day_start)
    if now_ms - day_start > ENTRY_WINDOW_MS:
        return out
    if daily_pnl <= daily_loss_limit:
        if state.get("kill_day") != day:
            state["kill_day"] = day
            log_event({"event": "ladder", "strategy_book": _BOOK_NAME, "action": "blocked",
                       "reason": f"daily loss ${daily_pnl:.2f} <= ${daily_loss_limit:.2f}"})
            write_json_atomic(state_path, state, indent=1)
        out["skipped"]["all"] = "daily loss gate"
        return out

    notional = rung_notional(equity, sleeve_frac)
    for coin in UNIVERSE:
        if state["eval_day"].get(coin) == day:
            continue
        if coin in state["ladders"] or coin in live:
            state["eval_day"][coin] = day
            continue
        owner = claims.owner_of(coin)
        if owner not in (None, _BOOK_NAME):
            state["eval_day"][coin] = day
            out["skipped"][coin] = f"claimed by {owner}"
            continue
        retry = state.setdefault("bars_retry_ms", {})
        if now_ms < retry.get(coin, 0):
            continue
        sig = trigger(venue.daily_bars(coin, LOOKBACK + 3), day_start)
        if sig is None:
            retry[coin] = now_ms + 10 * 60_000
            out["skipped"][coin] = "daily bars unavailable or stale, retrying in 10 min"
            continue
        state["eval_day"][coin] = day
        if not sig["fire"]:
            continue
        why = _entry_block(notional, main_available, equity, sleeve_frac)
        if why:
            out["skipped"][coin] = why
            log_event({"event": "ladder", "strategy_book": _BOOK_NAME, "coin": coin,
                       "action": "blocked", "reason": why, "close": sig["close"],
                       "level": round(sig["level"], 6)})
            logger.info("[%s] %s fired (close %.6g <= %.6g) but %s",
                        _BOOK_NAME, coin, sig["close"], sig["level"], why)
            continue
        if _open(coin, sig, notional, equity, sleeve_frac, state, venue, now_ms,
                 claims, log_event, lambda: write_json_atomic(state_path, state, indent=1)):
            out["opened"].append(coin)
            main_available -= RUNGS * notional / LEVERAGE
        write_json_atomic(state_path, state, indent=1)
    claims.save()
    write_json_atomic(state_path, state, indent=1)
    return out


def _entry_block(notional: float, main_available: float, equity: float,
                 sleeve_frac: float) -> Optional[str]:
    if notional < _MIN_ORDER_USD:
        return (f"rung ${notional:.2f} is under the ${_MIN_ORDER_USD} exchange minimum; "
                f"the book needs ${min_equity_to_trade(sleeve_frac):.2f} of equity "
                f"(have ${equity:.2f})")
    need = RUNGS * notional / LEVERAGE
    if main_available < need:
        return (f"main-dex free margin ${main_available:.2f} is under the whole "
                f"ladder's ${need:.2f}; transfer USDC to the main perp dex")
    return None


def _open(coin, sig, notional, equity, sleeve_frac, state, venue, now_ms, claims,
          log_event, save) -> bool:
    if not claims.claim(coin, _BOOK_NAME):
        return False
    if not venue.set_leverage(coin, LEVERAGE):
        claims.release(coin, _BOOK_NAME)
        logger.warning("[%s] %s set_leverage(%d) failed, not opened", _BOOK_NAME, coin, LEVERAGE)
        return False
    fill = venue.market_buy(coin, notional)
    avg, size = float(fill.get("avg_px") or 0), float(fill.get("total_sz") or 0)
    if not fill.get("ok") or avg <= 0 or size <= 0:
        claims.release(coin, _BOOK_NAME)
        log_event({"event": "ladder", "strategy_book": _BOOK_NAME, "coin": coin,
                   "action": "blocked", "reason": f"rung 1 did not fill: {fill.get('error')}"})
        return False
    prices = rung_prices(avg)
    lad = {
        "coin": coin, "opened_ms": now_ms, "status": "open",
        "rung_notional": notional, "sleeve_equity": equity * sleeve_frac,
        "signal": {k: round(v, 8) for k, v in sig.items() if k != "fire"},
        "rungs": [{"i": 1, "px": avg, "size": size, "status": "filled", "oid": None}]
                 + [{"i": i + 1, "px": prices[i], "size": venue.size_for(coin, notional, prices[i]),
                     "status": "pending", "oid": None, "retry_ms": 0} for i in range(1, RUNGS)],
        "tp": None, "last_size": size,
    }
    state["ladders"][coin] = lad
    save()      # a filled rung 1 the book has no record of is an unmanaged position
    for r in lad["rungs"][1:]:
        _place_rung(coin, r, venue, now_ms)
    _place_tp(coin, lad, size, avg, venue, now_ms)
    log_event({"event": "ladder", "strategy_book": _BOOK_NAME, "coin": coin,
               "action": "open", "side": "long", "fill_px": avg, "size": size,
               "rung_notional": round(notional, 2),
               "rungs": [round(r["px"], 6) for r in lad["rungs"]],
               "tp_px": (lad["tp"] or {}).get("px")})
    logger.info("[%s] LONG %s rung 1 %.6g x %s, rungs to %.6g, target %.6g",
                _BOOK_NAME, coin, avg, size, prices[-1], avg * (1 + TP))
    return True


def _place_rung(coin, r, venue, now_ms) -> None:
    res = venue.limit(coin, True, r["size"], r["px"])
    if not res.get("ok"):
        r["retry_ms"] = now_ms + RETRY_MS
        r["error"] = res.get("error")
        return
    r["oid"] = int(res["order_id"]) if res.get("order_id") else None
    r["status"] = "filled" if res.get("avg_px") else "open"
    r.pop("error", None)


def _place_tp(coin, lad, size, avg_entry, venue, now_ms) -> None:
    px = avg_entry * (1 + TP)
    res = venue.limit(coin, False, size, px, reduce_only=True)
    if not res.get("ok"):
        lad["tp"] = {"oid": None, "px": px, "size": size, "retry_ms": now_ms + RETRY_MS,
                     "error": res.get("error")}
        logger.warning("[%s] %s take-profit at %.6g rejected: %s",
                       _BOOK_NAME, coin, px, res.get("error"))
        return
    lad["tp"] = {"oid": int(res["order_id"]) if res.get("order_id") else None,
                 "px": px, "size": size, "filled": bool(res.get("avg_px"))}


def _manage(coin, state, pos, venue, now_ms, claims, log_event, out) -> None:
    lad = state["ladders"][coin]
    size = float(pos.get("szi") or 0) if pos else 0.0
    entry = float(pos.get("entryPx") or 0) if pos else 0.0

    tp = lad.get("tp") or {}
    tp_status = venue.order_status(tp["oid"]) if tp.get("oid") else None
    if tp.get("filled") or tp_status == "filled":
        tp["filled"] = True
        lad["tp"] = tp

    if lad["status"] == "open" and (size <= 0 or tp.get("filled")):
        # The ladder is over: its target filled, or something outside the book
        # closed it. Rungs must go before anything else, or a later dip reopens
        # a position with no take-profit behind it.
        _cancel_resting(coin, lad, venue)
        lad["status"] = "closing"
        lad["closed_by"] = "tp" if tp.get("filled") else "external"
        if size > 0:
            # This size was read before the target's fill status was, so it may
            # already be stale. The next cycle's read comes after the cancel.
            return

    if lad["status"] == "closing":
        if size > 0:
            # Rungs were cancelled on an earlier cycle, so this size is final: a
            # rung filled after the target did while nothing was watching. It is
            # outside the rule, so it goes.
            logger.warning("[%s] %s: %.6g left after the ladder closed, flattening",
                           _BOOK_NAME, coin, size)
            venue.close(coin)
            return
        realized = venue.realized_since(coin, lad["opened_ms"])
        if realized is None:
            return          # read failed; finish next cycle rather than record a guess
        state["realized_cum"] = float(state.get("realized_cum", 0.0)) + realized
        claims.release(coin, _BOOK_NAME)
        del state["ladders"][coin]
        filled = sum(1 for r in lad["rungs"] if r["status"] == "filled")
        out["closed"].append(coin)
        log_event({"event": "ladder", "strategy_book": _BOOK_NAME, "coin": coin,
                   "action": "close", "closed_by": lad.get("closed_by"),
                   "rungs_filled": filled, "realized_usd": round(realized, 4),
                   "days": round((now_ms - lad["opened_ms"]) / _DAY_MS, 2)})
        logger.info("[%s] %s closed by %s after %d rung(s): %+.2f USD",
                    _BOOK_NAME, coin, lad.get("closed_by"), filled, realized)
        return

    for r in lad["rungs"]:
        if r["status"] == "open" and r.get("oid"):
            st = venue.order_status(r["oid"])
            if st == "filled":
                r["status"] = "filled"
            elif st in _DEAD:
                r["status"], r["oid"], r["retry_ms"] = "pending", None, now_ms + RETRY_MS
                logger.warning("[%s] %s rung %d at %.6g went %s, re-placing after back-off",
                               _BOOK_NAME, coin, r["i"], r["px"], st)
    for r in lad["rungs"]:
        if r["status"] == "pending" and now_ms >= r.get("retry_ms", 0):
            _place_rung(coin, r, venue, now_ms)

    grew = size > lad["last_size"] * (1 + 1e-9)
    tp_dead = (not tp.get("oid")) or tp_status in _DEAD
    if grew or (tp_dead and now_ms >= tp.get("retry_ms", 0)):
        if tp.get("oid") and tp_status == "open":
            venue.cancel(coin, tp["oid"])
        _place_tp(coin, lad, size, entry, venue, now_ms)
        if grew:
            log_event({"event": "ladder", "strategy_book": _BOOK_NAME, "coin": coin,
                       "action": "rung_filled", "size": size, "avg_entry": entry,
                       "tp_px": (lad["tp"] or {}).get("px")})
    lad["last_size"] = size


def _cancel_resting(coin, lad, venue) -> None:
    for r in lad["rungs"]:
        if r["status"] == "open" and r.get("oid"):
            if venue.cancel(coin, r["oid"]) or venue.order_status(r["oid"]) != "open":
                r["status"] = "cancelled"
        elif r["status"] == "pending":
            r["status"] = "cancelled"
    tp = lad.get("tp") or {}
    if tp.get("oid") and not tp.get("filled"):
        venue.cancel(coin, tp["oid"])


def _snapshot_equity(state, live, equity, sleeve_frac, now_ms, path) -> None:
    """One mark-to-market row per UTC day: the grader's only input."""
    day = _utc_day(now_ms)
    if state.get("last_snapshot_day") == day:
        return
    # HL's cumFunding.sinceOpen is funding PAID (positive = paid), which the
    # position's unrealizedPnl leaves out.
    unrealized = sum(float(live[c].get("unrealizedPnl") or 0)
                     - float((live[c].get("cumFunding") or {}).get("sinceOpen") or 0)
                     for c in state["ladders"] if c in live)
    row = {"day": day, "equity": round(equity, 4), "sleeve": round(equity * sleeve_frac, 4),
           "realized_cum": round(float(state.get("realized_cum", 0.0)), 4),
           "unrealized": round(unrealized, 4),
           "pnl": round(float(state.get("realized_cum", 0.0)) + unrealized, 4),
           "open": sorted(state["ladders"])}
    append_json_line(path, row)
    state["last_snapshot_day"] = day
