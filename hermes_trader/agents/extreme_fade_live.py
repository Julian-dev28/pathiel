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
import statistics
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.extreme_fade import FadeSignal, compute_signals, log_signals
from hermes_trader.agents.rebalancer_owned import state_file
from hermes_trader.session_log import append as log_event

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


def _market_skew(cbc: Dict[str, List[Any]], window: int = 20,
                 min_coins: int = 10) -> Optional[float]:
    """Trailing-`window` skewness of the EQUAL-WEIGHT market daily return, known at
    the entry bar (W-B2 spec, findings/W-B2.md: W=20, hard-zero threshold). Built
    from the same completed daily bars the fade signal already fetched. Returns
    None when history is too thin to trust (young dataset / degraded fetch)."""
    # per-day equal-weight return: group per-coin daily returns by bar timestamp
    by_t: Dict[int, List[float]] = {}
    for bars in cbc.values():
        for i in range(1, len(bars)):
            t = _bar_t(bars[i])
            try:
                prev_c = float(bars[i - 1].get("c") if isinstance(bars[i - 1], dict) else bars[i - 1].c)
                cur_c = float(bars[i].get("c") if isinstance(bars[i], dict) else bars[i].c)
            except Exception:
                continue
            if t and prev_c > 0:
                by_t.setdefault(t, []).append(cur_c / prev_c - 1.0)
    days = sorted(t for t, rs in by_t.items() if len(rs) >= min_coins)[-window:]
    if len(days) < max(5, window // 2):
        return None
    mkt = [statistics.mean(by_t[t]) for t in days]
    m = statistics.mean(mkt)
    sd = statistics.pstdev(mkt)
    if sd == 0:
        return 0.0
    return statistics.mean([(r - m) ** 3 for r in mkt]) / (sd ** 3)


def _fade_analysis(sig: FadeSignal, ef: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Synthetic analysis for the executor. ``strategy_book`` bypasses the thought-engine ENTRY
    gates (a counter-trend fade can't clear runner/trend/counter-trend gates by design) while every
    SAFETY gate still applies.

    Carries the VALIDATED exit structure (W-B2 base, findings/W-B2.md: 20% stop,
    3d horizon, stop-or-horizon-close, NO trail). Before 2026-07-09 this book
    inherited the main engine's 15x + 1-2.5% ATR tight stop, which clipped the
    post-crash wobble the fade must sit through — a validated +4.5%/trade edge
    realized -$3.57 live (the rally_exhaustion stop-inversion class)."""
    ef = ef or {}
    stop_pct = float(ef.get("stop_pct", 20.0))
    leverage = max(1, int(ef.get("leverage", 1)))
    hold_days = float(ef.get("hold_days", 3.0))
    out = {
        "id": str(uuid.uuid4()), "coin": sig.coin,
        "verdict": "LONG", "side": "long",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": f"[extreme_fade] long-after-crash {sig.prior_daily_ret * 100:+.1f}%",
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": "extreme_fade",
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": float(ef.get("tp_scale_fraction", 0.0)),
        "dsl_exit_override": {
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            # protect_pct 9999 never arms the trail: exit is stop-or-horizon-close,
            # exactly the structure the edge was validated with (no trail clipping).
            "protect_pct": float(ef.get("protect_pct", 9999.0)),
            "retrace_threshold": float(ef.get("retrace_threshold", 0.5)),
            "hard_timeout_minutes": hold_days * 1440.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
        },
    }
    frac = float(ef.get("equity_fraction", 0.0) or 0.0)
    # Deep-crash satellite tier (alpha rescrape 2026-07-09): crashes <= deep
    # crash_pct (default -20%) validated at higher EV on two independent
    # datasets (extreme_surface + the SETTLE-2 cross-check) — same structure,
    # bigger size.
    deep = ef.get("deep_tier") or {}
    if deep and sig.prior_daily_ret <= float(deep.get("crash_pct", -0.20)):
        frac = float(deep.get("equity_fraction", frac) or frac)
        out["reasoning"] += " [deep-tier]"
    if frac > 0:
        out["strategy_book_equity_frac_override"] = frac
    return out


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
    # Fetch depth covers the W-B2 skew window (findings/W-B2.md) when the arm is on;
    # same call count as before, just deeper bars.
    skew_cfg = ef.get("skew_arm") or {}
    skew_enabled = bool(skew_cfg.get("enabled", True))
    skew_window = int(skew_cfg.get("window", 20))
    n_bars = max(6, skew_window + 8) if skew_enabled else 6
    cbc: Dict[str, List[Any]] = {}
    crash_bar_t: Dict[str, int] = {}
    for m in (universe or []):
        coin = m.get("coin") or ""
        if not coin or coin.startswith("@") or ":" in coin or m.get("type") == "spot":
            continue
        try:
            bars = fetch_candles(coin, "1d", n_bars)
        except Exception:
            bars = None
        bars = _completed_bars(bars, now_ms)
        if bars and len(bars) >= 2:
            cbc[coin] = bars
            crash_bar_t[coin] = _bar_t(bars[-1])

    # W-B2 skew-arm overlay: armed (market 20d skew < 0) fades earned +7.29%/trade,
    # disarmed -2.42% (MC p=0.020). SHADOW by default: tag + record, only skip
    # entries when skew_arm.enforce=true (flip after >=15 forward events confirm).
    skew = _market_skew(cbc, skew_window) if skew_enabled else None
    armed = (skew is None) or (skew < float(skew_cfg.get("threshold", 0.0)))
    enforce = bool(skew_cfg.get("enforce", False))

    signals = compute_signals(cbc, config)
    log_signals(signals, config)

    # Record every fade signal to the unified shadow ledger (validated exit shape:
    # stop_pct/hold_days) so the arm split and the book itself grade forward.
    shadow_ledger.record_many("extreme_fade", [{
        "coin": s.coin, "side": "long",
        "signal_bar_t": crash_bar_t.get(s.coin, 0),
        "entry_ref_px": _last_close(cbc.get(s.coin)),
        "horizon_days": float(ef.get("hold_days", 3.0)),
        "stop_pct": float(ef.get("stop_pct", 20.0)),
        "ts": now_ms,
        "meta": {"prior_ret_pct": round(s.prior_daily_ret * 100, 2),
                 "skew": round(skew, 4) if skew is not None else None,
                 "armed": armed, "enforced": enforce, "shadow": False},
    } for s in signals])

    # SHADOW: record + grade the fade signals but open nothing live. Set when the forward
    # read goes EV- (fade-longs catching knives in a down regime) — the ledger keeps grading
    # so the book auto-re-promotes when the edge returns. Reversible: flip shadow_only back off.
    if bool(ef.get("shadow_only", False)):
        if signals:
            logger.info(f"[extreme-fade] SHADOW_ONLY — {len(signals)} signal(s) recorded, no live entries")
        return {"signals": len(signals), "opened": 0, "skew": skew, "armed": armed, "shadow_only": True}

    if not signals:
        return {"signals": 0, "opened": 0, "skew": skew, "armed": armed}

    # LIVE: open the fade leg — skip held coins (no stacking), dedup per crash-bar (restart-safe),
    # only within the entry window (≈ next-bar open, so a stale restart can't chase a bounce), capped.
    held = _held_coins(positions)
    faded = _load_faded()
    max_new = int(ef.get("max_new_per_cycle", 2))
    opened = 0
    # Concurrent-position cap (2026-07-24). max_new_per_cycle only bounds opens
    # per 30-min cycle; with a 3-day hold a sustained crash regime can stack far
    # more legs than the account can margin. That was survivable at
    # equity_fraction 0.05 (20 legs = 100% margin) but not at 0.15, where only 6
    # legs fit — and the sizing backtest that justified 0.15 assumed exactly
    # this cap (int(1/f) legs). Without it the live book takes a risk the
    # measurement never covered. `faded` keys are the coins THIS book opened, so
    # intersecting with live holdings counts its own positions, not the account's.
    max_book = int(ef.get("max_book_positions", 0) or 0)
    book_open = len(set(faded) & held)
    if max_book > 0 and book_open >= max_book:
        logger.info(f"[extreme-fade] max_book_positions reached "
                    f"({book_open}/{max_book}) — {len(signals)} signal(s) "
                    f"recorded, no entries")
        return {"signals": len(signals), "opened": 0, "skew": skew,
                "armed": armed, "book_full": True}
    if enforce and not armed:
        logger.info(f"[extreme-fade] DISARMED (market 20d skew {skew:+.3f} >= 0, enforce=on) — "
                    f"{len(signals)} signal(s) recorded, no entries")
        return {"signals": len(signals), "opened": 0, "skew": skew, "armed": False}
    for s in signals:
        if opened >= max_new:
            logger.info(f"[extreme-fade] max_new_per_cycle reached ({max_new}) — remaining signals skipped")
            break
        if max_book > 0 and book_open + opened >= max_book:
            logger.info(f"[extreme-fade] max_book_positions reached "
                        f"({book_open + opened}/{max_book}) — remaining signals skipped")
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
            result = execute_fn(_fade_analysis(s, ef))
            if _execute_opened(result):
                faded[s.coin] = bt
                opened += 1
                log_event({"event": "book_open", "book": "extreme_fade", "coin": s.coin,
                           "side": s.side, "sig_t": bt,
                           "skew": round(skew, 4) if skew is not None else None,
                           "armed": armed})
                logger.info(f"[extreme-fade] LIVE opened {s.side} {s.coin} "
                            f"(prior {s.prior_daily_ret * 100:+.1f}%, "
                            f"skew {'n/a' if skew is None else f'{skew:+.3f}'} armed={armed})")
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
    return {"signals": len(signals), "opened": opened, "skew": skew, "armed": armed}


def _last_close(bars) -> float:
    try:
        last = bars[-1]
        return round(float(last.get("c") if isinstance(last, dict) else last.c), 8)
    except Exception:
        return 0.0
