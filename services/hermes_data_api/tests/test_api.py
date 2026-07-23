"""Contract tests for the Hermes Data API.

Every test is offline: the Hyperliquid seam is monkeypatched (see conftest),
the DB is a throwaway sqlite file, and the licensed options-flow slot is left
unset so net-flow returns an honest 501.

These assert the *contract* (envelope, auth, RFC7807 errors, rate limiting).
The full run happens after the integration owner wires `app.main`; until then
the fixtures skip with a clear message rather than failing collection.
"""
from __future__ import annotations

import pytest

# --------------------------------------------------------------------------- #
# Health — no auth required
# --------------------------------------------------------------------------- #
def test_health_ok_no_auth(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    # Health reports liveness; accept either a bare {"status": "ok"} or an
    # envelope {"data": {"status": "ok"}} without over-constraining the wrapper.
    status = body.get("status") or body.get("data", {}).get("status")
    assert status == "ok", body


# --------------------------------------------------------------------------- #
# Auth gating
# --------------------------------------------------------------------------- #
def test_ohlc_without_auth_is_401(client) -> None:
    resp = client.get("/api/stock/BTC/ohlc", params={"interval": "1d", "limit": 5})
    assert resp.status_code == 401


def test_ohlc_with_auth_returns_data_envelope(client, auth_headers) -> None:
    resp = client.get(
        "/api/stock/BTC/ohlc",
        params={"interval": "1d", "limit": 5},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body, body
    data = body["data"]
    assert isinstance(data, list) and len(data) == 5
    # Shape of a bar comes straight from the stubbed HL resource.
    first = data[0]
    for key in ("t", "o", "h", "l", "c", "v"):
        assert key in first, first


def test_momentum_with_auth_returns_signal(client, auth_headers) -> None:
    resp = client.get(
        "/api/stock/BTC/momentum",
        params={"lookback": 7},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body, body
    # our_momentum_signal returns a trailing_return computed from OUR HL candles.
    assert "trailing_return" in body["data"], body["data"]


# --------------------------------------------------------------------------- #
# Licensed-data adapter slot — honest 501, never fabricated
# --------------------------------------------------------------------------- #
def test_net_flow_upstream_unset_is_501(client, auth_headers) -> None:
    resp = client.get(
        "/api/stock/BTC/net-flow",
        params={"date": "2026-07-23"},
        headers=auth_headers,
    )
    assert resp.status_code == 501, resp.text


def test_error_response_is_problem_json(client, auth_headers) -> None:
    """RFC7807: error bodies are application/problem+json with problem fields.

    The net-flow 501 (licensed slot not configured) is the controlled error we
    assert against.
    """
    resp = client.get(
        "/api/stock/BTC/net-flow",
        params={"date": "2026-07-23"},
        headers=auth_headers,
    )
    assert resp.status_code == 501
    assert resp.headers.get("content-type", "").startswith("application/problem+json"), (
        resp.headers.get("content-type"),
        resp.text,
    )
    body = resp.json()
    # RFC7807 members — at least the human-facing ones should be present.
    assert "title" in body or "detail" in body, body
    # `status` member, when present, mirrors the HTTP status.
    assert body.get("status", 501) == 501, body


# --------------------------------------------------------------------------- #
# Rate limiting — token bucket per key, 429 + Retry-After past the burst.
# Kept LAST so any shared-bucket draining can't affect the tests above.
# --------------------------------------------------------------------------- #
def test_rate_limit_returns_429_past_burst(client, api_key_store) -> None:
    # Dedicated low-rate key so this test is isolated from the demo key's bucket
    # and trips whether the limiter's capacity comes from the per-key rate or
    # from the global burst (30). Either way, hammering 120x exceeds it.
    api_key_store.seed_demo_key("ratelimit-token", rate_per_min=1)
    headers = {"Authorization": "Bearer ratelimit-token"}

    saw_429 = False
    retry_after = None
    for _ in range(120):
        resp = client.get(
            "/api/stock/BTC/ohlc",
            params={"interval": "1d", "limit": 1},
            headers=headers,
        )
        if resp.status_code == 429:
            saw_429 = True
            retry_after = resp.headers.get("Retry-After")
            break
        assert resp.status_code == 200, resp.text

    assert saw_429, "expected a 429 after exceeding the burst, got none in 120 requests"
    # Contract: 429 carries Retry-After so clients can back off deterministically.
    assert retry_after is not None, "429 must carry a Retry-After header"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
