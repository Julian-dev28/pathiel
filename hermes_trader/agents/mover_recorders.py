"""Zero-capital mover recorders (Lane M follow-ups, 2026-07-11).

Two hypotheses earned FORWARD ledgers — not capital — from the W-M studies:

1. mover_pass: W-M4 gate audit measured the AI's PASS veto on researched
   movers forfeiting +4.48% mean forward-24h (n=15, 79% positive, one coin
   PASSed 28x while running +29.5%). Small n, no matched null, one 14-day
   window — so every researched mover the AI PASSes now records a
   hypothetical LONG for the fixed grader. Promotion bar: >=30 episodes,
   EV25 > 0 both halves.

2. b15_up: W-M1's single near-miss in the 624-cell grid — LONG when rolling
   24h return crosses +15% on >= $5M volume in a BTC-20d-up regime (n=66,
   EV25 +0.85%, OOS +0.58/+1.14, p=0.022) — which FAILED Bonferroni (alpha
   8e-5), i.e. plausibly grid luck. The recorder settles it forward at zero
   cost. Promotion bar: >=30 episodes, EV25 > 0 both halves, and the W-M1
   cell's own re-run agreeing.

Both record to the unified shadow ledger; scripts/shadow_status.py grades
them with the funding-aware, dedup-correct pipeline. Nothing here trades.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file

logger = logging.getLogger(__name__)

_DAY_MS = 86_400_000
_SEEN_FILE = state_file(".mover_recorders_seen.json")


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


def _dedup_key_hit(kind: str, coin: str, now_ms: int) -> bool:
    """True (and marks) if this (kind, coin) hasn't recorded this UTC day."""
    seen = _load_seen()
    key = f"{kind}:{coin}"
    day = now_ms // _DAY_MS
    if seen.get(key) == day:
        return False
    seen[key] = day
    _save_seen(seen)
    return True


def _pass_live_analysis(coin: str, move: float, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Bounded book order for the LIVE mover_pass arm (operator flip
    2026-07-12): buy the mover the AI just PASSed, recorded geometry
    ($20/1x, 15% stop, 1d hold, no trail). W-M4 basis: PASS vetoes forfeited
    +4.48% mean fwd-24h (n=15, no null — thin; kill at 10 episodes EV25<0)."""
    stop_pct = float(cfg.get("stop_pct", 15.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "LONG", "side": "long",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": f"[mover_pass] AI PASSed a +{move:.1f}% mover — W-M4 counter-buy",
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": "mover_pass",
        "strategy_book_notional": float(cfg.get("notional_usd", 20.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "dsl_exit_override": {
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            "protect_pct": 9999.0,
            "retrace_threshold": 0.5,
            "hard_timeout_minutes": float(cfg.get("hold_days", 1.0)) * 1440.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
        },
    }


def record_mover_pass(analysis: Dict[str, Any], config: Dict[str, Any],
                      execute_fn: Optional[Callable] = None) -> bool:
    """Call on every AI PASS verdict. Records a hypothetical LONG when the
    PASSed coin is a real mover (daily move >= min_move_pct on >= min vol).
    When mover_recorders.pass_live.shadow_only=false and an execute_fn is
    provided, also opens the bounded live counter-buy (same dedup: one per
    coin per UTC day, so live entries mirror the ledger 1:1)."""
    cfg = (config.get("mover_recorders") or {})
    if not bool(cfg.get("enabled", True)):
        return False
    try:
        move = float(analysis.get("daily_move_pct") or 0.0)
        vol = float(analysis.get("daily_volume_usd") or 0.0)
        px = float(analysis.get("last_price") or analysis.get("entry_ref_px") or 0.0)
        coin = analysis.get("coin") or ""
    except (TypeError, ValueError):
        return False
    if not coin or move < float(cfg.get("pass_min_move_pct", 8.0)):
        return False
    if vol and vol < float(cfg.get("min_volume_usd", 5_000_000.0)):
        return False
    now_ms = int(time.time() * 1000)
    if not _dedup_key_hit("pass", coin, now_ms):
        return False
    if px <= 0:
        # entry reference from live mid is the caller's job; degrade to skip
        return False
    live_cfg = cfg.get("pass_live") or {}
    live = (bool(live_cfg.get("enabled", False))
            and not bool(live_cfg.get("shadow_only", True))
            and execute_fn is not None)
    shadow_ledger.record("mover_pass", coin=coin, side="long",
                         signal_bar_t=(now_ms // 3_600_000) * 3_600_000,
                         entry_ref_px=px, horizon_days=1.0, stop_pct=15.0,
                         meta={"confidence": float(analysis.get("confidence") or 0),
                               "move_pct": round(move, 2), "shadow": not live})
    logger.info(f"[mover-recorders] PASS-veto counterfactual recorded: {coin} "
                f"(+{move:.1f}%, conf {float(analysis.get('confidence') or 0):.2f})")
    if live:
        claims = get_claims_registry()
        if coin not in claims.claimed_by_others("mover_pass") and claims.claim(coin, "mover_pass"):
            try:
                result = execute_fn(_pass_live_analysis(coin, move, live_cfg))
                opened = isinstance(result, dict) and (
                    bool(result.get("executed"))
                    or bool((result.get("result") or {}).get("executed") if isinstance(result.get("result"), dict) else False)
                )
                if opened:
                    claims.save()
                    logger.info(f"[mover-pass] LIVE opened long {coin} (+{move:.1f}% PASSed mover)")
                else:
                    claims.release(coin, "mover_pass")
                    logger.warning(f"[mover-pass] {coin} not opened: "
                                   f"{result.get('blocked_by') if isinstance(result, dict) else result}")
            except Exception as exc:
                claims.release(coin, "mover_pass")
                logger.warning(f"[mover-pass] open {coin} failed: {exc}")
    return True


def record_b15_crossings(universe, btc_up: Optional[bool],
                         config: Dict[str, Any]) -> int:
    """Call once per scan with the fresh universe. Records hypothetical LONGs
    for coins whose 24h move sits at/above the +15% band in a BTC-up regime
    (first touch per coin per UTC day)."""
    cfg = (config.get("mover_recorders") or {})
    if not bool(cfg.get("enabled", True)) or not btc_up:
        return 0
    band = float(cfg.get("b15_band_pct", 15.0))
    min_vol = float(cfg.get("min_volume_usd", 5_000_000.0))
    now_ms = int(time.time() * 1000)
    n = 0
    for m in universe or []:
        coin = m.get("coin") or ""
        if not coin or coin.startswith("@") or m.get("type") == "spot":
            continue
        try:
            prev = float(m.get("prevDayPx") or 0)
            cur = float(m.get("midPx") or m.get("markPx") or 0)
            vol = float(m.get("dayNtlVlm") or 0)
        except (TypeError, ValueError):
            continue
        if prev <= 0 or cur <= 0 or vol < min_vol:
            continue
        move = (cur / prev - 1.0) * 100.0
        if move < band:
            continue
        if not _dedup_key_hit("b15", coin, now_ms):
            continue
        shadow_ledger.record("mover_b15_up", coin=coin, side="long",
                             signal_bar_t=(now_ms // 3_600_000) * 3_600_000,
                             entry_ref_px=round(cur, 8), horizon_days=1.0,
                             stop_pct=15.0,
                             meta={"move_pct": round(move, 2), "btc_up": True,
                                   "shadow": True})
        n += 1
    if n:
        logger.info(f"[mover-recorders] b15_up recorded {n} crossing(s)")
    return n
