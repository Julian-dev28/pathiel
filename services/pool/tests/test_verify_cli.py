"""services/pool/verify_cli.py: offline, --paths, and --pr-base. The
--pr-base tests build a real throwaway git repository under tmp_path; no
network, no real pathiel checkout is touched."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict

import pytest

import contracts.pool as pool
from services.pool import venue, verify_cli

NOW = pool.ms_from_iso("2026-11-20T12:00:00Z")


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    monkeypatch.setattr(verify_cli, "_now_ms", lambda: NOW)


# ── offline ──────────────────────────────────────────────────────────────────

def _write_statement(root: Path, kind: str, doc: Dict[str, Any]) -> Path:
    sub = pool.MEMBERS_SUBDIR if kind == "join" else pool.LEAVES_SUBDIR
    folder = root / pool.POOL_DIR / sub
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / pool.statement_filename(doc["address"])
    path.write_text(pool.dump_statement_file(doc), encoding="ascii")
    return path


def test_offline_pass(tmp_path, new_account, signed):
    acct = new_account()
    (tmp_path / pool.POOL_DIR / pool.MEMBERS_SUBDIR).mkdir(parents=True)
    _write_statement(tmp_path, "join", signed("join", acct))
    assert verify_cli.main(["verify", "--root", str(tmp_path)]) == 0


def test_offline_fail_on_a_broken_file(tmp_path, capsys):
    folder = tmp_path / pool.POOL_DIR / pool.MEMBERS_SUBDIR
    folder.mkdir(parents=True)
    (folder / ("0x" + "ab" * 20 + ".json")).write_text("{}", encoding="ascii")
    assert verify_cli.main(["verify", "--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "POOL-VERIFY mode=offline files=1 ok=0 fail=1" in out


def test_offline_json_shape(tmp_path, new_account, signed):
    acct = new_account()
    (tmp_path / pool.POOL_DIR / pool.MEMBERS_SUBDIR).mkdir(parents=True)
    _write_statement(tmp_path, "join", signed("join", acct))
    assert verify_cli.main(["verify", "--root", str(tmp_path), "--json"]) == 0
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        verify_cli.main(["verify", "--root", str(tmp_path), "--json"])
    out = json.loads(buf.getvalue())
    assert out["mode"] == "offline" and out["ok"] is True and len(out["files"]) == 1
    assert out["files"][0]["address"] == acct.address.lower()


def test_last_line_carries_at(tmp_path, capsys):
    (tmp_path / pool.POOL_DIR / pool.MEMBERS_SUBDIR).mkdir(parents=True)
    verify_cli.main(["verify", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert f"at={pool.iso_from_ms(NOW)}" in out


def test_stale_scoreboard_note(tmp_path, capsys):
    (tmp_path / pool.POOL_DIR / pool.MEMBERS_SUBDIR).mkdir(parents=True)
    fixtures = Path(pool.__file__).resolve().parent / "fixtures"
    board = json.loads((fixtures / "scoreboard.json").read_text(encoding="ascii"))
    assert pool.validate_scoreboard(board) == []
    board_dir = tmp_path / pool.SCOREBOARD_DIR
    board_dir.mkdir(parents=True)
    (board_dir / pool.SCOREBOARD_JSON).write_text(pool.dump_scoreboard(board), encoding="ascii")
    assert verify_cli.main(["verify", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "NOTE" in out and "no longer active" in out and "services.pool build" in out


# ── --paths ──────────────────────────────────────────────────────────────────

def test_paths_fresh_rejects_a_stale_file(tmp_path, new_account, signed):
    acct = new_account()
    old = pool.iso_from_ms(NOW - pool.FRESH_MAX_AGE_S * 1000 - 1000)
    path = _write_statement(tmp_path, "join", signed("join", acct, at=old))
    assert verify_cli.main(["verify", "--paths", str(path), "--fresh"]) == 1


def test_paths_fresh_accepts_a_fresh_file(tmp_path, new_account, signed):
    acct = new_account()
    path = _write_statement(tmp_path, "join", signed("join", acct, at=pool.iso_from_ms(NOW)))
    assert verify_cli.main(["verify", "--paths", str(path), "--fresh"]) == 0


def test_paths_online_agent_approved(tmp_path, new_account, monkeypatch):
    main, agent = new_account(), new_account()
    (tmp_path / pool.POOL_DIR / pool.MEMBERS_SUBDIR).mkdir(parents=True)

    def fake_post(payload):
        if payload["type"] == "extraAgents":
            return [{"name": "p", "address": agent.address.lower(), "validUntil": NOW + 1}]
        return {"role": "user"}
    monkeypatch.setattr(venue, "post_info", fake_post)
    doc = _build_join(main, agent, pool.iso_from_ms(NOW))
    p = _write_statement(tmp_path, "join", doc)
    assert verify_cli.main(["verify", "--paths", str(p), "--online"]) == 0


def test_paths_online_agent_not_approved(tmp_path, new_account, monkeypatch):
    main, agent = new_account(), new_account()
    tmp_path_members = tmp_path / pool.POOL_DIR / pool.MEMBERS_SUBDIR
    tmp_path_members.mkdir(parents=True)

    def fake_post(payload):
        return [] if payload["type"] == "extraAgents" else {"role": "user"}
    monkeypatch.setattr(venue, "post_info", fake_post)
    doc = _build_join(main, agent, pool.iso_from_ms(NOW))
    p = _write_statement(tmp_path, "join", doc)
    assert verify_cli.main(["verify", "--paths", str(p), "--online"]) == 1


def _build_join(account, signer, at: str) -> Dict[str, Any]:
    from eth_account import Account
    from eth_account.messages import encode_defunct
    message = pool.statement_text("join", account.address, signer.address, at)
    sig = Account.sign_message(encode_defunct(text=message), signer.key).signature.hex()
    return pool.build_statement_file("join", account.address, signer.address, at, "0x" + sig.removeprefix("0x"))


# ── --pr-base: a throwaway git repository ───────────────────────────────────

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    # Strip every GIT_* variable, then set only author/committer: GIT_DIR and
    # (above all) GIT_INDEX_FILE override `-C` outright, and this process
    # inherits GIT_INDEX_FILE pointing at the REAL repository's in-progress
    # commit when the gate runs inside git's own pre-commit hook. Without
    # this, `git -C <this throwaway repo> commit` silently commits into that
    # index instead, corrupting it. Confirmed with GIT_DIR set: `git -C
    # <throwaway> rev-parse --show-toplevel` fails rather than naming it.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="pathiel-test", GIT_AUTHOR_EMAIL="test@pathiel.invalid",
              GIT_COMMITTER_NAME="pathiel-test", GIT_COMMITTER_EMAIL="test@pathiel.invalid")
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    assert res.returncode == 0, f"git {args} failed: {res.stderr}"
    return res


def _rev(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _rev(repo)


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "git_repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / pool.POOL_DIR / pool.MEMBERS_SUBDIR).mkdir(parents=True)
    (r / pool.POOL_DIR / pool.LEAVES_SUBDIR).mkdir(parents=True)
    (r / pool.POOL_DIR / pool.MEMBERS_SUBDIR / ".gitkeep").write_text("")
    (r / pool.POOL_DIR / pool.LEAVES_SUBDIR / ".gitkeep").write_text("")
    _commit(r, "base")
    return r


def _put(repo_root: Path, kind: str, doc: Dict[str, Any]) -> Path:
    return _write_statement(repo_root, kind, doc)


def _rm(repo_root: Path, kind: str, address: str) -> None:
    sub = pool.MEMBERS_SUBDIR if kind == "join" else pool.LEAVES_SUBDIR
    (repo_root / pool.POOL_DIR / sub / pool.statement_filename(address)).unlink()


def _run(repo_root: Path, ref: str, *, extra=()) -> tuple:
    import io
    import contextlib
    buf = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        code = verify_cli.main(["verify", "--root", str(repo_root), "--pr-base", ref, *extra])
    return code, buf.getvalue(), err.getvalue()


def test_no_membership_change_is_fast_and_reads_no_venue(repo, new_account, monkeypatch):
    ref = _rev(repo)
    (repo / "README.md").write_text("hello\n", encoding="ascii")
    _commit(repo, "unrelated")

    def boom(payload):
        raise AssertionError("must not read the venue when nothing pool-related changed")
    monkeypatch.setattr(venue, "post_info", boom)
    code, out, _err = _run(repo, ref)
    assert code == 0 and "no membership change" in out


def test_one_valid_join(repo, new_account, signed):
    ref = _rev(repo)
    acct = new_account()
    _put(repo, "join", signed("join", acct, at=pool.iso_from_ms(NOW - 1000)))
    _commit(repo, "join")
    code, out, _err = _run(repo, ref)
    assert code == 0 and "OK" in out


def test_mixed_change(repo, new_account, signed):
    ref = _rev(repo)
    acct = new_account()
    _put(repo, "join", signed("join", acct, at=pool.iso_from_ms(NOW - 1000)))
    (repo / "unrelated.txt").write_text("x\n", encoding="ascii")
    _commit(repo, "mixed")
    code, out, _err = _run(repo, ref)
    assert code == 1 and "mixed_change" in out


def test_too_many_files(repo, new_account, signed):
    ref = _rev(repo)
    a, b = new_account(), new_account()
    _put(repo, "join", signed("join", a, at=pool.iso_from_ms(NOW - 1000)))
    _put(repo, "join", signed("join", b, at=pool.iso_from_ms(NOW - 1000)))
    _commit(repo, "two addresses")
    code, out, _err = _run(repo, ref)
    assert code == 1 and "too_many" in out


def test_stale_file(repo, new_account, signed):
    ref = _rev(repo)
    acct = new_account()
    old = pool.iso_from_ms(NOW - pool.FRESH_MAX_AGE_S * 1000 - 1000)
    _put(repo, "join", signed("join", acct, at=old))
    _commit(repo, "stale join")
    code, out, _err = _run(repo, ref)
    assert code == 1 and "stale" in out


def test_unapproved_agent(repo, new_account, monkeypatch):
    ref = _rev(repo)
    main, agent = new_account(), new_account()
    _put(repo, "join", _build_join(main, agent, pool.iso_from_ms(NOW - 1000)))
    _commit(repo, "agent join")

    def fake_post(payload):
        return [] if payload["type"] == "extraAgents" else {"role": "user"}
    monkeypatch.setattr(venue, "post_info", fake_post)
    code, out, _err = _run(repo, ref)
    assert code == 1 and "agent_not_approved" in out


def test_a_deleted_member_file_is_a_note(repo, new_account, signed):
    acct = new_account()
    _put(repo, "join", signed("join", acct, at=pool.iso_from_ms(NOW - 10_000)))
    _commit(repo, "join")
    ref = _rev(repo)
    _rm(repo, "join", acct.address.lower())
    _commit(repo, "remove")
    code, out, _err = _run(repo, ref)
    assert code == 0 and "NOTE" in out and "the maintainer confirms" in out


def test_venue_error_exits_2(repo, new_account, monkeypatch):
    ref = _rev(repo)
    main, agent = new_account(), new_account()
    _put(repo, "join", _build_join(main, agent, pool.iso_from_ms(NOW - 1000)))
    _commit(repo, "agent join")

    def boom(payload):
        raise venue.VenueError("down")
    monkeypatch.setattr(venue, "post_info", boom)
    code, _out, err = _run(repo, ref)
    assert code == 2 and "venue" in err


# ── reinstated: refused ──────────────────────────────────────────────────────

def test_reinstated_a_deleted_leave_while_member_stays(repo, new_account, signed):
    """The review finding of 2026-11-12: deleting the leave file while the
    member file stays would put the old join back in force. Also shows the
    hole this closes: a plain offline scan of the resulting tree WOULD list
    the address as a member again, from the OLD joined_at."""
    acct = new_account()
    t_join, t_leave = "2026-11-18T10:00:00Z", "2026-11-19T10:00:00Z"
    _put(repo, "join", signed("join", acct, at=t_join))
    _put(repo, "leave", signed("leave", acct, at=t_leave))
    _commit(repo, "join then leave")
    ref = _rev(repo)
    _rm(repo, "leave", acct.address.lower())
    _commit(repo, "delete the leave only")

    scanned = pool.scan(str(repo / pool.POOL_DIR), NOW)
    assert [m["address"] for m in scanned["members"]] == [acct.address.lower()]
    assert scanned["members"][0]["count_from"] == t_join

    code, out, _err = _run(repo, ref)
    assert code == 1 and "reinstated" in out


def test_reinstated_b_leave_replaced_by_an_older_one(repo, new_account, signed):
    acct = new_account()
    t_join, t_leave = "2026-11-10T10:00:00Z", "2026-11-19T10:00:00Z"
    _put(repo, "join", signed("join", acct, at=t_join))
    _put(repo, "leave", signed("leave", acct, at=t_leave))
    _commit(repo, "join then leave")
    ref = _rev(repo)
    older_leave = "2026-11-05T10:00:00Z"   # before the join
    _put(repo, "leave", signed("leave", acct, at=older_leave))
    _commit(repo, "swap in an older leave")
    code, out, _err = _run(repo, ref)
    assert code == 1 and "reinstated" in out


def test_reinstated_c_member_file_restored_from_history(repo, new_account, signed):
    acct = new_account()
    t_join = "2026-11-10T10:00:00Z"
    doc = signed("join", acct, at=t_join)
    _put(repo, "join", doc)
    _commit(repo, "join")
    _rm(repo, "join", acct.address.lower())
    _commit(repo, "remove it")
    ref = _rev(repo)
    _put(repo, "join", doc)     # the exact same statement, put back
    _commit(repo, "put it back")
    code, out, _err = _run(repo, ref)
    assert code == 1 and "reinstated" in out


def test_reinstated_d_member_file_swapped_for_an_earlier_join(repo, new_account, signed):
    acct = new_account()
    _put(repo, "join", signed("join", acct, at="2026-11-15T10:00:00Z"))
    _commit(repo, "join")
    ref = _rev(repo)
    _put(repo, "join", signed("join", acct, at="2026-11-01T10:00:00Z"))   # earlier: counts further back
    _commit(repo, "swap for an earlier join")
    code, out, _err = _run(repo, ref)
    assert code == 1 and "reinstated" in out


# ── reinstated-adjacent: allowed ─────────────────────────────────────────────

def test_allowed_leave_deleted_together_with_its_member_file(repo, new_account, signed):
    acct = new_account()
    _put(repo, "join", signed("join", acct, at="2026-11-10T10:00:00Z"))
    _put(repo, "leave", signed("leave", acct, at="2026-11-11T10:00:00Z"))
    _commit(repo, "join then leave")
    ref = _rev(repo)
    _rm(repo, "join", acct.address.lower())
    _rm(repo, "leave", acct.address.lower())
    _commit(repo, "remove both")
    code, out, _err = _run(repo, ref)
    assert code == 0 and "reinstated" not in out


def test_allowed_rejoin_deleting_the_old_leave(repo, new_account, signed):
    acct = new_account()
    _put(repo, "join", signed("join", acct, at="2026-11-10T10:00:00Z"))
    _put(repo, "leave", signed("leave", acct, at="2026-11-11T10:00:00Z"))
    _commit(repo, "join then leave")
    ref = _rev(repo)
    _rm(repo, "leave", acct.address.lower())
    _put(repo, "join", signed("join", acct, at="2026-11-19T10:00:00Z"))
    _commit(repo, "rejoin, dropping the old leave")
    code, out, _err = _run(repo, ref)
    assert code == 0 and "reinstated" not in out


def test_allowed_rejoin_without_deleting_the_old_leave(repo, new_account, signed):
    acct = new_account()
    _put(repo, "join", signed("join", acct, at="2026-11-10T10:00:00Z"))
    _put(repo, "leave", signed("leave", acct, at="2026-11-11T10:00:00Z"))
    _commit(repo, "join then leave")
    ref = _rev(repo)
    _put(repo, "join", signed("join", acct, at="2026-11-19T10:00:00Z"))   # the old leave file stays
    _commit(repo, "rejoin, leaving the old leave file in place")
    code, out, _err = _run(repo, ref)
    assert code == 0 and "reinstated" not in out


def test_allowed_first_join(repo, new_account, signed):
    ref = _rev(repo)
    acct = new_account()
    _put(repo, "join", signed("join", acct, at=pool.iso_from_ms(NOW - 1000)))
    _commit(repo, "first join")
    code, out, _err = _run(repo, ref)
    assert code == 0 and "reinstated" not in out


# ── guards ───────────────────────────────────────────────────────────────────

def test_not_merged_exits_2(repo):
    first = _rev(repo)
    (repo / "x.txt").write_text("x\n", encoding="ascii")
    second = _commit(repo, "second")
    _git(repo, "checkout", "-q", first)
    code, _out, err = _run(repo, second)
    assert code == 2 and "not_merged" in err


def test_shallow_exits_2(repo, tmp_path):
    (repo / "x.txt").write_text("x\n", encoding="ascii")
    _commit(repo, "second")
    shallow = tmp_path / "shallow"
    # Not through `_git` (no `-C`: clone takes its destination as a plain
    # argument), but the same stripped env: `git clone` writes a fresh index
    # for the new clone, and an inherited GIT_INDEX_FILE (set, as a RELATIVE
    # path, by git's own pre-commit hook machinery) can redirect where that
    # write lands. This is the exact call that corrupted this worktree's
    # real index while this file was being written: confirmed by reproducing
    # it, then clearing by this fix.
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "clone", "--depth", "1", f"file://{repo}", str(shallow)],
                   capture_output=True, text=True, check=True, env=clean_env)
    code, _out, err = _run(shallow, "HEAD")
    assert code == 2 and "shallow" in err
