"""young_listings — bounded lane for xyz listings under the 60-day history floor.

WHY: the min_history_bars=60 preflight (added after new listings traded on ~6
bars) structurally excludes every young HIP-3 listing from the main engine —
xyz:ZHIPU +17.6% (18d old) and xyz:MINIMAX -23% (22d old) were untouchable top
movers on 2026-07-10. This lane trades ONLY that excluded population, bounded
hard: min 2 completed bars (skip day-1 chaos), min dollar volume, max 1
concurrent position, small fixed notional, 1x.

ENTRY (config-driven so the W-Y1 study output plugs in without code changes):
a young coin prints an absolute daily move >= trigger_pct on real volume.
`up_action` / `down_action` decide what to DO about it ("long" / "short" /
"off") — continuation vs fade is an empirical question the W-Y1 backtest and
this lane's own forward ledger answer, not an opinion.

Starts shadow_only=true. Every trigger event records to the shadow ledger in
BOTH modes with side = move direction (continuation frame; the fade EV is its
mirror) + age/move meta, so forward evidence accrues even while actions are
off. The history floor itself is untouched — it still protects the main engine.
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

_BOOK_NAME = "young_listings"
_DAY_MS = 86_400_000
_TS_FILE = state_file(".young_listings_live_ts")
_SEEN_FILE = state_file(".young_listings_live_seen.json")


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


def _young_universe(universe, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """xyz-dex coins with enough dollar volume — age is checked from candles later."""
    min_vol = float(cfg.get("min_volume_usd", 3_000_000.0))
    dexes = tuple(cfg.get("dex_allowlist", ["xyz"]))
    out = []
    for m in universe or []:
        coin = m.get("coin") or ""
        if not any(coin.startswith(d + ":") for d in dexes):
            continue
        if _val(m, "dayNtlVlm") >= min_vol:
            out.append(m)
    return out


def _mover_signal(cb: List[Any], cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Young window + big completed daily move -> signal dict, else None."""
    min_age = int(cfg.get("min_age_bars", 2))
    max_age = int(cfg.get("max_age_bars", 60))
    trigger = float(cfg.get("trigger_pct", 8.0))
    if not (min_age <= len(cb) < max_age):
        return None
    if len(cb) < 2:
        return None
    prev_c = _val(cb[-2], "c")
    last_c = _val(cb[-1], "c")
    if prev_c <= 0 or last_c <= 0:
        return None
    move = (last_c / prev_c - 1.0) * 100.0
    if abs(move) < trigger:
        return None
    return {
        "signal_bar_t": _bar_t(cb[-1]),
        "entry_ref_px": round(last_c, 8),
        "move_pct": round(move, 2),
        "age_bars": len(cb),
        "direction": "up" if move > 0 else "down",
    }


def _candidate_signals(cfg: Dict[str, Any], universe, fetch_candles: Callable,
                       now_ms: int) -> List[Dict[str, Any]]:
    entry_window_ms = float(cfg.get("entry_window_hours", 8.0)) * 3_600_000
    max_age = int(cfg.get("max_age_bars", 60))
    signals: List[Dict[str, Any]] = []
    for m in _young_universe(universe, cfg):
        coin = m["coin"]
        try:
            cb = _completed_bars(fetch_candles(coin, "1d", max_age + 3), now_ms)
        except Exception:
            continue
        # Coins at/over the floor belong to the main engine, not this lane.
        sig = _mover_signal(cb, cfg)
        if not sig:
            continue
        if now_ms - (sig["signal_bar_t"] + _DAY_MS) > entry_window_ms:
            continue
        sig["coin"] = coin
        signals.append(sig)
    return signals


def _action_for(direction: str, cfg: Dict[str, Any]) -> str:
    key = "up_action" if direction == "up" else "down_action"
    act = str(cfg.get(key, "off")).lower()
    return act if act in ("long", "short") else "off"


def _analysis(sig: Dict[str, Any], side: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    stop_pct = float(cfg.get("stop_pct", 15.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    hold_days = float(cfg.get("hold_days", 2.0))
    out = {
        "id": str(uuid.uuid4()), "coin": sig["coin"],
        "verdict": side.upper(), "side": side,
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (f"[{_BOOK_NAME}] young listing ({sig['age_bars']}d) moved "
                      f"{sig['move_pct']:+.1f}% -> {side}"),
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": _BOOK_NAME,
        "strategy_book_notional": float(cfg.get("notional_usd", 15.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "dsl_exit_override": {
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            "protect_pct": 9999.0,          # stop-or-horizon, no trail (study frame)
            "retrace_threshold": 0.5,
            "hard_timeout_minutes": hold_days * 1440.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
        },
    }
    if side == "short":
        out["min_short_volume_usd_override"] = float(cfg.get("executor_short_volume_floor_usd",
                                                             cfg.get("min_volume_usd", 3_000_000.0)))
    return out


def maybe_run(config: Dict[str, Any], universe, positions,
              fetch_candles: Callable, execute_fn: Callable,
              close_fn: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    cfg = config.get("young_listings") or {}
    if not bool(cfg.get("enabled", False)):
        return None

    interval_min = float(cfg.get("scan_interval_minutes", 30.0))
    now = time.time()
    if now - _last_ts() < interval_min * 60:
        return None

    now_ms = int(now * 1000)
    signals = _candidate_signals(cfg, universe, fetch_candles, now_ms)
    shadow_only = bool(cfg.get("shadow_only", True))

    # Record EVERY trigger as a hypothetical LONG (W-Y1 spec 2026-07-10): for
    # down-moves that IS the one non-refuted hypothesis (crash-fade-long,
    # +1.17% young / +1.63% mature @25bps); for up-moves it is the refuted
    # chase, kept as the null control. Go-live rule: >=60 forward days with
    # young EV25 > 0, mature EV25 > 0, young n >= 15 -> down_action="long".
    shadow_ledger.record_many(_BOOK_NAME, [{
        "coin": s["coin"],
        "side": "long",
        "signal_bar_t": s["signal_bar_t"], "entry_ref_px": s["entry_ref_px"],
        "horizon_days": float(cfg.get("hold_days", 2.0)),
        "stop_pct": float(cfg.get("stop_pct", 15.0)),
        "ts": now_ms,
        "meta": {"move_pct": s["move_pct"], "age_bars": s["age_bars"],
                 "direction": s["direction"], "shadow": shadow_only},
    } for s in signals])

    opened = 0
    skipped = {"held": 0, "claimed": 0, "blocked": 0, "action_off": 0, "dedup": 0}

    if shadow_only:
        _save_ts(now)
        rec = {"event": _BOOK_NAME, "ts": now_ms, "shadow": True,
               "signals": len(signals), "opened": 0, "skipped": skipped,
               "candidates": signals[:10]}
        log_event(rec)
        logger.info(f"[young-listings] SHADOW signals={len(signals)}")
        return rec

    seen = _load_seen()
    held = _held_coins(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK_NAME)
    blocked_by_claim = claims.claimed_by_others(_BOOK_NAME)
    max_book = int(cfg.get("max_book_positions", 1))
    book_open = sum(1 for owner in claims.claims().values() if owner == _BOOK_NAME)
    room = max(0, min(int(cfg.get("max_new_per_cycle", 1)), max_book - book_open))

    for sig in signals:
        coin = sig["coin"]
        sig_t = int(sig["signal_bar_t"])
        if opened >= room:
            break
        side = _action_for(sig["direction"], cfg)
        if side == "off":
            skipped["action_off"] += 1
            continue
        if coin in held:
            skipped["held"] += 1
            continue
        if coin in blocked_by_claim:
            skipped["claimed"] += 1
            continue
        if seen.get(coin) == sig_t:
            skipped["dedup"] += 1
            continue
        if not claims.claim(coin, _BOOK_NAME):
            skipped["claimed"] += 1
            continue
        try:
            result = execute_fn(_analysis(sig, side, cfg))
            if _execute_opened(result):
                opened += 1
                held.add(coin)
                seen[coin] = sig_t
                log_event({"event": "book_open", "book": _BOOK_NAME, "coin": coin,
                           "side": side, "sig_t": sig_t})
                logger.info(f"[young-listings] LIVE opened {side} {coin} "
                            f"({sig['age_bars']}d old, {sig['move_pct']:+.1f}%)")
            else:
                skipped["blocked"] += 1
                claims.release(coin, _BOOK_NAME)
                logger.warning(f"[young-listings] {coin} not opened"
                               + (f": {_execute_block_detail(result)}" if result else ""))
        except Exception as exc:
            skipped["blocked"] += 1
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[young-listings] open {coin} failed: {exc}")

    if opened:
        _save_seen(seen)
    claims.save()
    _save_ts(now)

    rec = {"event": _BOOK_NAME, "ts": now_ms, "shadow": False,
           "signals": len(signals), "opened": opened, "skipped": skipped,
           "candidates": signals[:10]}
    log_event(rec)
    logger.info(f"[young-listings] LIVE signals={len(signals)} opened={opened} skipped={skipped}")
    return rec
