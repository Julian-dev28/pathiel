"""Shared fixtures for services/intel's test suite.

Every test gets an isolated `PATHIEL_STATE_DIR` (a fresh tmp_path) — never
the live state dir, per docs/overhaul/SWARM_RULES.md section 1. This is
`autouse` so a test can't accidentally skip it and touch real state.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contracts.strategies.models import DeskSpec, Instrument

FIXTURES = Path(__file__).parent / "fixtures"
OIL_SPEC_PATH = (Path(__file__).parent.parent.parent.parent
                  / "contracts" / "strategies" / "fixtures" / "oil_spec.json")

T0 = datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    yield tmp_path


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()


def fixture_json(name: str):
    return json.loads(fixture_text(name))


@pytest.fixture
def oil_spec() -> DeskSpec:
    return DeskSpec.model_validate(json.loads(OIL_SPEC_PATH.read_text()))


@pytest.fixture
def btc_spec() -> DeskSpec:
    """A minimal crypto desk — no equivalent fixture exists yet in
    contracts/strategies/fixtures/ (only oil does), so this is
    hand-built for test coverage per the brief's instruction to
    hand-label/eval "oil, btc at least." Mirrors the oil fixture's shape."""
    return DeskSpec(
        id="btc", name="Bitcoin", tagline="BTC perp on Hyperliquid.",
        instruments=[Instrument(coin="BTC", label="BTC", role="primary")],
        news_queries=["bitcoin price", "bitcoin ETF", "BTC"],
        policy_topics=["digital asset market structure"],
        prediction_market_queries=["bitcoin price", "bitcoin ETF"],
        catalyst_kinds=["cftc_cot"],
        horizon_days=7,
    )


@pytest.fixture
def ai_equity_spec() -> DeskSpec:
    """A minimal AI-equities desk, used only to exercise the best-effort
    earnings-date catalyst generator (desk_rules.is_equity_desk)."""
    return DeskSpec(
        id="ai_equities", name="AI Equities", tagline="AI hardware/software names.",
        instruments=[Instrument(coin="NVDA", label="Nvidia", role="primary")],
        catalyst_kinds=["earnings"],
        horizon_days=14,
    )


def _network_allowed(config) -> bool:
    expr = (config.getoption("markexpr", "") or "").replace(" ", "")
    return "online" in expr and "notonline" not in expr


_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@pytest.fixture(autouse=True, scope="session")
def _no_real_network_in_gate_tests(request):
    """Block real outbound HTTP for the rest of the process unless online
    tests are selected.

    build_packet abandons stragglers past its deadline (shutdown(wait=False)).
    A straggler can wake after its test's http mocks were torn down and hit
    the real API: seen 2026-10-17 as a real GDELT 429 printed after pytest's
    summary. The guard sits on httpx's real network transport, so tests that
    mock httpx.Client.request and the in-process TestClient are unaffected,
    loopback stays allowed, and it is deliberately never undone: a straggler
    can outlive session teardown too.
    """
    if _network_allowed(request.config):
        yield
        return
    import httpx

    real = httpx.HTTPTransport.handle_request
    if getattr(real, "_pathiel_gate_guard", False):
        yield
        return

    def guarded(self, req):
        if req.url.host in _LOOPBACK:
            return real(self, req)
        raise httpx.ConnectError(f"real network disabled in gate tests: {req.url}", request=req)

    guarded._pathiel_gate_guard = True
    httpx.HTTPTransport.handle_request = guarded
    yield
