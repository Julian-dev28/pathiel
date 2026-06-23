"""Live wiring for the cross-sectional momentum rebalancer (SHADOW-first).

Drives the pure engine (agents/xs_momentum.py) on a hold-days timer: builds the target book from
cached daily candles, diffs vs the live book, then SHADOW-logs the plan (no orders) or LIVE-executes
the diff. Default shadow_mode=True — validate forward before risking capital. The rebalance timer is
persisted so a loop restart doesn't re-fire it.

Wired as one self-gating call per loop cycle: maybe_rebalance(config, positions, execute_fn, close_fn).
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents.xs_momentum import rank_universe, rebalance_plan, is_empty_plan, TargetBook
from hermes_trader.indicators.math import candle_val
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

_TS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        ".xs_rebalance_ts")


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


def _eligible(universe: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[str]:
    """Top-N liquid TRADEABLE perps by volume (no HIP-3 `:`, no `@` spot/index, no spot type)."""
    xs = cfg.get("xs_momentum") or {}
    floor = float(xs.get("min_volume_usd", cfg.get("min_market_volume_usd", 5_000_000)) or 0)
    topn = int(xs.get("universe_top_n", 50))
    elig = []
    for m in universe or []:
        coin = m.get("coin") or ""
        if not coin or coin.startswith("@") or ":" in coin or m.get("type") == "spot":
            continue
        vol = float(m.get("dayNtlVlm") or 0)
        if vol >= floor:
            elig.append((coin, vol))
    elig.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in elig[:topn]]


def _target_book(universe, cfg, fetch_candles):
    xs = cfg.get("xs_momentum") or {}
    lb = int(xs.get("lookback_days", 14))
    k = int(xs.get("k_per_leg", 8))
    beta_window = int(xs.get("beta_window", 30))
    nbars = max(lb + 10, beta_window + 5, 40)
    cbc = {}
    for coin in _eligible(universe, cfg):
        try:
            bars = fetch_candles(coin, "1d", nbars)
        except Exception:
            bars = None
        if bars and len(bars) >= lb + 1:               # trailing_return(lb) needs lb+1 bars
            cbc[coin] = bars
    # RESIDUAL (BTC-neutral) ranking — validated stronger + smoother than total return (edge_sweep4).
    bench = None
    if bool(xs.get("residual", True)):
        try:
            bench = fetch_candles("BTC", "1d", nbars)
        except Exception:
            bench = None
    return rank_universe(cbc, lb, k, bench_bars=bench, beta_window=beta_window)


def _btc_vol_regime(fetch_candles, short: int = 14, long: int = 90) -> str:
    """'high' if BTC's current `short`-day return-vol exceeds its trailing `long`-day median, else
    'low'. The momentum edge concentrates in LOW vol (edge_sweep3); fail-open to 'low' on bad data."""
    try:
        bars = fetch_candles("BTC", "1d", long + short + 5)
    except Exception:
        return "low"
    closes = [candle_val(b, "c") for b in (bars or [])]
    if len(closes) < short + 10:
        return "low"
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
    vols = [statistics.pstdev(rets[i - short:i]) for i in range(short, len(rets) + 1)]
    if len(vols) < 10:
        return "low"
    med = statistics.median(vols[-long:] if len(vols) >= long else vols)
    return "high" if vols[-1] > med else "low"


def _book_from_positions(positions) -> (List[str], List[str]):
    longs, shorts = [], []
    for p in positions or []:
        pos = p.get("position", p) if isinstance(p, dict) else {}
        coin = pos.get("coin")
        try:
            szi = float(pos.get("szi", 0) or 0)
        except (TypeError, ValueError):
            szi = 0.0
        if not coin or szi == 0:
            continue
        (longs if szi > 0 else shorts).append(coin)
    return longs, shorts


def _analysis(coin: str, side: str, rank_score: float) -> Dict[str, Any]:
    """Synthetic analysis for the executor. external_alpha bypasses the thought-engine entry gates
    (runner/trend) — this is a separate validated edge — while every SAFETY gate still applies."""
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "LONG" if side == "long" else "SHORT", "side": side,
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": f"[xs_momentum] {side} (trailing {rank_score*100:+.1f}%)",
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "external_alpha": "xs_momentum",
    }


def maybe_rebalance(config: Dict[str, Any], universe, positions,
                    fetch_candles: Callable, execute_fn: Callable, close_fn: Callable) -> Optional[Dict]:
    """Self-gating rebalance: fires at most once per hold-days. Returns the plan (or None if not
    time / disabled / empty). SHADOW logs only; LIVE executes the diff (close drops, open adds)."""
    xs = config.get("xs_momentum") or {}
    if not bool(xs.get("enabled", False)):
        return None
    hold_days = float(xs.get("hold_days", 10))
    now = time.time()
    if now - _last_ts() < hold_days * 86400:
        return None                                            # not time to rebalance yet

    # VOL-REGIME GATE: the momentum edge concentrates in LOW BTC-vol (audit/edge_sweep3). In a
    # HIGH-vol regime, go FLAT (empty target → close everything) to sit out the dead/choppy periods.
    regime = "low"
    if bool(xs.get("vol_gate", True)):
        regime = _btc_vol_regime(fetch_candles, int(xs.get("vol_short", 14)), int(xs.get("vol_long", 90)))
    if regime == "high":
        book = TargetBook([], [], {})
    else:
        book = _target_book(universe, config, fetch_candles)
        if not book.longs or not book.shorts:
            logger.info("[xs-momentum] no target book (too few coins) — skip rebalance")
            return None
    cur_long, cur_short = _book_from_positions(positions)
    plan = rebalance_plan(book, cur_long, cur_short)
    _save_ts(now)                                              # arm the timer regardless of shadow/live

    shadow = bool(xs.get("shadow_mode", True))
    log_event({"event": "xs_rebalance", "shadow": shadow, "regime": regime,
               "longs": book.longs, "shorts": book.shorts,
               "open_long": plan["open_long"], "open_short": plan["open_short"],
               "close": plan["close_long"] + plan["close_short"]})
    logger.info(f"[xs-momentum]{' SHADOW' if shadow else ' LIVE'} rebalance [{regime}-vol] — "
                f"target {len(book.longs)}L/{len(book.shorts)}S; "
                f"open {len(plan['open_long'])}L+{len(plan['open_short'])}S, "
                f"close {len(plan['close_long']) + len(plan['close_short'])}"
                + ("  (flat: high-vol regime)" if regime == "high" else ""))

    if shadow or is_empty_plan(plan):
        return plan                                            # SHADOW: logged the target book, no orders

    # LIVE: close drops first (free capital), then open adds — both legs.
    for coin in plan["close_long"] + plan["close_short"]:
        try:
            close_fn(coin)
        except Exception as e:
            logger.warning(f"[xs-momentum] close {coin} failed: {e}")
    for coin in plan["open_long"]:
        try:
            execute_fn(_analysis(coin, "long", book.scores.get(coin, 0.0)))
        except Exception as e:
            logger.warning(f"[xs-momentum] open long {coin} failed: {e}")
    for coin in plan["open_short"]:
        try:
            execute_fn(_analysis(coin, "short", book.scores.get(coin, 0.0)))
        except Exception as e:
            logger.warning(f"[xs-momentum] open short {coin} failed: {e}")
    return plan
