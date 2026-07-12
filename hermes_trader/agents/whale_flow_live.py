"""whale_flow — zero-capital whale order-flow recorder (2026-07-12).

Operator asked for UW-style whale signals. The free primitive we already
built (bc1df87, recovered) is Binance public aggTrades: aggressive taker
prints >= $100k over a rolling 15-minute window, split buy/sell. This lane
records the hypothesis forward: whale_buying -> hypothetical LONG,
whale_selling -> hypothetical SHORT, balanced reads -> control rows.

Nothing trades. Promotion bar (scripts/shadow_status.py): >=30 episodes per
side, EV25 > 0 both halves, and the biased sides must beat the balanced
control. Prior art says humility: the whale-override PASS-upgrade bled in
chop and the GEX veto fired 0x in 3 weeks — forward evidence or nothing.
Kill: whale_flow.enabled=false (hot-read).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.crypto_whale import crypto_whale_signal
from hermes_trader.agents.rebalancer_owned import state_file

logger = logging.getLogger(__name__)

_BOOK_NAME = "whale_flow"
_HOUR_MS = 3_600_000
_TS_FILE = state_file(".whale_flow_ts.json")
_MAX_COINS_PER_PASS = 6


def _last_pass_ms() -> int:
    try:
        raw = json.load(open(_TS_FILE))
        return int(raw.get("ts", 0)) if isinstance(raw, dict) else 0
    except Exception:
        return 0


def _mark_pass(now_ms: int) -> None:
    try:
        with open(_TS_FILE, "w") as fh:
            json.dump({"ts": now_ms}, fh)
    except Exception:
        pass


def maybe_run(config: Dict[str, Any],
              perceptions: Optional[List[Dict[str, Any]]]) -> int:
    """Call once per scan with the cycle's perceptions. Throttled; records a
    row per crypto candidate with a readable whale report. Returns rows."""
    cfg = (config.get("whale_flow") or {})
    if not bool(cfg.get("enabled", True)):
        return 0
    now_ms = int(time.time() * 1000)
    interval_min = float(cfg.get("scan_interval_min", 30.0))
    if now_ms - _last_pass_ms() < interval_min * 60_000:
        return 0
    _mark_pass(now_ms)  # mark first: fetch failures must not retry-storm

    min_usd = float(cfg.get("min_print_usd", 100_000.0))
    window_min = float(cfg.get("window_minutes", 15.0))
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for p in perceptions or []:
        coin = str(p.get("coin") or "")
        if not coin or coin in seen or ":" in coin:   # crypto only (Binance)
            continue
        seen.add(coin)
        if len(seen) > _MAX_COINS_PER_PASS:
            break
        try:
            mid = float(p.get("mid") or 0.0)
        except (TypeError, ValueError):
            mid = 0.0
        if mid <= 0:
            continue
        try:
            rep = crypto_whale_signal(coin, min_usd=min_usd,
                                      window_minutes=window_min)
        except Exception as exc:
            logger.debug(f"[whale-flow] {coin}: read failed ({exc})")
            continue
        if rep is None:
            continue
        side = ("long" if rep.bias == "whale_buying"
                else "short" if rep.bias == "whale_selling" else "long")
        rows.append({
            "coin": coin, "side": side,
            "signal_bar_t": (now_ms // _HOUR_MS) * _HOUR_MS,
            "entry_ref_px": mid, "horizon_days": 1.0, "stop_pct": 15.0,
            "meta": {"bias": rep.bias, "net_usd": round(rep.net_usd),
                     "buy_usd": round(rep.buy_usd), "sell_usd": round(rep.sell_usd),
                     "whale_n": rep.whale_n, "window_min": window_min,
                     "control": rep.bias == "balanced", "shadow": True},
        })
    n = shadow_ledger.record_many(_BOOK_NAME, rows)
    if n:
        biased = sum(1 for r in rows if not r["meta"]["control"])
        logger.info(f"[whale-flow] recorded {n} read(s), {biased} biased")
    return n
