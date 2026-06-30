"""Live wiring for the volume-follow-through breakout LONG (operator's eye, 2026-06-29).

Discovered from the operator's MANTA 5m chart read and validated on 180 small-cap
movers (research/alpha_swarm/runners/volume_followthrough.md): a 5m breakout candle
whose volume spike is CONFIRMED by the next candle holding elevated volume runs >=20%
at ~5x the rate of an unconfirmed one-bar spike (which is a pump-and-dump that reverts).

Signal (lookahead-safe, on COMPLETED 5m bars only):
- breakout bar = last-but-one completed bar: new W-bar high, volume >= Bx * trailing
  mean, green (close > open).
- confirm bar = last completed bar: volume >= Cx * the same trailing mean (the
  follow-through — demand persists instead of dying).
- enter at the next bar (== now) market, hold ~4h, TIGHT profit-floor exit.

HONESTY (CLAUDE.md): the CONFIRMED breakout is a relative QUALITY FILTER, not a proven
standalone +EV trigger — even the best config nets ~-0.10% in backtest (5m breakouts
fizzle often; MANTA-scale +50% runs are rare). This book is a SMALL live forward test
($8/pos, 1x) the operator green-lit to gather real fills, NOT a proven money-maker.
Revert instantly with `vol_breakout_long.shadow_only=true` (hot-read, no restart).

Exit is the live TIGHT profit-floor (retrace 0.10, no phase-2 loosening): the
breakout-long exit study (findings/exit_strategy_kaito.md) proved every looser variant
is -EV — breakouts mean-revert fast, so bank quickly.

To bound API load (429 history), the 5m scan is restricted to coins already MOVING
(>= min_mover_pct 24h and >= min_volume_usd), capped at max_scan_coins by dollar volume.
Routes through maybe_execute so every safety gate (margin, liquidity floor, dedup)
applies; strategy-book entries are exempt only from the discretionary runner gate.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.dsl_exit import active_position_coins
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

_BOOK_NAME = "vol_breakout_long"
_BAR_MS = 300_000  # 5m
_TS_FILE = state_file(".vol_breakout_long_live_ts")
_SEEN_FILE = state_file(".vol_breakout_long_live_seen.json")


def _bar_t(bar) -> int:
    try:
        return int(bar.get("t") if isinstance(bar, dict) else getattr(bar, "t", 0))
    except Exception:
        return 0


def _val(bar, key: str) -> float:
    try:
        return float(bar.get(key) if isinstance(bar, dict) else getattr(bar, key))
    except Exception:
        return 0.0


def _completed_bars(bars, now_ms: int) -> List[Any]:
    """Drop the still-forming last bar (its close ts is in the future)."""
    if not bars:
        return []
    out = list(bars)
    last_t = _bar_t(out[-1])
    if last_t and (now_ms - last_t) < _BAR_MS:
        out = out[:-1]
    return out


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _is_confirmed_breakout(cb: List[Any], window: int, bx: float, cx: float,
                           require_new_high: bool = True,
                           confirm_require_green: bool = False,
                           min_influx_dollar: float = 0.0) -> Optional[Dict[str, Any]]:
    """cb = COMPLETED 5m bars. breakout = cb[-2], confirm = cb[-1]. Returns a signal
    dict if the confirmed-breakout pattern fires on the freshly-closed bars, else None.

    require_new_high: gate the entry candle on a new W-bar high (breakout). When false,
    the entry is the pure volume-influx (green candle + vol >= bx*trailing-mean), which is
    the operator's forward-test variant.
    confirm_require_green: also require the follow-through candle to close green."""
    if len(cb) < window + 2:
        return None
    bi = len(cb) - 2                      # breakout bar index
    win = cb[bi - window:bi]              # W bars strictly BEFORE the breakout
    vmean = _mean([_val(b, "v") for b in win])
    if vmean <= 0:
        return None
    hi = max(_val(b, "h") for b in win)
    bo, bc, bv = _val(cb[bi], "o"), _val(cb[bi], "c"), _val(cb[bi], "v")
    # Entry candle: green + volume influx, and (optionally) a new W-bar high. The pure
    # volume-influx variant (operator forward-test) sets require_new_high=false: just a
    # green 5m candle whose volume >= bx * the short trailing mean.
    if not (bc > bo and bv >= bx * vmean):
        return None
    if min_influx_dollar > 0 and bc * bv < min_influx_dollar:   # absolute $-volume floor (anti-thin/game)
        return None
    if require_new_high and not (bc > hi):
        return None
    conf = cb[bi + 1]
    if _val(conf, "v") < cx * vmean:                     # follow-through: volume persists
        return None
    if confirm_require_green and not (_val(conf, "c") >= _val(conf, "o")):  # 2nd candle holds up
        return None
    return {
        "breakout_bar_t": _bar_t(cb[bi]),
        "confirm_bar_t": _bar_t(conf),
        "entry_ref_px": round(_val(conf, "c"), 8),
        "breakout_vol_x": round(bv / vmean, 2),
        "confirm_vol_x": round(_val(conf, "v") / vmean, 2),
    }


def _immediate_signal(cb: List[Any], window: int, bx: float, vol_ref: str,
                      min_influx_dollar: float = 0.0) -> Optional[Dict[str, Any]]:
    """Operator's method (2026-06-29): the LAST completed bar is a GREEN candle whose volume
    is >= bx * the reference, entered IMMEDIATELY next bar (no confirm-candle lag, no SMA-at-
    climax). vol_ref='prev' = vs the immediately previous candle (catches the expansion off a
    quiet base, median entry at +0.00% extension); 'sma' = vs the trailing-`window` mean.

    min_influx_dollar: the influx candle must also trade >= this many DOLLARS (close*volume).
    '1.5x of nothing is still nothing' - the relative gate alone is gameable on thin coins and
    fires on noise; the absolute floor removes that and took the crypto backtest from breakeven
    (OOS h2 -0.07%) to OOS-positive (+0.096% / +0.16/+0.03 at $250k). 0 disables."""
    if len(cb) < window + 1:
        return None
    bi = len(cb) - 1
    o, c, v = _val(cb[bi], "o"), _val(cb[bi], "c"), _val(cb[bi], "v")
    if not (c > o):                       # same direction = green for a long
        return None
    if min_influx_dollar > 0 and c * v < min_influx_dollar:
        return None
    if vol_ref == "prev":
        ref = _val(cb[bi - 1], "v")
    else:
        ref = _mean([_val(b, "v") for b in cb[bi - window:bi]])
    if ref <= 0 or v < bx * ref:
        return None
    return {"breakout_bar_t": _bar_t(cb[bi]), "confirm_bar_t": _bar_t(cb[bi]),
            "entry_ref_px": round(c, 8), "breakout_vol_x": round(v / ref, 2),
            "confirm_vol_x": round(v / ref, 2)}


def _last_ts() -> float:
    try:
        return float(open(_TS_FILE).read().strip())
    except Exception:
        return 0.0


def _save_ts(t: float) -> None:
    try:
        open(_TS_FILE, "w").write(str(t))
    except Exception:
        pass


def _load_seen() -> Dict[str, int]:
    try:
        raw = json.load(open(_SEEN_FILE))
        return {str(k): int(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_seen(seen: Dict[str, int]) -> None:
    try:
        with open(_SEEN_FILE, "w") as fh:
            json.dump(seen, fh, sort_keys=True)
    except Exception:
        pass


def _held_coins(positions) -> set:
    held = set()
    for p in positions or []:
        pos = p.get("position", p) if isinstance(p, dict) else {}
        coin = pos.get("coin")
        try:
            szi = float(pos.get("szi", 0) or 0)
        except (TypeError, ValueError):
            szi = 0.0
        if coin and szi != 0:
            held.add(coin)
    try:
        held.update(active_position_coins().keys())
    except Exception:
        pass
    return held


def _execute_opened(result: Any) -> bool:
    if isinstance(result, dict):
        nested = result.get("result")
        if isinstance(nested, dict):
            return bool(nested.get("executed"))
        if "executed" in result:
            return bool(result.get("executed"))
        if "ok" in result:
            return bool(result.get("ok"))
    return result is None


def _execute_block_detail(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    return (result.get("reason") or result.get("error")
            or result.get("blocked_by") or result.get("gate_results") or result)


def _is_tradeable_perp(m: Dict[str, Any]) -> bool:
    coin = m.get("coin") or ""
    return bool(coin) and not coin.startswith("@") and ":" not in coin and m.get("type") != "spot"


def _mover_universe(universe, cfg: Dict[str, Any]) -> List[str]:
    """Cheap pre-filter from the cached universe (NO extra API): coins moving >=
    min_mover_pct over 24h AND >= min_volume_usd dollar volume, top max_scan_coins
    by dollar volume. Bounds the 5m candle fetches to the handful that can MANTA."""
    min_pct = float(cfg.get("min_mover_pct", 8.0))
    min_vol = float(cfg.get("min_volume_usd", 5_000_000.0))
    cap = int(cfg.get("max_scan_coins", 25))
    rows: List[tuple] = []
    for m in universe or []:
        if not _is_tradeable_perp(m) or (m.get("coin") or "") == "BTC":
            continue
        prev = _val(m, "prevDayPx")
        cur = _val(m, "midPx") or _val(m, "markPx")
        dvol = _val(m, "dayNtlVlm")
        if prev <= 0 or cur <= 0 or dvol < min_vol:
            continue
        move_pct = abs(cur / prev - 1.0) * 100.0
        if move_pct < min_pct:
            continue
        rows.append((dvol, m["coin"]))
    rows.sort(reverse=True)
    return [c for _, c in rows[:cap]]


def _analysis(coin: str, sig: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    stop_pct = float(cfg.get("stop_pct", 20.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    hold_hours = float(cfg.get("hold_hours", 4.0))
    retrace = float(cfg.get("retrace_threshold", 0.10))   # TIGHT floor — do NOT loosen
    protect = float(cfg.get("protect_pct", 1.0))
    # side: 'long' = ride the breakout (refuted, no edge). 'short' = FADE the green
    # volume pop (validated 2026-06-29: with the $250k $-floor, shorting the 1.5x-vol
    # green candle beats a random-short null by +0.120% — the pop is a blow-off that reverts).
    side = "short" if str(cfg.get("side", "long")).lower() == "short" else "long"
    is_short = side == "short"
    out = {
        "id": str(uuid.uuid4()),
        "coin": coin,
        "verdict": "SHORT" if is_short else "LONG",
        "side": side,
        "confidence": 0.99,
        "entry_px": 0.0,
        "stop_px": 0.0,
        "tp_px": 0.0,
        "reasoning": (
            (f"[{_BOOK_NAME}] FADE the 5m green vol-pop "
             if is_short else f"[{_BOOK_NAME}] confirmed 5m breakout: ")
            + f"breakout vol {sig['breakout_vol_x']:.1f}x, confirm vol {sig['confirm_vol_x']:.1f}x"
            + (" -> SHORT the blow-off" if is_short else " (follow-through)")
        ),
        "news_risk": "none",
        "ai_down": False,
        "created_at": int(time.time() * 1000),
        "composite_score": 0.0,
        "strategy_book": _BOOK_NAME,
        "strategy_book_notional": float(cfg.get("notional_usd", 8.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": float(cfg.get("tp_scale_fraction", 0.0)),
        "dsl_exit_override": {
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            "protect_pct": protect,
            "retrace_threshold": retrace,
            "hard_timeout_minutes": hold_hours * 60.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
            # No phase-2 loosening: breakout-longs mean-revert fast (exit_strategy_kaito.md).
        },
    }
    if is_short:
        out["min_short_volume_usd_override"] = float(cfg.get("executor_short_volume_floor_usd",
                                                             cfg.get("min_volume_usd", 5_000_000.0)))
    return out


def _candidate_signals(cfg: Dict[str, Any], universe, fetch_candles: Callable,
                       now_ms: int) -> List[Dict[str, Any]]:
    window = int(cfg.get("vol_window", 48))
    bx = float(cfg.get("breakout_vol_x", 3.0))
    cx = float(cfg.get("confirm_vol_x", 1.5))
    require_new_high = bool(cfg.get("require_new_high", True))
    confirm_require_green = bool(cfg.get("confirm_require_green", False))
    # entry_mode 'immediate' = operator's rule (green + vol>=bx*ref, enter on the next bar,
    # no confirm lag). vol_ref 'prev' = vs the previous candle. Default 'confirm'/'sma'
    # preserves the existing book behavior.
    entry_mode = str(cfg.get("entry_mode", "confirm"))
    vol_ref = str(cfg.get("vol_ref", "sma"))
    min_influx_dollar = float(cfg.get("min_influx_dollar_vol", 0.0) or 0.0)
    history_bars = max(window + 5, int(cfg.get("history_bars", 70)))
    entry_window_ms = float(cfg.get("entry_window_minutes", 7.0)) * 60_000

    signals: List[Dict[str, Any]] = []
    for coin in _mover_universe(universe, cfg):
        try:
            cb = _completed_bars(fetch_candles(coin, "5m", history_bars), now_ms)
        except Exception:
            continue
        if entry_mode == "immediate":
            sig = _immediate_signal(cb, window, bx, vol_ref, min_influx_dollar)
        else:
            sig = _is_confirmed_breakout(cb, window, bx, cx, require_new_high,
                                         confirm_require_green, min_influx_dollar)
        if not sig:
            continue
        # Freshness: only act while the confirm bar just closed (enter near the next
        # bar's open, like the backtest). Skip stale signals from a slow cycle.
        confirm_close = sig["confirm_bar_t"] + _BAR_MS
        if now_ms - confirm_close > entry_window_ms:
            continue
        sig["coin"] = coin
        sig["side"] = "long"
        signals.append(sig)
    return signals


def maybe_run(config: Dict[str, Any], universe, positions,
              fetch_candles: Callable, execute_fn: Callable,
              close_fn: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    cfg = config.get("vol_breakout_long") or {}
    if not bool(cfg.get("enabled", False)):
        return None

    interval_min = float(cfg.get("scan_interval_minutes", 5.0))
    now = time.time()
    if now - _last_ts() < interval_min * 60:
        return None

    now_ms = int(now * 1000)
    signals = _candidate_signals(cfg, universe, fetch_candles, now_ms)
    shadow_only = bool(cfg.get("shadow_only", True))
    opened = 0
    skipped = {"held": 0, "claimed": 0, "dedup": 0, "blocked": 0}

    if shadow_only:
        _save_ts(now)
        shadow_ledger.record_many(_BOOK_NAME, [{
            "coin": s["coin"],
            "side": "long",
            "signal_bar_t": s.get("confirm_bar_t"),
            "entry_ref_px": s.get("entry_ref_px"),
            "horizon_days": float(cfg.get("hold_hours", 4.0)) / 24.0,
            "stop_pct": float(cfg.get("stop_pct", 20.0)),
            "ts": now_ms,
            "meta": {"breakout_vol_x": s.get("breakout_vol_x"),
                     "confirm_vol_x": s.get("confirm_vol_x")},
        } for s in signals])
        rec = {
            "event": "vol_breakout_long", "ts": now_ms, "shadow": True,
            "signals": len(signals), "opened": 0, "skipped": skipped,
            "candidates": signals[:10],
        }
        log_event(rec)
        logger.info(f"[vol-breakout-long] SHADOW signals={len(signals)}")
        return rec

    seen = _load_seen()
    held = _held_coins(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK_NAME)
    blocked_by_claim = claims.claimed_by_others(_BOOK_NAME)
    max_new = int(cfg.get("max_new_per_cycle", 1))
    # Per-book concurrency cap: the looser 1.5x trigger fires often, so cap how many
    # positions THIS book may hold at once. Leaves the rest of max_concurrent for the
    # main engine — no slot collision. Counted from the claims registry (post-prune =
    # live). 0 disables the cap.
    max_book = int(cfg.get("max_book_positions", 3))
    book_open = sum(1 for owner in claims.claims().values() if owner == _BOOK_NAME)
    room = max_new if max_book <= 0 else max(0, min(max_new, max_book - book_open))
    if room <= 0:
        skipped["book_cap"] = max_book
        _save_ts(now)
        rec = {"event": "vol_breakout_long", "ts": now_ms, "shadow": False,
               "signals": len(signals), "opened": 0, "skipped": skipped,
               "book_open": book_open, "candidates": signals[:10]}
        log_event(rec)
        logger.info(f"[vol-breakout-long] at book cap ({book_open}/{max_book}) — leaving slots for main engine")
        return rec

    for sig in signals:
        coin = sig["coin"]
        sig_t = int(sig.get("confirm_bar_t") or 0)
        if opened >= room:
            break
        if coin in held:
            skipped["held"] += 1
            continue
        if coin in blocked_by_claim:
            skipped["claimed"] += 1
            continue
        if sig_t and seen.get(coin) == sig_t:
            skipped["dedup"] += 1
            continue
        if not claims.claim(coin, _BOOK_NAME):
            skipped["claimed"] += 1
            continue
        try:
            result = execute_fn(_analysis(coin, sig, cfg))
            if _execute_opened(result):
                opened += 1
                held.add(coin)
                if sig_t:
                    seen[coin] = sig_t
                logger.info(f"[vol-breakout-long] LIVE opened {cfg.get("side","long")} {coin} "
                            f"(breakout {sig['breakout_vol_x']:.1f}x, confirm {sig['confirm_vol_x']:.1f}x)")
            else:
                skipped["blocked"] += 1
                claims.release(coin, _BOOK_NAME)
                reason = _execute_block_detail(result)
                logger.warning(f"[vol-breakout-long] {coin} not opened by executor"
                               + (f": {reason}" if reason else ""))
        except Exception as exc:
            skipped["blocked"] += 1
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[vol-breakout-long] open {coin} failed: {exc}")

    if opened:
        _save_seen(seen)
    claims.save()
    _save_ts(now)

    rec = {
        "event": "vol_breakout_long", "ts": now_ms, "shadow": False,
        "signals": len(signals), "opened": opened, "skipped": skipped,
        "candidates": signals[:10],
    }
    log_event(rec)
    logger.info(f"[vol-breakout-long] LIVE signals={len(signals)} opened={opened} skipped={skipped}")
    return rec
