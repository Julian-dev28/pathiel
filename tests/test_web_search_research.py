"""Gate tests for the selective web-search research path (2026-07-11).

The claim under test: web search fires ONLY for big movers and held coins,
is hot-killable, tags the analysis for calibration, and reaches the brain as
a keyword — never as prompt-only decoration.
"""
import ast
import json
from pathlib import Path

import pytest

from hermes_trader.agents import research
from hermes_trader.models.types import Candle


def _cfg(enabled=True, min_move=8.0, held=True):
    return {"ai_brain": {"web_search": {"enabled": enabled, "min_move_pct": min_move, "held": held}}}


def test_disabled_by_default_without_config():
    assert research._should_web_search("BTC", {"daily_move_pct": 50}, [], {}) is False


def test_hot_kill():
    assert research._should_web_search("BTC", {"daily_move_pct": 50}, [], _cfg(enabled=False)) is False


def test_mover_threshold_and_sign():
    assert research._should_web_search("X", {"daily_move_pct": 8.0}, [], _cfg()) is True
    assert research._should_web_search("X", {"daily_move_pct": -9.5}, [], _cfg()) is True
    assert research._should_web_search("X", {"daily_move_pct": 7.9}, [], _cfg()) is False
    assert research._should_web_search("X", {"daily_move_pct": None}, [], _cfg()) is False


def test_held_coin_fires_regardless_of_move():
    held = [{"coin": "LIT", "side": "long", "size_usd": 20.0}]
    assert research._should_web_search("LIT", {"daily_move_pct": 0.1}, held, _cfg()) is True
    assert research._should_web_search("ETH", {"daily_move_pct": 0.1}, held, _cfg()) is False
    # held arm individually killable
    assert research._should_web_search("LIT", {"daily_move_pct": 0.1}, held, _cfg(held=False)) is False


def test_call_ai_forwards_web_search_to_configured_brain(monkeypatch):
    seen = {}

    class FakeBrain:
        provider = "claude_cli"

        def complete(self, system_prompt, user_message, web_search=False):
            seen["web_search"] = web_search
            return json.dumps({"verdict": "PASS", "confidence": 0.1})

    monkeypatch.setattr(research, "get_brain", lambda sel: FakeBrain())
    out = research._call_ai("S", "U", provider="claude_cli", web_search=True)
    assert seen["web_search"] is True
    assert "verdict" in out


def test_call_ai_injected_brain_never_gets_web_search_kwarg(monkeypatch):
    """Injected brains (MCP sampling) have an unknown signature — the kwarg
    must not reach them or a legacy two-arg brain would raise TypeError."""

    class LegacyBrain:
        provider = "mcp"

        def complete(self, system_prompt, user_message):  # no kwarg on purpose
            return json.dumps({"verdict": "PASS", "confidence": 0.1})

    out = research._call_ai("S", "U", brain=LegacyBrain(), web_search=True)
    assert "verdict" in out


def test_web_block_demands_real_fresh_headlines():
    assert "NEVER invent" in research._WEB_SEARCH_BLOCK
    # stale-catalyst incident (ARB 2026-07-12): freshness must be explicit
    assert "FRESHNESS" in research._WEB_SEARCH_BLOCK
    assert "14 days" in research._WEB_SEARCH_BLOCK
    # resolved-event incident (PUMP 2026-07-15): a Jul-9 PREVIEW of a Jul-12
    # unlock is only 6 days old (passes the 14-day age check) but the event
    # it describes has already happened — must be caught separately
    assert "RESOLVED event" in research._WEB_SEARCH_BLOCK
    assert "outcome unconfirmed" in research._WEB_SEARCH_BLOCK


def test_web_telemetry_does_not_confuse_request_with_actual_use(monkeypatch):
    """A model that answers from memory produces neither a request count nor
    citations — that (and only that) reads as used=False."""
    completion = object()
    monkeypatch.setattr(research, "completion_web_search_requests", lambda result: 0)
    monkeypatch.setattr(research, "completion_citations", lambda result: [])

    out = research._web_search_telemetry(completion, requested=True)

    assert out == {
        "web_search_requested": True,
        "web_search_used": False,
        "web_search_request_count": 0,
        "web_search_citations": [],
    }


def test_web_telemetry_citations_alone_prove_use(monkeypatch):
    """OpenRouter returns citation annotations but NO server_tool_use count
    (verified live 2026-07-12) — citations must count as search evidence."""
    completion = object()
    monkeypatch.setattr(research, "completion_web_search_requests", lambda result: 0)
    monkeypatch.setattr(research, "completion_citations",
                        lambda result: ["Bitcoin — https://coinmarketcap.com/currencies/bitcoin/"])

    out = research._web_search_telemetry(completion, requested=True)

    assert out["web_search_used"] is True
    assert out["web_search_request_count"] == 0
    assert out["web_search_citations"] == [
        "Bitcoin — https://coinmarketcap.com/currencies/bitcoin/"
    ]


def test_web_telemetry_bounds_provider_citations(monkeypatch):
    completion = object()
    raw = [f"https://source.test/{i}/" + ("x" * 600) for i in range(8)]
    monkeypatch.setattr(research, "completion_web_search_requests", lambda result: 2)
    monkeypatch.setattr(research, "completion_citations", lambda result: raw)

    out = research._web_search_telemetry(completion, requested=True)

    assert out["web_search_used"] is True
    assert out["web_search_request_count"] == 2
    assert len(out["web_search_citations"]) == research.MAX_WEB_CITATIONS
    assert all(len(c) <= research.MAX_WEB_CITATION_CHARS for c in out["web_search_citations"])


@pytest.mark.parametrize("actual_requests, expected_used", [(0, False), (2, True)])
def test_research_analysis_persists_requested_and_actual_use_separately(
    monkeypatch, actual_requests, expected_used
):
    candles = [
        Candle(t=i * 3_600_000, o=100 + i, h=101 + i, l=99 + i, c=100.5 + i, v=1_000)
        for i in range(40)
    ]
    completion = object()
    captured = []

    monkeypatch.setattr(research, "fetch_hl_candles", lambda *args, **kwargs: candles)
    monkeypatch.setattr(research, "_fetch_funding_rate", lambda coin: "0.0000%/hr")
    monkeypatch.setattr(research, "_fetch_news", lambda coin: "no news")
    monkeypatch.setattr(research, "read_agent_config", lambda: _cfg())
    monkeypatch.setattr(research, "resolve_user_address", lambda: None)
    monkeypatch.setattr(research, "selected_ai_brain_provider", lambda config=None: "claude_cli")
    monkeypatch.setattr(research, "build_system_prompt", lambda *args: "system")
    monkeypatch.setattr(research.memory, "get_win_rate", lambda: {"rate": 0, "total": 0})
    monkeypatch.setattr(research.memory, "record_analysis", captured.append)
    monkeypatch.setattr(research, "_call_ai", lambda *args, **kwargs: completion)
    monkeypatch.setattr(
        research,
        "completion_text",
        lambda result: json.dumps({"verdict": "PASS", "confidence": 0.3}),
    )
    monkeypatch.setattr(
        research, "completion_web_search_requests", lambda result: actual_requests
    )
    # No-search completions carry no citations either (both channels empty);
    # citations alone flip used=True (see the citations-prove-use test).
    monkeypatch.setattr(
        research, "completion_citations",
        lambda result: ["https://source.test/story"] if actual_requests else [],
    )

    analysis = research.research(
        "TEST",
        {
            "id": "perception-1",
            "coin": "TEST",
            "mid": 123.0,
            "daily_move_pct": 12.0,
            "daily_volume_usd": 2_000_000,
            "composite_score": 50,
            "triggers": [],
        },
    )

    assert analysis["web_search_requested"] is True
    assert analysis["web_search_used"] is expected_used
    assert analysis["web_search_request_count"] == actual_requests
    expected_citations = ["https://source.test/story"] if expected_used else []
    assert analysis["web_search_citations"] == expected_citations
    assert captured == [analysis]


def test_trading_loop_research_event_persists_calibration_fields_without_importing_loop():
    """The loop has no main guard, so inspect its research log call as source."""
    loop_path = Path(__file__).resolve().parents[1] / "scripts" / "trading_loop.py"
    tree = ast.parse(loop_path.read_text())
    research_event_keys = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "log_event":
            continue
        payload = node.args[0]
        if not isinstance(payload, ast.Dict):
            continue
        keys = {
            key.value
            for key in payload.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        values = {
            value.value
            for value in payload.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        }
        if "event" in keys and "research" in values:
            research_event_keys |= keys

    assert {
        "analysis_id",
        "web_search_requested",
        "web_search_used",
        "web_search_request_count",
        "web_search_citations",
        "daily_move_pct",
    } <= research_event_keys
