"""The plugin directory's build of pathiel (scripts/publish_directory_plugin.py).

The directory refused v0.6.0: from an organization that is not Verified it
takes no MCP server that runs on the user's computer and no file outside
Markdown, text, JSON, YAML and images. These tests pin the build to what it
accepts, and the policy check to the violations that refusal named.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("publish_directory_plugin", ROOT / "scripts" / "publish_directory_plugin.py")
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

import contracts.mcp_public as public_toolset  # noqa: E402


def _version() -> str:
    return re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "pathiel" / "__init__.py").read_text()).group(1)


@pytest.fixture(scope="module")
def tree():
    return D.contents(D.B.INDEX)


def test_the_build_holds_markdown_json_and_the_icon_only(tree):
    assert set(tree) == {".claude-plugin/plugin.json", ".claude-plugin/icon.png", "commands/setup.md",
                         "commands/share.md", "README.md", "LICENSE"}
    assert D.policy_problems(tree) == []


def test_the_manifest_bundles_no_server(tree):
    manifest = json.loads(tree[".claude-plugin/plugin.json"])
    for key in ("mcpServers", "displayName", "hooks", "lspServers"):
        assert key not in manifest, key
    assert manifest["name"] == "pathiel" and manifest["version"] == _version()
    assert manifest["privacyPolicyUrl"].startswith("https://")
    full = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert "mcpServers" in full, "the repo marketplace's own plugin keeps its bundled server"


def test_setup_installs_the_released_container_and_runs_no_script(tree):
    setup = tree["commands/setup.md"].decode()
    image = json.loads((ROOT / "server.json").read_text())["packages"][0]["identifier"]
    assert image == f"ghcr.io/julian-dev28/pathiel-mcp:{_version()}"
    assert image in setup and "claude mcp add pathiel" in setup
    assert "CLAUDE_PLUGIN_ROOT" not in setup and "pathiel-py" not in setup
    assert f"{len(public_toolset.NAMES)} tools" in setup
    for tool in re.findall(r"`([a-z]+_[a-z_]+)", setup):
        if tool in ("pathiel_network",):
            continue
        assert tool in public_toolset.NAMES or tool.isupper(), tool


def test_setup_and_readme_carry_the_disclosures(tree, copy_lint):
    for path in ("commands/setup.md", "README.md"):
        text = tree[path].decode()
        flat = re.sub(r"\s+", " ", re.sub(r"\n\s*>\s*", " ", text))   # read through blockquotes
        for must in ("mainnet", "real money", "not financial advice", "US persons"):
            assert must.lower() in flat.lower(), (path, must)
        assert copy_lint.style_hits(text) == [], (path, copy_lint.style_hits(text))
        assert copy_lint.claim_hits(text) == [], path


@pytest.mark.parametrize("path,data,needle", [
    ("scripts/run.py", b"print(1)\n", "not Markdown"),
    ("commands/x.md", b"#!/bin/sh\necho\n", "#!"),
    ("commands/y.md", b"a\x00b", "NUL"),
    ("commands/z.md", "run ${CLAUDE_PLUGIN_ROOT}/scripts/x".encode(), "bundled script"),
    (".claude-plugin/extra.json", b"{}", ".claude-plugin holds only"),
    (".mcp.json", b"{}", "not allowed"),
    ("requirements.lock", b"x==1\n", "not Markdown"),
])
def test_the_policy_check_catches_what_the_directory_refused(tree, path, data, needle):
    bad = dict(tree)
    bad[path] = data
    problems = D.policy_problems(bad)
    assert any(needle in p for p in problems), problems


def test_a_server_left_in_the_manifest_is_caught(tree):
    bad = dict(tree)
    manifest = json.loads(bad[".claude-plugin/plugin.json"])
    manifest["mcpServers"] = {"pathiel": {"command": "bash", "args": ["x"]}}
    bad[".claude-plugin/plugin.json"] = json.dumps(manifest).encode()
    assert any("mcpServers" in p for p in D.policy_problems(bad))
