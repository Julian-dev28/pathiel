"""Live wiring for the negative-funding volume-influx FADE (SHORT).

Swarm-discovered + validated 2026-06-29 (research/alpha_swarm/runners/influx_funding_fade.md):
the volume-influx LONG bled, but the SHORT pays. A coin with deep-negative 8h funding
(crowded shorts) that prints a green 5m volume-influx pop tends to FAIL the pop and
continue DOWN. Shorting the failed pop rides the continuation.

Validated: funding <= -0.10%/8h + green 5m vol-influx (vol >= 1.5x trailing-6 mean) ->
SHORT next-bar open. EV +0.46% net-of-funding-cost, +EV in BOTH OOS halves, across every
stop width 8-40% (squeeze adv only 7-20%), beats a matched random-short null (timing alpha,
not drift), 69 coins (top-3 only 25%, survives dropping the biggest contributor). Survivor
bias is DOWNWARD for shorts (dead coins = excluded short wins) so this is closer to a floor.
Single ~17-day regime -> records to the shadow ledger for forward grading even while LIVE.

Deployed LIVE small (operator sign-off 2026-06-29): $20 / 1x / 25% stop / hold ~8h, per-book
position cap, on liquid small-caps (>= min_volume_usd). Routes through maybe_execute so every
safety gate applies; strategy-book entries bypass only the discretionary runner gate. Kill with
neg_funding_fade.shadow_only=true (hot-read).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file
from hermes_trader.client.hl_client import fetch_funding_history
from hermes_trader.session_log import append as log_event
# Reuse the volume-influx detection primitives (DRY).
from hermes_trader.agents.vol_breakout_long_live import (
    _completed_bars, _val, _bar_t, _mean, _mover_universe,
    _held_coins, _execute_opened, _execute_block_detail, _BAR_MS,
)

logger = logging.getLogger(__name__)

_BOOK_NAME = "neg_funding_fade"
_DAY_MS = 86_400_000
_TS_FILE = state_file(".neg_funding_fade_live_ts")
_SEEN_FILE = state_file(".neg_funding_fade_live_seen.json")


# Injection seam: tests monkeypatch _funding_8h_pct.
def _funding_8h_pct(coin: str, now_ms: int) -> Optional[float]:
    """Latest hourly funding rate expressed per-8h, in PERCENT (matches the research
    convention + the exchange's '8h funding' column). Negative = shorts pay longs =
    crowded shorts. None on a thin/failed read (fail-safe: no signal, never a false short)."""
    try:
        rows = fetch_funding_history(coin, now_ms - 2 * _DAY_MS)
    except Exception:
        return None
    if not rows:
        return None
    try:
        latest = max(rows, key=lambda r: int(r.get("time", 0) or 0))
        rate = latest.get("fundingRate", latest.get("funding"))
        if rate is None:
            return None
        return float(rate) * 8.0 * 100.0
    except Exception:
        return None


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


def _influx_signal(cb: List[Any], window: int, bx: float) -> Optional[Dict[str, Any]]:
    """Last COMPLETED 5m bar is a GREEN volume influx (vol >= bx * trailing-`window` mean).
    Entry is immediate (short the pop next bar), unlike the long book's confirm-then-enter."""
    if len(cb) < window + 1:
        return None
    bi = len(cb) - 1
    win = cb[bi - window:bi]
    vmean = _mean([_val(b, "v") for b in win])
    if vmean <= 0:
        return None
    o, c, v = _val(cb[bi], "o"), _val(cb[bi], "c"), _val(cb[bi], "v")
    if not (c > o and v >= bx * vmean):           # green + volume influx
        return None
    return {"influx_bar_t": _bar_t(cb[bi]), "entry_ref_px": round(c, 8),
            "influx_vol_x": round(v / vmean, 2)}


def _analysis(coin: str, sig: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    stop_pct = float(cfg.get("stop_pct", 25.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    hold_hours = float(cfg.get("hold_hours", 8.0))
    return {
        "id": str(uuid.uuid4()),
        "coin": coin,
        "verdict": "SHORT",
        "side": "short",
        "confidence": 0.99,
        "entry_px": 0.0,
        "stop_px": 0.0,
        "tp_px": 0.0,
        "reasoning": (
            f"[neg_funding_fade] crowded-short fade: 8h funding {sig['funding_8h']:.2f}% <= "
            f"{float(cfg.get('funding_max_pct', -0.10)):.2f}%, green 5m vol-influx {sig['influx_vol_x']:.1f}x "
            f"-> short the failed pop"
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
                                                       cfg.get("min_volume_usd", 5_000_000.0))),
        "dsl_exit_override": {
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            "protect_pct": float(cfg.get("protect_pct", 1.0)),
            "retrace_threshold": float(cfg.get("retrace_threshold", 0.10)),
            "hard_timeout_minutes": hold_hours * 60.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
        },
    }


def _candidate_signals(cfg: Dict[str, Any], universe, fetch_candles: Callable,
                       now_ms: int) -> List[Dict[str, Any]]:
    window = int(cfg.get("vol_window", 6))
    bx = float(cfg.get("influx_vol_x", 1.5))
    funding_max = float(cfg.get("funding_max_pct", -0.10))
    history_bars = max(window + 3, int(cfg.get("history_bars", 20)))
    entry_window_ms = float(cfg.get("entry_window_minutes", 7.0)) * 60_000

    signals: List[Dict[str, Any]] = []
    for coin in _mover_universe(universe, cfg):
        try:
            cb = _completed_bars(fetch_candles(coin, "5m", history_bars), now_ms)
        except Exception:
            continue
        sig = _influx_signal(cb, window, bx)
        if not sig:
            continue
        # Freshness: only short while the influx bar just closed (immediate entry).
        if now_ms - (sig["influx_bar_t"] + _BAR_MS) > entry_window_ms:
            continue
        # Funding gate: deep-negative 8h funding = crowded shorts. Fetched only for the
        # few influx candidates, so API cost is bounded.
        f8h = _funding_8h_pct(coin, now_ms)
        if f8h is None or f8h > funding_max:
            continue
        sig["coin"] = coin
        sig["side"] = "short"
        sig["funding_8h"] = round(f8h, 4)
        signals.append(sig)
    return signals


def maybe_run(config: Dict[str, Any], universe, positions,
              fetch_candles: Callable, execute_fn: Callable,
              close_fn: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    cfg = config.get("neg_funding_fade") or {}
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

    # Record to the shadow ledger in BOTH modes (single 17-day regime -> forward-grade live too).
    shadow_ledger.record_many(_BOOK_NAME, [{
        "coin": s["coin"], "side": "short",
        "signal_bar_t": s.get("influx_bar_t"), "entry_ref_px": s.get("entry_ref_px"),
        "horizon_days": float(cfg.get("hold_hours", 8.0)) / 24.0,
        "stop_pct": float(cfg.get("stop_pct", 25.0)), "ts": now_ms,
        "meta": {"funding_8h": s.get("funding_8h"), "influx_vol_x": s.get("influx_vol_x")},
    } for s in signals])

    if shadow_only:
        _save_ts(now)
        rec = {"event": "neg_funding_fade", "ts": now_ms, "shadow": True,
               "signals": len(signals), "opened": 0, "skipped": skipped, "candidates": signals[:10]}
        log_event(rec)
        logger.info(f"[neg-funding-fade] SHADOW signals={len(signals)}")
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
        rec = {"event": "neg_funding_fade", "ts": now_ms, "shadow": False,
               "signals": len(signals), "opened": 0, "skipped": skipped,
               "book_open": book_open, "candidates": signals[:10]}
        log_event(rec)
        logger.info(f"[neg-funding-fade] at book cap ({book_open}/{max_book})")
        return rec

    for sig in signals:
        coin = sig["coin"]
        sig_t = int(sig.get("influx_bar_t") or 0)
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
                logger.info(f"[neg-funding-fade] LIVE opened short {coin} "
                            f"(funding {sig['funding_8h']:.2f}%/8h, influx {sig['influx_vol_x']:.1f}x)")
            else:
                skipped["blocked"] += 1
                claims.release(coin, _BOOK_NAME)
                reason = _execute_block_detail(result)
                logger.warning(f"[neg-funding-fade] {coin} not opened"
                               + (f": {reason}" if reason else ""))
        except Exception as exc:
            skipped["blocked"] += 1
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[neg-funding-fade] open {coin} failed: {exc}")

    if opened:
        _save_seen(seen)
    claims.save()
    _save_ts(now)

    rec = {"event": "neg_funding_fade", "ts": now_ms, "shadow": False,
           "signals": len(signals), "opened": opened, "skipped": skipped, "candidates": signals[:10]}
    log_event(rec)
    logger.info(f"[neg-funding-fade] LIVE signals={len(signals)} opened={opened} skipped={skipped}")
    return rec
