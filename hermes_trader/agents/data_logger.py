"""Forward data-collection logger — funding-rate + open-interest snapshots.

The candle/indicator space is EXHAUSTED (ALPHA-PLAN 🔁 WAVE LOG: ~35 indicators, 0 standalone +EV).
The next alpha frontier is data we have NO history for: funding rates, open interest, liquidations.
This logger appends a throttled snapshot of funding + OI for the whole universe to a JSONL file, so in
~1-2 weeks there's enough forward history to backtest funding-carry / OI-divergence / OI-vs-price.

ZERO added API load: the loop's `universe` (from `get_universe` → metaAndAssetCtxs) ALREADY carries
`funding`, `openInterest`, `markPx`, `dayNtlVlm` per coin. We just persist a snapshot of it on a timer.

PURE: reads the already-fetched `universe`, appends to disk. No network, no orders. enabled=False → no-op.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List

from hermes_trader.agents.rebalancer_owned import state_file

logger = logging.getLogger(__name__)

_LOG_FILE = state_file(".data_funding_oi.jsonl")
_TS_FILE = state_file(".data_logger_ts")


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


def maybe_log(config: Dict[str, Any], universe: List[Dict[str, Any]]) -> None:
    """Append a funding/OI snapshot of `universe` to the JSONL log, at most once per interval_hours.
    No-op when disabled. Safe to call every loop cycle."""
    dl = config.get("data_logger") or {}
    if not bool(dl.get("enabled", False)):
        return
    interval_h = float(dl.get("interval_hours", 1.0))
    now = time.time()
    if now - _last_ts() < interval_h * 3600:
        return

    rows = []
    for m in (universe or []):
        coin = m.get("coin") or ""
        if not coin:
            continue
        if coin.startswith("@") or m.get("type") == "spot":
            continue
        funding = m.get("funding")
        oi = m.get("openInterest")
        if funding is None and oi is None:
            continue
        try:
            if float(oi or 0) <= 0 and float(funding or 0) == 0:
                continue
        except Exception:
            pass
        rows.append({
            "c": coin,
            "type": m.get("type", "perp"),
            "dex": m.get("dex"),
            "f": funding,                                   # funding rate (per-hour, HL convention)
            "oi": oi,                                        # open interest (base units)
            "px": m.get("markPx") or m.get("midPx") or m.get("oraclePx"),
            "v": m.get("dayNtlVlm"),                         # 24h $ volume
        })
    if not rows:
        return

    try:
        with open(_LOG_FILE, "a") as fh:
            fh.write(json.dumps({"ts": int(now * 1000), "n": len(rows), "rows": rows}) + "\n")
        _save_ts(now)
        logger.info(f"[data-logger] snapshot: {len(rows)} coins funding/OI → {_LOG_FILE}")
    except Exception as e:
        logger.warning(f"[data-logger] write failed (non-fatal): {e}")
