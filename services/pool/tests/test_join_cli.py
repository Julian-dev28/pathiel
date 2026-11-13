"""services/pool/join_cli.py: python -m services.pool {join,leave,status}.
Offline. No test key hex ever reaches stdout, stderr, a file under tmp_path
or a log record."""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

import contracts.pool as pool
from services.pool import join, join_cli, paths

_PLUGIN_ENV_SH = Path(__file__).resolve().parents[3] / "scripts" / "_plugin_env.sh"


def _configure(monkeypatch, acct) -> None:
    import pathiel.client.exchange as exchange
    key = acct.key.hex()
    monkeypatch.setattr(exchange, "_resolve_identity", lambda: (key, acct.address, None, False))


# ── status ───────────────────────────────────────────────────────────────────

def test_status_help_exits_0(capsys):
    with pytest.raises(SystemExit) as e:
        join_cli.main(["status", "--help"])
    assert e.value.code == 0


def test_status_text(pool_env, monkeypatch, new_account, capsys):
    acct = new_account()
    monkeypatch.setattr("services.pool.account.configured_account", lambda: acct.address.lower())
    assert join_cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "state: none" in out and pool.SCOREBOARD_URL in out


def test_status_json(pool_env, monkeypatch, new_account, capsys):
    acct = new_account()
    monkeypatch.setattr("services.pool.account.configured_account", lambda: acct.address.lower())
    assert join_cli.main(["status", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "none"


# ── join / leave: dry run ────────────────────────────────────────────────────

def test_join_dry_run_signs_nothing_and_exits_0(pool_env, monkeypatch, new_account, capsys):
    acct = new_account()
    _configure(monkeypatch, acct)
    assert join_cli.main(["join"]) == 0
    out = capsys.readouterr().out
    assert "pathiel research pool: join" in out
    assert pool.local_standing(paths.repo_pool_root(), paths.outbox_root(), acct.address)["state"] == "none"


def test_join_dry_run_json(pool_env, monkeypatch, new_account, capsys):
    acct = new_account()
    _configure(monkeypatch, acct)
    assert join_cli.main(["join", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["account"] == acct.address.lower() and "message" in out and "joined_at" in out


def test_join_yes_signs_and_prints_the_file(pool_env, monkeypatch, new_account, capsys):
    acct = new_account()
    _configure(monkeypatch, acct)
    assert join_cli.main(["join", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "pathiel research pool: join" in out
    assert "wrote " in out and "repo path: " in out and "open: " in out
    assert pool.local_standing(paths.repo_pool_root(), paths.outbox_root(),
                               acct.address)["state"] == "signed_not_merged"


def test_leave_requires_membership(pool_env, monkeypatch, new_account, capsys):
    acct = new_account()
    _configure(monkeypatch, acct)
    assert join_cli.main(["leave"]) == 2
    assert "not_member" in capsys.readouterr().err


def test_join_no_key_exits_2_with_master_command(pool_env, capsys):
    assert join_cli.main(["join"]) == 2
    err = capsys.readouterr().err
    assert "no_key" in err and "services.pool join --master-prompt --yes" in err


def test_join_no_key_json(pool_env, capsys):
    assert join_cli.main(["join", "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": False, "code": "no_key", "error": out["error"], "master_command": out["master_command"]}


def test_help_exits_0_for_join_and_leave(capsys):
    for command in ("join", "leave"):
        with pytest.raises(SystemExit) as e:
            join_cli.main([command, "--help"])
        assert e.value.code == 0


# ── master_command drift ─────────────────────────────────────────────────────

def test_master_command_matches_the_launchers_paste_ready_commands(monkeypatch):
    """The launcher's pathiel_print_commands() builds its own paste-ready
    prefix from these exact NAME=value assignments. If the launcher adds or
    renames one and join.master_command does not follow, this test catches
    the drift before a user hits it."""
    text = _PLUGIN_ENV_SH.read_text(encoding="utf-8")
    m = re.search(r"pathiel_print_commands\s*\(\)\s*\{(.*?)\n\}", text, re.DOTALL)
    assert m, "pathiel_print_commands() not found in scripts/_plugin_env.sh"
    body = m.group(1)
    names = re.findall(r'"([A-Z][A-Z0-9_]*)=', body)
    assert set(names) >= {"CLAUDE_PLUGIN_DATA", "PATHIEL_HOME", "PATHIEL_PYTHON"}

    monkeypatch.setenv("PATHIEL_ROOT", "/opt/pathiel")
    monkeypatch.setenv("PATHIEL_HOME", "/srv/.pathiel")
    monkeypatch.setenv("PATHIEL_DATA", "/srv/.pathiel/data")
    monkeypatch.setenv("PATHIEL_VENV", "/srv/.pathiel/data/venv")
    cmd = join.master_command("join")
    for name in names:
        assert f"{name}=" in cmd, name
    tokens = shlex.split(cmd)
    assert tokens[0] == "env" and tokens[-4:] == ["services.pool", "join", "--master-prompt", "--yes"]
