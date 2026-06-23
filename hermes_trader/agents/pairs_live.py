"""Live wiring for the pairs stat-arb engine (SHADOW-first, disabled by default).

Drives the pure engine (agents/pairs_engine.py) on a timer-gated cycle: builds co-moving pair
signals from cached daily candles, then SHADOW-logs the open/close plan (NO orders) or LIVE-
executes the diff.

Validated: +1.08%/trade (V4: entry_z=2.5, exit_z=0.5, corr>0.6, window=30).
Market-neutral, ORTHOGONAL to momentum → stacks cleanly with xs_momentum and vol_dispersion.

Safety defaults:
- enabled = False in DEFAULT_CONFIG → loop hook is a NO-OP until operator explicitly flips it.
- shadow_mode = True → even when enabled, only logs; never places orders until shadow_mode=False.
- State persisted to .pairs_state.json → tracks open pairs across restarts (avoids re-opening
  already-open pairs on startup).

Wired as one self-gating call per loop cycle after the factor-rebalancer hooks.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents.pairs_engine import (
    PairTrade, compute_signals, pair_correlation, spread_zscore,
)
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           ".pairs_state.json")
_TS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        ".pairs_ts")


# ── Persistence helpers ───────────────────────────────────────────────────────

def _load_open_pairs() -> List[PairTrade]:
    """Load open pairs from disk (restart-safe)."""
    try:
        with open(_STATE_FILE) as fh:
            data = json.load(fh)
        pairs = []
        for d in data:
            pairs.append(PairTrade(
                coin_a=d["coin_a"], coin_b=d["coin_b"], side=d["side"],
                z_entry=d["z_entry"], spread_mean=d["spread_mean"], spread_std=d["spread_std"],
            ))
        return pairs
    except Exception:
        return []


def _save_open_pairs(pairs: List[PairTrade]) -> None:
    try:
        with open(_STATE_FILE, "w") as fh:
            json.dump([{
                "coin_a": p.coin_a, "coin_b": p.coin_b, "side": p.side,
                "z_entry": p.z_entry, "spread_mean": p.spread_mean, "spread_std": p.spread_std,
            } for p in pairs], fh)
    except Exception:
        pass


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
    pc = cfg.get("pairs_statarb") or {}
    floor = float(pc.get("min_volume_usd", cfg.get("min_market_volume_usd", 5_000_000)) or 0)
    topn = int(pc.get("universe_top_n", 40))
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


def _analysis(coin: str, side: str, z: float, pair_label: str) -> Dict[str, Any]:
    """Synthetic analysis for the executor. external_alpha tag bypasses thought-engine entry gates
    while all safety gates still apply. Mirrors vol_dispersion_live._analysis."""
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "LONG" if side == "long" else "SHORT", "side": side,
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": f"[pairs_statarb] {side} pair={pair_label} z={z:.2f}",
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "external_alpha": "pairs_statarb",
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def maybe_run(config: Dict[str, Any], universe, positions,
              fetch_candles: Callable, execute_fn: Callable, close_fn: Callable) -> Optional[Dict]:
    """Self-gating pairs scan: fires at most once per scan_interval_hours. Returns summary or None.

    Guard: enabled=False in config → immediate no-op (loop hook is safe to call every cycle).
    Shadow: shadow_mode=True (default) → logs the plan, places NO orders.
    """
    pc = config.get("pairs_statarb") or {}
    if not bool(pc.get("enabled", False)):
        return None                                            # master gate — no-op when disabled

    scan_interval_hours = float(pc.get("scan_interval_hours", 6))
    now = time.time()
    if now - _last_ts() < scan_interval_hours * 3600:
        return None                                            # not time to scan yet

    entry_z = float(pc.get("entry_z", 2.5))
    exit_z = float(pc.get("exit_z", 0.5))
    min_corr = float(pc.get("min_corr", 0.6))
    window = int(pc.get("window", 30))
    nbars = window + 15

    # Fetch candles for eligible universe
    elig_coins = _eligible(universe, config)
    cbc: Dict[str, List[Any]] = {}
    for coin in elig_coins:
        try:
            bars = fetch_candles(coin, "1d", nbars)
        except Exception:
            bars = None
        if bars and len(bars) >= window + 2:
            cbc[coin] = bars

    if len(cbc) < 4:
        logger.info("[pairs-statarb] too few coins with candle history — skip")
        return None

    open_pairs = _load_open_pairs()
    max_open_pairs = int(pc.get("max_open_pairs", 4))

    to_open, to_close = compute_signals(
        cbc, entry_z=entry_z, exit_z=exit_z,
        min_corr=min_corr, window=window, open_pairs=open_pairs,
    )
    _save_ts(now)

    shadow = bool(pc.get("shadow_mode", True))

    # Build the updated open_pairs list (close first, then open)
    remaining = [p for p in open_pairs if p not in to_close]
    # Enforce max_open_pairs cap: only admit as many new pairs as the cap allows.
    slots_available = max(0, max_open_pairs - len(remaining))
    if len(to_open) > slots_available:
        logger.info(
            f"[pairs-statarb] max_open_pairs={max_open_pairs} cap: "
            f"{len(to_open)} signals → keeping top {slots_available} by |z|"
        )
        to_open = sorted(to_open, key=lambda p: abs(p.z_entry), reverse=True)[:slots_available]
    new_open = remaining + to_open

    log_event({
        "event": "pairs_scan", "shadow": shadow,
        "open_signals": len(to_open),
        "close_signals": len(to_close),
        "open_pairs_after": len(new_open),
        "new_pairs": [{"long": p.long_coin, "short": p.short_coin, "z": round(p.z_entry, 2)}
                      for p in to_open],
        "closed_pairs": [{"long": p.long_coin, "short": p.short_coin} for p in to_close],
    })
    logger.info(
        f"[pairs-statarb]{' SHADOW' if shadow else ' LIVE'} scan — "
        f"{len(cbc)} coins | {len(to_open)} new | {len(to_close)} close | {len(new_open)} held"
    )

    if shadow:
        return {"to_open": len(to_open), "to_close": len(to_close)}   # SHADOW: logged, no orders

    # LIVE: close reverting pairs first (free capital)
    for pt in to_close:
        try:
            close_fn(pt.long_coin)
        except Exception as e:
            logger.warning(f"[pairs-statarb] close long {pt.long_coin} failed: {e}")
        try:
            close_fn(pt.short_coin)
        except Exception as e:
            logger.warning(f"[pairs-statarb] close short {pt.short_coin} failed: {e}")

    # LIVE: open new divergent pairs
    for pt in to_open:
        label = f"{pt.coin_a}/{pt.coin_b}"
        try:
            execute_fn(_analysis(pt.long_coin, "long", pt.z_entry, label))
        except Exception as e:
            logger.warning(f"[pairs-statarb] open long {pt.long_coin} failed: {e}")
        try:
            execute_fn(_analysis(pt.short_coin, "short", pt.z_entry, label))
        except Exception as e:
            logger.warning(f"[pairs-statarb] open short {pt.short_coin} failed: {e}")

    _save_open_pairs(new_open)
    return {"to_open": len(to_open), "to_close": len(to_close)}
