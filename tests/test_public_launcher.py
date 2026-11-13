"""The public launcher, scripts/_plugin_env.sh and scripts/pathiel-mcp.

What a stranger's install resolves to before Python starts: install kind,
network, state dir, config file, and the paste-ready commands that
/pathiel:setup hands them for their own terminal.

Every test runs bash in a subprocess with env=public_env(...): a temp HOME,
an env file the fixture created, dead proxies. No test builds a venv: the
two that run the full launcher point CLAUDE_PLUGIN_DATA at a fake data dir
whose venv/bin/python is a stub that records its argv, with the real sha256
of requirements.lock as the stamp, so pathiel_build_venv is a no-op. Nothing
touches the real ~/.pathiel.

    .venv/bin/python -m pytest -q tests/test_public_launcher.py
"""
from __future__ import annotations

import hashlib
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ENV = ROOT / "scripts" / "_plugin_env.sh"
MCP_WRAPPER = ROOT / "scripts" / "pathiel-mcp"

# Unset by default so the launcher's own defaults are what a test sees.
LAUNCHER_VARS = ("PATHIEL_INSTALL", "PATHIEL_HOME", "PATHIEL_ENV_FILE", "PATHIEL_STATE_DIR",
                 "PATHIEL_AGENT_CONFIG_FILE", "PATHIEL_UNIVERSE_CACHE_DIR", "PATHIEL_NETWORK",
                 "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT")
REPORTED = ("PATHIEL_ROOT", "PATHIEL_HOME", "PATHIEL_DATA", "PATHIEL_VENV", "PATHIEL_INSTALL",
            "PATHIEL_NETWORK", "PATHIEL_NETWORK_SOURCE", "PATHIEL_ENV_FILE", "PATHIEL_STATE_DIR",
            "PATHIEL_UNIVERSE_CACHE_DIR", "PATHIEL_AGENT_CONFIG_FILE")


def fresh_env(public_env, **overrides) -> Dict[str, str]:
    """public_env with every launcher variable unset, then `overrides`."""
    unset = {k: None for k in LAUNCHER_VARS}
    unset.update(overrides)
    return public_env(**unset)


def run_functions(env: Dict[str, str], out: Path,
                  funcs=("pathiel_paths", "pathiel_resolve_network", "pathiel_state_paths"),
                  ) -> Tuple[Dict[str, str], subprocess.CompletedProcess]:
    """Source _plugin_env.sh, call `funcs` under set -euo pipefail, write the
    resulting variables to `out` (never stdout). Returns (vars, proc)."""
    report = " ".join(f'"{v}=${{{v}:-}}"' for v in REPORTED)
    script = (f'set -euo pipefail\n. {shlex.quote(str(PLUGIN_ENV))}\n'
              + "\n".join(funcs)
              + f"\nprintf '%s\\n' {report} > {shlex.quote(str(out))}\n")
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True,
                          timeout=30)
    assert proc.returncode == 0, proc.stderr
    values = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return values, proc


def make_fake_data_dir(base: Path, exit_code: int = 0) -> Tuple[Path, Path]:
    """<base>/venv/bin/python is a stub that appends one CALL block per run
    (PATHIEL_VENV, then each argv item) to <base>/calls.log. The stamp holds
    the real lock hash, so pathiel_build_venv sees a ready venv."""
    venv = base / "venv"
    (venv / "bin").mkdir(parents=True)
    log = base / "calls.log"
    stub = venv / "bin" / "python"
    # `-c` calls are pathiel_build_venv's version check and lock hash (the
    # printed commands pin PATHIEL_PYTHON to this stub): run them on the real
    # interpreter, unlogged.
    stub.write_text(
        "#!/bin/sh\n"
        f'if [ "${{1:-}}" = "-c" ]; then exec {shlex.quote(sys.executable)} "$@"; fi\n'
        "{ printf 'CALL\\n'; printf 'VENV=%s\\n' \"$PATHIEL_VENV\"; "
        "printf 'INSTALL=%s\\n' \"$PATHIEL_INSTALL\"; "
        "for a in \"$@\"; do printf 'ARG=%s\\n' \"$a\"; done; } >> " + shlex.quote(str(log)) + "\n"
        f"exit {exit_code}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    want = hashlib.sha256((ROOT / "requirements.lock").read_bytes()).hexdigest()
    (venv / ".requirements.sha256").write_text(want + "\n")
    return base, log


def calls(log: Path) -> List[Dict[str, object]]:
    if not log.exists():
        return []
    out: List[Dict[str, object]] = []
    for line in log.read_text().splitlines():
        if line == "CALL":
            out.append({"venv": None, "install": None, "args": []})
        elif line.startswith("VENV="):
            out[-1]["venv"] = line[5:]
        elif line.startswith("INSTALL="):
            out[-1]["install"] = line[8:]
        elif line.startswith("ARG="):
            out[-1]["args"].append(line[4:])
    return out


def run_commands(public_env, tmp_path: Path) -> Tuple[List[Tuple[str, str]], Path, Path, Dict[str, str]]:
    """`scripts/pathiel-mcp --commands` against a fake data dir, with a space
    in every user path. Returns (lines, data_dir, call log, env)."""
    data, log = make_fake_data_dir(tmp_path / "plugin data")
    home = tmp_path / "my home"
    env = fresh_env(public_env, HOME=str(home), CLAUDE_PLUGIN_DATA=str(data),
                    CLAUDE_PLUGIN_ROOT=str(ROOT), PATHIEL_PYTHON=sys.executable)
    proc = subprocess.run([str(MCP_WRAPPER), "--commands"], env=env, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    lines = [tuple(line.split("\t", 1)) for line in proc.stdout.splitlines()]
    return lines, data, log, env


# ── network and paths ─────────────────────────────────────────────────────────

def test_default_is_public_mainnet_with_the_mainnet_state_dir(public_env, tmp_path):
    """Decision 2026-11-12: nothing set means mainnet (real money), state in
    ~/.pathiel/state where 0.3.0 kept it. The source says nobody chose it."""
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text("")   # an empty env file
    got, proc = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_INSTALL"] == "public"
    assert got["PATHIEL_NETWORK"] == "mainnet"
    assert got["PATHIEL_NETWORK_SOURCE"] == "default"
    assert got["PATHIEL_HOME"] == str(home / ".pathiel")
    assert got["PATHIEL_STATE_DIR"] == str(home / ".pathiel" / "state")
    assert "testnet" not in got["PATHIEL_STATE_DIR"]
    assert got["PATHIEL_UNIVERSE_CACHE_DIR"] == str(home / ".pathiel" / "state" / "universe_cache")
    assert "warning" not in proc.stderr
    assert got["PATHIEL_ENV_FILE"] == str(home / ".pathiel" / ".env.local")
    assert got["PATHIEL_AGENT_CONFIG_FILE"] == str(home / ".pathiel" / ".agent-config.json")
    assert got["PATHIEL_DATA"] == str(home / ".pathiel" / "data")
    assert got["PATHIEL_ROOT"] == str(ROOT)
    assert Path(got["PATHIEL_STATE_DIR"]).is_dir() and Path(got["PATHIEL_UNIVERSE_CACHE_DIR"]).is_dir()


def test_no_env_file_at_all_is_mainnet_too(public_env, tmp_path):
    env = fresh_env(public_env, HOME=str(tmp_path / "nohome"))
    got, _ = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == "mainnet" and got["PATHIEL_NETWORK_SOURCE"] == "default"


def test_env_file_line_selects_testnet(public_env, tmp_path):
    """Testnet is the explicit opt-in: one line, its own state dir."""
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text("# keys\nHYPERLIQUID_PRIVATE_KEY=\nPATHIEL_NETWORK=testnet\n")
    got, proc = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == "testnet"
    assert got["PATHIEL_NETWORK_SOURCE"] == "env file"
    assert got["PATHIEL_STATE_DIR"] == str(home / ".pathiel" / "state-testnet")
    assert got["PATHIEL_UNIVERSE_CACHE_DIR"] == str(home / ".pathiel" / "state-testnet" / "universe_cache")
    assert "warning" not in proc.stderr


def test_env_file_line_selects_mainnet(public_env, tmp_path):
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text("# keys\nHYPERLIQUID_PRIVATE_KEY=\nPATHIEL_NETWORK=mainnet\n")
    got, proc = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == "mainnet"
    assert got["PATHIEL_NETWORK_SOURCE"] == "env file"
    assert got["PATHIEL_STATE_DIR"] == str(home / ".pathiel" / "state")
    assert "warning" not in proc.stderr


def test_the_first_matching_line_wins(public_env, tmp_path):
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text("PATHIEL_NETWORK=testnet\nPATHIEL_NETWORK=mainnet\n")
    got, _ = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == "testnet"


@pytest.mark.parametrize("process,file_value,want", [
    ("testnet", "mainnet", "testnet"),
    ("mainnet", "testnet", "mainnet"),
])
def test_process_env_beats_the_file(public_env, tmp_path, process, file_value, want):
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home), PATHIEL_NETWORK=process)
    (home / ".pathiel" / ".env.local").write_text(f"PATHIEL_NETWORK={file_value}\n")
    got, _ = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == want
    assert got["PATHIEL_NETWORK_SOURCE"] == "environment"


@pytest.mark.parametrize("line", [
    "PATHIEL_NETWORK=Mainnet",
    "PATHIEL_NETWORK= mainnet ",
    'PATHIEL_NETWORK="mainnet"',
    "PATHIEL_NETWORK='mainnet'",
    "  PATHIEL_NETWORK = MAINNET",
    "PATHIEL_NETWORK=mainnet\r",
])
def test_case_whitespace_and_quotes(public_env, tmp_path, line):
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text(line + "\n")
    got, proc = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == "mainnet", line
    assert "warning" not in proc.stderr


@pytest.mark.parametrize("value", [" Mainnet ", '"mainnet"', "MAINNET"])
def test_process_env_is_normalized_too(public_env, tmp_path, value):
    env = fresh_env(public_env, HOME=str(tmp_path / "home"), PATHIEL_NETWORK=value)
    got, _ = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == "mainnet"


@pytest.mark.parametrize("line", [
    "PATHIEL_NETWORK=live", "PATHIEL_NETWORK=main", "PATHIEL_NETWORK=test",
    "PATHIEL_NETWORK=testnet#practice", "PATHIEL_NETWORK=testnet-v2",
])
def test_garbage_value_falls_back_to_testnet_with_a_warning(public_env, tmp_path, line):
    """A value that is set but is neither network: the user tried to choose,
    so the launcher takes the side that cannot lose real money and says so."""
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text(line + "\n")
    got, proc = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == "testnet", line
    assert got["PATHIEL_NETWORK_SOURCE"] == "invalid", "a value nobody could read is not a choice"
    assert got["PATHIEL_STATE_DIR"].endswith("/state-testnet")
    warnings = [w for w in proc.stderr.splitlines() if "warning" in w]
    assert len(warnings) == 1 and "using testnet" in warnings[0], proc.stderr


@pytest.mark.parametrize("line", [
    "# PATHIEL_NETWORK=testnet",                  # the template's commented opt-in line
    "# PATHIEL_NETWORK=mainnet",
    "PATHIEL_NETWORK=",
    "PATHIEL_NETWORK=   ",
    "HYPERLIQUID_PRIVATE_KEY=",
])
def test_unset_lines_mean_the_mainnet_default(public_env, tmp_path, line):
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text(line + "\n")
    got, proc = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == "mainnet", line
    assert got["PATHIEL_NETWORK_SOURCE"] == "default"
    assert "warning" not in proc.stderr


@pytest.mark.parametrize("line,want", [
    ("export PATHIEL_NETWORK=testnet", "testnet"),
    ("  export   PATHIEL_NETWORK = testnet", "testnet"),
    ("PATHIEL_NETWORK=testnet # practice first", "testnet"),
    ("export PATHIEL_NETWORK=testnet # practice", "testnet"),
    ("export PATHIEL_NETWORK=mainnet", "mainnet"),
    ("PATHIEL_NETWORK=mainnet # real money", "mainnet"),
])
def test_shell_style_lines_are_read_as_written(public_env, tmp_path, line, want):
    """With mainnet the default, a user who writes the testnet line the shell
    way must still get testnet: an ignored line would mean real money."""
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text(line + "\n")
    got, proc = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == want, line
    assert got["PATHIEL_NETWORK_SOURCE"] == "env file"
    assert "warning" not in proc.stderr


@pytest.mark.parametrize("line", [
    "export PATHIEL_NETWORK=mainnet",
    "PATHIEL_NETWORK=mainnet # real money",
    "PATHIEL_NETWORK=live",
    'PATHIEL_NETWORK="mainnet"',
])
def test_python_agrees_with_the_launcher_on_odd_lines(public_env, tmp_path, line):
    """The launcher exports what it resolved; Python's loader (setdefault)
    can then never read the line another way."""
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text(line + "\n")
    out = tmp_path / "py"
    code = ("import sys; sys.path.insert(0, 'scripts'); import _state_env; "
            "_state_env.load_env_local('.'); import contracts.runtime as r; "
            f"open({str(out)!r}, 'w').write(r.network())")
    script = (f"set -euo pipefail\n. {shlex.quote(str(PLUGIN_ENV))}\n"
              "pathiel_paths\npathiel_resolve_network\npathiel_state_paths\n"
              f"cd {shlex.quote(str(ROOT))}\n{shlex.quote(sys.executable)} -c {shlex.quote(code)}\n"
              f"printf '%s' \"$PATHIEL_NETWORK\" > {shlex.quote(str(tmp_path / 'sh'))}\n")
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert out.read_text() == (tmp_path / "sh").read_text()


def test_operator_shell_values_are_kept(public_env, tmp_path):
    mine = tmp_path / "mine"
    env = fresh_env(public_env, HOME=str(tmp_path / "home"),
                    PATHIEL_INSTALL="operator",
                    PATHIEL_HOME=str(mine),
                    PATHIEL_ENV_FILE=str(mine / "keys.env"),
                    PATHIEL_STATE_DIR=str(mine / "my-state"),
                    PATHIEL_AGENT_CONFIG_FILE=str(mine / "my-config.json"),
                    PATHIEL_UNIVERSE_CACHE_DIR=str(mine / "my-universe"),
                    PATHIEL_NETWORK="mainnet")
    got, _ = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_INSTALL"] == "operator"
    assert got["PATHIEL_HOME"] == str(mine)
    assert got["PATHIEL_ENV_FILE"] == str(mine / "keys.env")
    assert got["PATHIEL_STATE_DIR"] == str(mine / "my-state")
    assert got["PATHIEL_AGENT_CONFIG_FILE"] == str(mine / "my-config.json")
    assert got["PATHIEL_UNIVERSE_CACHE_DIR"] == str(mine / "my-universe")
    assert got["PATHIEL_NETWORK"] == "mainnet"


def test_a_relative_home_becomes_absolute(public_env, tmp_path):
    """The launchers cd to the plugin root before exec; a relative path would
    point inside it."""
    env = fresh_env(public_env, HOME=str(tmp_path / "home"), PATHIEL_HOME="rel/pathiel")
    script = (f"set -euo pipefail\ncd {shlex.quote(str(tmp_path))}\n. {shlex.quote(str(PLUGIN_ENV))}\n"
              "pathiel_paths\npathiel_resolve_network\npathiel_state_paths\n"
              f"printf '%s\\n' \"$PATHIEL_HOME\" \"$PATHIEL_STATE_DIR\" > {shlex.quote(str(tmp_path / 'o'))}\n")
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    home, state = (tmp_path / "o").read_text().splitlines()
    assert home == str(tmp_path / "rel" / "pathiel")
    assert state == str(tmp_path / "rel" / "pathiel" / "state")


def test_nothing_goes_to_stdout_from_prepare_functions(public_env, tmp_path):
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text("PATHIEL_NETWORK=live\n")   # forces a warning
    _got, proc = run_functions(env, tmp_path / "out")
    assert proc.stdout == ""
    assert "warning" in proc.stderr


# ── config seeding ────────────────────────────────────────────────────────────

def _seed(env: Dict[str, str], tmp_path: Path) -> subprocess.CompletedProcess:
    script = (f"set -euo pipefail\n. {shlex.quote(str(PLUGIN_ENV))}\n"
              "pathiel_paths\npathiel_resolve_network\npathiel_state_paths\npathiel_seed_config\n"
              f"printf 'done\\n' > {shlex.quote(str(tmp_path / 'done'))}\n")
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)


def test_seed_runs_only_when_the_config_is_missing(public_env, tmp_path):
    data, log = make_fake_data_dir(tmp_path / "data")
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home), CLAUDE_PLUGIN_DATA=str(data))

    proc = _seed(env, tmp_path)
    assert proc.returncode == 0 and proc.stdout == "", proc.stderr
    seen = calls(log)
    assert len(seen) == 1
    assert seen[0]["args"] == [str(ROOT / "scripts" / "config_preset.py"), "seed-public"]
    assert seen[0]["venv"] == str(data / "venv")
    assert seen[0]["install"] == "public"

    # The file exists now (the real seed would have written it): no second call.
    (home / ".pathiel" / ".agent-config.json").write_text("{}\n")
    proc = _seed(env, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert len(calls(log)) == 1


def test_a_failed_seed_warns_and_continues(public_env, tmp_path):
    data, log = make_fake_data_dir(tmp_path / "data", exit_code=1)
    env = fresh_env(public_env, HOME=str(tmp_path / "home"), CLAUDE_PLUGIN_DATA=str(data))
    proc = _seed(env, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "done").read_text() == "done\n"
    assert "public default risk profile still applies" in proc.stderr
    assert proc.stdout == ""
    assert len(calls(log)) == 1


# ── paste-ready commands ──────────────────────────────────────────────────────

def test_commands_prints_three_absolute_lines(public_env, tmp_path):
    lines, data, _log, _env = run_commands(public_env, tmp_path)
    assert [label for label, _ in lines] == ["status", "approve-dry", "approve-sign"]
    for label, command in lines:
        words = shlex.split(command)
        assert words[0] == "env", command
        assert words[1] == f"CLAUDE_PLUGIN_DATA={data}"
        assert words[2] == f"PATHIEL_HOME={tmp_path / 'my home' / '.pathiel'}"
        assert words[3] == f"PATHIEL_PYTHON={data / 'venv' / 'bin' / 'python'}"
        assert words[4] == str(ROOT / "scripts" / "pathiel-py")
        for word in words[1:]:
            path = word.split("=", 1)[1] if "=" in word else word
            if path != "--yes":
                assert path.startswith("/"), f"{label}: {word} is not absolute"
        assert "${" not in command and "CLAUDE_PLUGIN_ROOT" not in command
    by_label = dict(lines)
    assert shlex.split(by_label["status"])[5] == str(ROOT / "scripts" / "public_status.py")
    assert shlex.split(by_label["approve-dry"])[5:] == [str(ROOT / "scripts" / "approve_builder_fee.py")]


def old_python3_dir(base: Path) -> Path:
    """A directory whose python3 is a stand-in for macOS /usr/bin/python3
    (3.9): it fails the 3.11 check. First on PATH, it is what a plain
    terminal resolves `python3` to."""
    bin_dir = base / "old-python-bin"
    bin_dir.mkdir(parents=True)
    py = bin_dir / "python3"
    py.write_text('#!/bin/sh\n[ "${1:-}" = "--version" ] && { echo "Python 3.9.6"; exit 0; }\nexit 1\n')
    py.chmod(py.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def plain_terminal_env(public_env, tmp_path: Path) -> Dict[str, str]:
    """A plain terminal: no Claude Code variables, no launcher variables, no
    PATHIEL_PYTHON, and an old python3 first on PATH."""
    path = f"{old_python3_dir(tmp_path)}:{public_env()['PATH']}"
    env = fresh_env(public_env, HOME=str(tmp_path / "my home"), PATHIEL_PYTHON=None, PATH=path)
    assert "CLAUDE_PLUGIN_ROOT" not in env and "CLAUDE_PLUGIN_DATA" not in env
    assert "PATHIEL_PYTHON" not in env
    return env


def test_printed_approve_command_runs_without_plugin_env(public_env, tmp_path):
    lines, data, log, _env = run_commands(public_env, tmp_path)
    dry = dict(lines)["approve-dry"]
    before = len(calls(log))

    plain = plain_terminal_env(public_env, tmp_path)
    proc = subprocess.run(["bash", "-c", dry], env=plain, capture_output=True, text=True,
                          timeout=60, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert "too old" not in proc.stderr
    assert "building the Python environment" not in proc.stderr

    seen = calls(log)[before:]
    approve = [c for c in seen if any("approve_builder_fee.py" in a for a in c["args"])]
    assert len(approve) == 1, seen
    assert approve[0]["args"] == [str(ROOT / "scripts" / "approve_builder_fee.py")]
    assert approve[0]["venv"] == str(data / "venv"), "a second venv would be built"
    assert approve[0]["install"] == "public"
    assert not (tmp_path / "my home" / ".pathiel" / "data").exists()


def test_without_the_pinned_python_an_old_python3_breaks_the_line(public_env, tmp_path):
    """The regression the PATHIEL_PYTHON pin fixes: the same line with the
    pin dropped fails in a terminal whose python3 is 3.9, venv or not."""
    lines, _data, _log, _env = run_commands(public_env, tmp_path)
    words = shlex.split(dict(lines)["approve-dry"])
    unpinned = shlex.join([w for w in words if not w.startswith("PATHIEL_PYTHON=")])
    proc = subprocess.run(["bash", "-c", unpinned], env=plain_terminal_env(public_env, tmp_path),
                          capture_output=True, text=True, timeout=60, cwd=str(tmp_path))
    assert proc.returncode != 0 and "Python 3.9.6 is too old" in proc.stderr


def test_sign_line_is_the_dry_line_plus_yes(public_env, tmp_path):
    lines, _data, log, _env = run_commands(public_env, tmp_path)
    by_label = dict(lines)
    assert by_label["approve-sign"] == by_label["approve-dry"] + " --yes"
    # The sign line is compared as text only; it never runs here.
    assert not any("--yes" in a for c in calls(log) for a in c["args"])


# ── review 2026-11-12: lines that used to resolve to mainnet silently ─────────

@pytest.mark.parametrize("content", [
    "﻿PATHIEL_NETWORK=testnet\n",                       # editor-saved UTF-8 BOM
    "PATHIEL_NETWORK=\nPATHIEL_NETWORK=testnet\n",           # empty line first
    "pathiel_network=testnet\n",                            # lowercase key
    "Pathiel_Network=testnet\r\n",                          # mixed case, CRLF
])
def test_testnet_lines_are_never_read_as_the_mainnet_default(public_env, tmp_path, content):
    """Each of these used to come out mainnet with source 'default' and no
    warning: a user who chose testnet would trade real money."""
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text(content, encoding="utf-8")
    got, proc = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == "testnet", repr(content)
    assert got["PATHIEL_NETWORK_SOURCE"] == "env file"


def test_disagreeing_lines_warn_and_use_the_first(public_env, tmp_path):
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text("PATHIEL_NETWORK=testnet\nPATHIEL_NETWORK=mainnet\n")
    got, proc = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == "testnet"
    warnings = [w for w in proc.stderr.splitlines() if "warning" in w]
    assert len(warnings) == 1 and "more than once" in warnings[0], proc.stderr


def test_repeated_identical_lines_do_not_warn(public_env, tmp_path):
    home = tmp_path / "home"
    env = fresh_env(public_env, HOME=str(home))
    (home / ".pathiel" / ".env.local").write_text("PATHIEL_NETWORK=testnet\nexport PATHIEL_NETWORK=Testnet\n")
    got, proc = run_functions(env, tmp_path / "out")
    assert got["PATHIEL_NETWORK"] == "testnet"
    assert "warning" not in proc.stderr
