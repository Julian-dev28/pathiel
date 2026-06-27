"""Live wiring for the premium-extreme crowded-long fade SHORT edge.

Swarm-discovered on the DATA frontier (Lane D, D4/D5, 2026-06-27). A perp whose
trailing-24h mean PREMIUM (mark vs oracle) z-scores >= 2.0 above its own 30-day
distribution is a crowded long (longs paying up); it mean-reverts → short it.

- signal = z(trailing-24h mean premium) vs the coin's own trailing-30d daily-premium dist
- z >= threshold (2.0) → SHORT next open, hold 5 days, wide 20% stop (stop-insensitive 8-40%)
- trailing dollar-volume >= floor; NO BTC-regime gate (fires in all regimes — that's its
  value: 62/109 backtest events were in BTC-UP where rally_exhaustion is silent). Regime TAGGED.

Validation (survivor universe = upper bound; a SHORT result is CONSERVATIVE — dead coins,
the best shorts, are absent): premium-z>=2 / 5d / 20% stop → net +3.67%/event @25bps, win 67%,
both OOS halves + (+1.94 / +6.07), null p=0.0002 vs a BETA-MATCHED random-short pool (so real
reversal alpha, not down-beta). Orthogonality check: only 18% of events overlap the live
rally_exhaustion / crash_continue_div_short cells (0% with crash_continue) → 82% NEW coin-days.
The premium trigger fires ~3x more events than the funding trigger with comparable EV (D5 > D4).

DEFAULT SHADOW (`shadow_only:true`): records candidates to the unified shadow ledger and
allocates ZERO capital. Flip to live only after `scripts/shadow_status.py` returns a VALIDATED
verdict (esp. that the first-OOS-half / up-regime end holds forward — the edge is regime-tilted,
paying most when BTC falls) AND operator sign-off. When live, orders route through maybe_execute
so every safety gate applies. Distinct trigger from the price-based short books.
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

_BOOK_NAME = "premium_fade_short"
_DAY_MS = 86_400_000
_TS_FILE = state_file(".premium_fade_short_live_ts")
_SEEN_FILE = state_file(".premium_fade_short_live_seen.json")


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
    if len(btc) < window:
        return False
    c0 = _val(btc[-window], "c")
    c1 = _val(btc[-1], "c")
    return c0 > 0 and (c1 / c0 - 1.0) > 0


# Injection seam: tests monkeypatch _fetch_funding; live calls the HL API.
def _fetch_funding(coin: str, start_ms: int) -> List[Dict[str, Any]]:
    try:
        return fetch_funding_history(coin, start_ms)
    except Exception:
        return []


def _premium_z(coin: str, now_ms: int, lookback_days: int) -> Optional[float]:
    """z-score of the trailing-24h mean premium vs the coin's own trailing-`lookback_days`
    daily-mean-premium distribution. Lookahead-safe (only data <= now_ms). None if thin."""
    start_ms = now_ms - (lookback_days + 3) * _DAY_MS
    rows = _fetch_funding(coin, start_ms)
    if not rows:
        return None
    by_day: Dict[int, List[float]] = {}
    for r in rows:
        try:
            t = int(r["time"])
            if t > now_ms:
                continue
            by_day.setdefault(t // _DAY_MS, []).append(float(r.get("premium", 0.0)))
        except Exception:
            continue
    if len(by_day) < lookback_days // 2:
        return None
    daily = {day: _mean(v) for day, v in by_day.items() if v}
    days = sorted(daily)
    if len(days) < 5:
        return None
    today = days[-1]
    hist = [daily[day] for day in days[:-1][-lookback_days:]]
    if len(hist) < 5:
        return None
    mu = statistics.mean(hist)
    sd = statistics.pstdev(hist)
    if sd <= 0:
        return None
    return (daily[today] - mu) / sd


def _trailing_dvol(bars: List[Any], window: int) -> float:
    if not bars:
        return 0.0
    return _mean([_val(b, "v") * _val(b, "c") for b in bars[-window:]])


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


def _analysis(signal: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    stop_pct = float(cfg.get("stop_pct", 20.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    hold_days = float(cfg.get("hold_days", 5.0))
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
            f"[premium_fade_short] crowded long: trailing-24h premium z={signal['premium_z']:.2f} "
            f">= {cfg.get('z_threshold', 2.0)}; trailing dvol ${signal['trailing_dvol']/1e6:.1f}M"
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
            "phase2_tiers": [{"pct_above_entry": 1000.0, "retrace_threshold": 1.0}],
        },
    }


def _candidate_signals(config: Dict[str, Any], universe, fetch_candles: Callable,
                       now_ms: int) -> tuple[bool, List[Dict[str, Any]]]:
    min_dvol = float(config.get("min_volume_usd", 20_000_000.0))
    vol_window = int(config.get("volume_window", 30))
    z_threshold = float(config.get("z_threshold", 2.0))
    lookback_days = int(config.get("premium_lookback_days", 30))
    regime_window = int(config.get("btc_window", 20))
    history_bars = max(vol_window + 3, regime_window + 2, int(config.get("history_bars", 40)))
    max_eval = int(config.get("max_eval_coins", 60))
    # staleness cap on the PRICE candle: a coin with a live premium spike but a stale/missing
    # daily bar (e.g. delisted/sparse feed) has no valid current entry reference — skip it, or it
    # records an ungradeable junk candidate that pollutes the forward verdict. (premium updates
    # hourly, so this is NOT the daily-roll entry-window the price-signal books use.)
    stale_cap_ms = float(config.get("max_bar_age_hours", 48.0)) * 3_600_000

    try:
        btc = _completed_bars(fetch_candles("BTC", "1d", history_bars), now_ms)
    except Exception:
        btc = []
    btc_up = _btc_up(btc, regime_window)  # tag only, NOT a gate

    # rank by volume so the funding-history fetches are bounded to the liquid set
    rows = [m for m in (universe or []) if _is_tradeable_perp(m) and (m.get("coin") or "") != "BTC"]
    rows.sort(key=lambda m: float(m.get("dayNtlVlm", 0) or 0), reverse=True)

    signals: List[Dict[str, Any]] = []
    evaluated = 0
    for m in rows:
        if evaluated >= max_eval:
            break
        coin = m.get("coin") or ""
        try:
            bars = _completed_bars(fetch_candles(coin, "1d", history_bars), now_ms)
        except Exception:
            bars = []
        if len(bars) < 3:
            continue
        sig_t = _bar_t(bars[-1])
        if sig_t and (now_ms - (sig_t + _DAY_MS)) > stale_cap_ms:
            continue  # stale daily candle -> no valid current entry reference
        dvol = _trailing_dvol(bars, vol_window)
        if dvol < min_dvol:
            continue
        evaluated += 1
        z = _premium_z(coin, now_ms, lookback_days)
        if z is None or z < z_threshold:
            continue
        signals.append({
            "coin": coin,
            "side": "short",
            "signal_bar_t": sig_t,
            "entry_ref_px": round(_val(bars[-1], "c"), 8),
            "premium_z": round(z, 3),
            "trailing_dvol": round(dvol, 2),
            "btc_up": btc_up,
        })
    return btc_up, signals


def maybe_run(config: Dict[str, Any], universe, positions,
              fetch_candles: Callable, execute_fn: Callable,
              close_fn: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    cfg = config.get("premium_fade_short") or {}
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
        shadow_ledger.record_many(_BOOK_NAME, [{
            "coin": s["coin"],
            "side": "short",
            "signal_bar_t": s.get("signal_bar_t"),
            "entry_ref_px": s.get("entry_ref_px"),
            "horizon_days": float(cfg.get("hold_days", 5.0)),
            "stop_pct": float(cfg.get("stop_pct", 20.0)),
            "ts": now_ms,
            "meta": {"premium_z": s.get("premium_z"),
                     "trailing_dvol": s.get("trailing_dvol"),
                     "btc_up": s.get("btc_up")},
        } for s in signals])
        rec = {
            "event": "premium_fade_short", "ts": now_ms, "shadow": True, "btc_up": btc_up,
            "signals": len(signals), "opened": 0, "skipped": skipped,
            "candidates": signals[:10],
        }
        log_event(rec)
        logger.info(f"[premium-fade-short] SHADOW btc_up={btc_up} signals={len(signals)}")
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
                logger.info(f"[premium-fade-short] LIVE opened short {coin} "
                            f"(premium_z {sig['premium_z']:.2f}, dvol ${sig['trailing_dvol']/1e6:.1f}M)")
            else:
                skipped["blocked"] += 1
                claims.release(coin, _BOOK_NAME)
                reason = _execute_block_detail(result)
                logger.warning(f"[premium-fade-short] {coin} not recorded - executor did not open"
                               + (f": {reason}" if reason else ""))
        except Exception as exc:
            skipped["blocked"] += 1
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[premium-fade-short] open {coin} failed: {exc}")

    if opened:
        _save_seen(seen)
    claims.save()
    _save_ts(now)

    rec = {
        "event": "premium_fade_short", "ts": now_ms, "shadow": False, "btc_up": btc_up,
        "signals": len(signals), "opened": opened, "skipped": skipped,
        "candidates": signals[:10],
    }
    log_event(rec)
    logger.info(f"[premium-fade-short] LIVE btc_up={btc_up} signals={len(signals)} "
                f"opened={opened} skipped={skipped}")
    return rec
