"""services/pool/join.py: prepare, confirm, status. Offline, under the
pool_env fixture's throwaway state and repo roots."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pytest

import contracts.pool as pool
from services.pool import join, paths, signing

FIXTURES = Path(pool.__file__).resolve().parent / "fixtures"


def _configure(monkeypatch, acct) -> None:
    import pathiel.client.exchange as exchange
    key = acct.key.hex()
    monkeypatch.setattr(exchange, "_resolve_identity", lambda: (key, acct.address, None, False))


def _configure_agent(monkeypatch, main, agent) -> None:
    import pathiel.client.exchange as exchange
    key = agent.key.hex()
    monkeypatch.setattr(exchange, "_resolve_identity", lambda: (key, main.address, agent.address, True))


# ── prepare ──────────────────────────────────────────────────────────────────

def test_prepare_signs_and_writes_nothing(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    called = []
    monkeypatch.setattr(signing, "sign_configured", lambda message: called.append(message) or ("x", "y"))
    out = join.prepare("join")
    assert called == []
    assert out["account"] == out["signer"] == acct.address.lower()
    assert out["signer_role"] == "master"
    assert out["message"] == pool.statement_text("join", acct.address, acct.address, out["joined_at"])
    assert out["published"] == list(pool.PUBLISHED) and out["not_published"] == list(pool.NOT_PUBLISHED)
    outbox_members = os.path.join(paths.outbox_root(), pool.MEMBERS_SUBDIR)
    assert not os.path.isdir(outbox_members) or os.listdir(outbox_members) == []


def test_prepare_leave_shows_the_effect_text(pool_env, monkeypatch, new_account, signed, put):
    acct = new_account()
    _configure(monkeypatch, acct)
    put(signed("join", acct), root=paths.repo_pool_root())
    out = join.prepare("leave")
    assert out["left_at"]
    assert "effect" in out and "published" not in out
    assert out["effect"] == join.LEAVE_EFFECT


def test_prepare_refuses_testnet(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")
    with pytest.raises(pool.PoolError) as e:
        join.prepare("join")
    assert e.value.code == "testnet"


def test_prepare_refuses_no_key(pool_env):
    with pytest.raises(pool.PoolError) as e:
        join.prepare("join")
    assert e.value.code == "no_key"


def test_prepare_refuses_no_account_with_master_prompt(pool_env, monkeypatch):
    monkeypatch.setattr("services.pool.account.configured_account", lambda: "")
    with pytest.raises(pool.PoolError) as e:
        join.prepare("join", master_prompt=True)
    assert e.value.code == "no_account"


def test_prepare_already_joined(pool_env, monkeypatch, new_account, signed, put):
    acct = new_account()
    _configure(monkeypatch, acct)
    put(signed("join", acct, at="2026-11-12T10:00:00Z"), root=paths.repo_pool_root())
    with pytest.raises(pool.PoolError) as e:
        join.prepare("join")
    assert e.value.code == "already_joined" and "2026-11-12T10:00:00Z" in str(e.value)


def test_prepare_house_row_refuses_to_join(pool_env, monkeypatch, new_account):
    from contracts import runtime
    monkeypatch.setattr("services.pool.account.configured_account", lambda: runtime.DEFAULT_BUILDER_ADDRESS.lower())
    (Path(paths.repo_pool_root()) / pool.HOUSE_FILE).write_text(
        json.dumps({"schema": 1, "kind": "house", "network": "mainnet",
                   "address": runtime.DEFAULT_BUILDER_ADDRESS.lower(),
                   "count_from": pool.HOUSE_COUNT_FROM, "label": "maintainer"}), encoding="ascii")
    with pytest.raises(pool.PoolError) as e:
        join.prepare("join", master_prompt=True)
    assert e.value.code == "house"


def test_prepare_leave_when_not_a_member(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    with pytest.raises(pool.PoolError) as e:
        join.prepare("leave")
    assert e.value.code == "not_member"


def test_prepare_invalid_local_files(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    members = Path(paths.repo_pool_root()) / pool.MEMBERS_SUBDIR
    members.mkdir(parents=True, exist_ok=True)
    (members / f"{acct.address.lower()}.json").write_text("{}", encoding="ascii")
    with pytest.raises(pool.PoolError) as e:
        join.prepare("join")
    assert e.value.code == "invalid_local_files"


# ── confirm ──────────────────────────────────────────────────────────────────

def test_confirm_writes_a_file_that_verifies_and_matches_the_dry_run(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    dry = join.prepare("join")
    done = join.confirm("join", dry["joined_at"])
    assert done["statement_sha256"] == dry["statement_sha256"]
    v = pool.load_statement(done["file"], "join")
    assert v["address"] == acct.address.lower() and v["signer_role"] == "master"
    assert done["file"] == pool.member_path(paths.outbox_root(), acct.address)
    assert done["repo_path"] == pool.member_repo_path(acct.address)
    assert Path(done["file"]).read_text(encoding="ascii") == done["content"]
    standing = pool.local_standing(paths.repo_pool_root(), paths.outbox_root(), acct.address)
    assert standing["state"] == "signed_not_merged"


def test_confirm_file_bytes_equal_dump_statement_file(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    dry = join.prepare("join")
    done = join.confirm("join", dry["joined_at"])
    doc = json.loads(done["content"])
    assert done["content"] == pool.dump_statement_file(doc)


def test_confirm_refuses_a_time_older_than_fifteen_minutes(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    dry = join.prepare("join")
    now = pool.ms_from_iso(dry["joined_at"]) + pool.CONFIRM_MAX_AGE_S * 1000 + 1000
    with pytest.raises(pool.PoolError) as e:
        join.confirm("join", dry["joined_at"], now_ms=now)
    assert e.value.code == "expired"


def test_confirm_refuses_a_future_time(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    now = pool.ms_from_iso("2026-11-12T12:00:00Z")
    at = pool.iso_from_ms(now + 121_000)
    with pytest.raises(pool.PoolError) as e:
        join.confirm("join", at, now_ms=now)
    assert e.value.code == "expired"


def test_confirm_refuses_a_malformed_time(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    with pytest.raises(pool.PoolError) as e:
        join.confirm("join", "not-a-time")
    assert e.value.code == "time"


def test_confirm_refuses_testnet(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")
    with pytest.raises(pool.PoolError) as e:
        join.confirm("join", pool.iso_from_ms(pool.ms_from_iso("2026-11-12T12:00:00Z")))
    assert e.value.code == "testnet"


def test_confirm_refuses_no_key(pool_env):
    with pytest.raises(pool.PoolError) as e:
        join.confirm("join", "2026-11-12T12:00:00Z",
                     now_ms=pool.ms_from_iso("2026-11-12T12:00:00Z"))
    assert e.value.code == "no_key"


def test_confirm_refuses_no_account(pool_env, monkeypatch):
    monkeypatch.setattr("services.pool.account.configured_account", lambda: "")
    with pytest.raises(pool.PoolError) as e:
        join.confirm("join", "2026-11-12T12:00:00Z", master_prompt=True,
                     now_ms=pool.ms_from_iso("2026-11-12T12:00:00Z"))
    assert e.value.code == "no_account"


def test_confirm_already_joined(pool_env, monkeypatch, new_account, signed, put):
    acct = new_account()
    _configure(monkeypatch, acct)
    put(signed("join", acct, at="2026-11-12T10:00:00Z"), root=paths.repo_pool_root())
    with pytest.raises(pool.PoolError) as e:
        join.confirm("join", "2026-11-12T10:05:00Z", now_ms=pool.ms_from_iso("2026-11-12T10:05:00Z"))
    assert e.value.code == "already_joined"


def test_confirm_house(pool_env, monkeypatch):
    from contracts import runtime
    monkeypatch.setattr("services.pool.account.configured_account", lambda: runtime.DEFAULT_BUILDER_ADDRESS.lower())
    (Path(paths.repo_pool_root()) / pool.HOUSE_FILE).write_text(
        json.dumps({"schema": 1, "kind": "house", "network": "mainnet",
                   "address": runtime.DEFAULT_BUILDER_ADDRESS.lower(),
                   "count_from": pool.HOUSE_COUNT_FROM, "label": "maintainer"}), encoding="ascii")
    with pytest.raises(pool.PoolError) as e:
        join.confirm("join", "2026-11-12T10:05:00Z", master_prompt=True,
                     now_ms=pool.ms_from_iso("2026-11-12T10:05:00Z"))
    assert e.value.code == "house"


def test_confirm_leave_when_not_a_member(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    with pytest.raises(pool.PoolError) as e:
        join.confirm("leave", "2026-11-12T10:05:00Z", now_ms=pool.ms_from_iso("2026-11-12T10:05:00Z"))
    assert e.value.code == "not_member"


def test_join_then_leave_gives_standing_left_or_leave_signed_not_merged(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    joined = join.confirm("join", join.prepare("join")["joined_at"])
    standing = pool.local_standing(paths.repo_pool_root(), paths.outbox_root(), acct.address)
    assert standing["state"] == "signed_not_merged"

    join.confirm("leave", join.prepare("leave")["left_at"])
    standing = pool.local_standing(paths.repo_pool_root(), paths.outbox_root(), acct.address)
    assert standing["state"] == "left"

    # merge the join into the repo copy: the repo now shows "member", so a
    # pending local leave reads leave_signed_not_merged instead of left.
    repo_member = Path(pool.member_path(paths.repo_pool_root(), acct.address))
    repo_member.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(joined["file"], repo_member)
    standing = pool.local_standing(paths.repo_pool_root(), paths.outbox_root(), acct.address)
    assert standing["state"] == "leave_signed_not_merged"


def test_rejoin_after_leave_gives_a_new_time_with_no_reinstatement_problem(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    t1 = join.confirm("join", join.prepare("join", now_ms=pool.ms_from_iso("2026-11-10T10:00:00Z"))["joined_at"],
                      now_ms=pool.ms_from_iso("2026-11-10T10:00:00Z"))
    os.makedirs(os.path.dirname(pool.member_path(paths.repo_pool_root(), acct.address)), exist_ok=True)
    shutil.copy(t1["file"], pool.member_path(paths.repo_pool_root(), acct.address))

    t2 = join.confirm("leave", join.prepare("leave", now_ms=pool.ms_from_iso("2026-11-11T10:00:00Z"))["left_at"],
                      now_ms=pool.ms_from_iso("2026-11-11T10:00:00Z"))
    os.makedirs(os.path.dirname(pool.leave_path(paths.repo_pool_root(), acct.address)), exist_ok=True)
    shutil.copy(t2["file"], pool.leave_path(paths.repo_pool_root(), acct.address))

    t3 = join.confirm("join", join.prepare("join", now_ms=pool.ms_from_iso("2026-11-12T10:00:00Z"))["joined_at"],
                      now_ms=pool.ms_from_iso("2026-11-12T10:00:00Z"))
    before = pool.membership(paths.repo_pool_root(), acct.address)
    shutil.copy(t3["file"], pool.member_path(paths.repo_pool_root(), acct.address))
    after = pool.membership(paths.repo_pool_root(), acct.address)
    assert pool.reinstatement_problem(before, after) is None
    assert after["join"]["at"] == "2026-11-12T10:00:00Z"


def test_confirm_pr_url_decodes_back_to_path_and_content(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    done = join.confirm("join", join.prepare("join")["joined_at"])
    parsed = urlparse(done["pr_url"])
    q = parse_qs(parsed.query, keep_blank_values=True)
    assert unquote(q["filename"][0]) == done["repo_path"]
    assert q["value"][0] == done["content"]
    assert "pr_note" not in done


def test_confirm_gives_an_edit_link_when_the_repo_copy_already_has_the_file(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    first = join.confirm("join", join.prepare("join", now_ms=pool.ms_from_iso("2026-11-10T10:00:00Z"))["joined_at"],
                         now_ms=pool.ms_from_iso("2026-11-10T10:00:00Z"))
    repo_file = Path(pool.member_path(paths.repo_pool_root(), acct.address))
    repo_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(first["file"], repo_file)

    # a second leave: sign one, merge it, then sign another (a replacement)
    leave1 = join.confirm("leave", join.prepare("leave", now_ms=pool.ms_from_iso("2026-11-11T10:00:00Z"))["left_at"],
                          now_ms=pool.ms_from_iso("2026-11-11T10:00:00Z"))
    leave_repo_file = Path(pool.leave_path(paths.repo_pool_root(), acct.address))
    leave_repo_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(leave1["file"], leave_repo_file)

    rejoin = join.confirm("join", join.prepare("join", now_ms=pool.ms_from_iso("2026-11-12T10:00:00Z"))["joined_at"],
                          now_ms=pool.ms_from_iso("2026-11-12T10:00:00Z"))
    assert rejoin["pr_url"] == f"https://github.com/{pool.REPO_SLUG}/edit/{pool.DEFAULT_BRANCH}/{rejoin['repo_path']}"
    assert "replace" in rejoin["pr_note"].lower()


def test_confirm_signer_changed_when_the_key_flips_mid_call(pool_env, monkeypatch, new_account):
    """confirm() reads the identity twice (once to predict the signer, once
    inside sign_configured to actually sign): if the configured key changes
    between those two reads, the predicted and actual signer disagree."""
    acct, other = new_account(), new_account()
    import pathiel.client.exchange as exchange
    calls = []

    def flipping():
        calls.append(1)
        return (acct.key.hex(), acct.address, None, False) if len(calls) == 1 \
            else (other.key.hex(), other.address, None, False)
    monkeypatch.setattr(exchange, "_resolve_identity", flipping)
    now = pool.ms_from_iso("2026-11-12T10:00:00Z")
    with pytest.raises(pool.PoolError) as e:
        join.confirm("join", pool.iso_from_ms(now), now_ms=now)
    assert e.value.code == "signer_changed"
    assert len(calls) == 2


# ── master-prompt flow ───────────────────────────────────────────────────────

def test_confirm_master_prompt_signs_with_the_typed_key(pool_env, monkeypatch, new_account):
    acct = new_account()
    monkeypatch.setattr("services.pool.account.configured_account", lambda: acct.address.lower())
    dry = join.prepare("join", master_prompt=True)
    done = join.confirm("join", dry["joined_at"], master_prompt=True, getpass_fn=lambda prompt: acct.key.hex(),
                        isatty=lambda: True)
    assert pool.load_statement(done["file"], "join")["signer"] == acct.address.lower()


def test_confirm_master_prompt_wrong_key(pool_env, monkeypatch, new_account):
    acct, other = new_account(), new_account()
    monkeypatch.setattr("services.pool.account.configured_account", lambda: acct.address.lower())
    dry = join.prepare("join", master_prompt=True)
    with pytest.raises(pool.PoolError) as e:
        join.confirm("join", dry["joined_at"], master_prompt=True, getpass_fn=lambda prompt: other.key.hex(),
                     isatty=lambda: True)
    assert e.value.code == "wrong_key"


# ── status ───────────────────────────────────────────────────────────────────

def test_status_with_no_account(pool_env, monkeypatch):
    monkeypatch.setattr("services.pool.account.configured_account", lambda: "")
    out = join.status()
    assert out == {
        "ok": True, "network": "mainnet", "account": None, "state": "no_account",
        "joined_at": None, "left_at": None, "published": list(pool.PUBLISHED),
        "not_published": list(pool.NOT_PUBLISHED), "pool": None, "scoreboard_url": pool.SCOREBOARD_URL,
        "how_to_join": "/pathiel:share", "how_to_leave": "/pathiel:share leave, or ask the maintainer in an issue",
        "copy_note": out["copy_note"], "problems": [],
    }
    assert "scoreboard" in out["copy_note"] or "repository" in out["copy_note"]


def test_status_member_with_no_scoreboard(pool_env, monkeypatch, new_account, signed, put):
    acct = new_account()
    monkeypatch.setattr("services.pool.account.configured_account", lambda: acct.address.lower())
    put(signed("join", acct, at="2026-11-12T10:00:00Z"), root=paths.repo_pool_root())
    out = join.status()
    assert out["state"] == "member" and out["joined_at"] == "2026-11-12T10:00:00Z"
    assert out["pool"] is None


def test_status_reads_the_shipped_scoreboard_counts(pool_env, monkeypatch, new_account, signed, put):
    acct = new_account()
    monkeypatch.setattr("services.pool.account.configured_account", lambda: acct.address.lower())
    put(signed("join", acct, at="2026-11-12T10:00:00Z"), root=paths.repo_pool_root())
    board = json.loads((FIXTURES / "scoreboard.json").read_text(encoding="ascii"))
    board_dir = Path(paths.scoreboard_dir())
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / pool.SCOREBOARD_JSON).write_text(pool.dump_scoreboard(board), encoding="ascii")
    out = join.status()
    assert out["pool"] == {"day": board["day"], "cutoff": board["cutoff"], "counts": board["counts"]}


def test_status_an_invalid_scoreboard_reads_as_none(pool_env, monkeypatch, new_account):
    acct = new_account()
    monkeypatch.setattr("services.pool.account.configured_account", lambda: acct.address.lower())
    board_dir = Path(paths.scoreboard_dir())
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / pool.SCOREBOARD_JSON).write_text('{"not": "a scoreboard"}', encoding="ascii")
    out = join.status()
    assert out["pool"] is None


# ── master_command ───────────────────────────────────────────────────────────

def test_master_command_without_pathiel_root_uses_sys_executable(monkeypatch):
    import sys
    monkeypatch.delenv("PATHIEL_ROOT", raising=False)
    cmd = join.master_command("join")
    assert cmd.startswith("cd ")
    assert sys.executable in cmd
    assert "python3" not in cmd.split("&&", 1)[1].split()[0:1]
    assert cmd.endswith("-m services.pool join --master-prompt --yes")


def test_master_command_with_pathiel_root_uses_the_launcher_variables(monkeypatch):
    import shlex
    monkeypatch.setenv("PATHIEL_ROOT", "/opt/pathiel")
    monkeypatch.setenv("PATHIEL_HOME", "/srv/.pathiel")
    monkeypatch.setenv("PATHIEL_DATA", "/srv/.pathiel/data")
    monkeypatch.setenv("PATHIEL_VENV", "/srv/.pathiel/data/venv")
    cmd = join.master_command("leave")
    assert shlex.split(cmd) == [
        "env", "CLAUDE_PLUGIN_DATA=/srv/.pathiel/data", "PATHIEL_HOME=/srv/.pathiel",
        "PATHIEL_PYTHON=/srv/.pathiel/data/venv/bin/python", "/opt/pathiel/scripts/pathiel-py",
        "-m", "services.pool", "leave", "--master-prompt", "--yes",
    ]


def test_master_command_paths_with_spaces(monkeypatch):
    import shlex
    monkeypatch.setenv("PATHIEL_ROOT", "/opt/path iel")
    monkeypatch.setenv("PATHIEL_HOME", "/srv/a b/.pathiel")
    monkeypatch.setenv("PATHIEL_DATA", "/srv/a b/.pathiel/data")
    monkeypatch.delenv("PATHIEL_VENV", raising=False)
    cmd = join.master_command("join")
    assert shlex.split(cmd) == [
        "env", "CLAUDE_PLUGIN_DATA=/srv/a b/.pathiel/data", "PATHIEL_HOME=/srv/a b/.pathiel",
        "PATHIEL_PYTHON=/srv/a b/.pathiel/data/venv/bin/python", "/opt/path iel/scripts/pathiel-py",
        "-m", "services.pool", "join", "--master-prompt", "--yes",
    ]
