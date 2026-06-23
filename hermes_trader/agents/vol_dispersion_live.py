"""Live wiring for the vol-dispersion rebalancer (SHADOW-first, always disabled by default).

Drives the pure engine (agents/vol_dispersion.py) on a hold-days timer: builds the beta-neutral
within-tercile TargetBook from cached daily candles, diffs vs the live book, then SHADOW-logs the
plan (NO orders placed) or LIVE-executes the diff.

Safety defaults:
- enabled = False in DEFAULT_CONFIG → loop hook is a NO-OP until operator explicitly flips it.
- shadow_mode = True → even when enabled, only logs; never places orders until shadow_mode=False.
- Timer persisted to .vol_dispersion_ts → restart-safe, won't re-fire the rebalance.

Wired as one self-gating call per loop cycle immediately after the xs_momentum rebalance hook.
Pattern mirrors xs_momentum_live.py exactly.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set

from hermes_trader.agents.vol_dispersion import (
    TargetBook, rank_universe, rebalance_plan, is_empty_plan,
)
from hermes_trader.agents.rebalancer_owned import OwnedPositions, _live_coin_set
from hermes_trader.agents.corr_gate import compute_corr_regime
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

_TS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        ".vol_dispersion_ts")
_OWNED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           ".vol_dispersion_positions.json")

_CORR_HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                   ".vd_corr_history")

# Module-level singleton — loaded lazily on first maybe_rebalance call.
_owned: Optional[OwnedPositions] = None


def _get_owned() -> OwnedPositions:
    global _owned
    if _owned is None:
        _owned = OwnedPositions(_OWNED_FILE)
    return _owned.load()


def _load_corr_history() -> List[float]:
    try:
        with open(_CORR_HISTORY_FILE) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return [float(x) for x in data]
    except Exception:
        pass
    return []


def _save_corr_history(history: List[float], max_len: int = 200) -> None:
    try:
        with open(_CORR_HISTORY_FILE, "w") as fh:
            json.dump(history[-max_len:], fh)
    except Exception:
        pass


# ── Timer helpers (mirrors xs_momentum_live) ──────────────────────────────────

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


# ── Universe filter (same eligibility rules as xs_momentum_live) ──────────────

def _eligible(universe: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[str]:
    """Top-N liquid TRADEABLE perps by volume (no HIP-3 `:`, no `@` spot/index, no spot type)."""
    vd = cfg.get("vol_dispersion") or {}
    floor = float(vd.get("min_volume_usd", cfg.get("min_market_volume_usd", 5_000_000)) or 0)
    topn = int(vd.get("universe_top_n", 50))
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


def _build_target_book(universe, cfg, fetch_candles):
    """Build the target TargetBook. Returns (book, cbc) where cbc is the candles dict;
    cbc is passed to the corr-gate so it can reuse already-fetched (cached) candles."""
    vd = cfg.get("vol_dispersion") or {}
    window = int(vd.get("idio_vol_window", 30))
    nbars = window + 10           # a little headroom beyond the window
    cbc = {}
    for coin in _eligible(universe, cfg):
        try:
            bars = fetch_candles(coin, "1d", nbars)
        except Exception:
            bars = None
        if bars and len(bars) >= window + 1:
            cbc[coin] = bars
    # BTC as benchmark for beta and residual computation
    try:
        bench = fetch_candles("BTC", "1d", nbars)
    except Exception:
        bench = None
    if not bench or len(bench) < window + 1:
        return TargetBook([], [], {}, {}), cbc
    k = int(vd.get("k_per_tercile", 3))
    return rank_universe(cbc, bench, window, k), cbc


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


def _analysis(coin: str, side: str, idio_vol: float) -> Dict[str, Any]:
    """Synthetic analysis for the executor. external_alpha tag bypasses thought-engine entry gates
    while all safety gates still apply. Mirrors xs_momentum_live._analysis."""
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "LONG" if side == "long" else "SHORT", "side": side,
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": f"[vol_dispersion] {side} (idio_vol={idio_vol:.4f})",
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "external_alpha": "vol_dispersion",
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def maybe_rebalance(config: Dict[str, Any], universe, positions,
                    fetch_candles: Callable, execute_fn: Callable, close_fn: Callable) -> Optional[Dict]:
    """Self-gating rebalance: fires at most once per hold_days. Returns plan or None.

    Guard: enabled=False in config → immediate no-op (loop hook is safe to call every cycle).
    Shadow: shadow_mode=True (default) → logs the target book, places NO orders.
    """
    vd = config.get("vol_dispersion") or {}
    if not bool(vd.get("enabled", False)):
        return None                                            # master gate — no-op when disabled

    hold_days = float(vd.get("hold_days", 10))
    now = time.time()
    if now - _last_ts() < hold_days * 86400:
        return None                                            # not time to rebalance yet

    book, cbc_for_corr = _build_target_book(universe, config, fetch_candles)
    if not book.longs or not book.shorts:
        logger.info("[vol-dispersion] no target book (too few coins or no BTC bench) — skip")
        return None

    # ── Correlation-regime gate (V3 — validated: vol-disp Sharpe 9.06→13.27 in high-corr).
    # When correlation_gate.enabled=True, scale vol-dispersion notional UP in high-corr regimes
    # (beta-neutralised → still finds dispersion even when coins correlate). Pure no-op while
    # enabled=False (scalar=1.0 → zero behavior change).
    cg_cfg = config.get("correlation_gate") or {}
    corr_scalar = 1.0
    if bool(cg_cfg.get("enabled", False)) and cbc_for_corr:
        corr_history = _load_corr_history()
        cg_state = compute_corr_regime(
            cbc_for_corr, corr_history,
            window=int(cg_cfg.get("window", 14)),
            cap=float(cg_cfg.get("cap", 1.5)),
            low_scalar=float(cg_cfg.get("low_corr_scalar", 1.2)),
            high_scalar=float(cg_cfg.get("high_corr_scalar", 1.2)),
        )
        if cg_state.avg_corr > 0:
            corr_history.append(cg_state.avg_corr)
            _save_corr_history(corr_history)
        corr_scalar = cg_state.vol_disp_scalar
        logger.debug(f"[vol-dispersion] corr_gate: avg_corr={cg_state.avg_corr:.3f} "
                     f"high={cg_state.corr_high} vol_disp_scalar={corr_scalar:.3f}")

    # ── Ownership-scoped current book ─────────────────────────────────────────
    owned = _get_owned()
    owned.prune(_live_coin_set(positions))
    cur_long, cur_short = owned.filter_to_owned(positions)

    plan = rebalance_plan(book, cur_long, cur_short)
    _save_ts(now)                                              # arm timer regardless of shadow/live

    shadow = bool(vd.get("shadow_mode", True))
    log_event({"event": "vd_rebalance", "shadow": shadow,
               "longs": book.longs, "shorts": book.shorts,
               "open_long": plan["open_long"], "open_short": plan["open_short"],
               "close": plan["close_long"] + plan["close_short"],
               "tercile_assignments": book.tercile_assignments,
               "corr_scalar": corr_scalar})
    logger.info(
        f"[vol-dispersion]{' SHADOW' if shadow else ' LIVE'} rebalance — "
        f"target {len(book.longs)}L/{len(book.shorts)}S; "
        f"open {len(plan['open_long'])}L+{len(plan['open_short'])}S, "
        f"close {len(plan['close_long']) + len(plan['close_short'])}"
        + (f"  [corr_scalar={corr_scalar:.2f}]" if corr_scalar != 1.0 else "")
    )

    if shadow or is_empty_plan(plan):
        return plan                                            # SHADOW: logged only, no orders

    # LIVE: close drops first (free capital), then open adds — both legs.
    # corr_scalar multiplies the per-trade notional (via external_alpha_notional in the analysis).
    # While correlation_gate.enabled=False corr_scalar=1.0 → no change in behaviour.
    for coin in plan["close_long"] + plan["close_short"]:
        try:
            close_fn(coin)
            owned.remove(coin)
        except Exception as e:
            logger.warning(f"[vol-dispersion] close {coin} failed: {e}")
    for coin in plan["open_long"]:
        try:
            a = _analysis(coin, "long", book.scores.get(coin, 0.0))
            if corr_scalar != 1.0 and a.get("external_alpha_notional", 0):
                a["external_alpha_notional"] = a["external_alpha_notional"] * corr_scalar
            execute_fn(a)
            owned.add(coin, "long")
        except Exception as e:
            logger.warning(f"[vol-dispersion] open long {coin} failed: {e}")
    for coin in plan["open_short"]:
        try:
            a = _analysis(coin, "short", book.scores.get(coin, 0.0))
            if corr_scalar != 1.0 and a.get("external_alpha_notional", 0):
                a["external_alpha_notional"] = a["external_alpha_notional"] * corr_scalar
            execute_fn(a)
            owned.add(coin, "short")
        except Exception as e:
            logger.warning(f"[vol-dispersion] open short {coin} failed: {e}")
    owned.save()
    return plan
