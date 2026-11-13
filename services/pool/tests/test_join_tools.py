"""services/pool/join_tools.py: the three MCP handlers. Offline; every
handler returns one JSON string and never raises."""
from __future__ import annotations

import json

import contracts.pool as pool
from services.pool import join_tools, paths


def _configure(monkeypatch, acct) -> None:
    import pathiel.client.exchange as exchange
    key = acct.key.hex()
    monkeypatch.setattr(exchange, "_resolve_identity", lambda: (key, acct.address, None, False))


def test_handle_pool_status_shape(pool_env, monkeypatch, new_account):
    acct = new_account()
    monkeypatch.setattr("services.pool.account.configured_account", lambda: acct.address.lower())
    out = json.loads(join_tools.handle_pool_status({}))
    assert out["ok"] is True and out["state"] == "none"
    assert out["scoreboard_url"] == pool.SCOREBOARD_URL


def test_handle_pool_status_never_raises_on_a_broken_scoreboard(pool_env, monkeypatch, new_account, tmp_path):
    acct = new_account()
    monkeypatch.setattr("services.pool.account.configured_account", lambda: acct.address.lower())
    bad = tmp_path / "scoreboard.json"
    bad.write_bytes(b"\xff\xfe not json")
    monkeypatch.setattr(paths, "scoreboard_json", lambda: str(bad))
    out = json.loads(join_tools.handle_pool_status({}))
    assert out["ok"] is True and out["pool"] is None


# ── pool_join / pool_leave: dry run ──────────────────────────────────────────

def test_handle_pool_join_dry_run_signs_nothing(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    out = json.loads(join_tools.handle_pool_join({}))
    assert out["ok"] is True and out["signed"] is False
    assert out["account"] == acct.address.lower()
    assert "Call again with confirm=true" in out["next"]
    assert out["joined_at"] in out["next"]
    assert out["published"] == list(pool.PUBLISHED)


def test_handle_pool_join_confirm_true_as_the_string(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    dry = json.loads(join_tools.handle_pool_join({}))
    out = json.loads(join_tools.handle_pool_join({"confirm": "true", "joined_at": dry["joined_at"]}))
    assert out["ok"] is True and out["signed"] is True
    assert out["next"] == "Give the user pr_url to open the pull request."


def test_handle_pool_join_confirm_without_the_time_is_refused(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    out = json.loads(join_tools.handle_pool_join({"confirm": True}))
    assert out == {"ok": False, "code": "no_time", "error": "run it without confirm first"}


def test_handle_pool_join_confirm_false_string_is_a_dry_run(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    out = json.loads(join_tools.handle_pool_join({"confirm": "false"}))
    assert out["signed"] is False


def test_handle_pool_leave_shape(pool_env, monkeypatch, new_account, signed, put):
    acct = new_account()
    _configure(monkeypatch, acct)
    put(signed("join", acct), root=paths.repo_pool_root())
    dry = json.loads(join_tools.handle_pool_leave({}))
    assert dry["ok"] is True and dry["signed"] is False and "effect" in dry
    out = json.loads(join_tools.handle_pool_leave({"confirm": True, "left_at": dry["left_at"]}))
    assert out["ok"] is True and out["signed"] is True and "pr_url" in out


def test_handle_pool_join_no_key_carries_master_command(pool_env):
    out = json.loads(join_tools.handle_pool_join({}))
    assert out == {"ok": False, "code": "no_key", "error": out["error"], "master_command": out["master_command"]}
    assert "services.pool join --master-prompt --yes" in out["master_command"]


def test_handle_pool_leave_already_left_refused(pool_env, monkeypatch, new_account):
    acct = new_account()
    _configure(monkeypatch, acct)
    out = json.loads(join_tools.handle_pool_leave({}))
    assert out == {"ok": False, "code": "not_member", "error": out["error"]}


def test_handlers_never_leak_the_key(pool_env, monkeypatch, new_account, capsys, caplog):
    import logging
    acct = new_account()
    key_hex = acct.key.hex()
    _configure(monkeypatch, acct)
    with caplog.at_level(logging.DEBUG):
        dry_text = join_tools.handle_pool_join({})
        dry = json.loads(dry_text)
        confirmed_text = join_tools.handle_pool_join({"confirm": True, "joined_at": dry["joined_at"]})
    needle = key_hex.removeprefix("0x").lower()
    out, err = capsys.readouterr()
    haystack = (dry_text + confirmed_text + out + err).lower()
    assert needle not in haystack
    assert all(needle not in r.getMessage().lower() for r in caplog.records)
