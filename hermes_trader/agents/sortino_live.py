"""Live wiring for the Sortino-ranked factor rebalancer (SHADOW-first, disabled by default).

Drives the shared factor engine (agents/vol_dispersion.py) using ``score_fn="sortino"``:
within-β-tercile rebalancer where coins are scored by mean(daily return)/downside-deviation over
``window`` days. LONG the high-Sortino (reward/risk efficient) / SHORT the low-Sortino (poor
risk-adjusted return) within each BTC-beta tercile.

Validated: +3.66%/rebal (V2, within-β-tercile). More regime-stable than idio-vol (holds in
down-regime: +2.24%), corr +0.07 to momentum / +0.37 to idio-vol → partially orthogonal.

Safety defaults:
- enabled = False in DEFAULT_CONFIG → loop hook is a NO-OP until operator explicitly flips it.
- shadow_mode = True → even when enabled, only logs; never places orders until shadow_mode=False.
- Timer persisted to .sortino_ts → restart-safe, won't re-fire the rebalance on restart.

Mirrors vol_dispersion_live.py exactly (pattern: same _eligible, _book_from_positions, timer
helpers, _analysis). Only difference: config key "sortino_factor" and score_fn="sortino".
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set

from hermes_trader.agents.vol_dispersion import (
    TargetBook, rank_universe, rebalance_plan, is_empty_plan,
)
from hermes_trader.agents.rebalancer_owned import OwnedPositions, _live_coin_set
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

_TS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        ".sortino_ts")
_OWNED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           ".sortino_positions.json")

# Module-level singleton — loaded lazily on first maybe_rebalance call.
_owned: Optional[OwnedPositions] = None


def _get_owned() -> OwnedPositions:
    global _owned
    if _owned is None:
        _owned = OwnedPositions(_OWNED_FILE)
    return _owned.load()


# ── Timer helpers (mirrors vol_dispersion_live) ───────────────────────────────

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


# ── Universe filter (identical to vol_dispersion_live._eligible) ─────────────

def _eligible(universe: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[str]:
    """Top-N liquid TRADEABLE perps by volume (no HIP-3 `:`, no `@` spot/index, no spot type)."""
    sf = cfg.get("sortino_factor") or {}
    floor = float(sf.get("min_volume_usd", cfg.get("min_market_volume_usd", 5_000_000)) or 0)
    topn = int(sf.get("universe_top_n", 50))
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


def _build_target_book(universe, cfg, fetch_candles) -> TargetBook:
    sf = cfg.get("sortino_factor") or {}
    window = int(sf.get("window", 60))
    nbars = window + 10
    cbc = {}
    for coin in _eligible(universe, cfg):
        try:
            bars = fetch_candles(coin, "1d", nbars)
        except Exception:
            bars = None
        if bars and len(bars) >= window + 1:
            cbc[coin] = bars
    try:
        bench = fetch_candles("BTC", "1d", nbars)
    except Exception:
        bench = None
    if not bench or len(bench) < window + 1:
        return TargetBook([], [], {}, {})
    k = int(sf.get("k_per_tercile", 2))
    return rank_universe(cbc, bench, window, k, score_fn="sortino")


def _book_from_positions(positions) -> tuple:
    """Kept for backward-compat; internal callers now use OwnedPositions.filter_to_owned."""
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


def _analysis(coin: str, side: str, sortino: float) -> Dict[str, Any]:
    """Synthetic analysis for the executor. external_alpha tag bypasses thought-engine entry gates
    while all safety gates still apply. Mirrors vol_dispersion_live._analysis."""
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "LONG" if side == "long" else "SHORT", "side": side,
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": f"[sortino_factor] {side} (sortino={sortino:.4f})",
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "external_alpha": "sortino_factor",
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def maybe_rebalance(config: Dict[str, Any], universe, positions,
                    fetch_candles: Callable, execute_fn: Callable, close_fn: Callable) -> Optional[Dict]:
    """Self-gating rebalance: fires at most once per hold_days. Returns plan or None.

    Guard: enabled=False in config → immediate no-op (loop hook is safe to call every cycle).
    Shadow: shadow_mode=True (default) → logs the target book, places NO orders.
    """
    sf = config.get("sortino_factor") or {}
    if not bool(sf.get("enabled", False)):
        return None                                            # master gate — no-op when disabled

    hold_days = float(sf.get("hold_days", 10))
    now = time.time()
    if now - _last_ts() < hold_days * 86400:
        return None                                            # not time to rebalance yet

    book = _build_target_book(universe, config, fetch_candles)
    if not book.longs or not book.shorts:
        logger.info("[sortino-factor] no target book (too few coins or no BTC bench) — skip")
        return None

    # ── Ownership-scoped current book ─────────────────────────────────────────
    owned = _get_owned()
    owned.prune(_live_coin_set(positions))
    cur_long, cur_short = owned.filter_to_owned(positions)

    plan = rebalance_plan(book, cur_long, cur_short)
    _save_ts(now)                                              # arm timer regardless of shadow/live

    shadow = bool(sf.get("shadow_mode", True))
    log_event({"event": "sortino_rebalance", "shadow": shadow,
               "longs": book.longs, "shorts": book.shorts,
               "open_long": plan["open_long"], "open_short": plan["open_short"],
               "close": plan["close_long"] + plan["close_short"],
               "tercile_assignments": book.tercile_assignments})
    logger.info(
        f"[sortino-factor]{' SHADOW' if shadow else ' LIVE'} rebalance — "
        f"target {len(book.longs)}L/{len(book.shorts)}S; "
        f"open {len(plan['open_long'])}L+{len(plan['open_short'])}S, "
        f"close {len(plan['close_long']) + len(plan['close_short'])}"
    )

    if shadow or is_empty_plan(plan):
        return plan                                            # SHADOW: logged only, no orders

    # LIVE: close drops first (free capital), then open adds — both legs.
    for coin in plan["close_long"] + plan["close_short"]:
        try:
            close_fn(coin)
            owned.remove(coin)
        except Exception as e:
            logger.warning(f"[sortino-factor] close {coin} failed: {e}")
    for coin in plan["open_long"]:
        try:
            execute_fn(_analysis(coin, "long", book.scores.get(coin, 0.0)))
            owned.add(coin, "long")
        except Exception as e:
            logger.warning(f"[sortino-factor] open long {coin} failed: {e}")
    for coin in plan["open_short"]:
        try:
            execute_fn(_analysis(coin, "short", book.scores.get(coin, 0.0)))
            owned.add(coin, "short")
        except Exception as e:
            logger.warning(f"[sortino-factor] open short {coin} failed: {e}")
    owned.save()
    return plan
