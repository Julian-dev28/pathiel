"""python -m services.pool build, end to end in the test repo root
(pool_env, conftest.py). Offline: every scenario uses --offline, except the
venue-error and testnet checks, which never reach the network (the root
conftest also blocks any socket)."""
from __future__ import annotations

import json
import os

import pytest

import contracts.pool as pool
from contracts import runtime
from services.pool import build_cli as BC
from services.pool import fetch as F
from services.pool import paths
from services.pool import venue
from services.pool.tests import pool_fakes as PF

AS_OF_ISO = "2026-11-14T12:00:00Z"
AS_OF_MS = pool.ms_from_iso(AS_OF_ISO)
COIN = "xyz:BRENTOIL"


def _house(pool_env, count_from="2026-10-20T00:00:00Z"):
    doc = {"schema": pool.FILE_SCHEMA, "kind": "house", "network": pool.NETWORK,
          "address": runtime.DEFAULT_BUILDER_ADDRESS.lower(), "count_from": count_from, "label": "maintainer"}
    with open(os.path.join(str(pool_env["repo"]), pool.POOL_DIR, pool.HOUSE_FILE), "w") as fh:
        json.dump(doc, fh)
    return doc["address"]


def _join(new_account, signed, put, at="2026-11-12T00:00:00Z"):
    acct = new_account()
    put(signed("join", acct, at=at))
    return acct


def _bundle_for(tmp_path, name, address, as_of_ms=AS_OF_MS, fills=None, orders=None):
    d = str(tmp_path / name)
    F.save_bundle(d, {"as_of_ms": as_of_ms, "inputs": {address: {"fills": fills or [], "orders": orders or []}},
                      "bars5": {}, "bars1h": {}})
    return d


def _empty_bundle(tmp_path, name="empty", as_of_ms=AS_OF_MS):
    d = str(tmp_path / name)
    F.save_bundle(d, {"as_of_ms": as_of_ms, "inputs": {}, "bars5": {}, "bars1h": {}})
    return d


def _run(argv, capsys):
    rc = BC.main(["build"] + argv)
    out, err = capsys.readouterr()
    return rc, out, err


def test_offline_end_to_end_exit_0_scoreboard_written_and_valid(pool_env, new_account, signed, put, tmp_path,
                                                                capsys):
    acct = _join(new_account, signed, put)
    fills, orders = PF.bracket_trade(COIN, "long", 70.0, 69.0, 5.0, pool.ms_from_iso("2026-10-21T00:00:00Z"),
                                     target_px=72.0)
    bundle = _bundle_for(tmp_path, "b1", acct.address.lower(), fills=fills, orders=orders)
    rc, out, err = _run(["--offline", bundle], capsys)
    assert rc == 0
    path = paths.scoreboard_json()
    assert os.path.exists(path)
    with open(path) as fh:
        doc = json.load(fh)
    assert pool.validate_scoreboard(doc) == []
    assert out.startswith("POOL ") and "changed=yes" in out
    assert f"out={path}" in out


def test_a_pool_file_that_does_not_verify_exits_2_and_writes_nothing(pool_env, tmp_path, capsys):
    members_dir = os.path.join(str(pool_env["repo"]), pool.POOL_DIR, pool.MEMBERS_SUBDIR)
    with open(os.path.join(members_dir, ("0x" + "11" * 20) + ".json"), "w") as fh:
        fh.write("not json")
    bundle = _empty_bundle(tmp_path)
    rc, out, err = _run(["--offline", bundle], capsys)
    assert rc == 2
    assert not os.path.exists(paths.scoreboard_json())
    assert "not_json" in err or "unreadable" in err


def test_a_live_run_with_a_venue_error_exits_3_writes_no_ledger_no_scoreboard(pool_env, monkeypatch, capsys):
    _house(pool_env)

    def boom(payload):
        raise venue.VenueError("the venue is down")

    monkeypatch.setattr(venue, "post_info", boom)
    rc, out, err = _run([], capsys)
    assert rc == 3
    assert not os.path.exists(paths.scoreboard_json())
    assert not os.path.isdir(paths.ledger_dir()) or not os.listdir(paths.ledger_dir())


def test_testnet_exits_2_before_any_network_call(pool_env, monkeypatch, capsys):
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")
    rc, out, err = _run([], capsys)
    assert rc == 2
    assert "mainnet" in err


def test_dry_run_writes_nothing_but_still_prints_pool_new(pool_env, new_account, signed, put, tmp_path, capsys):
    _house(pool_env)
    rc1, _, _ = _run(["--offline", _empty_bundle(tmp_path, "first"), "--as-of", AS_OF_ISO], capsys)
    assert rc1 == 0
    with open(paths.scoreboard_json()) as fh:
        before = fh.read()

    acct = _join(new_account, signed, put, at="2026-11-13T00:00:00Z")
    bundle2 = _bundle_for(tmp_path, "second", acct.address.lower())
    rc2, out2, err2 = _run(["--offline", bundle2, "--as-of", AS_OF_ISO, "--dry-run"], capsys)
    assert rc2 == 0
    assert "changed=dry" in out2
    assert f"POOL-NEW {pool.short_address(acct.address.lower())}" in err2
    with open(paths.scoreboard_json()) as fh:
        after = fh.read()
    assert before == after   # the dry run never touched the file
    assert not os.path.isdir(paths.ledger_dir()) or not os.listdir(paths.ledger_dir())


def test_a_member_who_leaves_between_builds_loses_row_and_ledger_and_counts_left(pool_env, new_account, signed,
                                                                                 put, tmp_path, capsys):
    acct = _join(new_account, signed, put, at="2026-10-21T00:00:00Z")
    addr = acct.address.lower()
    fills, orders = PF.bracket_trade(COIN, "long", 70.0, 69.0, 5.0, pool.ms_from_iso("2026-10-22T00:00:00Z"),
                                     target_px=72.0)
    bundle1 = _bundle_for(tmp_path, "b1", addr, fills=fills, orders=orders)
    rc1, out1, err1 = _run(["--offline", bundle1, "--as-of", AS_OF_ISO], capsys)
    assert rc1 == 0
    assert os.path.exists(paths.ledger_path(addr))
    with open(paths.scoreboard_json()) as fh:
        doc1 = json.load(fh)
    assert any(r["address"] == addr for r in doc1["members"])

    put(signed("leave", acct, at="2026-11-13T00:00:00Z"))
    bundle2 = _empty_bundle(tmp_path, "b2")
    rc2, out2, err2 = _run(["--offline", bundle2, "--as-of", AS_OF_ISO], capsys)
    assert rc2 == 0
    with open(paths.scoreboard_json()) as fh:
        doc2 = json.load(fh)
    assert not any(r["address"] == addr for r in doc2["members"])
    assert doc2["counts"]["left"] == 1
    assert not os.path.exists(paths.ledger_path(addr))


def test_a_member_added_between_builds_prints_pool_new_once_then_never_again(pool_env, new_account, signed, put,
                                                                              tmp_path, capsys):
    _house(pool_env)
    rc1, out1, err1 = _run(["--offline", _empty_bundle(tmp_path, "b1"), "--as-of", AS_OF_ISO], capsys)
    assert rc1 == 0 and "POOL-NEW" not in err1   # first build ever: no previous to compare against

    acct = _join(new_account, signed, put, at="2026-11-13T00:00:00Z")
    bundle2 = _bundle_for(tmp_path, "b2", acct.address.lower())
    rc2, out2, err2 = _run(["--offline", bundle2, "--as-of", AS_OF_ISO], capsys)
    assert rc2 == 0
    assert err2.count("POOL-NEW") == 1
    assert f"POOL-NEW {pool.short_address(acct.address.lower())}" in err2

    bundle3 = _bundle_for(tmp_path, "b3", acct.address.lower())
    rc3, out3, err3 = _run(["--offline", bundle3, "--as-of", AS_OF_ISO], capsys)
    assert rc3 == 0 and "POOL-NEW" not in err3   # the address already had a row


def test_the_reinstatement_hole_is_shown_not_refused_by_the_build(pool_env, new_account, signed, put, tmp_path,
                                                                  capsys):
    acct = _join(new_account, signed, put, at="2026-10-21T00:00:00Z")
    addr = acct.address.lower()
    put(signed("leave", acct, at="2026-11-01T00:00:00Z"))
    rc1, out1, err1 = _run(["--offline", _empty_bundle(tmp_path, "b1"), "--as-of", AS_OF_ISO], capsys)
    assert rc1 == 0
    with open(paths.scoreboard_json()) as fh:
        doc1 = json.load(fh)
    assert not any(r["address"] == addr for r in doc1["members"])

    leave_path = os.path.join(str(pool_env["repo"]), pool.POOL_DIR, pool.LEAVES_SUBDIR,
                              pool.statement_filename(addr))
    os.remove(leave_path)   # deleted by hand: the pull request check would refuse this; a direct push would not

    rc2, out2, err2 = _run(["--offline", _empty_bundle(tmp_path, "b2"), "--as-of", AS_OF_ISO], capsys)
    assert rc2 == 0
    with open(paths.scoreboard_json()) as fh:
        doc2 = json.load(fh)
    row = next(r for r in doc2["members"] if r["address"] == addr)
    assert row["since"] == pool.day_of(pool.ms_from_iso("2026-10-21T00:00:00Z"))   # the OLD joined_at
    assert f"POOL-NEW {pool.short_address(addr)}" in err2   # the build cannot refuse; it shows it


def test_pool_warn_when_graded_falls_with_no_new_leave(pool_env, new_account, signed, put, tmp_path, capsys):
    acct = _join(new_account, signed, put, at="2026-10-20T00:00:00Z")
    addr = acct.address.lower()
    f1, o1 = PF.bracket_trade(COIN, "long", 70.0, 69.0, 5.0, pool.ms_from_iso("2026-10-21T00:00:00Z"),
                             target_px=72.0)
    f2, o2 = PF.bracket_trade(COIN, "long", 80.0, 79.0, 5.0, pool.ms_from_iso("2026-10-23T00:00:00Z"),
                             target_px=82.0)
    bundle1 = _bundle_for(tmp_path, "b1", addr, fills=f1 + f2, orders=o1 + o2)
    rc1, out1, err1 = _run(["--offline", bundle1, "--as-of", AS_OF_ISO, "--no-ledger"], capsys)
    assert rc1 == 0
    assert "graded=2" in out1

    bundle2 = _bundle_for(tmp_path, "b2", addr, fills=f1, orders=o1)   # the venue "forgot" the second trade's stop
    rc2, out2, err2 = _run(["--offline", bundle2, "--as-of", AS_OF_ISO, "--no-ledger"], capsys)
    assert rc2 == 0
    assert "graded fell from 2 to 1 with no new leave" in err2


def test_the_new_files_live_only_under_state_root_or_the_out_dir(pool_env, new_account, signed, put, tmp_path,
                                                                 capsys):
    acct = _join(new_account, signed, put, at="2026-10-21T00:00:00Z")
    bundle = _bundle_for(tmp_path, "b1", acct.address.lower())
    tmp = pool_env["state"].parent   # the pool_env fixture's own tmp_path
    before = {p for p in tmp.rglob("*") if p.is_file()}
    rc, out, err = _run(["--offline", bundle, "--as-of", AS_OF_ISO], capsys)
    assert rc == 0
    after = {p for p in tmp.rglob("*") if p.is_file()}
    state_root, out_dir = str(pool_env["state"] / "pool"), str(pool_env["repo"] / "docs" / "scoreboard")
    for p in after - before:
        assert str(p).startswith(state_root) or str(p).startswith(out_dir), p


def test_build_help_exits_0(capsys):
    with pytest.raises(SystemExit) as exc:
        BC.main(["build", "--help"])
    assert exc.value.code == 0


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_the_committed_scoreboard_validates_and_matches_the_member_files():
    """What a committed build must always hold, placeholder or real. It used
    to require 0 trades, which only the placeholder met; the first real build
    (2026-11-13, 18 graded house trades) failed the commit on that."""
    with open(os.path.join(REPO_ROOT, "docs", "scoreboard", "scoreboard.json")) as fh:
        doc = json.load(fh)
    assert pool.validate_scoreboard(doc) == []
    rows = doc["members"]
    assert sum(r["kind"] == "house" for r in rows) == doc["counts"]["house"] == 1
    assert all(r["kind"] in ("house", "member") for r in rows)
    members_dir = os.path.join(REPO_ROOT, "pool", "members")
    files = [f for f in os.listdir(members_dir) if f.endswith(".json")]
    assert sum(r["kind"] == "member" for r in rows) == doc["counts"]["members"] <= len(files)
    assert 0 <= doc["counts"]["independent_trades"] <= doc["counts"]["graded_trades"] \
        <= doc["counts"]["closed_trades"]
