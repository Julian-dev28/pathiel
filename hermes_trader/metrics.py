"""Prometheus metrics for the trading agent.

The `/metrics` endpoint (served by `server.py`) is scraped by Prometheus. It is
deliberately **network-free**: every gauge is refreshed from local state only
(`memory`, the agent config, and the cross-process positions snapshot the loop
writes each cycle), so a scrape never hits Hyperliquid and never contends with
the loop's rate limiter. Process/GC collectors are auto-registered by
prometheus_client on import (they populate on Linux — i.e. in the container/k8s,
which is where the ops signal matters).
"""

from __future__ import annotations

import logging

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Gauge, generate_latest

logger = logging.getLogger(__name__)

EQUITY = Gauge("hermes_equity_usd", "Last known account equity in USD")
OPEN_POSITIONS = Gauge(
    "hermes_open_positions", "Open positions (from the loop snapshot)"
)
OPEN_NOTIONAL = Gauge(
    "hermes_open_notional_usd", "Sum of open position notional in USD"
)
UNREALIZED_PNL = Gauge(
    "hermes_unrealized_pnl_usd", "Sum of unrealized PnL across open positions in USD"
)
TRADES_TOTAL = Gauge("hermes_trades_total", "Number of recorded trades")
LIVE_MODE = Gauge("hermes_live_mode", "1 when agent mode is LIVE, 0 otherwise")


def _to_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _refresh() -> None:
    """Pull current values from local state. Never raises — a partial scrape
    beats a 500 that blinds the dashboard."""
    try:
        from hermes_trader.agents.memory import memory

        memory.load()
        EQUITY.set(_to_float(memory.get_full_state().get("equity", 0)))
        TRADES_TOTAL.set(len(memory.get_all_trades() or []))
    except Exception as e:  # noqa: BLE001 — metrics must never break the endpoint
        logger.debug(f"[metrics] memory read failed: {e}")

    try:
        from hermes_trader.agents.config_store import read_agent_config

        mode = str(read_agent_config().get("mode", "OFF")).upper()
        LIVE_MODE.set(1.0 if mode == "LIVE" else 0.0)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[metrics] config read failed: {e}")

    try:
        from hermes_trader.positions_snapshot import read_snapshot

        snap = read_snapshot(max_age_s=600.0) or {}
        count = 0
        notional = 0.0
        upnl = 0.0
        for entry in snap.get("asset_positions", []):
            pos = entry.get("position", {}) if isinstance(entry, dict) else {}
            if _to_float(pos.get("szi")) == 0:
                continue
            count += 1
            notional += abs(_to_float(pos.get("positionValue")))
            upnl += _to_float(pos.get("unrealizedPnl"))
        OPEN_POSITIONS.set(count)
        OPEN_NOTIONAL.set(notional)
        UNREALIZED_PNL.set(upnl)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[metrics] snapshot read failed: {e}")


def render_metrics() -> tuple[bytes, str]:
    """Refresh gauges and return (body, content_type) for the HTTP response."""
    _refresh()
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
