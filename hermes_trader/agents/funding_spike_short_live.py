"""funding_spike_short — crowded-long funding-spike fade (W-F2A, VALIDATED 2026-07-09).

Swarm Lane F re-built the D4 funding-extreme idea with independent-episode dedup
and the funding term the short COLLECTS (findings/W-F2A... in W-F2.md): when a
coin's trailing-24h funding z-scores >= 2.0 against its own trailing-30d daily
distribution, the longs are crowded and the next 5 days pay the short:
+6.2%/episode net @12bps (+6.0% @25bps), both OOS halves positive (+2.44/+8.98),
MC p=0.0027 over n=25 deduplicated episodes, plus +0.19%/event of funding
collected. Dedup got STRONGER, not weaker — the opposite of a spurious result.

VALIDATED STRUCTURE (trade exactly what was tested): decide on settled funding
rows at day granularity, enter next open, 5d hold, 15% hard stop, no trail.
Episode dedup: once a coin fires, NO re-entry until its z falls back below 1.0.

Starts shadow_only=true: records every signal to the unified shadow ledger (in
BOTH modes) and must hold up in scripts/shadow_status.py forward grading before
any operator live flip. KILL: forward EV25 < 0 over 15 episodes.
"""
from __future__ import annotations

import json
import logging
import statistics
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.dsl_exit import active_position_coins
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file
from hermes_trader.client.hl_client import fetch_funding_history
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

_BOOK_NAME = "funding_spike_short"
_DAY_MS = 86_400_000
_TS_FILE = state_file(".funding_spike_short_live_ts")
_SEEN_FILE = state_file(".funding_spike_short_live_seen.json")


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
    """coin -> entry-day ms of the ACTIVE episode (cleared when z < exit_z)."""
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


def _val(m, key: str) -> float:
    try:
        return float(m.get(key) if isinstance(m, dict) else getattr(m, key))
    except Exception:
        return 0.0


def _is_tradeable_perp(m: Dict[str, Any]) -> bool:
    coin = m.get("coin") or ""
    return bool(coin) and not coin.startswith("@") and ":" not in coin and m.get("type") != "spot"


def _scan_coins(universe, cfg: Dict[str, Any]) -> List[str]:
    """Top max_scan_coins liquid perps (funding history is one HTTP call per coin,
    so the scan is volume-bounded like premium-fade's was)."""
    min_vol = float(cfg.get("min_volume_usd", 20_000_000.0))
    cap = int(cfg.get("max_scan_coins", 40))
    rows = []
    for m in universe or []:
        if not _is_tradeable_perp(m):
            continue
        dvol = _val(m, "dayNtlVlm")
        if dvol >= min_vol:
            rows.append((dvol, m["coin"]))
    rows.sort(reverse=True)
    return [c for _, c in rows[:cap]]


def funding_z(rows: List[Dict[str, Any]], now_ms: int,
              lookback_days: int = 30) -> Optional[float]:
    """z of the trailing-24h funding sum vs the coin's own daily-sum distribution
    over the prior `lookback_days` (settled hourly rows only — W-F2A spec).
    Pure + injectable for tests."""
    daily: Dict[int, float] = {}
    last24 = 0.0
    for r in rows or []:
        try:
            t = int(r.get("time") or 0)
            rate = float(r.get("fundingRate", r.get("funding", 0.0)) or 0.0)
        except Exception:
            continue
        if not t or t > now_ms:
            continue                       # settled rows only
        if t > now_ms - _DAY_MS:
            last24 += rate
        else:
            daily[t // _DAY_MS] = daily.get(t // _DAY_MS, 0.0) + rate
    hist = [daily[d] for d in sorted(daily)][-lookback_days:]
    if len(hist) < max(10, lookback_days // 2):
        return None
    mu = statistics.mean(hist)
    sd = statistics.pstdev(hist)
    if sd == 0:
        return 0.0
    return (last24 - mu) / sd


# Injection seam: tests monkeypatch _coin_funding_z.
def _coin_funding_z(coin: str, now_ms: int, lookback_days: int) -> Optional[float]:
    try:
        rows = fetch_funding_history(coin, now_ms - (lookback_days + 2) * _DAY_MS, now_ms)
        return funding_z(rows, now_ms, lookback_days)
    except Exception:
        return None


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


def _analysis(coin: str, z: float, cfg: Dict[str, Any]) -> Dict[str, Any]:
    stop_pct = float(cfg.get("stop_pct", 15.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    hold_days = float(cfg.get("hold_days", 5.0))
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "SHORT", "side": "short",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": f"[{_BOOK_NAME}] crowded-long funding spike: 24h funding z={z:.2f} vs own 30d",
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": _BOOK_NAME,
        "strategy_book_notional": float(cfg.get("notional_usd", 20.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "min_short_volume_usd_override": float(cfg.get("executor_short_volume_floor_usd",
                                                       cfg.get("min_volume_usd", 20_000_000.0))),
        "dsl_exit_override": {
            # Validated exit shape: 15% stop or 5d horizon close, NO trail.
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            "protect_pct": 9999.0,
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


def maybe_run(config: Dict[str, Any], universe, positions,
              fetch_candles: Callable, execute_fn: Callable,
              close_fn: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    cfg = config.get("funding_spike_short") or {}
    if not bool(cfg.get("enabled", False)):
        return None

    interval_h = float(cfg.get("scan_interval_hours", 6.0))
    now = time.time()
    if now - _last_ts() < interval_h * 3600:
        return None

    now_ms = int(now * 1000)
    entry_z = float(cfg.get("entry_z", 2.0))
    exit_z = float(cfg.get("exit_z", 1.0))
    lookback = int(cfg.get("lookback_days", 30))

    mids = {m.get("coin"): (_val(m, "midPx") or _val(m, "markPx"))
            for m in universe or [] if m.get("coin")}
    seen = _load_seen()
    signals: List[Dict[str, Any]] = []
    scanned = 0
    for coin in _scan_coins(universe, cfg):
        z = _coin_funding_z(coin, now_ms, lookback)
        scanned += 1
        if z is None:
            continue
        if coin in seen:
            if z < exit_z:
                seen.pop(coin, None)       # episode over — coin re-armable
            continue
        if z >= entry_z:
            signals.append({"coin": coin, "side": "short", "z": round(z, 3),
                            "signal_bar_t": (now_ms // _DAY_MS) * _DAY_MS,
                            "entry_ref_px": round(mids.get(coin, 0.0), 8)})

    shadow_only = bool(cfg.get("shadow_only", True))

    # Record in BOTH modes (grading never stops when capital goes on).
    shadow_ledger.record_many(_BOOK_NAME, [{
        "coin": s["coin"], "side": "short",
        "signal_bar_t": s["signal_bar_t"], "entry_ref_px": s["entry_ref_px"],
        "horizon_days": float(cfg.get("hold_days", 5.0)),
        "stop_pct": float(cfg.get("stop_pct", 15.0)),
        "ts": now_ms,
        "meta": {"funding_z": s["z"], "shadow": shadow_only},
    } for s in signals])

    opened = 0
    skipped = {"held": 0, "claimed": 0, "blocked": 0}

    if shadow_only:
        for s in signals:
            seen[s["coin"]] = s["signal_bar_t"]    # arm episode dedup in shadow too
        _save_seen(seen)
        _save_ts(now)
        rec = {"event": _BOOK_NAME, "ts": now_ms, "shadow": True, "scanned": scanned,
               "signals": len(signals), "opened": 0, "skipped": skipped,
               "candidates": signals[:10]}
        log_event(rec)
        logger.info(f"[funding-spike-short] SHADOW scanned={scanned} signals={len(signals)}")
        return rec

    held = _held_coins(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK_NAME)
    blocked_by_claim = claims.claimed_by_others(_BOOK_NAME)
    max_new = int(cfg.get("max_new_per_cycle", 1))

    for sig in signals:
        coin = sig["coin"]
        if opened >= max_new:
            break
        if coin in held:
            skipped["held"] += 1
            continue
        if coin in blocked_by_claim:
            skipped["claimed"] += 1
            continue
        if not claims.claim(coin, _BOOK_NAME):
            skipped["claimed"] += 1
            continue
        try:
            result = execute_fn(_analysis(coin, sig["z"], cfg))
            if _execute_opened(result):
                opened += 1
                held.add(coin)
                seen[coin] = sig["signal_bar_t"]
                log_event({"event": "book_open", "book": _BOOK_NAME, "coin": coin,
                           "side": "short", "sig_t": sig["signal_bar_t"]})
                logger.info(f"[funding-spike-short] LIVE opened short {coin} (funding z {sig['z']:.2f})")
            else:
                skipped["blocked"] += 1
                claims.release(coin, _BOOK_NAME)
                reason = _execute_block_detail(result)
                logger.warning(f"[funding-spike-short] {coin} not opened"
                               + (f": {reason}" if reason else ""))
        except Exception as exc:
            skipped["blocked"] += 1
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[funding-spike-short] open {coin} failed: {exc}")

    _save_seen(seen)
    claims.save()
    _save_ts(now)

    rec = {"event": _BOOK_NAME, "ts": now_ms, "shadow": False, "scanned": scanned,
           "signals": len(signals), "opened": opened, "skipped": skipped,
           "candidates": signals[:10]}
    log_event(rec)
    logger.info(f"[funding-spike-short] LIVE signals={len(signals)} opened={opened} skipped={skipped}")
    return rec
