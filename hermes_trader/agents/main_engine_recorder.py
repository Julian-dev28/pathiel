"""Zero-capital forward ledger for the MAIN ENGINE's own thesis.

THE GAP THIS CLOSES (2026-07-20): every strategy book records each signal to
the shadow ledger and is graded forward, so a book that stops working is
caught at its bar and demoted. The main engine — the AI-verdict surface — was
the ONE surface that traded without recording. That is precisely why it bled
-$172.33 over 157 trades in 30 days before anyone measured it: there was no
ledger to grade, so no bar to fail.

Its thesis is a thesis like any other: "the AI's directional verdict, at
confidence >= the entry floor, predicts the next day." It gets a ledger, it
gets graded at the same min-n, and it wins or loses on the same evidence
everyone else does. Being the flagship earns it no exemption.

Nothing here trades. The live arm is `main_engine.entries_enabled`, owned by
scripts/autonomous_cycle.py: when this ledger grades VALIDATED at the bar the
cycle turns entries back on; when it grades REFUTED the cycle turns them off.
That is the whole loop — THESIS, TEST, EVOLUTION — with no human in it.

Recorded on EVERY directional verdict, whether or not it executed, so the
ledger measures the SIGNAL rather than the (capital-constrained, gate-filtered)
subset that happened to get filled. A verdict blocked by the notional cap is
still evidence about the thesis.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.rebalancer_owned import state_file

logger = logging.getLogger(__name__)

_BOOK_NAME = "main_engine"
_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_SEEN_FILE = state_file(".main_engine_recorder_seen.json")

# Graded geometry. 1-day horizon matches the engine's own realized hold profile
# (the 2h-6h bucket was its only positive one; >24h holds are a rounding error
# at 11 trades). The 6% stop is what the executor's backup-SL clamp ACTUALLY
# executes at 10x — grading a 15% stop the engine can never reach would judge a
# policy it does not run.
_HORIZON_DAYS = 1.0
_STOP_PCT = 6.0


def _load_seen() -> Dict[str, int]:
    import json
    try:
        raw = json.load(open(_SEEN_FILE))
        return {str(k): int(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_seen(seen: Dict[str, int]) -> None:
    import json
    try:
        with open(_SEEN_FILE, "w") as fh:
            json.dump(seen, fh, sort_keys=True)
    except Exception:
        pass


def _dedup(coin: str, now_ms: int) -> bool:
    """One row per coin per UTC day — the engine re-researches a held coin
    every 10 minutes, and 144 rows of the same standing opinion is not 144
    pieces of evidence."""
    seen = _load_seen()
    day = now_ms // _DAY_MS
    if seen.get(coin) == day:
        return False
    seen[coin] = day
    cutoff = day - 60
    _save_seen({k: v for k, v in seen.items() if v >= cutoff})
    return True


def record_verdict(analysis: Dict[str, Any], config: Dict[str, Any],
                   executed: Optional[bool] = None) -> bool:
    """Call on every AI research verdict. Records LONG/SHORT as a zero-capital
    forward signal; PASS is not a directional claim and is not recorded (the
    mover_pass books already measure the PASS veto separately)."""
    cfg = (config.get("main_engine") or {})
    if not bool(cfg.get("recorder_enabled", True)):
        return False
    verdict = str(analysis.get("verdict") or "").upper()
    if verdict not in ("LONG", "SHORT"):
        return False
    coin = analysis.get("coin") or ""
    try:
        px = float(analysis.get("last_price") or analysis.get("entry_px") or 0.0)
        conf = float(analysis.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return False
    if not coin or px <= 0:
        return False
    now_ms = int(time.time() * 1000)
    if not _dedup(coin, now_ms):
        return False
    shadow_ledger.record(
        _BOOK_NAME, coin=coin,
        side="long" if verdict == "LONG" else "short",
        signal_bar_t=(now_ms // _HOUR_MS) * _HOUR_MS,
        entry_ref_px=px, horizon_days=_HORIZON_DAYS, stop_pct=_STOP_PCT,
        meta={"confidence": round(conf, 3),
              "executed": bool(executed) if executed is not None else None,
              "web_search_used": bool(analysis.get("web_search_used")),
              "ai_brain_provider": analysis.get("ai_brain_provider"),
              "composite_score": float(analysis.get("composite_score") or 0.0),
              "shadow": True},
    )
    logger.info(f"[main-engine-recorder] {coin} {verdict} conf {conf:.2f} "
                f"recorded (zero-capital thesis ledger)")
    return True
