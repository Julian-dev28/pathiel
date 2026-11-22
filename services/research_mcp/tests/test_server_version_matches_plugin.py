"""The version the research server reports in initialize must match its
plugin.json (2026-11-22: plugin.json said 0.2.0, the server still said 0.1.0)."""
import json
import pathlib

from services.research_mcp import server

PLUGIN_JSON = pathlib.Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json"


def test_server_version_matches_plugin_json():
    assert server.SERVER_VERSION == json.loads(PLUGIN_JSON.read_text())["version"]
