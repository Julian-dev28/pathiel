"""Offline test for the Prometheus /metrics endpoint.

No state files exist in CI (.agent-memory.json / .agent-config.json /
.positions-snapshot.json are gitignored), so the gauges default to 0 — but the
endpoint must still serve valid Prometheus text without any network call.
"""

from fastapi.testclient import TestClient

from hermes_trader.server import app

client = TestClient(app)


def test_metrics_endpoint_serves_prometheus_text():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]

    body = resp.text
    for name in (
        "hermes_equity_usd",
        "hermes_open_positions",
        "hermes_open_notional_usd",
        "hermes_unrealized_pnl_usd",
        "hermes_trades_total",
        "hermes_live_mode",
    ):
        assert name in body


def test_metrics_endpoint_needs_no_operator_token():
    # Prometheus can't send the operator token; /metrics must be open like /api/health.
    assert client.get("/metrics").status_code == 200
