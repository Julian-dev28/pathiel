"""unlock_short_runin — pre-unlock supply-drift short (W-U1, LIVE by operator order 2026-07-11).

W-U1 (915 unlock events >=1% circ, 90 HL coins, research/alpha_swarm/hypotheses/
W-U1_results.json): price falls -2.1% close-to-close over the 3 days INTO a
scheduled unlock (n=408, win-for-short 60.5%) and the drop is DONE by the event
(post-event drift ~0). So this book shorts INSIDE the run-in window
(48-72h before the unlock) and exits AT the unlock time — never holds through it.

The registered T-1d cell was NOT validated (p=0.10, sign flip across halves) and
stays recorder-only in unlock_recorder.py. That module also keeps recording this
arm's shadow rows independently, so grading never stops while capital is on.

Calendar: reads the upcoming-events state maintained by unlock_recorder.py
(same DefiLlama open-datasets source, refreshed there every 12h). This module
never fetches the 21MB index itself.

Sizing: fixed $20 notional, 1x, 15% backup stop. Operator-ordered live flip
WITHOUT forward validation — hence the tighter kill: set shadow_only=true (or
enabled=false) if forward EV25 < 0 after 10 episodes. Fees note: ~2.5d hold
clears the >=6h fee-viability bar easily.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents.book_helpers import bounded_exit_override
from hermes_trader.agents.book_helpers import execute_block_detail as _execute_block_detail
from hermes_trader.agents.book_helpers import execute_opened as _execute_opened
from hermes_trader.agents.book_helpers import load_seen, save_seen
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file
from hermes_trader.agents.rebalancer_owned import held_coins_with_dsl as _held_coins
from hermes_trader.agents import unlock_recorder
from hermes_trader.models.types import BookAnalysis

logger = logging.getLogger(__name__)

_BOOK_NAME = "unlock_short_runin"
_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000
_SEEN_FILE = state_file(".unlock_short_live_seen.json")

from hermes_trader.session_log import append as log_event


def _load_seen() -> Dict[str, int]:
    return load_seen(_SEEN_FILE)


def _save_seen(seen: Dict[str, int]) -> None:
    save_seen(_SEEN_FILE, seen)


def _analysis(coin: str, ev: Dict[str, Any], hours_to_unlock: float,
              cfg: Dict[str, Any]) -> BookAnalysis:
    stop_pct = float(cfg.get("stop_pct", 15.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "SHORT", "side": "short",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (f"[{_BOOK_NAME}] scheduled unlock of "
                      f"{float(ev.get('pct') or 0):.1f}% circ in {hours_to_unlock:.0f}h — "
                      "W-U1 run-in drift short, exits at the event"),
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": _BOOK_NAME,
        "strategy_book_notional": float(cfg.get("notional_usd", 20.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "min_short_volume_usd_override": float(cfg.get("min_volume_usd", 5_000_000.0)),
        # Backtested shape: ride the run-in, exit AT the unlock, 15% stop,
        # no trail (protect disabled via huge threshold). Timeout is set to
        # the unlock event itself (hours_to_unlock), NOT a fixed hold-days
        # horizon like the other reverse-refuted books -- this book's whole
        # thesis is "exit AT the event," so this is the one deliberate input
        # difference vs. bounded_exit_override's other 3 callers.
        "dsl_exit_override": bounded_exit_override(
            stop_pct, leverage, max(hours_to_unlock, 1.0) * 60.0),
    }


def maybe_run(config: Dict[str, Any],
             universe: Optional[List[Dict[str, Any]]],
             positions: Optional[List[Dict[str, Any]]],
             execute_fn: Callable[[BookAnalysis], Any]) -> Optional[Dict[str, Any]]:
    """Open bounded live shorts inside the [lo_h, hi_h) pre-unlock window.

    Shadow rows for this arm keep coming from unlock_recorder (runs every
    scan); this module only handles capital, so there is no double-recording.
    """
    cfg = config.get("unlock_short") or {}
    if not bool(cfg.get("enabled", False)) or bool(cfg.get("shadow_only", False)):
        return None

    now_ms = int(time.time() * 1000)
    lo_h = float(cfg.get("window_lo_h", 48.0))
    hi_h = float(cfg.get("window_hi_h", 72.0))
    min_pct = float(cfg.get("min_pct_circ", 1.0))
    min_vol = float(cfg.get("min_volume_usd", 5_000_000.0))
    max_new = int(cfg.get("max_new_per_cycle", 1))

    # calendar maintained by unlock_recorder (refreshes every 12h)
    upcoming = (unlock_recorder._load_state().get("upcoming") or [])

    vols: Dict[str, float] = {}
    for m in universe or []:
        c = m.get("coin") or ""
        if c:
            try:
                vols[c] = float(m.get("dayNtlVlm") or 0.0)
            except (TypeError, ValueError):
                vols[c] = 0.0

    candidates: List[Dict[str, Any]] = []
    for ev in upcoming:
        coin = str(ev.get("coin") or "")
        try:
            t_ms = int(float(ev.get("t_ms") or 0))
            pct = float(ev.get("pct") or 0.0)
        except (TypeError, ValueError):
            continue
        hours_out = (t_ms - now_ms) / _HOUR_MS
        if not coin or not (lo_h <= hours_out < hi_h) or pct < min_pct:
            continue
        if vols.get(coin, 0.0) < min_vol:
            continue
        candidates.append({"coin": coin, "ev": ev, "hours_out": hours_out, "pct": pct})
    if not candidates:
        return None
    candidates.sort(key=lambda c: -c["pct"])      # biggest unlock first

    seen = _load_seen()
    held = _held_coins(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK_NAME)
    blocked_by_claim = claims.claimed_by_others(_BOOK_NAME)

    opened = 0
    skipped = {"held": 0, "claimed": 0, "blocked": 0, "seen": 0}
    for cand in candidates:
        if opened >= max_new:
            break
        coin = cand["coin"]
        key = f"{coin}:{int(float(cand['ev'].get('t_ms') or 0)) // _DAY_MS}"
        if key in seen:
            skipped["seen"] += 1
            continue
        if coin in held:
            skipped["held"] += 1
            continue
        if coin in blocked_by_claim or not claims.claim(coin, _BOOK_NAME):
            skipped["claimed"] += 1
            continue
        try:
            result = execute_fn(_analysis(coin, cand["ev"], cand["hours_out"], cfg))
            if _execute_opened(result):
                opened += 1
                seen[key] = now_ms
                log_event({"event": "book_open", "book": _BOOK_NAME, "coin": coin,
                           "side": "short", "sig_t": now_ms})
                logger.info(f"[unlock-short-live] LIVE opened short {coin} "
                            f"({cand['pct']:.1f}% circ unlock in {cand['hours_out']:.0f}h)")
            else:
                skipped["blocked"] += 1
                claims.release(coin, _BOOK_NAME)
                why = _execute_block_detail(result)
                logger.warning(f"[unlock-short-live] {coin} not opened: {why}")
        except Exception as exc:
            skipped["blocked"] += 1
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[unlock-short-live] open {coin} failed: {exc}")

    _save_seen(seen)
    claims.save()
    rec = {"event": _BOOK_NAME, "ts": now_ms, "shadow": False,
           "candidates": len(candidates), "opened": opened, "skipped": skipped}
    log_event(rec)
    return rec
