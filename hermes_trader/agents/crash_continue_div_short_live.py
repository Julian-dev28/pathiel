"""Live wiring for the crash-continuation divergent-weakness short edge.

Discovered by the alpha-hunt swarm (extreme_surface, 2026-06-27). The ONE
genuinely new non-overlapping cell on the extreme-move response surface:

- BTC UP tape (the coin is diverging DOWN from a rising market = relative weakness)
- coin 2-day completed-bar return <= -8% (the divergence)
- trailing completed-bar dollar volume >= floor ($20M)
- SHORT next daily open, continuation bet (it keeps falling)
- 8% stop, 10-day hold, 1x default leverage, small notional, no ATR TP scale-out

Backtest (survivor universe = upper bound): +9.1%@12bps, excess +7.0% over a
matched random-entry baseline, win 76%, OOS both halves +11.0/+7.1.

DEFAULT IS SHADOW (`shadow_only: true`): it logs candidate signals + a forward-
validation record and does NOT execute. Flip `shadow_only: false` only after the
forward log confirms the edge AND with operator sign-off (real money, opposite the
crash-fade LONG book — never auto-flip). When live, every order still routes through
maybe_execute so account/margin/news/concurrency/order-safety gates remain in force.

This is intentionally separate from extreme_fade (crash -> LONG) and
rally_exhaustion (rally -> SHORT in a DOWN tape). Distinct regime, distinct side.
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

_BOOK_NAME = "crash_continue_div_short"
_DAY_MS = 86_400_000
_TS_FILE = state_file(".crash_continue_div_short_live_ts")
_SEEN_FILE = state_file(".crash_continue_div_short_live_seen.json")


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
    if not bars:
        return []
    out = list(bars)
    last_t = _bar_t(out[-1])
    if last_t and (now_ms - last_t) < _DAY_MS:
        out = out[:-1]
    return out


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _btc_up(btc: List[Any], window: int) -> bool:
    """Up tape: BTC close now strictly above its close `window` bars ago."""
    if len(btc) < window:
        return False
    c0 = _val(btc[-window], "c")
    c1 = _val(btc[-1], "c")
    return c0 > 0 and (c1 / c0 - 1.0) > 0


def _move_ret(bars: List[Any], lookback: int) -> Optional[float]:
    if len(bars) < lookback + 1:
        return None
    c0 = _val(bars[-1 - lookback], "c")
    c1 = _val(bars[-1], "c")
    if c0 <= 0:
        return None
    return c1 / c0 - 1.0


def _trailing_dvol(bars: List[Any], window: int) -> float:
    if not bars:
        return 0.0
    xs = [_val(b, "v") * _val(b, "c") for b in bars[-window:]]
    return _mean(xs)


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
    return (
        result.get("reason")
        or result.get("error")
        or result.get("blocked_by")
        or result.get("gate_results")
        or result
    )


def _is_tradeable_perp(m: Dict[str, Any]) -> bool:
    coin = m.get("coin") or ""
    return bool(coin) and not coin.startswith("@") and ":" not in coin and m.get("type") != "spot"


def _analysis(signal: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    stop_pct = float(cfg.get("stop_pct", cfg.get("research_stop_pct", 8.0)))
    leverage = max(1, int(cfg.get("leverage", 1)))
    hold_days = float(cfg.get("hold_days", cfg.get("research_hold_days", 10.0)))
    return {
        "id": str(uuid.uuid4()),
        "coin": signal["coin"],
        "verdict": "SHORT",
        "side": "short",
        "confidence": 0.99,
        "entry_px": 0.0,
        "stop_px": 0.0,
        "tp_px": 0.0,
        "reasoning": (
            f"[crash_continue_div_short] BTC-up tape; {signal['lookback_days']}d divergent "
            f"weakness {signal['move_pct']:+.1f}%; trailing dvol ${signal['trailing_dvol']/1e6:.1f}M"
        ),
        "news_risk": "none",
        "ai_down": False,
        "created_at": int(time.time() * 1000),
        "composite_score": 0.0,
        "strategy_book": _BOOK_NAME,
        "strategy_book_notional": float(cfg.get("notional_usd", 20.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": float(cfg.get("tp_scale_fraction", 0.0)),
        "min_short_volume_usd_override": float(cfg.get("executor_short_volume_floor_usd",
                                                       cfg.get("min_volume_usd", 20_000_000.0))),
        "dsl_exit_override": {
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            "protect_pct": float(cfg.get("protect_pct", 1000.0)),
            "retrace_threshold": float(cfg.get("retrace_threshold", 1.0)),
            "hard_timeout_minutes": hold_days * 1440.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
            "phase2_tiers": [
                {"pct_above_entry": 1000.0, "retrace_threshold": 1.0},
            ],
        },
    }


def _candidate_signals(config: Dict[str, Any], universe, fetch_candles: Callable,
                       now_ms: int) -> tuple[bool, List[Dict[str, Any]]]:
    lookback = int(config.get("lookback_days", 2))
    threshold = float(config.get("threshold_pct", 8.0)) / 100.0  # magnitude of the drop
    regime_window = int(config.get("btc_window", 20))
    min_dvol = float(config.get("min_volume_usd", 20_000_000.0))
    vol_window = int(config.get("volume_window", 30))
    history_bars = max(vol_window + lookback + 2, regime_window + 2,
                       int(config.get("history_bars", 40)))

    try:
        btc = _completed_bars(fetch_candles("BTC", "1d", history_bars), now_ms)
    except Exception:
        btc = []
    btc_up = _btc_up(btc, regime_window)
    if not btc_up:
        return False, []

    signals: List[Dict[str, Any]] = []
    entry_window_ms = float(config.get("entry_window_hours", 8.0)) * 3_600_000
    for m in universe or []:
        if not _is_tradeable_perp(m):
            continue
        coin = m.get("coin") or ""
        if coin == "BTC":
            continue
        try:
            bars = _completed_bars(fetch_candles(coin, "1d", history_bars), now_ms)
        except Exception:
            bars = []
        if len(bars) < max(lookback + 1, history_bars // 2):
            continue
        sig_t = _bar_t(bars[-1])
        if sig_t:
            bar_close = sig_t + _DAY_MS
            if now_ms - bar_close > entry_window_ms:
                continue
        dvol = _trailing_dvol(bars, vol_window)
        if dvol < min_dvol:
            continue
        rr = _move_ret(bars, lookback)
        if rr is None or rr > -threshold:  # need a drop of at least `threshold`
            continue
        signals.append({
            "coin": coin,
            "side": "short",
            "signal_bar_t": sig_t,
            "entry_ref_px": round(_val(bars[-1], "c"), 8),
            "lookback_days": lookback,
            "move_pct": round(rr * 100, 2),
            "trailing_dvol": round(dvol, 2),
        })
    return True, signals


def maybe_run(config: Dict[str, Any], universe, positions,
              fetch_candles: Callable, execute_fn: Callable,
              close_fn: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    cfg = config.get("crash_continue_div_short") or {}
    if not bool(cfg.get("enabled", False)):
        return None

    interval_h = float(cfg.get("scan_interval_hours", 6.0))
    now = time.time()
    if now - _last_ts() < interval_h * 3600:
        return None

    now_ms = int(now * 1000)
    btc_up, signals = _candidate_signals(cfg, universe, fetch_candles, now_ms)
    shadow_only = bool(cfg.get("shadow_only", True))
    opened = 0
    skipped = {"held": 0, "claimed": 0, "dedup": 0, "blocked": 0}

    if shadow_only:
        _save_ts(now)
        # record each candidate to the unified shadow ledger so scripts/shadow_status.py
        # can survey + forward-grade (refute/validate) before any operator live flip.
        shadow_ledger.record_many(_BOOK_NAME, [{
            "coin": s["coin"],
            "side": "short",
            "signal_bar_t": s.get("signal_bar_t"),
            "entry_ref_px": s.get("entry_ref_px"),
            "horizon_days": float(cfg.get("hold_days", 10.0)),
            "stop_pct": float(cfg.get("stop_pct", 8.0)),
            "ts": now_ms,
            "meta": {"move_pct": s.get("move_pct"),
                     "trailing_dvol": s.get("trailing_dvol"),
                     "btc_up": btc_up},
        } for s in signals])
        rec = {
            "event": "crash_continue_div_short",
            "ts": now_ms,
            "shadow": True,
            "btc_up": btc_up,
            "signals": len(signals),
            "opened": 0,
            "skipped": skipped,
            "candidates": signals[:10],
        }
        log_event(rec)
        logger.info(
            f"[crash-continue-div-short] SHADOW btc_up={btc_up} signals={len(signals)}"
        )
        return rec

    seen = _load_seen()
    held = _held_coins(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK_NAME)
    blocked_by_claim = claims.claimed_by_others(_BOOK_NAME)
    max_new = int(cfg.get("max_new_per_cycle", 1))

    for sig in signals:
        coin = sig["coin"]
        sig_t = int(sig.get("signal_bar_t") or 0)
        if opened >= max_new:
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
            result = execute_fn(_analysis(sig, cfg))
            if _execute_opened(result):
                opened += 1
                held.add(coin)
                if sig_t:
                    seen[coin] = sig_t
                logger.info(
                    f"[crash-continue-div-short] LIVE opened short {coin} "
                    f"(div {sig['move_pct']:+.1f}%, dvol ${sig['trailing_dvol']/1e6:.1f}M)"
                )
            else:
                skipped["blocked"] += 1
                claims.release(coin, _BOOK_NAME)
                reason = _execute_block_detail(result)
                logger.warning(
                    f"[crash-continue-div-short] {coin} not recorded - executor did not open"
                    + (f": {reason}" if reason else "")
                )
        except Exception as exc:
            skipped["blocked"] += 1
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[crash-continue-div-short] open {coin} failed: {exc}")

    if opened:
        _save_seen(seen)
    claims.save()
    _save_ts(now)

    rec = {
        "event": "crash_continue_div_short",
        "ts": now_ms,
        "shadow": False,
        "btc_up": btc_up,
        "signals": len(signals),
        "opened": opened,
        "skipped": skipped,
        "candidates": signals[:10],
    }
    log_event(rec)
    logger.info(
        f"[crash-continue-div-short] LIVE btc_up={btc_up} "
        f"signals={len(signals)} opened={opened} skipped={skipped}"
    )
    return rec
