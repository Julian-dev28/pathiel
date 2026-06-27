"""Live wiring for the extreme-fade edge (validated LONG-only @ -12% = +4.71%/trade; SETTLE-2,
2026-06-24, both OOS halves +4.57/+4.85, 66% win, 21/24 coins positive).

The pure engine (``extreme_fade.compute_signals``) only FLAGS candidates — it places no orders.
This module turns a candidate into a real order, with the entry timing aligned to the backtest.

Two bugs this module fixes (both surfaced going live 2026-06-24):
  1. EXECUTION: the loop computed + logged fade signals and stopped — the edge never traded,
     once enabled.
  2. ENTRY TIMING: the signal read the still-FORMING daily bar (today-so-far). The backtest faded
     a COMPLETED daily crash and entered at the next bar's open. We therefore (a) drop the forming
     bar so the signal is the last *completed* daily return, (b) only enter within
     ``entry_window_hours`` of that bar's close (≈ next-bar open) so a mid-day restart can't chase
     a crash that already bounced, and (c) dedup per (coin, crash-bar) so the persistent intraday
     signal opens the fade exactly once.

Counter-trend by construction (long a coin that just crashed >= 12%): it can NEVER clear the
runner/trend ENTRY gates, so fade orders carry the ``strategy_book`` tag. That bypasses the thought-engine ENTRY gates while
every SAFETY gate still applies: margin floor, kill-switch, per-position DSL stop, backup-SL,
TP scale-out, reentry cap, and sizing (strategy_book_notional_usd).

Safety:
- enabled=False    → no-op (loop hook is safe to call every cycle).
- skips coins already held (no stacking) + dedup per crash-bar (persisted, restart-safe).
- max_new_per_cycle caps opens so a multi-coin crash day can't pile onto a tiny account.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents.extreme_fade import FadeSignal, compute_signals, log_signals
from hermes_trader.agents.rebalancer_owned import state_file

logger = logging.getLogger(__name__)

_STATE_FILE = state_file(".extreme_fade_state.json")
_TS_FILE = state_file(".extreme_fade_live_ts")
_DAY_MS = 86_400_000


# ── crash-bar dedup state (coin -> crash bar start-ts already faded) ────────────

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

def _load_faded() -> Dict[str, int]:
    try:
        with open(_STATE_FILE) as fh:
            d = json.load(fh)
        return {str(k): int(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_faded(faded: Dict[str, int]) -> None:
    try:
        with open(_STATE_FILE, "w") as fh:
            json.dump(faded, fh)
    except Exception:
        pass


def _bar_t(bar) -> int:
    try:
        return int(bar.get("t") if isinstance(bar, dict) else getattr(bar, "t", 0))
    except Exception:
        return 0


def _completed_bars(bars, now_ms: int):
    """Drop the still-forming current daily bar so the signal uses COMPLETED bars only (the backtest
    faded completed-daily crashes). A daily bar is 'forming' iff it STARTED < 24h ago — a just-closed
    bar started exactly 24h ago and is kept; a partial today-bar started < 24h ago and is dropped."""
    if not bars:
        return bars
    last_t = _bar_t(bars[-1])
    if last_t and (now_ms - last_t) < _DAY_MS:
        return list(bars)[:-1]
    return list(bars)


def _fade_analysis(sig: FadeSignal) -> Dict[str, Any]:
    """Synthetic analysis for the executor. ``strategy_book`` bypasses the thought-engine ENTRY
    gates (a counter-trend fade can't clear runner/trend/counter-trend gates by design) while every
    SAFETY gate still applies. Sized via strategy_book_notional_usd, like other strategy books."""
    return {
        "id": str(uuid.uuid4()), "coin": sig.coin,
        "verdict": "LONG", "side": "long",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": f"[extreme_fade] long-after-crash {sig.prior_daily_ret * 100:+.1f}%",
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": "extreme_fade",
    }


def _held_coins(positions) -> set:
    """Coins with a live non-zero position (handles both the {'position': {...}} and flat shapes)."""
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
    return held


def _execute_opened(result: Any) -> bool:
    """True only when the executor actually opened exchange risk.

    maybe_execute returns {"executed": false, ...} for gate/order/margin blocks.
    Tests and simple spies may return None after accepting the analysis; keep
    that legacy test shape as success.
    """
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


def maybe_run(config: Dict[str, Any], universe, positions,
              fetch_candles: Callable, execute_fn: Callable,
              close_fn: Optional[Callable] = None) -> Optional[Dict]:
    """Self-gating extreme-fade cycle. Returns a small summary dict, or None when disabled.

    enabled=False → no-op. Enabled books open the validated fade leg via execute_fn, using
    COMPLETED bars, the freshness window, held-coin + crash-bar dedup, and the per-cycle cap.
    """
    ef = config.get("extreme_fade") or {}
    if not bool(ef.get("enabled", False)):
        return None

    now = time.time()
    interval_min = float(ef.get("scan_interval_min", ef.get("scan_interval_minutes", 0.0)) or 0.0)
    if interval_min > 0 and now - _last_ts() < interval_min * 60:
        return None
    _save_ts(now)

    now_ms = int(now * 1000)
    entry_window_ms = float(ef.get("entry_window_hours", 6.0)) * 3_600_000

    # Build candles_by_coin from the live universe, using COMPLETED daily bars only.
    cbc: Dict[str, List[Any]] = {}
    crash_bar_t: Dict[str, int] = {}
    for m in (universe or []):
        coin = m.get("coin") or ""
        if not coin or coin.startswith("@") or ":" in coin or m.get("type") == "spot":
            continue
        try:
            bars = fetch_candles(coin, "1d", 6)
        except Exception:
            bars = None
        bars = _completed_bars(bars, now_ms)
        if bars and len(bars) >= 2:
            cbc[coin] = bars
            crash_bar_t[coin] = _bar_t(bars[-1])

    signals = compute_signals(cbc, config)
    log_signals(signals, config)

    if not signals:
        return {"signals": 0, "opened": 0}

    # LIVE: open the fade leg — skip held coins (no stacking), dedup per crash-bar (restart-safe),
    # only within the entry window (≈ next-bar open, so a stale restart can't chase a bounce), capped.
    held = _held_coins(positions)
    faded = _load_faded()
    max_new = int(ef.get("max_new_per_cycle", 2))
    opened = 0
    for s in signals:
        if opened >= max_new:
            logger.info(f"[extreme-fade] max_new_per_cycle reached ({max_new}) — remaining signals skipped")
            break
        if s.coin in held:
            logger.info(f"[extreme-fade] skip {s.coin}: already held")
            continue                               # already have a position — don't stack
        bt = crash_bar_t.get(s.coin, 0)
        if bt and faded.get(s.coin) == bt:
            logger.info(f"[extreme-fade] skip {s.coin}: crash bar already faded")
            continue                               # already faded THIS crash bar
        if bt:
            bar_close = bt + _DAY_MS               # the crash bar closed here ≈ our intended open
            age_ms = now_ms - bar_close
            if age_ms > entry_window_ms:
                logger.info(
                    f"[extreme-fade] skip {s.coin}: stale entry window "
                    f"({age_ms / 3_600_000:.1f}h > {entry_window_ms / 3_600_000:.1f}h)"
                )
                continue                           # stale: past the entry window → don't chase
        try:
            result = execute_fn(_fade_analysis(s))
            if _execute_opened(result):
                faded[s.coin] = bt
                opened += 1
                logger.info(f"[extreme-fade] LIVE opened {s.side} {s.coin} "
                            f"(prior {s.prior_daily_ret * 100:+.1f}%)")
            else:
                reason = _execute_block_detail(result)
                logger.warning(
                    f"[extreme-fade] open {s.coin} not recorded — executor did not open"
                    + (f": {reason}" if reason else "")
                )
        except Exception as e:
            logger.warning(f"[extreme-fade] open {s.coin} failed: {e}")

    if opened:
        _save_faded(faded)
    return {"signals": len(signals), "opened": opened}
