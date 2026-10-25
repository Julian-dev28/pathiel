"""Prometheus metrics for the trading agent.

The `/metrics` endpoint (served by `server.py`) is scraped by Prometheus. It is
deliberately **network-free**: every gauge is refreshed from local state only
(the agent config and on-disk receipts), so a scrape never hits Hyperliquid and
never contends with any live request. Process/GC collectors are auto-registered
by prometheus_client on import (they populate on Linux — i.e. in the
container/k8s, which is where the ops signal matters).

2026-10-23 (wave 2): the autonomous scan/research/execute engine is retired —
Claude trades through the MCP server now, there is no trading loop, no
scanner, no process supervisor and no alert evaluator running as a scheduled
job on the operator's machine. EQUITY/OPEN_POSITIONS/OPEN_NOTIONAL/
UNREALIZED_PNL (sourced from a position snapshot only that loop ever wrote),
TRADES_TOTAL/the bot's own win-rate ledger, HEARTBEAT_AGE (the loop's
heartbeat), FEED_GAP_FRAC/FEED_TRUSTWORTHY (the scanner's feed-integrity
read), SUPERVISOR_AGE and ALERT_EVAL_AGE/ALERTS_FIRING (both scheduler jobs)
are all dropped rather than kept and left reading a permanent, false
"dead"/"zero" — the same reasoning that dropped GRADING_AGE in wave 1. Every
gauge below still has a real writer.

2026-10-25: BRAIN_READY (`pathiel_ai_brain_ready`) dropped the same way. The
AI brain module it probed is deleted; the one LLM step left (the desk thesis
writer, services/llm) has no provider to be "ready" ahead of a call.
"""

from __future__ import annotations

import logging
import time

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Gauge, generate_latest

logger = logging.getLogger(__name__)

LIVE_MODE = Gauge("pathiel_live_mode", "1 when agent mode is LIVE, 0 otherwise")

# ── failure-mode metrics ─────────────────────────────────────────────────────
# Everything that measures the happy path moved to the desk-grading service's
# own trace; these are the failures a human would otherwise have to notice.
DISK_FREE = Gauge("pathiel_disk_free_bytes", "Free bytes on the state filesystem")
LOG_DIR_BYTES = Gauge("pathiel_log_dir_bytes", "Total bytes under logs/")
CAN_TRADE = Gauge(
    "pathiel_can_trade", "1 when equity clears the structural minimum. LIVE mode "
    "with 0 here means every order attempt is refused")
DRAWDOWN_PCT = Gauge(
    "pathiel_drawdown_pct", "Current drawdown from peak, flow-neutral where "
    "capital flows are recorded (negative)")
MAX_DRAWDOWN_PCT = Gauge(
    "pathiel_max_drawdown_pct", "Worst drawdown over the risk window (negative)")
BACKUP_AGE = Gauge(
    "pathiel_backup_age_seconds",
    "Seconds since the last VERIFIED state backup. The evidence base under "
    "every desk's track record is gitignored and lives on one disk")


def _to_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _refresh() -> None:
    """Pull current values from local state. Never raises — a partial scrape
    beats a 500 that blinds the dashboard."""
    try:
        from pathiel.agents.config_store import read_agent_config

        mode = str(read_agent_config().get("mode", "OFF")).upper()
        LIVE_MODE.set(1.0 if mode == "LIVE" else 0.0)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[metrics] config read failed: {e}")

    # `_never_ran` is a large sentinel, not 0: "no backup receipt" must read as
    # maximally stale, never as "ran just now".
    _never_ran = 10 ** 6

    try:
        from pathiel.agents.atomic_io import read_json
        from pathiel.state import state_file

        receipt = read_json(state_file("backup.json"), default=None) or {}
        ts = _to_float(receipt.get("ts"))
        # An UNVERIFIED backup is not a backup. Report it as stale so the alert
        # fires on a corrupt archive exactly as it does on a missing one.
        BACKUP_AGE.set(time.time() - ts if ts > 0 and receipt.get("verified")
                       else _never_ran)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[metrics] backup receipt read failed: {e}")
        BACKUP_AGE.set(_never_ran)

    # ── the failure modes ────────────────────────────────────────────────────
    # Each block is independently guarded: one broken source must degrade a
    # single metric, never blank the whole scrape. A monitoring gap that hides
    # the other metrics is worse than the gap itself.
    try:
        from pathiel import log_setup

        g = log_setup.check_disk_guard()
        DISK_FREE.set(float(g.free_bytes))
        LOG_DIR_BYTES.set(float(g.log_dir_bytes))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[metrics] disk read failed: {e}")

    try:
        from pathiel import dashboard as _db

        r = _db._risk_payload()
        CAN_TRADE.set(1.0 if r.get("can_trade") else 0.0)
        DRAWDOWN_PCT.set(_to_float(r.get("drawdown_pct")))
        MAX_DRAWDOWN_PCT.set(_to_float(r.get("max_drawdown_pct")))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[metrics] risk read failed: {e}")


def render_metrics() -> tuple[bytes, str]:
    """Refresh gauges and return (body, content_type) for the HTTP response."""
    _refresh()
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
