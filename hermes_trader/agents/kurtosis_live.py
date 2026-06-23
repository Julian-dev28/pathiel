"""Live wiring for the kurtosis factor rebalancer (SHADOW-first, disabled by default).

Validated V2: +1.71%/rebal (beta-neutral, within-β-tercile). HIGH trailing-kurtosis = LONG
direction. MODEST edge — bleeds in sustained down-regimes. Start with k_per_tercile=1 and
shadow-validate forward before going live. Treat as a diversifier, not a primary edge.

Drives the shared factor engine (agents/vol_dispersion.py) using ``score_fn="kurtosis"``:
within-β-tercile rebalancer where coins are scored by excess kurtosis of daily returns over
``window`` days (~60d). LONG the high-kurtosis coins (fat-tailed, prone to positive surprises)
/ SHORT the low-kurtosis coins (thin-tailed, mean-reverting), within each BTC-beta tercile.

Safety defaults:
- enabled = False in DEFAULT_CONFIG → loop hook is NO-OP until operator explicitly flips it.
- shadow_mode = True → even when enabled, only logs; never places orders until shadow_mode=False.
- Timer persisted to .kurtosis_ts → restart-safe.

Mirrors amihud_live.py / sortino_live.py / vol_dispersion_live.py exactly. Config key: "kurtosis_factor".
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
                        ".kurtosis_ts")
_OWNED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           ".kurtosis_positions.json")

# Module-level singleton — loaded lazily on first maybe_rebalance call.
_owned: Optional[OwnedPositions] = None


def _get_owned() -> OwnedPositions:
    global _owned
    if _owned is None:
        _owned = OwnedPositions(_OWNED_FILE)
    return _owned.load()


# ── Timer helpers ─────────────────────────────────────────────────────────────

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


# ── Universe filter ──────────────────────────────────────────────────────────

def _eligible(universe: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[str]:
    """Top-N liquid TRADEABLE perps by volume (no HIP-3 `:`, no `@` spot/index, no spot type)."""
    kf = cfg.get("kurtosis_factor") or {}
    floor = float(kf.get("min_volume_usd", cfg.get("min_market_volume_usd", 5_000_000)) or 0)
    topn = int(kf.get("universe_top_n", 50))
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
    kf = cfg.get("kurtosis_factor") or {}
    window = int(kf.get("window", 60))
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
    k = int(kf.get("k_per_tercile", 1))
    return rank_universe(cbc, bench, window, k, score_fn="kurtosis")


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


def _analysis(coin: str, side: str, kurtosis: float) -> Dict[str, Any]:
    """Synthetic analysis for the executor. external_alpha tag bypasses thought-engine entry gates
    while all safety gates still apply."""
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "LONG" if side == "long" else "SHORT", "side": side,
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": f"[kurtosis_factor] {side} (excess_kurtosis={kurtosis:.3f})",
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "external_alpha": "kurtosis_factor",
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def maybe_rebalance(config: Dict[str, Any], universe, positions,
                    fetch_candles: Callable, execute_fn: Callable, close_fn: Callable) -> Optional[Dict]:
    """Self-gating rebalance: fires at most once per hold_days. Returns plan or None.

    Validated V2: +1.71%/rebal (HIGH-kurtosis long-short, beta-neutral within-β-tercile).
    MODEST edge — bleeds in down-regime. enabled=False + shadow_mode=True are both required defaults.
    Guard: enabled=False → immediate no-op. Shadow: logs only, no orders.
    """
    kf = config.get("kurtosis_factor") or {}
    if not bool(kf.get("enabled", False)):
        return None                                            # master gate — no-op when disabled

    hold_days = float(kf.get("hold_days", 10))
    now = time.time()
    if now - _last_ts() < hold_days * 86400:
        return None                                            # not time to rebalance yet

    book = _build_target_book(universe, config, fetch_candles)
    if not book.longs or not book.shorts:
        logger.info("[kurtosis-factor] no target book (too few coins or no BTC bench) — skip")
        return None

    # ── Ownership-scoped current book ─────────────────────────────────────────
    owned = _get_owned()
    owned.prune(_live_coin_set(positions))
    cur_long, cur_short = owned.filter_to_owned(positions)

    plan = rebalance_plan(book, cur_long, cur_short)
    _save_ts(now)                                              # arm timer regardless of shadow/live

    shadow = bool(kf.get("shadow_mode", True))
    log_event({"event": "kurtosis_rebalance", "shadow": shadow,
               "longs": book.longs, "shorts": book.shorts,
               "open_long": plan["open_long"], "open_short": plan["open_short"],
               "close": plan["close_long"] + plan["close_short"],
               "tercile_assignments": book.tercile_assignments})
    logger.info(
        f"[kurtosis-factor]{' SHADOW' if shadow else ' LIVE'} rebalance — "
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
            logger.warning(f"[kurtosis-factor] close {coin} failed: {e}")
    for coin in plan["open_long"]:
        try:
            execute_fn(_analysis(coin, "long", book.scores.get(coin, 0.0)))
            owned.add(coin, "long")
        except Exception as e:
            logger.warning(f"[kurtosis-factor] open long {coin} failed: {e}")
    for coin in plan["open_short"]:
        try:
            execute_fn(_analysis(coin, "short", book.scores.get(coin, 0.0)))
            owned.add(coin, "short")
        except Exception as e:
            logger.warning(f"[kurtosis-factor] open short {coin} failed: {e}")
    owned.save()
    return plan
