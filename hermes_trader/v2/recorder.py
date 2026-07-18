"""v2 recorder — funding/OI accrual only (the named alpha frontier keeps accruing).

Port of agents/data_logger essentials. DELIBERATELY writes the SAME files as v1
(.data_funding_oi.jsonl + .data_logger_ts via state_file) so the forward dataset
stays ONE time-series across the migration — the graded history is the asset,
and the shared throttle timestamp keeps the combined v1+v2 cadence at one
snapshot per interval instead of doubling it during the Phase-2 parallel run.

ZERO added API load: it persists fields the loop's universe fetch already
carries (funding, openInterest, markPx, dayNtlVlm). No network, no orders.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from hermes_trader.agents.rebalancer_owned import state_file

logger = logging.getLogger(__name__)

_LOG_FILE = state_file(".data_funding_oi.jsonl")
_TS_FILE = state_file(".data_logger_ts")


def _last_ts(ts_path: str) -> float:
    try:
        return float(open(ts_path).read().strip())
    except Exception:
        return 0.0


def maybe_record(cfg: Dict[str, Any], universe: Optional[List[Dict[str, Any]]],
                 now_s: Optional[float] = None, log_path: Optional[str] = None,
                 ts_path: Optional[str] = None) -> int:
    """Append one funding/OI snapshot of `universe`, at most once per interval_hours.

    Returns rows written (0 when disabled / throttled / nothing to log). Safe to
    call every signal cycle; never raises into the loop.
    """
    if not bool(cfg.get("enabled", True)):
        return 0
    log_path = log_path or _LOG_FILE
    ts_path = ts_path or _TS_FILE
    interval_h = float(cfg.get("interval_hours", 1.0))
    now = float(now_s if now_s is not None else time.time())
    if now - _last_ts(ts_path) < interval_h * 3600:
        return 0

    rows = []
    for m in universe or []:
        coin = m.get("coin") or ""
        if not coin or coin.startswith("@") or m.get("type") == "spot":
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
            "f": funding,                            # per-hour funding (HL convention)
            "oi": oi,                                # open interest (base units)
            "px": m.get("markPx") or m.get("midPx") or m.get("oraclePx"),
            "v": m.get("dayNtlVlm"),                 # 24h $ volume
        })
    if not rows:
        return 0
    try:
        with open(log_path, "a") as fh:
            fh.write(json.dumps({"ts": int(now * 1000), "n": len(rows), "rows": rows}) + "\n")
        open(ts_path, "w").write(str(now))
        logger.info(f"[v2-recorder] snapshot: {len(rows)} coins funding/OI → {log_path}")
    except Exception as exc:
        logger.warning(f"[v2-recorder] write failed (non-fatal): {exc}")
        return 0
    return len(rows)
