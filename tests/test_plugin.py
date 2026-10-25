"""The Claude Code plugin packaging (2026-10-25): this repo is its own plugin
marketplace. `claude plugin marketplace add Julian-dev28/pathiel` then
`claude plugin install pathiel@pathiel` gives a user the MCP server, the
desk-tick skill and /pathiel:setup, with keys and state under ~/.pathiel and
the Python environment under the plugin data dir.

Everything here is offline and deterministic. The one check that needs the
`claude` binary skips when it is not on PATH (CI).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
MARKET_JSON = ROOT / ".claude-plugin" / "marketplace.json"
MCP_WRAPPER = ROOT / "scripts" / "pathiel-mcp"
PY_WRAPPER = ROOT / "scripts" / "pathiel-py"
PLUGIN_ENV = ROOT / "scripts" / "_plugin_env.sh"


def _manifest() -> dict:
    return json.loads(PLUGIN_JSON.read_text())


def _marketplace() -> dict:
    return json.loads(MARKET_JSON.read_text())


# ── manifests ────────────────────────────────────────────────────────────────

def test_the_plugin_and_marketplace_names_agree_and_the_version_is_the_packages():
    """The install id is <entry name>@<marketplace name>; a mismatch between
    the entry and the manifest is the documented way an install fails."""
    from pathiel import __version__
    m, mk = _manifest(), _marketplace()
    assert m["name"] == "pathiel"
    assert mk["name"] == "pathiel"
    entries = mk["plugins"]
    assert len(entries) == 1 and entries[0]["name"] == m["name"]
    assert entries[0]["source"] in ("./", "."), "this repo is the plugin; the source is its own root"
    assert m["version"] == __version__, "bump plugin.json with pathiel/__init__.py"
    assert m["author"]["name"] and mk["owner"]["name"]


def test_the_plugin_manifest_has_no_component_paths_outside_the_defaults():
    """Skills, commands and the MCP wrapper all sit in their default
    locations; the manifest only names the MCP server, so a future field
    cannot quietly replace the default scans."""
    m = _manifest()
    assert set(m) <= {"name", "displayName", "version", "description", "author", "homepage",
                      "repository", "license", "keywords", "mcpServers"}, sorted(m)
    assert (ROOT / "skills" / "desk-tick" / "SKILL.md").is_file()
    assert (ROOT / "skills" / "pathiel-agent" / "SKILL.md").is_file()
    assert (ROOT / "commands" / "setup.md").is_file()
    assert sorted(p.name for p in (ROOT / ".claude-plugin").iterdir()) == ["marketplace.json", "plugin.json"]


# ── the MCP entry point ──────────────────────────────────────────────────────

def test_the_mcp_server_runs_through_the_plugin_wrapper():
    m = _manifest()
    server = m["mcpServers"]["pathiel"]
    assert server["command"] == "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-mcp"
    assert server.get("args", []) == []
    for path in (MCP_WRAPPER, PY_WRAPPER):
        assert path.is_file(), path
        assert path.stat().st_mode & stat.S_IXUSR, f"{path.name} is not executable"
    assert PLUGIN_ENV.is_file()


def test_the_wrappers_parse_and_keep_stdout_clean_for_the_mcp_transport():
    """`bash -n` on all three, and every echo goes to stderr: one stray byte on
    stdout before the exec breaks the MCP handshake."""
    for path in (PLUGIN_ENV, MCP_WRAPPER, PY_WRAPPER):
        subprocess.run(["bash", "-n", str(path)], check=True)
        for line in path.read_text().splitlines():
            s = line.strip()
            if s.startswith(("echo", "printf")) and "sha256" not in s:
                assert ">&2" in s, f"{path.name}: writes to stdout: {s}"
    text = MCP_WRAPPER.read_text()
    assert "exec " in text and "pathiel-mcp-server.py" in text
    assert "--bootstrap" in text


def test_the_wrappers_put_keys_and_state_outside_the_plugin_root():
    """The plugin root is a cache directory Claude Code replaces on update."""
    env = PLUGIN_ENV.read_text()
    assert 'PATHIEL_HOME="${PATHIEL_HOME:-$HOME/.pathiel}"' in env
    assert 'PATHIEL_DATA="${CLAUDE_PLUGIN_DATA:-$PATHIEL_HOME/data}"' in env
    for var in ("PATHIEL_ENV_FILE", "PATHIEL_STATE_DIR", "PATHIEL_AGENT_CONFIG_FILE"):
        assert f'export {var}="${{{var}:-$PATHIEL_HOME/' in env, var
    assert "requirements.lock" in env and "sha256" in env, "the venv must be rebuilt when the lock changes"


# ── the env file the scripts load ────────────────────────────────────────────

def _load_state_env():
    import importlib.util
    spec = importlib.util.spec_from_file_location("pathiel_state_env_under_test",
                                                  ROOT / "scripts" / "_state_env.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_env_file_precedence_explicit_then_checkout_then_home(tmp_path, monkeypatch):
    se = _load_state_env()
    home = tmp_path / "home"
    (home / ".pathiel").mkdir(parents=True)
    (home / ".pathiel" / ".env.local").write_text("PATHIEL_T_WHO=home\n")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    explicit = tmp_path / "explicit.env"
    explicit.write_text("PATHIEL_T_WHO=explicit\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PATHIEL_ENV_FILE", raising=False)
    monkeypatch.delenv("PATHIEL_T_WHO", raising=False)

    # nothing in the checkout: the home file is found
    assert se.load_env_local(str(checkout)) == str(home / ".pathiel" / ".env.local")
    assert os.environ["PATHIEL_T_WHO"] == "home"

    # a checkout file outranks home
    monkeypatch.delenv("PATHIEL_T_WHO")
    (checkout / ".env.local").write_text("PATHIEL_T_WHO=checkout\n")
    assert se.load_env_local(str(checkout)) == str(checkout / ".env.local")
    assert os.environ["PATHIEL_T_WHO"] == "checkout"

    # PATHIEL_ENV_FILE outranks both
    monkeypatch.delenv("PATHIEL_T_WHO")
    monkeypatch.setenv("PATHIEL_ENV_FILE", str(explicit))
    assert se.load_env_local(str(checkout)) == str(explicit)
    assert os.environ["PATHIEL_T_WHO"] == "explicit"


def test_env_file_never_overrides_a_real_env_var(tmp_path, monkeypatch):
    se = _load_state_env()
    (tmp_path / ".env.local").write_text("PATHIEL_T_WHO=file\n")
    monkeypatch.setenv("PATHIEL_T_WHO", "process")
    monkeypatch.delenv("PATHIEL_ENV_FILE", raising=False)
    se.load_env_local(str(tmp_path))
    assert os.environ["PATHIEL_T_WHO"] == "process"


def test_the_mcp_server_and_the_web_server_use_the_same_precedence():
    mcp = (ROOT / "scripts" / "pathiel-mcp-server.py").read_text()
    assert "_state_env.load_env_local(" in mcp
    srv = (ROOT / "pathiel" / "server.py").read_text()
    for needle in ('os.environ.get("PATHIEL_ENV_FILE")', '".pathiel", ".env.local"'):
        assert needle in srv, needle


# ── the skill and the command ────────────────────────────────────────────────

def test_the_plugin_desk_tick_skill_is_generated_from_the_project_skill():
    """Two files, one text. The committed plugin copy must equal what the
    generator renders from .claude/skills/desk-tick/SKILL.md."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_plugin_skill as gen
    rendered = gen.build(gen.SOURCE.read_text())
    assert gen.OUT.read_text() == rendered, "rerun scripts/build_plugin_skill.py"
    assert '"${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/desk_slot.py' in rendered
    assert "python3 scripts/" not in rendered
    assert "0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985" not in rendered
    assert "Julien" not in rendered
    assert rendered.startswith("---\nname: desk-tick\n")


def test_the_setup_command_names_files_that_exist_and_never_prints_a_key():
    text = (ROOT / "commands" / "setup.md").read_text()
    assert text.startswith("---\ndescription:")
    for rel in (".env.local.example", "scripts/preflight_secrets.py", "scripts/mcp_smoke.py",
                "scripts/pathiel-mcp", "scripts/pathiel-py"):
        assert rel in text and (ROOT / rel).exists(), rel
    assert "--bootstrap" in text and "chmod 600" in text and "/mcp" in text
    assert "Never print the value of any key" in text


def test_the_env_template_carries_no_real_value():
    """/pathiel:setup copies .env.local.example to ~/.pathiel/.env.local."""
    text = (ROOT / ".env.local.example").read_text()
    for line in text.splitlines():
        if line.startswith(("HYPERLIQUID_PRIVATE_KEY=", "HYPERLIQUID_MASTER_PRIVATE_KEY=")):
            value = line.split("=", 1)[1]
            assert "fake" in value.lower() or value.strip("0x") == "" , line


# ── the real validator, when it is on this machine ───────────────────────────

@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")
def test_claude_plugin_validate_passes():
    proc = subprocess.run(["claude", "plugin", "validate", str(ROOT)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passed" in (proc.stdout + proc.stderr).lower()
