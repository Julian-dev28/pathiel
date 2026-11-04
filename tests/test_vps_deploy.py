"""scripts/vps: the rented-server path for the desk loops.

The env builder is the one piece that handles a key, so it carries most of
the tests: the server gets its own API wallet, never the main key, never the
Mac's key, and no secret ever reaches stdout or stderr.
"""
from __future__ import annotations

import importlib.util
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VPS = ROOT / "scripts" / "vps"

spec = importlib.util.spec_from_file_location("make_server_env", VPS / "make_server_env.py")
mse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mse)

MASTER = "0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985"
OLD_AGENT = "0x1111111111111111111111111111111111111111"
NEW_AGENT = "0x2222222222222222222222222222222222222222"
OLD_KEY = "0x" + "a" * 64
NEW_KEY = "0x" + "b" * 64
MAC_HOME = "/mac-home/j"
MAC_REPO = MAC_HOME + "/code/pathiel"
MAC_STATE = MAC_HOME + "/code/pathia/.state"
SRV_REPO = "/srv/p/pathiel"
SRV_STATE = "/srv/p/state"

MAC_ENV = f"""# live
HYPERLIQUID_WALLET_ADDRESS={OLD_AGENT}
HYPERLIQUID_PRIVATE_KEY={OLD_KEY}
HYPERLIQUID_MASTER_ADDRESS={MASTER}
HYPERLIQUID_MASTER_PRIVATE_KEY=0x{"c" * 64}
PATHIEL_STATE_DIR={MAC_STATE}
PATHIEL_AGENT_CONFIG_FILE={MAC_REPO}/.agent-config.json
SESSION_LOG_PATH={MAC_STATE}/session-log.jsonl
SOME_CACHE={MAC_HOME}/Library/Caches/x
EIA_API_KEY=eia123
PATHIEL_PORT=8000
"""


def _build(env_text=MAC_ENV, key=NEW_KEY, derive=lambda k: NEW_AGENT, expected_agent=None):
    return mse.build_server_env(
        mse.parse_env(env_text), agent_key=key,
        path_map={MAC_STATE: SRV_STATE, MAC_REPO: SRV_REPO},
        local_home=MAC_HOME, server_state=SRV_STATE, derive=derive,
        expected_agent=expected_agent)


def test_server_env_swaps_in_the_new_api_wallet_and_keeps_the_rest():
    out, report = _build()
    env = dict(out)
    assert env["HYPERLIQUID_PRIVATE_KEY"] == NEW_KEY
    assert env["HYPERLIQUID_WALLET_ADDRESS"] == NEW_AGENT
    assert env["HYPERLIQUID_MASTER_ADDRESS"] == MASTER
    assert env["PATHIEL_STATE_DIR"] == SRV_STATE
    assert env["EIA_API_KEY"] == "eia123" and env["PATHIEL_PORT"] == "8000"
    assert "HYPERLIQUID_MASTER_PRIVATE_KEY" not in env
    assert OLD_KEY not in env.values()
    assert report["master"] == MASTER and report["agent"] == NEW_AGENT


def test_mac_paths_move_to_the_server_and_leftovers_are_dropped():
    out, report = _build()
    env = dict(out)
    assert env["PATHIEL_AGENT_CONFIG_FILE"] == SRV_REPO + "/.agent-config.json"
    assert env["SESSION_LOG_PATH"] == SRV_STATE + "/session-log.jsonl"
    assert "SOME_CACHE" not in env
    assert report["dropped_mac_paths"] == ["SOME_CACHE"]
    assert set(report["rewritten"]) == {"PATHIEL_AGENT_CONFIG_FILE", "SESSION_LOG_PATH"}
    assert not any(MAC_HOME in v for v in env.values())


def test_a_prefix_only_matches_on_a_path_boundary():
    text = f"HYPERLIQUID_WALLET_ADDRESS={MASTER}\nX={MAC_STATE}-old/y\n"
    out, report = _build(text)
    assert "X" not in dict(out) and report["dropped_mac_paths"] == ["X"]


def test_master_falls_back_to_the_wallet_when_the_mac_signs_as_main():
    text = f"HYPERLIQUID_WALLET_ADDRESS={MASTER}\nHYPERLIQUID_PRIVATE_KEY={OLD_KEY}\n"
    out, _ = _build(text)
    assert dict(out)["HYPERLIQUID_MASTER_ADDRESS"] == MASTER


@pytest.mark.parametrize("env_text,key,derive,needle", [
    (MAC_ENV, NEW_KEY, lambda k: MASTER.lower(), "main wallet"),
    (MAC_ENV, OLD_KEY, lambda k: NEW_AGENT, "already uses"),
    (MAC_ENV, OLD_KEY.upper().replace("0X", "0x"), lambda k: NEW_AGENT, "already uses"),
    (MAC_ENV, "0xabc", lambda k: NEW_AGENT, "64 hex"),
    (MAC_ENV, "", lambda k: NEW_AGENT, "64 hex"),
    ("EIA_API_KEY=x\n", NEW_KEY, lambda k: NEW_AGENT, "no main account"),
])
def test_refusals_never_echo_the_key(env_text, key, derive, needle):
    with pytest.raises(mse.EnvError) as e:
        _build(env_text, key, derive)
    assert needle in str(e.value)
    if key:
        assert key not in str(e.value) and key[2:] not in str(e.value)


def test_derive_address_matches_a_known_vector():
    # eth_account's documented example key.
    key = "0x4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
    assert mse.derive_address(key) == "0x2c7536E3605D9C16a7a3D7b1898e529396a65c23"


def test_written_env_is_owner_only(tmp_path):
    p = tmp_path / "env"
    mse.write_env(str(p), [("A", "1")])
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert p.read_text().endswith("A=1\n")


def test_main_writes_the_file_and_prints_no_secret(tmp_path, monkeypatch, capsys):
    src = tmp_path / "mac.env"
    src.write_text(MAC_ENV)
    monkeypatch.setattr(mse, "local_env_path", lambda: str(src))
    monkeypatch.setattr(mse.getpass, "getpass", lambda prompt="": NEW_KEY)
    monkeypatch.setattr(mse, "derive_address", lambda k: NEW_AGENT)
    out = tmp_path / "server.env"
    rc = mse.main(["--out", str(out), "--server-repo", SRV_REPO, "--server-state", SRV_STATE])
    printed = capsys.readouterr()
    assert rc == 0
    for secret in (NEW_KEY, OLD_KEY, "c" * 64, "eia123"):
        assert secret not in printed.out and secret not in printed.err
    assert NEW_AGENT in printed.out and MASTER in printed.out
    assert f"HYPERLIQUID_PRIVATE_KEY={NEW_KEY}" in out.read_text()


def test_main_refuses_without_writing(tmp_path, monkeypatch, capsys):
    src = tmp_path / "mac.env"
    src.write_text(MAC_ENV)
    monkeypatch.setattr(mse, "local_env_path", lambda: str(src))
    monkeypatch.setattr(mse.getpass, "getpass", lambda prompt="": OLD_KEY)
    monkeypatch.setattr(mse, "derive_address", lambda k: OLD_AGENT)
    out = tmp_path / "server.env"
    rc = mse.main(["--out", str(out), "--server-repo", SRV_REPO, "--server-state", SRV_STATE])
    printed = capsys.readouterr()
    assert rc == 1 and not out.exists()
    assert OLD_KEY not in printed.err and "refused" in printed.err


# ── the shell scripts ────────────────────────────────────────────────────────

SCRIPTS = ["setup_server.sh", "push.sh", "start.sh", "pull_state.sh", "e2e_docker.sh",
           "check_claude.sh"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_shell_scripts_parse_and_are_strict(name):
    path = VPS / name
    subprocess.run(["bash", "-n", str(path)], check=True)
    assert "set -euo pipefail" in path.read_text()


def test_push_never_copies_secrets_or_history():
    text = (VPS / "push.sh").read_text()
    assert "--exclude '.env*'" in text and "--exclude .git/" in text
    # The env file is built by the checked builder, never copied from the Mac.
    assert "make_server_env.py" in text
    assert ".env.local\" " not in text.split("make_server_env.py")[0]


def test_push_refuses_while_either_side_is_trading():
    text = (VPS / "push.sh").read_text()
    guard_mac = text.index("pgrep -f scripts/trigger_watch.py")
    guard_srv = text.index("tmux has-session -t desk")
    first_copy = text.index("rsync -az --delete")
    assert guard_mac < first_copy and guard_srv < first_copy


def test_pull_snapshots_before_it_overwrites_and_stops_the_server_first():
    text = (VPS / "pull_state.sh").read_text()
    stop = text.index("tmux kill-session -t desk")
    snap = text.index('cp -a "$LOCAL_STATE" "$SNAP"')
    pull = text.index('rsync -az "$HOST:$SERVER_HOME/state/"')
    assert stop < snap < pull


def test_server_only_opens_ssh():
    text = (VPS / "setup_server.sh").read_text()
    assert "ufw allow OpenSSH" in text and "ufw --force enable" in text
    assert "8000" not in text.split("ufw allow OpenSSH")[1]


def test_start_runs_the_same_loops_as_the_mac():
    text = (VPS / "start.sh").read_text()
    assert "'/desk-loops'" in text and "--permission-mode auto" in text
    assert "--require-hashes -r requirements.lock" in text
    assert "scripts/restart.sh" in text


# Both found by scripts/vps/e2e_docker.sh on 2026-11-04, before first use.

def test_push_env_copy_works_with_the_macos_rsync():
    """macOS ships openrsync, which has no --chmod: the env copy died with
    "--chmod=F600: invalid argument" after the repo and state were already
    pushed."""
    text = (VPS / "push.sh").read_text()
    assert not [ln for ln in text.splitlines() if ln.lstrip().startswith("rsync") and "--chmod" in ln]
    assert 'umask 077 && cat > $SERVER_REPO/.env.local' in text


def test_pull_pkill_cannot_match_its_own_ssh_shell():
    """`pkill -f scripts/trigger_watch.py` inside `ssh HOST '...'` matched the
    remote shell running that very command line and killed the session
    (exit 255): the Desk server kept running and the key was never deleted."""
    text = (VPS / "pull_state.sh").read_text()
    assert 'pkill -f "[s]cripts/trigger_watch.py"' in text
    remote = [ln for ln in text.splitlines() if "pkill -f" in ln or "pgrep -f" in ln]
    assert all("[" in ln for ln in remote), remote


def test_command_only_ssh_calls_do_not_eat_stdin():
    """A piped key must reach make_server_env.py, not the guard's ssh."""
    for name in ("push.sh", "pull_state.sh"):
        for ln in (VPS / name).read_text().splitlines():
            s = ln.strip()
            if s.startswith(("ssh ", "if ssh ")) and "< " not in s:
                assert "ssh -n" in s, (name, s)


# ── the server key kept in the Mac env (2026-11-04) ──────────────────────────

VPS_ENV = MAC_ENV + f"HYPERLIQUID_VPS_WALLET_ADDRESS={NEW_AGENT}\nHYPERLIQUID_VPS_PRIVATE_KEY={NEW_KEY}\n"


def test_vps_names_never_reach_the_server():
    out, _ = _build(VPS_ENV, expected_agent=NEW_AGENT)
    env = dict(out)
    assert "HYPERLIQUID_VPS_PRIVATE_KEY" not in env and "HYPERLIQUID_VPS_WALLET_ADDRESS" not in env
    assert env["HYPERLIQUID_PRIVATE_KEY"] == NEW_KEY
    assert [k for k, _ in out].count("HYPERLIQUID_PRIVATE_KEY") == 1


def test_key_must_match_the_vps_address():
    other = "0x3333333333333333333333333333333333333333"
    with pytest.raises(mse.EnvError) as e:
        _build(VPS_ENV, expected_agent=other)
    assert other in str(e.value) and NEW_AGENT in str(e.value)
    assert NEW_KEY not in str(e.value) and NEW_KEY[2:] not in str(e.value)
    # Case alone is not a mismatch (checksummed vs lower-case paste).
    _build(VPS_ENV, expected_agent=NEW_AGENT.upper().replace("0X", "0x"))


def _run_main(tmp_path, monkeypatch, env_text, argv, prompt_key=None):
    src = tmp_path / "mac.env"
    src.write_text(env_text)
    monkeypatch.setattr(mse, "local_env_path", lambda: str(src))
    monkeypatch.setattr(mse, "derive_address", lambda k: NEW_AGENT if k == NEW_KEY else OLD_AGENT)

    def no_prompt(prompt=""):
        if prompt_key is None:
            raise AssertionError("prompted although the Mac env holds the key")
        return prompt_key
    monkeypatch.setattr(mse.getpass, "getpass", no_prompt)
    return mse.main(argv)


def test_main_takes_the_key_from_the_mac_env_without_prompting(tmp_path, monkeypatch, capsys):
    out = tmp_path / "server.env"
    rc = _run_main(tmp_path, monkeypatch, VPS_ENV,
                   ["--out", str(out), "--server-repo", SRV_REPO, "--server-state", SRV_STATE])
    printed = capsys.readouterr()
    assert rc == 0
    assert "HYPERLIQUID_VPS_PRIVATE_KEY in" in printed.out
    for secret in (NEW_KEY, NEW_KEY[2:], OLD_KEY, "c" * 64):
        assert secret not in printed.out and secret not in printed.err
    text = out.read_text()
    assert f"HYPERLIQUID_PRIVATE_KEY={NEW_KEY}" in text and "VPS" not in text


@pytest.mark.parametrize("quote", ['"', "'"])
def test_a_quoted_vps_key_still_works(tmp_path, monkeypatch, capsys, quote):
    text = MAC_ENV + f"HYPERLIQUID_VPS_PRIVATE_KEY={quote}{NEW_KEY}{quote}\n"
    out = tmp_path / "server.env"
    assert _run_main(tmp_path, monkeypatch, text, ["--out", str(out), "--server-repo", SRV_REPO,
                                                   "--server-state", SRV_STATE]) == 0
    assert f"HYPERLIQUID_PRIVATE_KEY={NEW_KEY}\n" in out.read_text()


def test_main_falls_back_to_the_prompt_without_a_vps_key(tmp_path, monkeypatch, capsys):
    out = tmp_path / "server.env"
    argv = ["--out", str(out), "--server-repo", SRV_REPO, "--server-state", SRV_STATE]
    assert _run_main(tmp_path, monkeypatch, MAC_ENV, argv, prompt_key=NEW_KEY) == 0
    assert "hidden prompt" in capsys.readouterr().out


def test_check_writes_nothing_and_says_ok(tmp_path, monkeypatch, capsys):
    rc = _run_main(tmp_path, monkeypatch, VPS_ENV, ["--check"])
    printed = capsys.readouterr()
    assert rc == 0 and "OK to push" in printed.out
    assert NEW_KEY not in printed.out
    assert not [p for p in tmp_path.iterdir() if p.name != "mac.env"]


def test_check_refuses_a_mismatched_pair(tmp_path, monkeypatch, capsys):
    text = MAC_ENV + f"HYPERLIQUID_VPS_WALLET_ADDRESS={OLD_AGENT}\nHYPERLIQUID_VPS_PRIVATE_KEY={NEW_KEY}\n"
    rc = _run_main(tmp_path, monkeypatch, text, ["--check"])
    printed = capsys.readouterr()
    assert rc == 1 and "refused" in printed.err and NEW_KEY not in printed.err


def test_main_needs_out_or_check(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        _run_main(tmp_path, monkeypatch, VPS_ENV, [])


# ── Claude reaches the MCP server (check_claude.sh) ──────────────────────────

def test_start_proves_claude_reaches_the_mcp_server_before_the_loops():
    text = (VPS / "start.sh").read_text()
    check = text.index("scripts/vps/check_claude.sh\n")
    assert check < text.index("tmux new-session")
    assert check > text.index("scripts/restart.sh\n")


def test_claude_check_may_only_call_one_read_only_tool():
    text = (VPS / "check_claude.sh").read_text()
    call = [ln for ln in text.splitlines() if "--allowedTools" in ln and not ln.lstrip().startswith("#")]
    assert call == ["  --allowedTools mcp__pathiel__desk_list </dev/null >\"$LOG\" 2>&1 || rc=$?"]
    assert "--permission-mode" not in text and "dangerously" not in text


def test_claude_check_puts_the_prompt_before_the_tool_list():
    """--allowedTools takes a list: a prompt after it was swallowed and claude
    said "Input must be provided" (found running the check on the Mac)."""
    text = (VPS / "check_claude.sh").read_text()
    assert 'claude -p "$PROMPT"' in text
    assert text.index('claude -p "$PROMPT"') < text.index("--allowedTools mcp__")


def test_claude_check_only_passes_on_a_listed_oil_desk(tmp_path):
    """Run the real script against a fake `claude` that answers each way."""
    answers = {
        "DESKS=oil,metals": (0, ""),
        "NO_MCP": (1, "MCP server is not loaded"),
        "Invalid API key · Please run /login": (1, "not logged in"),
        "DESKS=": (1, "unexpected answer"),
        "I listed the desks for you.": (1, "unexpected answer"),
    }
    for answer, (want, reason) in answers.items():
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        fake = bin_dir / "claude"
        fake.write_text(f"#!/bin/bash\necho '{answer}'\n")
        fake.chmod(0o755)
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
        r = subprocess.run(["bash", str(VPS / "check_claude.sh")], env=env,
                           capture_output=True, text=True)
        assert r.returncode == want, (answer, r.stdout, r.stderr)
        assert ("CLAUDE-CHECK OK" in r.stdout) == (want == 0), answer
        assert reason in r.stderr, (answer, r.stderr)


def test_out_needs_the_server_paths(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        _run_main(tmp_path, monkeypatch, VPS_ENV, ["--out", str(tmp_path / "x")])
    assert not (tmp_path / "x").exists()
