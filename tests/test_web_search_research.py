"""Gate tests for the selective web-search research path (2026-07-11).

The claim under test: web search fires ONLY for big movers and held coins,
is hot-killable, tags the analysis for calibration, and reaches the brain as
a keyword — never as prompt-only decoration.
"""
import json

from hermes_trader.agents import research


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


def test_web_block_demands_real_headlines():
    assert "NEVER invent" in research._WEB_SEARCH_BLOCK
    assert "WebSearch" in research._WEB_SEARCH_BLOCK
