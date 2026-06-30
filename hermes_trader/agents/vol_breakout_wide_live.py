"""Minimal-risk WIDE-STOP runner sandbox (operator, 2026-06-29).

Same volume-influx entry as vol_breakout_long, but the opposite EXIT philosophy, to test
the "don't get whipsawed while catching runs" thesis live at near-zero risk.

Backtest (operator_vol_rule whipsaw study, 5m/180 movers): on this entry, a tight floor
(arm +1%) gets 0% whipsawed but captures only ~13% of a real runner's move; a WIDE stop
(25-40%) + arm-later trail (arm +10%, 35% give-back) also gets 0% whipsawed but captures
~64% of the runner. The wide-stop is NOT +EV on this breakeven entry (you hold the fizzles
longer: win 66%->45%, EV +0.01%->-0.14%) - it redistributes outcomes toward the runners.
The wide-stop STRUCTURE is right; it needs a +EV entry to pay. This bucket exists to watch
the runner-capture happen live, at $3/1x with a 30% stop = ~$0.90 max risk per trade.

Leverage note: wide stops and leverage fight (at 3x, liquidation ~-33% is INSIDE a 40% stop),
so this runs 1x. Separate from vol_breakout_long (tight-floor $8) so the two A/B on the same
entry. Kill with vol_breakout_wide.shadow_only=true (hot-read).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file
from hermes_trader.session_log import append as log_event
# Reuse the long book's volume-influx detection + plumbing (DRY) — identical ENTRY.
from hermes_trader.agents.vol_breakout_long_live import (
    _candidate_signals, _held_coins, _execute_opened, _execute_block_detail,
)

logger = logging.getLogger(__name__)

_BOOK_NAME = "vol_breakout_wide"
_TS_FILE = state_file(".vol_breakout_wide_live_ts")
_SEEN_FILE = state_file(".vol_breakout_wide_live_seen.json")


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


def _analysis(coin: str, sig: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    stop_pct = float(cfg.get("stop_pct", 30.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    hold_hours = float(cfg.get("hold_hours", 8.0))
    arm_pct = float(cfg.get("protect_pct", 10.0))          # arm the trail LATE (+10%)
    retrace = float(cfg.get("retrace_threshold", 0.35))    # give back 35% of peak gain
    tiers = cfg.get("phase2_tiers") or [{"pct_above_entry": 30.0, "retrace_threshold": 0.45}]
    side = "short" if str(cfg.get("side", "long")).lower() == "short" else "long"
    is_short = side == "short"
    out = {
        "id": str(uuid.uuid4()),
        "coin": coin,
        "verdict": "SHORT" if is_short else "LONG",
        "side": side,
        "confidence": 0.99,
        "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (
            (f"[{_BOOK_NAME}] FADE the 5m green vol-pop {sig['breakout_vol_x']:.1f}x -> SHORT, WIDE {stop_pct:.0f}% stop"
             if is_short else
             f"[{_BOOK_NAME}] 5m vol-influx {sig['breakout_vol_x']:.1f}x; WIDE {stop_pct:.0f}% stop, arm-late +{arm_pct:.0f}% trail")
        ),
        "news_risk": "none", "ai_down": False,
        "created_at": int(time.time() * 1000),
        "composite_score": 0.0,
        "strategy_book": _BOOK_NAME,
        "strategy_book_notional": float(cfg.get("notional_usd", 3.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": float(cfg.get("tp_scale_fraction", 0.0)),
        "dsl_exit_override": {
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            "protect_pct": arm_pct,
            "retrace_threshold": retrace,
            "hard_timeout_minutes": hold_hours * 60.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
            "phase2_tiers": tiers,        # loosen further on a real runner (ride the tail)
        },
    }
    if is_short:
        out["min_short_volume_usd_override"] = float(cfg.get("executor_short_volume_floor_usd",
                                                             cfg.get("min_volume_usd", 5_000_000.0)))
    return out


def maybe_run(config: Dict[str, Any], universe, positions,
              fetch_candles: Callable, execute_fn: Callable,
              close_fn: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    cfg = config.get("vol_breakout_wide") or {}
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
            "coin": s["coin"], "side": "long",
            "signal_bar_t": s.get("confirm_bar_t"), "entry_ref_px": s.get("entry_ref_px"),
            "horizon_days": float(cfg.get("hold_hours", 8.0)) / 24.0,
            "stop_pct": float(cfg.get("stop_pct", 30.0)), "ts": now_ms,
            "meta": {"breakout_vol_x": s.get("breakout_vol_x"), "confirm_vol_x": s.get("confirm_vol_x")},
        } for s in signals])
        rec = {"event": "vol_breakout_wide", "ts": now_ms, "shadow": True,
               "signals": len(signals), "opened": 0, "skipped": skipped, "candidates": signals[:10]}
        log_event(rec)
        logger.info(f"[vol-breakout-wide] SHADOW signals={len(signals)}")
        return rec

    seen = _load_seen()
    held = _held_coins(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK_NAME)
    blocked_by_claim = claims.claimed_by_others(_BOOK_NAME)
    max_new = int(cfg.get("max_new_per_cycle", 1))
    max_book = int(cfg.get("max_book_positions", 3))
    book_open = sum(1 for owner in claims.claims().values() if owner == _BOOK_NAME)
    room = max_new if max_book <= 0 else max(0, min(max_new, max_book - book_open))
    if room <= 0:
        skipped["book_cap"] = max_book
        _save_ts(now)
        rec = {"event": "vol_breakout_wide", "ts": now_ms, "shadow": False,
               "signals": len(signals), "opened": 0, "skipped": skipped,
               "book_open": book_open, "candidates": signals[:10]}
        log_event(rec)
        logger.info(f"[vol-breakout-wide] at book cap ({book_open}/{max_book})")
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
                logger.info(f"[vol-breakout-wide] LIVE opened {cfg.get("side","long")} {coin} "
                            f"(influx {sig['breakout_vol_x']:.1f}x, wide stop)")
            else:
                skipped["blocked"] += 1
                claims.release(coin, _BOOK_NAME)
                reason = _execute_block_detail(result)
                logger.warning(f"[vol-breakout-wide] {coin} not opened"
                               + (f": {reason}" if reason else ""))
        except Exception as exc:
            skipped["blocked"] += 1
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[vol-breakout-wide] open {coin} failed: {exc}")

    if opened:
        _save_seen(seen)
    claims.save()
    _save_ts(now)

    rec = {"event": "vol_breakout_wide", "ts": now_ms, "shadow": False,
           "signals": len(signals), "opened": opened, "skipped": skipped, "candidates": signals[:10]}
    log_event(rec)
    logger.info(f"[vol-breakout-wide] LIVE signals={len(signals)} opened={opened} skipped={skipped}")
    return rec
