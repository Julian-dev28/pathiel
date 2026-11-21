from __future__ import annotations

from services.research_mcp.schemas import TOOL_NAMES, TOOLS

EXPECTED_NAMES = {"research_brief", "events", "prediction_odds", "fred_series", "headlines", "levels"}


def test_tool_names_match_the_brief():
    assert set(TOOL_NAMES) == EXPECTED_NAMES
    assert len(TOOL_NAMES) == len(set(TOOL_NAMES)), "no duplicate tool names"


def test_every_tool_has_a_name_description_and_input_schema():
    for tool in TOOLS:
        assert isinstance(tool["name"], str) and tool["name"]
        assert isinstance(tool["description"], str) and len(tool["description"]) > 20
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)


def test_required_coin_tools_mark_coin_required():
    for name in ("research_brief", "levels"):
        schema = next(t["inputSchema"] for t in TOOLS if t["name"] == name)
        assert schema.get("required") == ["coin"]


def test_fred_series_requires_series_id_only():
    schema = next(t["inputSchema"] for t in TOOLS if t["name"] == "fred_series")
    assert schema.get("required") == ["series_id"]
    assert "start" in schema["properties"] and "end" in schema["properties"]


def test_neither_coin_only_tools_requires_either_argument():
    """prediction_odds and headlines accept coin OR a free-text alternative;
    neither is individually required at the schema level (the tool itself
    raises a clear error if both are missing -- see test_tools_*.py)."""
    for name in ("prediction_odds", "headlines"):
        schema = next(t["inputSchema"] for t in TOOLS if t["name"] == name)
        assert not schema.get("required")


def test_no_tool_description_mentions_a_trading_action():
    banned = ("place_bracket", "close_position", "set_stop", "set_target", "cancel_order",
             "sweep_brackets", "set_leverage")
    for tool in TOOLS:
        text = tool["description"].lower()
        for word in banned:
            assert word not in text, f"{tool['name']}'s description mentions {word!r}"
