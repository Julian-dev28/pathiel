"""Trigger-gated HIP-3/AI watchlist short book.

This module is intentionally not a thesis-only short. The watchlist expresses
where to look; entries still require confirmed bearish market structure:

- liquid watchlist market
- watchlist breadth risk-off
- proxy risk-off, usually SMH
- fresh completed-daily breakdown or sharp downside continuation

The default live config should run this in shadow mode until a dedicated
backtest/forward sample is strong enough to trade.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from hermes_trader.agents.dsl_exit import active_position_coins
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file
from hermes_trader.indicators.math import candle_val, ema
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

_BOOK_NAME = "hail_mary_short"
_DAY_MS = 86_400_000
_TS_FILE = state_file(".hail_mary_short_ts")
_SEEN_FILE = state_file(".hail_mary_short_seen.json")

_DEFAULT_NAMES = [
    "NVDA", "SMCI", "AVGO", "AMD", "TSM", "ASML", "ARM", "MSFT", "AMZN", "GOOGL",
    "META", "PLTR", "CRM", "ADBE", "NOW", "WDAY", "PATH", "AI", "SOUN", "UPST",
    "TSLA", "VRT", "MU", "CRWD", "SNOW", "DDOG", "HUBS", "ZS", "NET", "ARKK",
    "SOXX", "SMH", "OPENAI", "ANTHROPIC",
]


def _bar_t(bar: Any) -> int:
    try:
        return int(bar.get("t") if isinstance(bar, dict) else getattr(bar, "t", 0))
    except Exception:
        return 0


def _val(bar: Any, key: str) -> float:
    try:
        return float(candle_val(bar, key))
    except Exception:
        return 0.0


def _completed_bars(bars: Iterable[Any], now_ms: int) -> List[Any]:
    out = list(bars or [])
    if not out:
        return []
    last_t = _bar_t(out[-1])
    if last_t and (now_ms - last_t) < _DAY_MS:
        out = out[:-1]
    return out


def _dvol(market: Dict[str, Any]) -> float:
    for key in ("dayNtlVlm", "volume_usd", "vol_usd", "volumeUsd", "vol"):
        try:
            return float(market.get(key) or 0.0)
        except Exception:
            continue
    return 0.0


def _bare_name(coin: str) -> str:
    if ":" in coin:
        return coin.split(":", 1)[1].upper()
    return coin.upper()


def _names(cfg: Dict[str, Any]) -> List[str]:
    raw = cfg.get("names") or _DEFAULT_NAMES
    out = []
    for name in raw:
        s = str(name).strip().upper()
        if s:
            out.append(s)
    return out


def _tradeable(market: Dict[str, Any], allowed_dexes: set[str]) -> bool:
    coin = str(market.get("coin") or "")
    if not coin or coin.startswith("@") or market.get("type") == "spot":
        return False
    dex = market.get("dex")
    if dex and str(dex) not in allowed_dexes:
        return False
    return True


def _resolve_watchlist(
    universe: Iterable[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    wanted = set(_names(cfg))
    allowed_dexes = {str(x) for x in cfg.get("dex_allowlist", ["xyz", "vntl"])}
    best: Dict[str, Dict[str, Any]] = {}
    for market in universe or []:
        if not isinstance(market, dict) or not _tradeable(market, allowed_dexes):
            continue
        coin = str(market.get("coin") or "")
        bare = _bare_name(coin)
        if bare not in wanted:
            continue
        cur = best.get(bare)
        if cur is None or _dvol(market) > _dvol(cur):
            best[bare] = market
    ordered = [best[n] for n in _names(cfg) if n in best]
    missing = [n for n in _names(cfg) if n not in best]
    return ordered, missing


def _resolve_proxy_markets(
    universe: Iterable[Dict[str, Any]],
    cfg: Dict[str, Any],
    watchlist: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    proxies = [str(x) for x in cfg.get("proxy_coins", ["xyz:SMH", "xyz:SP500", "xyz:XYZ100"])]
    by_coin = {str(m.get("coin") or ""): m for m in universe or [] if isinstance(m, dict)}
    out = [by_coin[p] for p in proxies if p in by_coin]
    if not out:
        by_bare = {_bare_name(str(m.get("coin") or "")): m for m in watchlist}
        if "SMH" in by_bare:
            out.append(by_bare["SMH"])
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


def _held_coins(positions: Iterable[Dict[str, Any]]) -> set[str]:
    held = set()
    for p in positions or []:
        pos = p.get("position", p) if isinstance(p, dict) else {}
        coin = pos.get("coin")
        try:
            szi = float(pos.get("szi", 0) or 0)
        except (TypeError, ValueError):
            szi = 0.0
        if coin and szi != 0:
            held.add(str(coin))
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


def _trend_stats(bars: List[Any], cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if len(bars) < int(cfg.get("min_history_bars", 24)):
        return None
    closes = [_val(b, "c") for b in bars]
    highs = [_val(b, "h") for b in bars]
    lows = [_val(b, "l") for b in bars]
    if not closes or closes[-1] <= 0:
        return None

    fast_p = int(cfg.get("ema_fast", 8))
    slow_p = int(cfg.get("ema_slow", 21))
    trend_p = int(cfg.get("ema_trend", 50))
    if len(closes) < max(fast_p, slow_p) + 2:
        return None

    fast = ema(closes, fast_p)[-1]
    slow = ema(closes, slow_p)[-1]
    trend = ema(closes, min(trend_p, len(closes)))[-1]
    close = closes[-1]

    dd_lookback = min(int(cfg.get("drawdown_lookback_days", 20)), len(bars))
    high_ref = max(highs[-dd_lookback:]) if dd_lookback > 0 else close
    drawdown = close / high_ref - 1.0 if high_ref > 0 else 0.0

    ret_lookback = min(int(cfg.get("recent_drop_days", 5)), len(closes) - 1)
    recent_ret = close / closes[-1 - ret_lookback] - 1.0 if ret_lookback > 0 and closes[-1 - ret_lookback] > 0 else 0.0

    breakdown_lb = min(int(cfg.get("breakdown_lookback_days", 20)), len(bars) - 1)
    prior_low = min(lows[-1 - breakdown_lb:-1]) if breakdown_lb > 0 else lows[-1]
    breakdown_buffer = float(cfg.get("breakdown_buffer_pct", 0.0)) / 100.0
    breakdown = close <= prior_low * (1.0 + breakdown_buffer)

    min_drawdown = float(cfg.get("min_basket_drawdown_pct", 6.0)) / 100.0
    bearish = (fast < slow and close < trend) or (drawdown <= -min_drawdown and close < slow)

    return {
        "close": close,
        "fast": fast,
        "slow": slow,
        "trend": trend,
        "drawdown_pct": drawdown * 100.0,
        "recent_ret_pct": recent_ret * 100.0,
        "prior_low": prior_low,
        "breakdown": breakdown,
        "bearish": bearish,
    }


def _fetch_completed(coin: str, cfg: Dict[str, Any], fetch_candles: Callable, now_ms: int) -> List[Any]:
    history_bars = int(cfg.get("history_bars", 90))
    try:
        return _completed_bars(fetch_candles(coin, "1d", history_bars), now_ms)
    except Exception as exc:
        logger.debug(f"[hail-mary-short] candle fetch failed for {coin}: {exc}")
        return []


def _market_context(
    cfg: Dict[str, Any],
    universe: Iterable[Dict[str, Any]],
    watchlist: List[Dict[str, Any]],
    fetch_candles: Callable,
    now_ms: int,
) -> Tuple[Dict[str, Any], Dict[str, List[Any]]]:
    min_vol = float(cfg.get("min_volume_usd", 20_000_000.0))
    liquid = [m for m in watchlist if _dvol(m) >= min_vol]
    bars_by_coin: Dict[str, List[Any]] = {}
    bearish = []

    for market in liquid:
        coin = str(market.get("coin") or "")
        bars = _fetch_completed(coin, cfg, fetch_candles, now_ms)
        bars_by_coin[coin] = bars
        stats = _trend_stats(bars, cfg)
        if stats and stats["bearish"]:
            bearish.append(coin)

    breadth_pct = len(bearish) / len(liquid) if liquid else 0.0
    proxy_down = False
    proxy_rows = []
    for market in _resolve_proxy_markets(universe, cfg, watchlist):
        coin = str(market.get("coin") or "")
        bars = _fetch_completed(coin, cfg, fetch_candles, now_ms)
        bars_by_coin.setdefault(coin, bars)
        stats = _trend_stats(bars, cfg)
        is_down = bool(stats and stats["bearish"])
        proxy_down = proxy_down or is_down
        proxy_rows.append({"coin": coin, "bearish": is_down})

    min_breadth = float(cfg.get("min_breadth_bearish_pct", 0.55))
    require_proxy = bool(cfg.get("require_proxy_down", True))
    risk_off = breadth_pct >= min_breadth and (proxy_down or not require_proxy)
    ctx = {
        "risk_off": risk_off,
        "breadth_pct": round(breadth_pct, 4),
        "bearish_count": len(bearish),
        "liquid_count": len(liquid),
        "proxy_down": proxy_down,
        "proxies": proxy_rows,
        "min_volume_usd": min_vol,
    }
    return ctx, bars_by_coin


def _signal_for_market(
    market: Dict[str, Any],
    bars: List[Any],
    ctx: Dict[str, Any],
    cfg: Dict[str, Any],
    now_ms: int,
) -> Optional[Dict[str, Any]]:
    stats = _trend_stats(bars, cfg)
    if not stats:
        return None
    sig_t = _bar_t(bars[-1])
    if sig_t:
        bar_close = sig_t + _DAY_MS
        entry_window_ms = float(cfg.get("entry_window_hours", 10.0)) * 3_600_000
        if now_ms - bar_close > entry_window_ms:
            return None

    min_recent_drop = float(cfg.get("min_recent_drop_pct", 6.0))
    trend_ok = stats["fast"] < stats["slow"] and stats["close"] < stats["trend"]
    downside_continuation = stats["recent_ret_pct"] <= -min_recent_drop and stats["close"] < stats["slow"]
    if not trend_ok or not (stats["breakdown"] or downside_continuation):
        return None

    coin = str(market.get("coin") or "")
    score = 0.0
    score += abs(min(stats["drawdown_pct"], 0.0))
    score += abs(min(stats["recent_ret_pct"], 0.0)) * 0.5
    score += 6.0 if stats["breakdown"] else 0.0
    score += float(ctx.get("breadth_pct", 0.0)) * 10.0
    return {
        "coin": coin,
        "name": _bare_name(coin),
        "side": "short",
        "signal_bar_t": sig_t,
        "score": round(score, 4),
        "day_volume_usd": round(_dvol(market), 2),
        "breadth_pct": ctx.get("breadth_pct", 0.0),
        "proxy_down": bool(ctx.get("proxy_down")),
        "breakdown": bool(stats["breakdown"]),
        "close": round(stats["close"], 8),
        "prior_low": round(stats["prior_low"], 8),
        "drawdown_pct": round(stats["drawdown_pct"], 2),
        "recent_ret_pct": round(stats["recent_ret_pct"], 2),
    }


def _candidate_signals(
    cfg: Dict[str, Any],
    universe: Iterable[Dict[str, Any]],
    fetch_candles: Callable,
    now_ms: int,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    watchlist, missing = _resolve_watchlist(universe, cfg)
    ctx, bars_by_coin = _market_context(cfg, universe, watchlist, fetch_candles, now_ms)
    if not ctx["risk_off"]:
        return ctx, [], missing

    signals = []
    min_vol = float(cfg.get("min_volume_usd", 20_000_000.0))
    for market in watchlist:
        if _dvol(market) < min_vol:
            continue
        coin = str(market.get("coin") or "")
        sig = _signal_for_market(market, bars_by_coin.get(coin, []), ctx, cfg, now_ms)
        if sig:
            signals.append(sig)
    signals.sort(key=lambda s: (float(s.get("score", 0.0)), float(s.get("day_volume_usd", 0.0))), reverse=True)
    return ctx, signals, missing


def _analysis(signal: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    stop_pct = float(cfg.get("stop_pct", 12.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    hold_days = float(cfg.get("hold_days", 10.0))
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
            f"[hail_mary_short] {signal['name']} fresh bearish breakdown; "
            f"breadth={float(signal.get('breadth_pct', 0.0)):.0%}; "
            f"proxy_down={bool(signal.get('proxy_down'))}; "
            f"drawdown={float(signal.get('drawdown_pct', 0.0)):+.1f}%; "
            f"recent={float(signal.get('recent_ret_pct', 0.0)):+.1f}%"
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
        "min_short_volume_usd_override": float(
            cfg.get("executor_short_volume_floor_usd", cfg.get("min_volume_usd", 20_000_000.0))
        ),
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


def maybe_run(
    config: Dict[str, Any],
    universe: Iterable[Dict[str, Any]],
    positions: Iterable[Dict[str, Any]],
    fetch_candles: Callable,
    execute_fn: Callable,
    close_fn: Optional[Callable] = None,
) -> Optional[Dict[str, Any]]:
    cfg = config.get("hail_mary_short") or {}
    if not bool(cfg.get("enabled", False)):
        return None

    interval_h = float(cfg.get("scan_interval_hours", 6.0))
    now = time.time()
    if now - _last_ts() < interval_h * 3600:
        return None

    now_ms = int(now * 1000)
    ctx, signals, missing = _candidate_signals(cfg, universe, fetch_candles, now_ms)
    shadow_only = bool(cfg.get("shadow_only", True))
    opened = 0
    skipped = {"held": 0, "claimed": 0, "dedup": 0, "blocked": 0}

    if shadow_only:
        _save_ts(now)
        rec = {
            "event": "hail_mary_short",
            "ts": now_ms,
            "shadow": True,
            "context": ctx,
            "signals": len(signals),
            "opened": 0,
            "skipped": skipped,
            "missing": missing[:20],
            "candidates": signals[:10],
        }
        log_event(rec)
        logger.info(
            f"[hail-mary-short] SHADOW risk_off={ctx.get('risk_off')} "
            f"signals={len(signals)} breadth={float(ctx.get('breadth_pct', 0.0)):.0%}"
        )
        return rec

    seen = _load_seen()
    held = _held_coins(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK_NAME)
    blocked_by_claim = claims.claimed_by_others(_BOOK_NAME)
    max_new = int(cfg.get("max_new_per_cycle", 1))
    max_attempts = int(cfg.get("max_attempts_per_cycle", max_new))
    attempts = 0

    for sig in signals:
        coin = sig["coin"]
        sig_t = int(sig.get("signal_bar_t") or 0)
        if opened >= max_new:
            break
        if attempts >= max_attempts:
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
            attempts += 1
            result = execute_fn(_analysis(sig, cfg))
            if _execute_opened(result):
                opened += 1
                held.add(coin)
                if sig_t:
                    seen[coin] = sig_t
                logger.info(
                    f"[hail-mary-short] LIVE opened short {coin} "
                    f"(score {float(sig.get('score', 0.0)):.1f}, dvol ${float(sig.get('day_volume_usd', 0.0))/1e6:.1f}M)"
                )
            else:
                skipped["blocked"] += 1
                claims.release(coin, _BOOK_NAME)
                reason = _execute_block_detail(result)
                logger.warning(
                    f"[hail-mary-short] {coin} not recorded - executor did not open"
                    + (f": {reason}" if reason else "")
                )
        except Exception as exc:
            skipped["blocked"] += 1
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[hail-mary-short] open {coin} failed: {exc}")

    if opened:
        _save_seen(seen)
    claims.save()
    _save_ts(now)

    rec = {
        "event": "hail_mary_short",
        "ts": now_ms,
        "shadow": False,
        "context": ctx,
        "signals": len(signals),
        "opened": opened,
        "skipped": skipped,
        "missing": missing[:20],
        "candidates": signals[:10],
    }
    log_event(rec)
    logger.info(
        f"[hail-mary-short] LIVE risk_off={ctx.get('risk_off')} "
        f"signals={len(signals)} opened={opened} skipped={skipped}"
    )
    return rec
