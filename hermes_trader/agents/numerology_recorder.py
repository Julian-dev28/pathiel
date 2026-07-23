"""Zero-capital paper-trade of the numerology 'money formula' (W-FUN lane, 2026-07-23).

day_root_odd: reduce the day-of-month to a single digit; ODD -> LONG ETH, EVEN -> SHORT.
It was the top of 168 numerology formulas in-sample (W-FUN1) at +11.35%/trade — and the
matched null proved that is luck: 1000 random coin-flip direction sets median $139 vs the
formula's $3098 (top 4% tail), inverse formula -> $6, and it fails Bonferroni (p=0.009 vs
0.05/168=0.0003 needed). This recorder logs its daily call forward at ZERO capital so the
noise proves itself out of sample. Records one ETH signal per UTC day. Nothing trades.
Promotion would run through shadow_status like any book — it will not promote.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.rebalancer_owned import state_file

logger = logging.getLogger(__name__)

_BOOK = "numerology_eth"
_STATE_FILE = state_file(".numerology_recorder_state.json")


def _reduce(n: int) -> int:
    while n > 9:
        n = sum(int(d) for d in str(n))
    return n


def day_root_odd_dir(dt: datetime) -> int:
    """+1 LONG if the day-of-month digit-root is odd, else -1 SHORT."""
    return 1 if _reduce(dt.day) % 2 == 1 else -1


def _load_state() -> Dict[str, Any]:
    try:
        raw = json.load(open(_STATE_FILE))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        with open(_STATE_FILE, "w") as fh:
            json.dump(state, fh)
    except Exception:
        pass


def _eth_mid(universe: Optional[List[Dict[str, Any]]]) -> float:
    for m in universe or []:
        if str(m.get("coin")) == "ETH":
            try:
                return float(m.get("midPx") or m.get("markPx") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def maybe_record(universe: Optional[List[Dict[str, Any]]],
                 config: Dict[str, Any]) -> int:
    """Call once per scan. Records ONE ETH signal per UTC day at/after `hour`. Returns
    1 if a new day's signal was recorded, else 0."""
    cfg = (config.get(_BOOK) or {})
    if not bool(cfg.get("enabled", True)):
        return 0
    now = datetime.now(timezone.utc)
    hour = int(cfg.get("hour", 14))
    if now.hour < hour:
        return 0
    today = now.date().isoformat()
    state = _load_state()
    if state.get("last_day") == today:
        return 0

    direction = day_root_odd_dir(now)
    leverage = float(cfg.get("leverage", 40))
    equity_frac = float(cfg.get("equity_frac", 0.5))
    shadow_ledger.record(
        _BOOK, coin="ETH", side=("long" if direction > 0 else "short"),
        signal_bar_t=int(now.timestamp() * 1000), ts=int(now.timestamp() * 1000),
        entry_ref_px=_eth_mid(universe), horizon_days=float(cfg.get("horizon_days", 1.0)),
        # SHADOW ONLY. This book has no execution path — it writes to the ledger and
        # nothing else — so the leverage/equity_frac below are SIMULATION parameters a
        # grader replays, never a real order. 40x liquidates at 1/40 = 2.5% adverse.
        meta={"scheme": "day_root_odd", "day_root": _reduce(now.day), "hour": hour,
              "shadow": True, "leverage": leverage, "equity_frac": equity_frac,
              "liq_pct": round(100.0 / leverage, 3),
              "note": "known-noise paper trade (W-FUN1); zero capital; will not promote"},
    )
    state["last_day"] = today
    _save_state(state)
    logger.info(f"[numerology-eth] PAPER {('LONG' if direction > 0 else 'SHORT')} ETH "
                f"@ {leverage:.0f}x, {equity_frac:.0%} equity (day-root {_reduce(now.day)}) "
                f"— SHADOW, zero capital, known noise")
    return 1


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    d = day_root_odd_dir(now)
    print(f"{now.date()} day-root {_reduce(now.day)} -> {'LONG' if d > 0 else 'SHORT'} ETH (paper)")
