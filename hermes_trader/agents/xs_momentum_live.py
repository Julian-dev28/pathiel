"""Live wiring for the cross-sectional momentum rebalancer (SHADOW-first).

Drives the pure engine (agents/xs_momentum.py) on a hold-days timer: builds the target book from
cached daily candles, diffs vs the live book, then SHADOW-logs the plan (no orders) or LIVE-executes
the diff. Default shadow_mode=True — validate forward before risking capital. The rebalance timer is
persisted so a loop restart doesn't re-fire it.

Vol-managed sizing (Moreira-Muir, W6): when xs_momentum.vol_managed.enabled=true, each rebalance's
exposure is scaled by w_t = target_vol / realized_vol, clamped to [0.3, 2.0]. Realized vol is the
pstdev of the last N rebalance-period returns (persisted to .xs_volmgd_history). This is the
ONLY change: the TargetBook is unchanged; the scalar is logged and passed to the analysis block so
the executor can size accordingly. OFF by default — zero behavior change until enabled.

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
from hermes_trader.agents.rebalancer_owned import OwnedPositions, _live_coin_set
from hermes_trader.agents.corr_gate import compute_corr_regime
from hermes_trader.indicators.math import candle_val
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

_TS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        ".xs_rebalance_ts")
_OWNED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           ".xs_momentum_positions.json")

# Module-level singleton — loaded lazily on first maybe_rebalance call.
_owned: Optional[OwnedPositions] = None


def _get_owned() -> OwnedPositions:
    global _owned
    if _owned is None:
        _owned = OwnedPositions(_OWNED_FILE)
    return _owned.load()

# ── Vol-managed sizing state (W6: Moreira-Muir) ────────────────────────────────
_VOLMGD_HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".xs_volmgd_history",
)
_VOLMGD_WINDOW = 20   # default rolling window; matches edge_volmgd_amihud.py

# Corr-regime history file — persists rolling avg pairwise corr values across restarts.
_CORR_HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".xs_corr_history",
)


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


def _load_volmgd_history() -> List[float]:
    """Load persisted rebalance-return history (list of floats, most recent last)."""
    try:
        with open(_VOLMGD_HISTORY_FILE) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return [float(x) for x in data]
    except Exception:
        pass
    return []


def _save_volmgd_history(history: List[float], max_len: int = 200) -> None:
    """Persist the history, trimmed to max_len so the file stays tiny."""
    try:
        with open(_VOLMGD_HISTORY_FILE, "w") as fh:
            json.dump(history[-max_len:], fh)
    except Exception:
        pass


def compute_vol_scalar(history: List[float], target_vol: float, cap: float,
                       window: int = _VOLMGD_WINDOW) -> float:
    """Return the Moreira-Muir exposure scalar w_t = target_vol / realized_vol, clamped [0.3, cap].

    realized_vol = pstdev of last `window` rebalance-period returns.
    Falls back to 1.0 (no scaling) if history is too short or vol is zero.
    Lookahead-safe: uses only past returns (caller passes history WITHOUT the current rebal return).
    """
    if len(history) < max(window, 5):
        return 1.0    # insufficient history → no scaling; neutral weight
    rv = statistics.pstdev(history[-window:])
    if rv <= 0:
        return 1.0
    w = target_vol / rv
    return max(0.3, min(cap, w))


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
    """Build the target TargetBook. Returns (book, cbc) where cbc is the candles dict;
    cbc is passed to the corr-gate so it can reuse already-fetched (cached) candles."""
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
    return rank_universe(cbc, lb, k, bench_bars=bench, beta_window=beta_window), cbc


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


def _analysis(coin: str, side: str, rank_score: float, vol_scalar: float = 1.0) -> Dict[str, Any]:
    """Synthetic analysis for the executor. external_alpha bypasses the thought-engine entry gates
    (runner/trend) — this is a separate validated edge — while every SAFETY gate still applies.
    vol_scalar (Moreira-Muir W6) is stored in the analysis so the executor can note the scaling
    intent; actual position sizing is done by the executor via the standard notional path."""
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "LONG" if side == "long" else "SHORT", "side": side,
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (f"[xs_momentum] {side} (trailing {rank_score*100:+.1f}%)"
                      + (f" [vol_scalar={vol_scalar:.2f}]" if vol_scalar != 1.0 else "")),
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "external_alpha": "xs_momentum",
        "xs_vol_scalar": vol_scalar,   # downstream hooks may read this for sizing
    }


def maybe_rebalance(config: Dict[str, Any], universe, positions,
                    fetch_candles: Callable, execute_fn: Callable, close_fn: Callable,
                    _last_rebal_return: Optional[float] = None) -> Optional[Dict]:
    """Self-gating rebalance: fires at most once per hold-days. Returns the plan (or None if not
    time / disabled / empty). SHADOW logs only; LIVE executes the diff (close drops, open adds).

    _last_rebal_return: if provided by the caller, appended to the vol-managed history before
    computing the scalar for this rebalance (correct Moreira-Muir: update history with t-1 return,
    then compute w_t). The trading_loop may pass this once it tracks realized PnL; until then it
    remains None and the history accumulates via internal state only.
    """
    xs = config.get("xs_momentum") or {}
    if not bool(xs.get("enabled", False)):
        return None
    hold_days = float(xs.get("hold_days", 10))
    now = time.time()
    if now - _last_ts() < hold_days * 86400:
        return None                                            # not time to rebalance yet

    # ── Vol-managed scalar (W6, Moreira-Muir) ─────────────────────────────────
    # Load persisted rebalance-return history, optionally append the caller's last-period return,
    # compute the exposure scalar, then save. When vol_managed.enabled=false → scalar=1.0 (no-op).
    vmcfg = xs.get("vol_managed") or {}
    vm_enabled = bool(vmcfg.get("enabled", False))
    vol_scalar = 1.0
    volmgd_history = _load_volmgd_history()
    if _last_rebal_return is not None:
        volmgd_history.append(float(_last_rebal_return))
    if vm_enabled:
        target_vol = float(vmcfg.get("target_vol", 0.02))
        vm_cap = float(vmcfg.get("cap", 2.0))
        vol_scalar = compute_vol_scalar(volmgd_history, target_vol, vm_cap)
        logger.debug(f"[xs-momentum] vol_managed: history_len={len(volmgd_history)} "
                     f"scalar={vol_scalar:.3f} target_vol={target_vol}")
    # Always persist history so it grows even when vol_managed is off (ready to enable later)
    _save_volmgd_history(volmgd_history)

    # VOL-REGIME GATE: the momentum edge concentrates in LOW BTC-vol (audit/edge_sweep3). In a
    # HIGH-vol regime, go FLAT (empty target → close everything) to sit out the dead/choppy periods.
    regime = "low"
    cbc_for_corr: Dict[str, Any] = {}
    if bool(xs.get("vol_gate", True)):
        regime = _btc_vol_regime(fetch_candles, int(xs.get("vol_short", 14)), int(xs.get("vol_long", 90)))
    if regime == "high":
        book = TargetBook([], [], {})
    else:
        book, cbc_for_corr = _target_book(universe, config, fetch_candles)
        if not book.longs or not book.shorts:
            logger.info("[xs-momentum] no target book (too few coins) — skip rebalance")
            return None

    # ── Correlation-regime gate (V3 — validated: momentum Sharpe 4.95→8.36 in low-corr).
    # When correlation_gate.enabled=True, scale momentum notional UP in low-corr regimes (more
    # cross-sectional dispersion → momentum pays more). Pure no-op while enabled=False (scalar=1.0).
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
        corr_scalar = cg_state.momentum_scalar
        logger.debug(f"[xs-momentum] corr_gate: avg_corr={cg_state.avg_corr:.3f} "
                     f"high={cg_state.corr_high} momentum_scalar={corr_scalar:.3f}")

    # ── Ownership-scoped current book ─────────────────────────────────────────
    # cur_long/cur_short ONLY contain coins this rebalancer opened (intersected with
    # live positions). This guarantees close_long/close_short never contain foreign
    # positions opened by the thought-engine or other rebalancers.
    owned = _get_owned()
    owned.prune(_live_coin_set(positions))
    cur_long, cur_short = owned.filter_to_owned(positions)

    plan = rebalance_plan(book, cur_long, cur_short)
    _save_ts(now)                                              # arm the timer regardless of shadow/live

    shadow = bool(xs.get("shadow_mode", True))
    log_event({"event": "xs_rebalance", "shadow": shadow, "regime": regime,
               "longs": book.longs, "shorts": book.shorts,
               "open_long": plan["open_long"], "open_short": plan["open_short"],
               "close": plan["close_long"] + plan["close_short"],
               "vol_scalar": vol_scalar, "vol_managed": vm_enabled,
               "corr_scalar": corr_scalar})
    logger.info(f"[xs-momentum]{' SHADOW' if shadow else ' LIVE'} rebalance [{regime}-vol] — "
                f"target {len(book.longs)}L/{len(book.shorts)}S; "
                f"open {len(plan['open_long'])}L+{len(plan['open_short'])}S, "
                f"close {len(plan['close_long']) + len(plan['close_short'])}"
                + (f"  [vol_scalar={vol_scalar:.2f}]" if vm_enabled else "")
                + (f"  [corr_scalar={corr_scalar:.2f}]" if corr_scalar != 1.0 else "")
                + ("  (flat: high-vol regime)" if regime == "high" else ""))

    if shadow or is_empty_plan(plan):
        return plan                                            # SHADOW: logged the target book, no orders

    # LIVE: close drops first (free capital), then open adds — both legs.
    # corr_scalar multiplies the per-trade notional (via external_alpha_notional in the analysis).
    # While correlation_gate.enabled=False corr_scalar=1.0 → no change in behaviour.
    for coin in plan["close_long"] + plan["close_short"]:
        try:
            close_fn(coin)
            owned.remove(coin)
        except Exception as e:
            logger.warning(f"[xs-momentum] close {coin} failed: {e}")
    for coin in plan["open_long"]:
        try:
            a = _analysis(coin, "long", book.scores.get(coin, 0.0), vol_scalar)
            if corr_scalar != 1.0 and a.get("external_alpha_notional", 0):
                a["external_alpha_notional"] = a["external_alpha_notional"] * corr_scalar
            execute_fn(a)
            owned.add(coin, "long")
        except Exception as e:
            logger.warning(f"[xs-momentum] open long {coin} failed: {e}")
    for coin in plan["open_short"]:
        try:
            a = _analysis(coin, "short", book.scores.get(coin, 0.0), vol_scalar)
            if corr_scalar != 1.0 and a.get("external_alpha_notional", 0):
                a["external_alpha_notional"] = a["external_alpha_notional"] * corr_scalar
            execute_fn(a)
            owned.add(coin, "short")
        except Exception as e:
            logger.warning(f"[xs-momentum] open short {coin} failed: {e}")
    owned.save()
    return plan
