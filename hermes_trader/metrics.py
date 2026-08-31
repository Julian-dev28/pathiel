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
import time

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

# ── failure-mode metrics ─────────────────────────────────────────────────────
# Everything above measures the happy path: equity, positions, PnL. None of it
# moves when the system BREAKS, which is how a dead loop, a blind market feed
# and an unrotated disk all went unnoticed. These are the failures, exported so
# something can page on them instead of a human noticing weeks later.
HEARTBEAT_AGE = Gauge(
    "hermes_heartbeat_age_seconds",
    "Seconds since the trading loop last wrote a heartbeat. Grows without "
    "bound when the loop is dead — which is the point")
FEED_GAP_FRAC = Gauge(
    "hermes_feed_gap_fraction",
    "Share of the scanned universe that was unreadable on the last scan. A "
    "degraded feed reads downstream as a quiet market")
FEED_TRUSTWORTHY = Gauge(
    "hermes_feed_trustworthy", "1 when the last scan was trustworthy enough to "
    "trade on, 0 when entries are blocked")
DISK_FREE = Gauge("hermes_disk_free_bytes", "Free bytes on the state filesystem")
LOG_DIR_BYTES = Gauge("hermes_log_dir_bytes", "Total bytes under logs/")
BRAIN_READY = Gauge(
    "hermes_ai_brain_ready", "1 when the selected AI brain can actually run. A "
    "dead brain returns empty completions, which downstream reads as a PASS")
CAN_TRADE = Gauge(
    "hermes_can_trade", "1 when equity clears the structural minimum. LIVE mode "
    "with 0 here means the executor refuses every order")
DRAWDOWN_PCT = Gauge(
    "hermes_drawdown_pct", "Current drawdown from peak, flow-neutral where "
    "capital flows are recorded (negative)")
MAX_DRAWDOWN_PCT = Gauge(
    "hermes_max_drawdown_pct", "Worst drawdown over the risk window (negative)")
# Who watches the watchers. Every metric above depends on something still
# running to act on it. If the supervisor stops, dead processes stop being
# restarted; if the alert evaluator stops, nothing is delivered. Both fail the
# same silent way everything else here did — by simply not happening.
SUPERVISOR_AGE = Gauge(
    "hermes_supervisor_age_seconds",
    "Seconds since the process supervisor last ran. Grows without bound when "
    "supervision has stopped, which is when a dead process stays dead")
ALERT_EVAL_AGE = Gauge(
    "hermes_alert_eval_age_seconds",
    "Seconds since the alert evaluator last ran. Nothing is being delivered "
    "while this grows")
ALERTS_FIRING = Gauge(
    "hermes_alerts_firing", "Number of alert rules currently firing")
BACKUP_AGE = Gauge(
    "hermes_backup_age_seconds",
    "Seconds since the last VERIFIED state backup. The evidence base under "
    "every book verdict is gitignored and lives on one disk")


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

    # `_never_ran` is a large sentinel, not 0: "no supervisor state file" must
    # read as maximally stale, never as "ran just now". Same reason the
    # heartbeat uses one.
    _never_ran = 10 ** 6
    for gauge, name in ((SUPERVISOR_AGE, "supervisor.json"),
                        (ALERT_EVAL_AGE, "alerts.json")):
        try:
            from hermes_trader.agents.atomic_io import read_json
            from hermes_trader.agents.rebalancer_owned import state_file

            payload = read_json(state_file(name), default=None)
            ts = _to_float((payload or {}).get("ts"))
            gauge.set(time.time() - ts if ts > 0 else _never_ran)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[metrics] {name} read failed: {e}")
            gauge.set(_never_ran)

    try:
        from hermes_trader.agents.atomic_io import read_json
        from hermes_trader.agents.rebalancer_owned import state_file

        firing = (read_json(state_file("alerts.json"), default=None) or {}).get("firing")
        ALERTS_FIRING.set(len(firing) if isinstance(firing, list) else 0)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[metrics] alerts firing read failed: {e}")

    try:
        from hermes_trader.agents.atomic_io import read_json
        from hermes_trader.agents.rebalancer_owned import state_file

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
        from hermes_trader import dashboard as _db

        age = (_db._summary_payload() or {}).get("last_tick_age_s")
        # No heartbeat at all is not "age 0" — that would read as perfectly
        # healthy. A large sentinel keeps the alert firing.
        HEARTBEAT_AGE.set(float(age) if age is not None else 1e9)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[metrics] heartbeat read failed: {e}")

    try:
        from hermes_trader.agents import perception

        st = perception.last_scan_integrity()
        FEED_GAP_FRAC.set(_to_float(st.get("gap_frac")))
        FEED_TRUSTWORTHY.set(1.0 if perception.scan_is_trustworthy() else 0.0)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[metrics] feed read failed: {e}")

    try:
        from hermes_trader import log_setup

        g = log_setup.check_disk_guard()
        DISK_FREE.set(float(g.free_bytes))
        LOG_DIR_BYTES.set(float(g.log_dir_bytes))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[metrics] disk read failed: {e}")

    try:
        from hermes_trader.agents.ai_brain import provider_readiness

        BRAIN_READY.set(1.0 if provider_readiness().get("ready") else 0.0)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[metrics] brain read failed: {e}")

    try:
        from hermes_trader import dashboard as _db

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
