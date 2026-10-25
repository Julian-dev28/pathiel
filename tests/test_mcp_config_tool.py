"""pathiel.agents.config_store's read/write/merge behavior, and the MCP
`config` tool handler built on top of it.

Moved out of tests/test_cleanup.py 2026-10-23 (wave 2): that file was mostly
coverage for the retired autonomous engine (deleted with it), but these
tests cover config_store.py and the MCP config handler, both kept. The
DEFAULT_CONFIG key names they assert on are updated for wave 2's trimmed
config (the per-trade sizing/gating knobs the retired executor read are gone
— see config_store.py's DEFAULT_CONFIG for the full list).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# ── config_store.read_agent_config / write_agent_config / merge_agent_config ──

def test_config_store_rejects_non_object_json(monkeypatch, tmp_path):
    from pathiel.agents import config_store

    path = tmp_path / ".agent-config.json"
    path.write_text("[]")
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(path))

    cfg = config_store.read_agent_config()
    assert cfg["mode"] == "OFF"
    assert cfg["hip3_dex_allowlist"] == ["xyz"]


def test_config_store_normalizes_partial_live_config(monkeypatch, tmp_path):
    from pathiel.agents import config_store

    path = tmp_path / ".agent-config.json"
    path.write_text('{"mode":"LIVE","enable_hip3":false}')
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(path))

    cfg = config_store.read_agent_config()
    assert cfg["mode"] == "LIVE"
    assert cfg["enable_hip3"] is False                 # explicit override kept
    assert cfg["coin_blocklist"] == ["TON", "TRX"]      # untouched key still defaults
    assert cfg["hip3_dex_allowlist"] == ["xyz"]         # untouched list still defaults


def test_config_store_writes_parent_directory(monkeypatch, tmp_path):
    from pathiel.agents import config_store

    path = tmp_path / "nested" / ".agent-config.json"
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(path))

    config_store.write_agent_config({"mode": "LIVE"})

    assert config_store.read_agent_config()["mode"] == "LIVE"


def test_config_store_deep_merge_preserves_nested_safety_keys():
    from pathiel.agents.config_store import merge_agent_config

    # A hand-added nested block stands in for the ai_brain block this test
    # used to merge (deleted 2026-10-25); the merge is generic over any
    # nested object, which is the property under test.
    base = {
        "mode": "LIVE",
        "experiment": {
            "provider": "a",
            "timeout_s": 120,
            "inner": {"command": "x", "max_turns": 1},
        },
    }

    merged = merge_agent_config(base, {"experiment": {"timeout_s": 30}})

    assert merged["experiment"]["timeout_s"] == 30
    assert merged["experiment"]["provider"] == "a"
    assert merged["experiment"]["inner"]["command"] == "x"
    assert base["experiment"]["timeout_s"] == 120   # base is not mutated

    ignored = merge_agent_config(base, {"experiment": None})
    assert ignored["experiment"]["provider"] == "a"
    assert ignored["experiment"]["timeout_s"] == 120


# ── the MCP `config` tool handler (scripts/pathiel-mcp-server.py) ───────────

def _load_mcp_named(name):
    script = ROOT / "scripts" / "pathiel-mcp-server.py"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec and spec.loader
    mcp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp)
    return mcp


def test_mcp_config_updates_any_existing_key_not_on_the_allowlist():
    """Regression 2026-10-23: a key already in the live config but not on
    _DIRECT_KEYS/_NESTED_KEYS (e.g. a hand-added experiment block, or
    risk_profile) was dropped silently and the call returned the unchanged
    config, which read as success."""
    from pathiel.agents import config_store

    mcp = _load_mcp_named("pathiel_mcp_server_config_passthrough_test")
    try:
        config_store.write_agent_config({
            "mode": "OFF",
            "an_experiment": {"enabled": True, "notional_usd": 11.0},
            "risk_profile": {"preset": "auto", "max_leverage": 3.0},
        })
        out = json.loads(mcp.handle_config({
            "an_experiment": {"enabled": False},
            "risk_profile": {"max_leverage": 10.0},
        }))
        saved = config_store.read_agent_config()
        for cfg in (out, saved):
            assert cfg["an_experiment"]["enabled"] is False
            assert cfg["an_experiment"]["notional_usd"] == 11.0  # deep-merged, sibling kept
            assert cfg["risk_profile"]["max_leverage"] == 10.0
            assert cfg["risk_profile"]["preset"] == "auto"
    finally:
        config_store.write_agent_config({"mode": "OFF"})


def test_mcp_config_rejects_unknown_key_and_writes_nothing():
    from pathiel.agents import config_store

    mcp = _load_mcp_named("pathiel_mcp_server_config_unknown_test")
    try:
        config_store.write_agent_config({"mode": "OFF", "an_experiment": {"enabled": True}})
        out = json.loads(mcp.handle_config({"an_experiment": {"enabled": False},
                                            "an_experimnt": {"enabled": False}}))
        assert out["status"] == "error" and "an_experimnt" in out["error"]
        assert config_store.read_agent_config()["an_experiment"]["enabled"] is True
        out = json.loads(mcp.handle_config({"an_experiment": False}))
        assert out["status"] == "error"
    finally:
        config_store.write_agent_config({"mode": "OFF"})


def test_mcp_config_accepts_nested_objects_sent_as_json_strings():
    """MCP clients send untyped nested params as JSON strings. 2026-10-23:
    {"risk_profile": "{\\"max_open_positions\\": 1}"} was refused as "must be
    an object" and the sizing stayed split across 3 slots."""
    from pathiel.agents import config_store

    mcp = _load_mcp_named("pathiel_mcp_server_config_string_test")
    try:
        config_store.write_agent_config({
            "mode": "OFF",
            "risk_profile": {"preset": "auto", "max_leverage": 10.0},
            "an_experiment": {"enabled": True},
        })
        out = json.loads(mcp.handle_config({"risk_profile": '{"max_open_positions": 1}',
                                            "an_experiment": '{"enabled": false}'}))
        assert out["risk_profile"] == {"preset": "auto", "max_leverage": 10.0, "max_open_positions": 1}
        assert out["an_experiment"]["enabled"] is False
        bad = json.loads(mcp.handle_config({"risk_profile": "not json"}))
        assert bad["status"] == "error"
    finally:
        config_store.write_agent_config({"mode": "OFF"})


def test_mcp_config_accepts_list_fields_sent_as_json_strings():
    """2026-10-25: {"active_desks": '["oil"]'} (the only way an untyped
    param reaches the tool from some clients) was saved as the string
    '["oil"]'. A list field in the live config takes a JSON list string, and
    refuses anything that is not a list, writing nothing."""
    from pathiel.agents import config_store

    mcp = _load_mcp_named("pathiel_mcp_server_config_list_string_test")
    try:
        config_store.write_agent_config({"mode": "OFF", "active_desks": ["oil", "metals"]})
        out = json.loads(mcp.handle_config({"active_desks": '["oil"]'}))
        assert out["active_desks"] == ["oil"]
        assert config_store.read_agent_config()["active_desks"] == ["oil"]
        for bad in ("oil", '{"desk": "oil"}', "not json"):
            res = json.loads(mcp.handle_config({"active_desks": bad}))
            assert res["status"] == "error" and "must be a list" in res["error"], bad
        assert config_store.read_agent_config()["active_desks"] == ["oil"]
    finally:
        config_store.write_agent_config({"mode": "OFF"})
