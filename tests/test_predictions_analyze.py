"""On-demand /predictions Analyze endpoint + the single-market analyze path.

Integration across BOTH brain providers: the endpoint runs a real BrainForecaster
whose only injected seam is the raw model transport, exercised as a claude_cli
JSON envelope AND an openrouter text reply. Proves the parse + routing work for
whichever provider AI_BRAIN_PROVIDER selects, without a paid call."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_trader import dashboard as db
from services.polymarket_scout import ask, board, ledger
from services.polymarket_scout.forecaster import BrainForecaster, StubForecaster


# ── the two provider transports ──────────────────────────────────────────────
class ClaudeCliBrain:
    """Shape of hermes_trader.agents.ai_brain.ClaudeCliBrain: returns the model's
    text (the CLI envelope's `result`), ending in the verdict JSON."""
    provider = "claude_cli"

    def __init__(self, yes_prob):
        self.yes_prob = yes_prob

    def complete(self, system, user, web_search=False):
        return ('Based on current polling and the fundamentals I searched, '
                'here is my estimate.\n'
                f'{{"verdict": "YES", "yes_prob": {self.yes_prob}, '
                f'"reasoning": "sourced claude_cli read"}}')


class OpenRouterBrain:
    """Shape of OpenRouterBrain: returns the assistant message text, verdict JSON
    on the final line."""
    provider = "openrouter"

    def __init__(self, yes_prob):
        self.yes_prob = yes_prob

    def complete(self, system, user, web_search=False):
        return (f'{{"verdict": "NO", "yes_prob": {self.yes_prob}, '
                f'"reasoning": "openrouter read"}}')


def _row(**over):
    r = {"market_id": "42", "question": "Will the ceasefire hold?",
         "event_title": "Israel x Iran", "tags": ["geopolitics"], "yes": 0.30,
         "ask": 0.32, "bid": 0.29, "yes_token": "y", "no_token": "n",
         "end_date": "2026-08-10T00:00:00Z", "url": "u", "live": False,
         "score": "", "breaking": False, "sport": ""}
    r.update(over)
    return r


def _board(rows=None):
    return {"trending": rows if rows is not None else [_row()],
            "breaking": [], "sports": [], "longshots": [], "edges": []}


# ── single-market path, both providers ───────────────────────────────────────
@pytest.mark.parametrize("brain,exp_side,exp_prob", [
    (ClaudeCliBrain(0.72), "YES", 0.72),      # 0.72 vs mkt 0.30 = +42pp -> BUY YES
    (OpenRouterBrain(0.05), "NO", 0.05),      # 0.05 vs mkt 0.30 = -25pp -> BUY NO
])
def test_analyze_row_routes_through_both_providers(brain, exp_side, exp_prob):
    v = ask.analyze_row(_row(), BrainForecaster(brain=brain))
    assert v["llm_yes"] == exp_prob
    assert v["side"] == exp_side
    assert v["recorded"] is False              # analyze-only by default


def test_analyze_row_reports_a_declining_brain():
    class Dead:
        provider = "x"

        def complete(self, *a, **k):
            return ""
    v = ask.analyze_row(_row(), BrainForecaster(brain=Dead()))
    assert v["llm_yes"] is None and "declined" in v["skip_reason"]


def test_analyze_row_holds_the_edge_threshold_on_a_click():
    # 0.36 vs mkt 0.30 = 6pp, under the 15pp trending bar -> no side, not recorded
    v = ask.analyze_row(_row(), StubForecaster(lambda q, d: (0.36, "meh")))
    assert v["side"] is None and v["recorded"] is False
    assert "threshold" in v["skip_reason"]


def test_analyze_market_id_finds_across_feeds():
    payload = _board(rows=[]) | {"sports": [_row(market_id="9", live=True)]}
    v = ask.analyze_market_id("9", StubForecaster(lambda q, d: (0.9, "x")), payload)
    assert v is not None and v["market_id"] == "9"
    assert ask.analyze_market_id("nope", StubForecaster(lambda q, d: (0.9, "x")), payload) is None


# ── the HTTP endpoint ────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("HERMES_OPERATOR_TOKEN", "s3cret")
    app = FastAPI()
    db.register_routes(app)
    return TestClient(app)


def test_endpoint_requires_the_operator_token(client, monkeypatch):
    monkeypatch.setattr(board, "load", lambda *a, **k: _board())
    r = client.post("/api/dashboard/predictions/analyze?market_id=42")
    assert r.status_code == 401


def _poll(client, job_id, tries=50):
    """Drive the async job to completion (the work runs on a daemon thread)."""
    import time as _t
    for _ in range(tries):
        r = client.get(f"/api/dashboard/predictions/analyze/result?job_id={job_id}",
                       headers={"X-Operator-Token": "s3cret"})
        assert r.status_code == 200
        j = r.json()
        if j["status"] != "running":
            return j
        _t.sleep(0.02)
    raise AssertionError("job never finished")


@pytest.mark.parametrize("brain,exp_side", [
    (ClaudeCliBrain(0.80), "YES"),
    (OpenRouterBrain(0.02), "NO"),
])
def test_endpoint_analyzes_with_either_provider(client, monkeypatch, brain, exp_side):
    monkeypatch.setattr(board, "load", lambda *a, **k: _board())
    # patch the forecaster the background job constructs so no real model is called
    import services.polymarket_scout.forecaster as fmod
    monkeypatch.setattr(fmod, "BrainForecaster", lambda *a, **k: BrainForecaster(brain=brain))
    r = client.post("/api/dashboard/predictions/analyze?market_id=42",
                    headers={"X-Operator-Token": "s3cret"})
    assert r.status_code == 200
    job = r.json()
    assert job["status"] == "running" and job["job_id"]
    result = _poll(client, job["job_id"])
    assert result["status"] == "done"
    assert result["verdict"]["side"] == exp_side and result["verdict"]["llm_yes"] is not None


def test_endpoint_404s_for_a_market_not_on_the_board(client, monkeypatch):
    monkeypatch.setattr(board, "load", lambda *a, **k: _board())
    r = client.post("/api/dashboard/predictions/analyze?market_id=nope",
                    headers={"X-Operator-Token": "s3cret"})
    assert r.status_code == 404


def test_result_poll_404s_for_an_unknown_job(client):
    r = client.get("/api/dashboard/predictions/analyze/result?job_id=nope",
                   headers={"X-Operator-Token": "s3cret"})
    assert r.status_code == 404


def test_endpoint_does_not_record_by_default(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(board, "load", lambda *a, **k: _board())
    import services.polymarket_scout.forecaster as fmod
    monkeypatch.setattr(fmod, "BrainForecaster",
                        lambda *a, **k: BrainForecaster(brain=ClaudeCliBrain(0.85)))
    r = client.post("/api/dashboard/predictions/analyze?market_id=42",
                    headers={"X-Operator-Token": "s3cret"})
    result = _poll(client, r.json()["job_id"])
    assert result["status"] == "done" and result["verdict"]["recorded"] is False
    assert ledger.load() == []                 # analyze-only wrote nothing


def test_predictions_page_ships_the_analyze_button(client):
    r = client.get("/predictions").text
    assert 'class="analyze"' in r and "analyzeMarket" in r
    assert "/api/dashboard/predictions/analyze" in r
    assert "X-Operator-Token" in r             # the click sends the operator token
