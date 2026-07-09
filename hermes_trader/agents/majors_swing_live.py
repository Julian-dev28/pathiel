"""Majors-swing book: trend + pullback-resume LONGS on a fixed deep-liquidity
allowlist (BTC/ETH/SOL/AAVE + xyz:SP500/xyz:XYZ100), operator-designed 2026-07-09.

WHAT IT TRADES (daily bars, completed only, lookahead-safe):
- REGIME: last completed daily close above the trend MA (200d; younger HIP-3
  markets use all available history down to a min_trend_bars floor — the MA
  length is recorded in the ledger meta so grading can slice by it).
- PULLBACK: the low of the last `pullback_lookback` days sits 3-8% below the
  20d high — a routine retrace inside an uptrend, not a crash.
- RESUME: the last completed daily close reclaims the prior day's high. Enter
  on the next bar (== now), within entry_window_hours of the signal close.

WHY THIS ENTRY: plain higher-TF breakouts were REFUTED by matched null on this
exact asset set (commit e7f3935), and alt price-pattern entries are refuted
wholesale. Pullback-resume inside an established trend is the one structure the
refutations did NOT cover; it is UNVALIDATED, which is why this book starts
shadow_only=true and must earn a VALIDATED verdict from scripts/shadow_status.py
before the operator flips it live.

GEOMETRY (operator's explicit choice, eyes open): equity_fraction 0.25 at 25x
= 625% notional per position. At 25x liquidation sits ~3.5-4% from entry, so
the effective stop is the liq-capped ~2.2-2.4% — structurally a tight stop, NOT
a hold-through-retrace swing. A single stop-out costs ~-14% of account equity
(0.25 x 25 x 2.2%); a liquidation costs the full 25% margin. The swing exposure
comes from the WIDE TRAIL on the upside (arm at +6%, phase-2 loosening at
+8/+15%) — it rides winners, it cannot survive deep retraces. The 5x variant
that survives retraces was offered and declined 2026-07-09.

KILL: majors_swing.shadow_only=true (hot-read). Signals record to the shadow
ledger in BOTH modes, so grading never stops when capital is on.
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

_BOOK_NAME = "majors_swing"
_DAY_MS = 86_400_000
_TS_FILE = state_file(".majors_swing_live_ts")
_SEEN_FILE = state_file(".majors_swing_live_seen.json")

_DEFAULT_COINS = ["BTC", "ETH", "SOL", "AAVE", "xyz:SP500", "xyz:XYZ100"]


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
    """Drop the still-forming daily bar (its close ts is in the future)."""
    if not bars:
        return []
    out = list(bars)
    last_t = _bar_t(out[-1])
    if last_t and (now_ms - last_t) < _DAY_MS:
        out = out[:-1]
    return out


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


def _pullback_resume_signal(cb: List[Any], cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """cb = COMPLETED daily bars. Returns a signal dict when trend + pullback +
    resume all hold on the freshly-closed bar, else None."""
    trend_ma = int(cfg.get("trend_ma_period", 200))
    min_bars = int(cfg.get("min_trend_bars", 60))
    hi_lb = int(cfg.get("high_lookback", 20))
    pb_lb = int(cfg.get("pullback_lookback", 5))
    pb_min = float(cfg.get("pullback_min_pct", 3.0)) / 100.0
    pb_max = float(cfg.get("pullback_max_pct", 8.0)) / 100.0

    if len(cb) < max(min_bars, hi_lb + 2, pb_lb + 2):
        return None
    last, prev = cb[-1], cb[-2]
    last_c = _val(last, "c")
    if last_c <= 0:
        return None

    # Trend regime: close above the MA. Younger markets (HIP-3) shrink the MA
    # window down to min_trend_bars instead of never signalling.
    ma_len = min(trend_ma, len(cb))
    closes = [_val(b, "c") for b in cb[-ma_len:]]
    ma = sum(closes) / len(closes)
    if ma <= 0 or last_c <= ma:
        return None

    # Pullback structure: the swing HIGH must precede the dip. hi comes from the
    # hi_lb-day window BEFORE the last pb_lb bars; lo from those last pb_lb bars
    # (resume bar excluded from both). Without this ordering, steady upward drift
    # reads as a "pullback" because any 20d window contains lows below its high.
    hi = max(_val(b, "h") for b in cb[-(hi_lb + pb_lb + 1):-(pb_lb + 1)])
    lo = min(_val(b, "l") for b in cb[-(pb_lb + 1):-1])
    if hi <= 0 or lo <= 0:
        return None
    depth = (hi - lo) / hi
    if not (pb_min <= depth <= pb_max):
        return None

    # Resume: last completed close reclaims the prior day's high.
    if last_c <= _val(prev, "h"):
        return None

    return {
        "signal_bar_t": _bar_t(last),
        "entry_ref_px": round(last_c, 8),
        "ma_len": ma_len,
        "ma": round(ma, 8),
        "pullback_pct": round(depth * 100, 2),
        "hi20": round(hi, 8),
    }


def _candidate_signals(cfg: Dict[str, Any], fetch_candles: Callable,
                       now_ms: int) -> List[Dict[str, Any]]:
    coins = list(cfg.get("coins") or _DEFAULT_COINS)
    history = int(cfg.get("history_bars", 230))
    entry_window_ms = float(cfg.get("entry_window_hours", 8.0)) * 3_600_000
    skipped_history = 0
    signals: List[Dict[str, Any]] = []
    for coin in coins:
        try:
            cb = _completed_bars(fetch_candles(coin, "1d", history), now_ms)
        except Exception:
            continue
        if len(cb) < int(cfg.get("min_trend_bars", 60)):
            skipped_history += 1
            continue
        sig = _pullback_resume_signal(cb, cfg)
        if not sig:
            continue
        # Freshness: enter near the next bar's open, not mid-day chasing.
        signal_close = sig["signal_bar_t"] + _DAY_MS
        if now_ms - signal_close > entry_window_ms:
            continue
        sig["coin"] = coin
        sig["side"] = "long"
        signals.append(sig)
    if skipped_history:
        logger.debug(f"[{_BOOK_NAME}] {skipped_history} coin(s) below min_trend_bars")
    return signals


def _analysis(sig: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    stop_pct = float(cfg.get("stop_pct", 2.2))
    leverage = max(1, int(cfg.get("leverage", 25)))
    hold_days = float(cfg.get("hold_days", 7.0))
    return {
        "id": str(uuid.uuid4()),
        "coin": sig["coin"],
        "verdict": "LONG",
        "side": "long",
        "confidence": 0.99,
        "entry_px": 0.0,
        "stop_px": 0.0,
        "tp_px": 0.0,
        "reasoning": (f"[{_BOOK_NAME}] trend+pullback-resume: close>{sig['ma_len']}d MA, "
                      f"pullback {sig['pullback_pct']:.1f}% reclaimed prior-day high"),
        "news_risk": "none",
        "ai_down": False,
        "created_at": int(time.time() * 1000),
        "composite_score": 0.0,
        "strategy_book": _BOOK_NAME,
        # Dynamic sizing: equity_fraction of the FUNDING account x leverage via the
        # executor's strategy_book_equity_frac_override path. NOTE: the executor
        # applies this as a CAP on the discretionary-path notional, so it binds
        # exactly only while the global equity_fraction_per_trade >= this fraction
        # (live: 0.50 >= 0.25).
        "strategy_book_equity_frac_override": float(cfg.get("equity_fraction", 0.25)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": float(cfg.get("tp_scale_fraction", 0.0)),
        "dsl_exit_override": {
            # Stop INSIDE the ~4% liquidation distance at 25x. This is the whole
            # downside budget; the swing is captured on the UPSIDE by the wide trail.
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            "protect_pct": float(cfg.get("protect_pct", 6.0)),      # arm late — let it swing
            "retrace_threshold": float(cfg.get("retrace_threshold", 0.35)),
            "hard_timeout_minutes": hold_days * 1440.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
            # Loosen on a real runner: give back more room the further it runs.
            "phase2_tiers": [
                {"pct_above_entry": 8.0, "retrace_threshold": 0.45},
                {"pct_above_entry": 15.0, "retrace_threshold": 0.50},
            ],
        },
    }


def maybe_run(config: Dict[str, Any], universe, positions,
              fetch_candles: Callable, execute_fn: Callable,
              close_fn: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    cfg = config.get("majors_swing") or {}
    if not bool(cfg.get("enabled", False)):
        return None

    interval_min = float(cfg.get("scan_interval_minutes", 30.0))
    now = time.time()
    if now - _last_ts() < interval_min * 60:
        return None

    now_ms = int(now * 1000)
    signals = _candidate_signals(cfg, fetch_candles, now_ms)
    shadow_only = bool(cfg.get("shadow_only", True))
    opened = 0
    skipped = {"held": 0, "claimed": 0, "dedup": 0, "blocked": 0}

    # Record in BOTH modes: grading must not stop when capital goes on.
    shadow_ledger.record_many(_BOOK_NAME, [{
        "coin": s["coin"], "side": "long",
        "signal_bar_t": s.get("signal_bar_t"), "entry_ref_px": s.get("entry_ref_px"),
        "horizon_days": float(cfg.get("hold_days", 7.0)),
        "stop_pct": float(cfg.get("stop_pct", 2.2)),
        "ts": now_ms,
        "meta": {"ma_len": s.get("ma_len"), "pullback_pct": s.get("pullback_pct"),
                 "shadow": shadow_only},
    } for s in signals])

    if shadow_only:
        _save_ts(now)
        rec = {"event": _BOOK_NAME, "ts": now_ms, "shadow": True,
               "signals": len(signals), "opened": 0, "skipped": skipped,
               "candidates": signals[:10]}
        log_event(rec)
        logger.info(f"[majors-swing] SHADOW signals={len(signals)}")
        return rec

    seen = _load_seen()
    held = _held_coins(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK_NAME)
    blocked_by_claim = claims.claimed_by_others(_BOOK_NAME)
    max_new = int(cfg.get("max_new_per_cycle", 1))
    max_book = int(cfg.get("max_book_positions", 2))
    book_open = sum(1 for owner in claims.claims().values() if owner == _BOOK_NAME)
    room = max_new if max_book <= 0 else max(0, min(max_new, max_book - book_open))
    if room <= 0:
        skipped["book_cap"] = max_book
        _save_ts(now)
        rec = {"event": _BOOK_NAME, "ts": now_ms, "shadow": False,
               "signals": len(signals), "opened": 0, "skipped": skipped,
               "book_open": book_open, "candidates": signals[:10]}
        log_event(rec)
        logger.info(f"[majors-swing] at book cap ({book_open}/{max_book})")
        return rec

    for sig in signals:
        coin = sig["coin"]
        sig_t = int(sig.get("signal_bar_t") or 0)
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
            result = execute_fn(_analysis(sig, cfg))
            if _execute_opened(result):
                opened += 1
                held.add(coin)
                if sig_t:
                    seen[coin] = sig_t
                log_event({"event": "book_open", "book": _BOOK_NAME, "coin": coin,
                           "side": "long", "sig_t": sig_t})
                logger.info(f"[majors-swing] LIVE opened long {coin} "
                            f"(pullback {sig['pullback_pct']:.1f}%, MA{sig['ma_len']})")
            else:
                skipped["blocked"] += 1
                claims.release(coin, _BOOK_NAME)
                reason = _execute_block_detail(result)
                logger.warning(f"[majors-swing] {coin} not opened"
                               + (f": {reason}" if reason else ""))
        except Exception as exc:
            skipped["blocked"] += 1
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[majors-swing] open {coin} failed: {exc}")

    if opened:
        _save_seen(seen)
    claims.save()
    _save_ts(now)

    rec = {"event": _BOOK_NAME, "ts": now_ms, "shadow": False,
           "signals": len(signals), "opened": opened, "skipped": skipped,
           "candidates": signals[:10]}
    log_event(rec)
    logger.info(f"[majors-swing] LIVE signals={len(signals)} opened={opened} skipped={skipped}")
    return rec
