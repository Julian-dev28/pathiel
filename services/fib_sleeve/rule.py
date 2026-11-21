"""The fib sleeve's rule, pure: candles in, one plan out. No network, no
clock, no state.

It is the backtest's rule by construction, not a copy of it: the swing (the
24h range or the zigzag), the level, the stop, the target and the ratchet
are the functions of `services/strategies/evals/fib_retrace_test.py`,
called with the data a live tick has. `tests/test_parity.py` replays the
backtest's walker on synthetic candles and asserts this module names the
same entry, stop and target at every fill, for both swings.

What a tick knows, exactly as the walker does at the open of a 15m bar:
1h bars that have CLOSED, 15m bars that have closed, and one current price.

Two swing anchors, `cfg.swing` ("range24" default, "zigzag" kept as an
option; see `config.py`'s module doc for which is live and why):
`current_swing` dispatches to `current_swing_range24` or
`current_swing_zigzag`. `SWING_MODES`, `other_swing_mode` and
`resting_matches` are `tick.py`'s tools for a resting entry placed under
the OTHER mode before `cfg.swing` changed: it is managed to completion
under ITS OWN mode's plan, never cancelled on the mode flip alone (the
active mode only governs a brand new entry). An open position never calls
any of this: its stop and target are whatever already rests on the venue,
and the break-even ratchet never reads the swing either, so it is
swing-change-safe by construction (`tick.py`'s "in a position" branch).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.agents.bracket import check_levels
from pathiel.models.types import Candle
from services.fib_sleeve.config import SleeveConfig
from services.strategies import next_trade as NT
from services.strategies.evals import fib_retrace_test as F
from services.strategies.evals import intraday_5m_test as I5
from services.strategies.weekend import weekend_closed

HOUR_MS, FIFTEEN_MS = F.HOUR_MS, F.FIFTEEN_MS
EPS = F.EPS
HOURS_WANTED = 500        # 1h bars a tick reads: the zigzag needs a long run-in to settle
BARS_WANTED = 1500        # 15m bars a tick reads: 15.6 days, far past a k=2 swing's life
SWING_MODES = ("zigzag", "range24")
RANGE24_WINDOW = 24       # closed 1h bars: services.strategies.levels' own "24h range" window


def closed(bars: Sequence[Candle], step_ms: int, now_ms: int) -> List[Candle]:
    """The bars that have closed at `now_ms`, oldest first."""
    return sorted((b for b in bars if b.t + step_ms <= now_ms), key=lambda b: b.t)


def current_swing_zigzag(hours: Sequence[Candle], k: float
                         ) -> Tuple[Optional[F.Swing], Optional[int], Optional[float]]:
    """(the last confirmed zigzag leg, the ms its extreme was confirmed, the
    ATR14 of the last closed hour) from CLOSED 1h bars. The swing is the
    backtest's `swing_from_pivots(zigzag_pivots(...), n_closed)`."""
    if not hours:
        return None, None, None
    atr = I5._atr(hours, F.ATR_N)
    pivots = F.zigzag_pivots(hours, atr, k)
    swing = F.swing_from_pivots(pivots, len(hours))
    if swing is None:
        return None, None, atr[-1]
    last = [p for p in pivots if p.confirmed_i < len(hours)][-1]
    return swing, hours[last.confirmed_i].t + HOUR_MS, atr[-1]


def current_swing_range24(hours: Sequence[Candle]
                          ) -> Tuple[Optional[F.Swing], Optional[int], Optional[float]]:
    """(the 24h-range swing, the ms its extreme's own hour closed, the ATR14
    of the last closed hour) from CLOSED 1h bars.

    The window is exactly `services.strategies.levels`' own "24h range" -
    the last 24 closed 1h bars, the same slice `price_range(closed[-24:])`
    reads for the Desk page's Levels card and `scripts/oil_levels.py`: its
    low and high are this swing's origin and extreme by construction
    (`tests/test_rule.py::test_range24_low_high_match_the_levels_service`
    proves the two never drift). The DIRECTION is the window's later
    extreme - the high after the low is an up-swing (buy the retrace), the
    low after the high a down-swing (sell the retrace) -
    `fib_retrace_test.swing_from_window`, the backtest's own "range24"
    anchor, called here on that same window, not re-derived."""
    if len(hours) < RANGE24_WINDOW:
        return None, None, None
    atr = I5._atr(hours, F.ATR_N)
    a = atr[-1]
    swing = F.swing_from_window(hours[-RANGE24_WINDOW:], a, "r24")
    if swing is None:
        return None, None, a
    return swing, swing.t_ext + HOUR_MS, a


def current_swing(cfg: SleeveConfig, hours: Sequence[Candle]
                  ) -> Tuple[Optional[F.Swing], Optional[int], Optional[float]]:
    """The swing `cfg.swing` names right now, from CLOSED 1h bars."""
    if cfg.swing == "zigzag":
        return current_swing_zigzag(hours, cfg.zigzag_k)
    if cfg.swing == "range24":
        return current_swing_range24(hours)
    raise ValueError(f"unknown swing {cfg.swing!r}")


def other_swing_mode(mode: str) -> Optional[str]:
    """The sleeve's other swing mode (for `tick.py`: managing an entry that
    rests from before a swing-mode change under ITS OWN mode)."""
    others = [m for m in SWING_MODES if m != mode]
    return others[0] if others else None


def resting_matches(plan: Dict[str, Any], resting_px: float, resting_long: bool, tick_size: float) -> bool:
    """Does a resting entry at (`resting_px`, `resting_long`) still match
    what `plan` (`plan_entry`'s answer) names right now?"""
    return bool(plan.get("ok")) and abs(resting_px - plan["entry"]) <= tick_size / 2 + 1e-9 \
        and resting_long == (plan["side"] == "long")


def deepest_retrace(bars: Sequence[Candle], swing: F.Swing) -> Tuple[bool, Optional[float]]:
    """(known, the deepest price against the swing since its extreme) over
    the closed 15m bars after the bar that made the extreme: the backtest's
    `retrace_series` at the tick. known is False when the 15m history
    starts after the extreme's hour, so a trade-through could be hidden."""
    if not bars or bars[0].t > swing.t_ext:
        return False, None
    t, h, l = [b.t for b in bars], [b.h for b in bars], [b.l for b in bars]
    ext_i = F._extreme_index(t, h, l, swing)
    vals = (l if swing.side > 0 else h)[ext_i + 1:]
    if not vals:
        return True, None
    return True, (min(vals) if swing.side > 0 else max(vals))


def session_block(now_ms: int) -> Optional[str]:
    """No order in the oil weekend, nor on the first 15m bar after it (the
    backtest's `weekend` and `reopen` masks)."""
    if weekend_closed(now_ms):
        return "weekend"
    bar_t = now_ms - now_ms % FIFTEEN_MS
    if weekend_closed(bar_t - FIFTEEN_MS):
        return "reopen_bar"
    return None


def plan_entry(cfg: SleeveConfig, hours: Sequence[Candle], bars: Sequence[Candle], price: float, now_ms: int,
               coin_max_leverage: Optional[float], resting: bool = False) -> Dict[str, Any]:
    """The order the rule wants resting right now, or why none.

    `hours` / `bars`: closed 1h / 15m bars. `price`: the current price (the
    walker's bar open). `resting`: the sleeve's own entry for this swing
    already rests (the walker's `armed`), so a price beyond the level is a
    fill in progress, not a missed level. Returns {"ok": True, side, entry,
    stop, target, r, leverage, ...} or {"ok": False, "reason": ...}."""
    out: Dict[str, Any] = {"ok": False, "variant": cfg.variant_name}
    block = session_block(now_ms)
    if block:
        return {**out, "reason": block}
    swing, confirmed_ms, atr = current_swing(cfg, hours)
    if swing is None or not atr:
        return {**out, "reason": "no_swing"}
    side = swing.side
    out.update({"swing": {"side": "up" if side > 0 else "down", "origin": swing.origin, "extreme": swing.extreme,
                          "extreme_ms": swing.t_ext, "confirmed_ms": confirmed_ms}, "atr": atr,
                "side": "long" if side > 0 else "short"})
    if cfg.direction == "long" and side < 0:
        return {**out, "reason": "down_swing_long_only"}
    level = F.fib_price(swing.origin, swing.extreme, cfg.level)
    stop = F.stop_price(cfg.stop, swing, cfg.level, atr)
    target = F.target_price(swing, cfg.target)
    out.update({"entry": level, "stop": stop, "target": target})
    known, ret = deepest_retrace(bars, swing)
    if not known:
        return {**out, "reason": "swing_older_than_15m_history"}
    if ret is not None and side * (ret - level) <= -cfg.tick_size + EPS:
        return {**out, "reason": "level_spent", "deepest_retrace": ret}
    if side * (price - level) <= 0 and not resting:
        return {**out, "reason": "price_beyond_level", "price": price}
    dist = side * (level - stop)
    if level <= 0 or dist / level < F.MIN_STOP_FRAC:
        return {**out, "reason": "tight_stop"}
    gate = check_levels(level, stop, target, cfg.min_r)
    out["r"] = gate.get("r_multiple")
    if not gate["ok"]:
        return {**out, "reason": "min_r", "detail": gate.get("reason")}
    lev, lev_gate = NT.fit_leverage(level, stop, float(cfg.leverage), coin_max_leverage, cfg.leverage_fit)
    out["leverage"] = int(lev)
    if not lev_gate.get("ok"):
        return {**out, "reason": "leverage", "detail": lev_gate.get("reason")}
    return {**out, "ok": True}


def size_for(cfg: SleeveConfig, equity: float, leverage: float, entry: float, stop: float,
             sz_decimals: int) -> Dict[str, Any]:
    """Size from the sub-account's OWN equity. The smaller of: the size
    whose stop-out (price to the stop, the taker fee both ways, the stop
    slippage) costs `risk_frac` of equity, and all of the equity as margin,
    net of the opening fee, at `leverage`. The formula of
    `desk_tick_sim.risk_capped_notional`; rounded DOWN to the size step."""
    if equity <= 0 or entry <= 0 or leverage <= 0:
        return {"size": 0.0, "notional": 0.0, "risk_usd": 0.0, "capped_by": "no_equity"}
    full = equity / (1.0 + leverage * cfg.taker_fee) * leverage
    per_dollar = abs(entry - stop) / entry + 2.0 * cfg.taker_fee + cfg.slippage
    by_risk = cfg.risk_frac * equity / per_dollar
    notional = min(full, by_risk)
    step = 10 ** sz_decimals
    size = math.floor(notional / entry * step + 1e-9) / step
    return {"size": size, "notional": round(size * entry, 4), "risk_usd": round(size * entry * per_dollar, 4),
            "risk_pct_of_equity": round(size * entry * per_dollar / equity * 100.0, 3),
            "capped_by": "risk" if by_risk <= full else "leverage", "equity": equity}


def ratchet_stop(cfg: SleeveConfig, is_long: bool, fill: float, target: float, current_stop: Optional[float],
                 bars_since_fill: Sequence[Candle]) -> Optional[float]:
    """The stop the ratchet asks for now, or None. `bars_since_fill`: the
    closed 15m bars AFTER the bar the entry filled in (the walker never
    counts the fill bar). The stop goes to the entry once the best price
    covered 75% (be75) of the way to the target, only if that is tighter
    than the stop resting now and the last close is more than a tick
    beyond it. Once at the entry it never moves again."""
    rule = F.RATCHET_RULES[cfg.ratchet]
    if rule is None or not bars_since_fill:
        return None
    side = 1 if is_long else -1
    best = max(b.h for b in bars_since_fill) if is_long else min(b.l for b in bars_since_fill)
    new = F.ratchet_stop(rule, side, fill, target, best, None)   # "be" mode never reads the swing
    if new is None:
        return None
    # On the price grid, and at least half a tick tighter than what rests:
    # the venue rounds the stop it is sent, so "tighter by a rounding
    # remainder" would re-place the same stop every tick.
    new = round(round(new / cfg.tick_size) * cfg.tick_size, 6)
    if current_stop is not None and side * (new - current_stop) <= cfg.tick_size / 2:
        return None
    if side * (bars_since_fill[-1].c - new) <= cfg.tick_size:
        return None
    return new
