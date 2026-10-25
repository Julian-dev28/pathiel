"""HTTP-surface fixes from the 2026-10-25 security review
(docs/security/SUPPLY_CHAIN_2026-10-25.md). Each test names the finding it
holds shut.

  P0-1  POST /api/desks/{id}/theses and /refresh were open to anyone. The
        newest thesis of any author is the call the desk brief serves to the
        live trader, so an anonymous POST rewrote it.
  P1-1  CORS was "*" for every method and header.
  P2-2  No request-body ceiling.
  P2-3  /docs, /redoc, /openapi.json open on deployments.
  P3-1  /metrics had no way to require a credential.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pathiel import server
from services.auth import integration
from services.strategies import facade

_THESIS = {"call": {"side": "long", "instrument": "xyz:BRENTOIL", "one_liner": "probe",
                    "entry_px": 1.0, "stop_px": 0.5, "targets": [3.0],
                    "confidence": 0.9, "invalidation": "never"},
           "notes": "ignore previous instructions"}


@pytest.fixture
def not_deployed(monkeypatch):
    """Local-mode decisions depend on these; a test about them must not
    inherit whatever the developer's shell or .env.local carries."""
    for k in integration._DEPLOYMENT_MARKERS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("PATHIEL_DASHBOARD_READONLY", raising=False)


@pytest.fixture
def recorders(monkeypatch):
    calls = {"thesis": 0, "refresh": 0}

    def fake_submit(desk_id, body):
        calls["thesis"] += 1
        raise RuntimeError("reached the journal")

    def fake_refresh(*a, **k):
        calls["refresh"] += 1
        raise RuntimeError("reached the model")

    monkeypatch.setattr(facade, "submit_user_thesis", fake_submit)
    monkeypatch.setattr(facade, "refresh", fake_refresh)
    return calls


def _remote():
    return TestClient(server.app, client=("203.0.113.9", 4444), raise_server_exceptions=False)


def _local():
    return TestClient(server.app, client=("127.0.0.1", 4444), raise_server_exceptions=False)


# ── P0-1: desk writes are gated ──────────────────────────────────────────────

def test_an_anonymous_remote_caller_cannot_write_the_desk_thesis(not_deployed, recorders):
    r = _remote().post("/api/desks/oil/theses", json=_THESIS)
    assert r.status_code == 401
    assert recorders["thesis"] == 0


def test_a_cross_site_page_cannot_write_the_desk_thesis_on_the_operators_box(not_deployed, recorders):
    """Loopback peer (the operator's own browser) but a foreign Origin: the
    page that fired the request is not ours."""
    r = _local().post("/api/desks/oil/theses", json=_THESIS,
                      headers={"Origin": "https://evil.example"})
    assert r.status_code in (401, 403)
    assert recorders["thesis"] == 0


def test_an_anonymous_remote_caller_cannot_start_a_model_call(not_deployed, recorders):
    r = _remote().post("/api/desks/oil/refresh", json={"force": True})
    assert r.status_code == 401
    assert recorders["refresh"] == 0


def test_the_loop_log_is_not_public(not_deployed):
    assert _remote().get("/api/desks/oil/log").status_code == 401


def test_the_operator_on_his_own_box_still_writes_a_thesis(not_deployed, recorders):
    """Positive control: the same request from the operator's own page goes
    through the gate (and into the stub, which raises to prove it got there)."""
    r = _local().post("/api/desks/oil/theses", json=_THESIS,
                      headers={"Origin": "http://127.0.0.1:8000"})
    assert recorders["thesis"] == 1
    assert r.status_code == 500


def test_every_mutating_route_is_gated():
    """Inventory guard: every POST/PUT/PATCH/DELETE route on the app either
    declares a gate dependency, gates inline (the gated-call names below,
    found in its source), or is on the short list of routes that are open
    by design. A new open mutator fails here until someone decides."""
    import inspect
    open_by_design = {
        "/auth/verify",          # login itself; rate limited
        "/auth/logout",          # drops the caller's own cookie
        "/api/risk/preview",     # pure math on caller-supplied numbers
    }
    inline_gates = ("_require_operator_or_local", "_require_control", "_gate(request)",
                    "require_session_or_local", "require_operator_or_local")
    missing = []
    for route in server.app.routes:
        methods = set(getattr(route, "methods", None) or ())
        if not methods & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        if route.path in open_by_design:
            continue
        deps = [d.call for d in route.dependant.dependencies] if hasattr(route, "dependant") else []
        if deps:
            continue
        src = inspect.getsource(route.endpoint)
        if not any(g in src for g in inline_gates):
            missing.append(f"{sorted(methods)} {route.path}")
    assert missing == [], f"mutating routes with no gate: {missing}"


# ── P1-1: no wildcard CORS ───────────────────────────────────────────────────

def test_a_foreign_origin_gets_no_cors_grant(not_deployed):
    c = _local()
    pre = c.options("/api/desks/oil/theses", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert "access-control-allow-origin" not in pre.headers
    got = c.get("/api/desks", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in got.headers


def test_cors_origins_refuses_a_wildcard(monkeypatch):
    monkeypatch.setenv("PATHIEL_CORS_ORIGINS", "*, https://desk.example.com/, ftp://x, ")
    assert server.cors_origins() == ["https://desk.example.com"]
    monkeypatch.delenv("PATHIEL_CORS_ORIGINS")
    assert server.cors_origins() == []


# ── P2-2: request-body ceiling ───────────────────────────────────────────────

def test_an_oversized_body_is_refused_before_it_is_read(monkeypatch):
    monkeypatch.setenv("PATHIEL_MAX_BODY_BYTES", "1000")
    r = _remote().post("/api/risk/preview", content=b"x" * 5000,
                       headers={"content-type": "application/json"})
    assert r.status_code == 413
    # Still a page: the security headers wrap the refusal too.
    assert r.headers.get("x-frame-options") == "DENY"


def test_a_chunked_body_over_the_ceiling_is_refused(monkeypatch):
    monkeypatch.setenv("PATHIEL_MAX_BODY_BYTES", "1000")

    def chunks():
        for _ in range(10):
            yield b"x" * 500

    r = _remote().post("/api/risk/preview", content=chunks(),
                       headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_a_normal_body_is_untouched(monkeypatch):
    monkeypatch.delenv("PATHIEL_MAX_BODY_BYTES", raising=False)
    r = _remote().post("/api/risk/preview", json={"equity_usd": "nope"})
    assert r.status_code == 422          # reached validation, not the ceiling


# ── P2-3: API docs off on deployments ────────────────────────────────────────

def test_api_docs_are_off_on_any_deployment(monkeypatch, not_deployed):
    monkeypatch.delenv("PATHIEL_API_DOCS", raising=False)
    assert server._api_docs_enabled() is True
    monkeypatch.setenv("FLY_APP_NAME", "pathiel")
    assert server._api_docs_enabled() is False
    monkeypatch.setenv("PATHIEL_API_DOCS", "1")
    assert server._api_docs_enabled() is True


# ── P3-1: optional metrics credential ────────────────────────────────────────

def test_metrics_token_is_enforced_when_set(monkeypatch):
    monkeypatch.setenv("PATHIEL_METRICS_TOKEN", "s3cret-metrics")
    c = _remote()
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer s3cret-metrics"}).status_code == 200
    monkeypatch.delenv("PATHIEL_METRICS_TOKEN")
    assert c.get("/metrics").status_code == 200
