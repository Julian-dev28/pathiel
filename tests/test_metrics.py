"""Offline test for the Prometheus /metrics endpoint.

No state files exist in CI (.agent-config.json is gitignored), so the gauges
default to 0 — but the endpoint must still serve valid Prometheus text
without any network call.

2026-10-23 (wave 2): pathiel_equity_usd/pathiel_open_positions/
pathiel_open_notional_usd/pathiel_unrealized_pnl_usd/pathiel_trades_total
dropped — all four read from either the bot's own memory ledger or a
position snapshot only the now-retired trading loop ever wrote. See
pathiel/metrics.py's module docstring.
"""

from fastapi.testclient import TestClient

from pathiel.server import app

client = TestClient(app)


def test_metrics_endpoint_serves_prometheus_text():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]

    body = resp.text
    for name in (
        "pathiel_live_mode",
        "pathiel_can_trade",
        "pathiel_ai_brain_ready",
        "pathiel_backup_age_seconds",
    ):
        assert name in body


def test_metrics_endpoint_needs_no_operator_token():
    # Prometheus can't send the operator token; /metrics must be open like /api/health.
    assert client.get("/metrics").status_code == 200
