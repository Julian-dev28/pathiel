"""v2 risk rails — kill switch, gross cap, liquidity floors, margin preflight.

Keeps exactly the gates MINIMAL_SYSTEM.md §3 names and nothing else (the
AI-specific gates — confidence floors, counter_regime, runner gate, sidestep —
died with the AI entry path).

Kill switch: reuses risk_gates.effective_daily_loss_limit (pct-of-SOD, shipped
2026-07-18) and adds the ONE fix the spec demands: start-of-day equity persists
keyed by UTC date, so a mid-day restart cannot re-baseline drawdown out of the
kill switch (project_sod_reset_on_restart).

All functions here are pure or file-backed with injectable paths — no network,
so every gate is testable in the <2s pre-commit lane.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from hermes_trader.agents.rebalancer_owned import state_file
from hermes_trader.agents.risk_gates import effective_daily_loss_limit

logger = logging.getLogger(__name__)

# Mirrors client/exchange.MIN_ORDER_USD. Kept as a local constant so importing
# v2.risk never drags the exchange/SDK module in; a gate test asserts parity so
# the two can never drift silently.
MIN_ORDER_USD = 10.5

# Spec §3 defaults (research/rebuild_2026_07_18/MINIMAL_SYSTEM.md).
GROSS_CAP_PCT = 3.0                      # 300% of equity
LONG_LIQUIDITY_FLOOR_USD = 5_000_000.0   # project_liquidity_floors_2026_06_28
SHORT_LIQUIDITY_FLOOR_USD = 20_000_000.0
MIN_AVAILABLE_MARGIN_PCT = 0.10
MAX_DAILY_LOSS_PCT = 0.15                # of start-of-day equity

_SOD_FILE = state_file(".v2_sod.json")


# ── Start-of-day equity, persisted per UTC date ────────────────────────────────

def utc_date(now_s: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now_s if now_s is not None else time.time()))


def start_of_day_equity(equity: float, now_s: Optional[float] = None,
                        path: Optional[str] = None) -> float:
    """Return today's (UTC) start-of-day equity, persisting it on first sight.

    - First call of a UTC day (or ever): baseline := current equity, written to disk.
    - Any later call the SAME UTC day returns the PERSISTED baseline — a mid-day
      restart cannot launder drawdown out of the kill switch (the v1 SOD-reset bug).
    - equity <= 0 is a degraded read: never baseline from it; return the persisted
      value if one exists (0.0 signals "unusable" to the caller otherwise).
    """
    path = path or _SOD_FILE
    today = utc_date(now_s)
    persisted_date, persisted_eq = "", 0.0
    try:
        with open(path) as fh:
            d = json.load(fh)
        persisted_date = str(d.get("date") or "")
        persisted_eq = float(d.get("equity") or 0.0)
    except Exception:
        pass
    if persisted_date == today and persisted_eq > 0:
        return persisted_eq
    if equity <= 0:
        # Degraded read on a fresh day: keep whatever baseline we had rather than
        # writing garbage. Caller must treat 0.0 as "cannot evaluate the kill switch".
        return persisted_eq if persisted_eq > 0 else 0.0
    try:
        with open(path, "w") as fh:
            json.dump({"date": today, "equity": float(equity)}, fh)
    except Exception as exc:
        logger.warning(f"[v2-risk] could not persist SOD equity to {path}: {exc}")
    return float(equity)


# ── Kill switch ────────────────────────────────────────────────────────────────

def kill_switch(risk_cfg: Dict[str, Any], equity: float, sod_equity: float) -> Dict[str, Any]:
    """Evaluate the daily-loss kill switch against the persisted SOD baseline.

    Delegates the limit math to risk_gates.effective_daily_loss_limit (the
    shipped pct-of-SOD implementation): it reconstructs SOD as equity - daily_pnl,
    and daily_pnl here is BY CONSTRUCTION equity - sod_equity, so the persisted
    baseline flows through exactly. Breach => flatten-all + no new entries.
    """
    if equity <= 0 or sod_equity <= 0:
        # Degraded read — never conclude "crash" from a partial fetch
        # (project_partial_dex_degraded_read). No breach verdict, no entries either.
        return {"breached": False, "degraded": True, "limit_usd": 0.0, "daily_pnl": 0.0}
    daily_pnl = equity - sod_equity
    cfg = {
        "max_daily_loss_pct": float(risk_cfg.get("max_daily_loss_pct", MAX_DAILY_LOSS_PCT) or 0.0),
        "max_daily_loss_usd": float(risk_cfg.get("max_daily_loss_usd", -100) or -100),
    }
    limit = effective_daily_loss_limit(cfg, equity, daily_pnl)
    return {
        "breached": daily_pnl <= limit,
        "degraded": False,
        "limit_usd": round(limit, 4),
        "daily_pnl": round(daily_pnl, 4),
    }


# ── Entry-time gates (pure) ────────────────────────────────────────────────────

def margin_ok(equity: float, available: float,
              min_avail_pct: float = MIN_AVAILABLE_MARGIN_PCT) -> bool:
    """Free-margin floor: leave headroom for maintenance + slippage (v1 executor port)."""
    if equity <= 0:
        return False
    return (available / equity) >= min_avail_pct


def gross_cap_ok(total_open_notional: float, add_notional: float, equity: float,
                 cap_pct: float = GROSS_CAP_PCT) -> bool:
    """Gross-notional cap: open + new must stay within cap_pct × equity (spec: 300%)."""
    if equity <= 0:
        return False
    return (float(total_open_notional or 0.0) + float(add_notional or 0.0)) <= cap_pct * equity


def liquidity_floor_ok(side: str, day_volume_usd: float,
                       long_floor: float = LONG_LIQUIDITY_FLOOR_USD,
                       short_floor: float = SHORT_LIQUIDITY_FLOOR_USD) -> bool:
    """$20M short floor / $5M long floor (thin shorts get squeezed; thin longs churn)."""
    floor = short_floor if side == "short" else long_floor
    return float(day_volume_usd or 0.0) >= floor


def entry_gates(*, side: str, notional_usd: float, day_volume_usd: float,
                equity: float, available: float, total_open_notional: float,
                daily_pnl: float, risk_cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """Run every v2 entry gate; return the list of block reasons (empty = pass).

    All gates are evaluated (no short-circuit) so the caller's log shows every
    failing rail, same telemetry contract as v1 risk_gates.eval_all_gates.
    """
    rc = risk_cfg or {}
    reasons: List[str] = []

    if equity <= 0:
        return ["equity_unavailable (degraded account read)"]

    limit = effective_daily_loss_limit({
        "max_daily_loss_pct": float(rc.get("max_daily_loss_pct", MAX_DAILY_LOSS_PCT) or 0.0),
        "max_daily_loss_usd": float(rc.get("max_daily_loss_usd", -100) or -100),
    }, equity, daily_pnl)
    if daily_pnl <= limit:
        reasons.append(f"daily_loss_kill_switch (PnL ${daily_pnl:.2f} <= ${limit:.2f})")

    if not margin_ok(equity, available, float(rc.get("min_available_margin_pct",
                                                     MIN_AVAILABLE_MARGIN_PCT))):
        reasons.append(f"insufficient_free_margin (available ${available:.2f} "
                       f"/ equity ${equity:.2f})")

    cap_pct = float(rc.get("gross_cap_pct", GROSS_CAP_PCT))
    if not gross_cap_ok(total_open_notional, notional_usd, equity, cap_pct):
        reasons.append(f"gross_notional_cap (${total_open_notional:.0f} open + "
                       f"${notional_usd:.0f} new > {cap_pct:.0%} of ${equity:.0f})")

    if not liquidity_floor_ok(side, day_volume_usd,
                              float(rc.get("long_liquidity_floor_usd", LONG_LIQUIDITY_FLOOR_USD)),
                              float(rc.get("short_liquidity_floor_usd", SHORT_LIQUIDITY_FLOOR_USD))):
        reasons.append(f"liquidity_floor ({side} on ${day_volume_usd/1e6:.1f}M 24h volume)")

    if notional_usd < MIN_ORDER_USD:
        reasons.append(f"below_min_order (${notional_usd:.2f} < ${MIN_ORDER_USD})")

    return reasons
