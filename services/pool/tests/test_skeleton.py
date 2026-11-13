"""The parts of services/pool every unit shares: paths, the venue POST, the
command dispatcher. Offline."""
from __future__ import annotations

import io
import json
import os
import sys
import types
import urllib.error

import pytest

import contracts.pool as pool
from contracts import runtime
from services.pool import __main__ as cli
from services.pool import account, paths, venue


# ── paths ───────────────────────────────────────────────────────────────────

def test_paths_follow_the_env_at_call_time(pool_env, monkeypatch, tmp_path):
    state, repo = str(pool_env["state"]), str(pool_env["repo"])
    assert paths.repo_root() == repo
    assert paths.repo_pool_root() == os.path.join(repo, "pool")
    assert paths.scoreboard_json() == os.path.join(repo, "docs", "scoreboard", "scoreboard.json")
    assert paths.state_root() == os.path.join(state, "pool")
    assert paths.outbox_root() == os.path.join(state, "pool", "outbox")
    addr = "0x" + "AB" * 20
    assert paths.ledger_path(addr) == os.path.join(state, "pool", "ledger", addr.lower() + ".jsonl")
    assert paths.work_dir("s1") == os.path.join(state, "pool", "work", "s1")
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(other))
    assert paths.state_root() == os.path.join(str(other), "pool")
    monkeypatch.delenv("PATHIEL_POOL_REPO_ROOT")
    assert os.path.isfile(os.path.join(paths.repo_root(), "pyproject.toml"))     # the real checkout


def test_the_outbox_is_a_pool_root(pool_env, new_account, signed, put):
    acct = new_account()
    put(signed("join", acct), root=paths.outbox_root())
    assert pool.membership(paths.outbox_root(), acct.address)["state"] == "member"
    assert pool.membership(paths.repo_pool_root(), acct.address)["state"] == "none"


def test_test_keys_are_pinned_empty():
    for key in ("HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_MASTER_PRIVATE_KEY"):
        assert os.environ.get(key) == ""


# ── account ─────────────────────────────────────────────────────────────────

def test_configured_account_is_the_master_then_the_wallet_then_nothing(monkeypatch):
    master, wallet = "0x" + "AB" * 20, "0x" + "CD" * 20
    assert account.configured_account() == ""                     # conftest pins both empty
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", wallet)
    assert account.configured_account() == wallet.lower()
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", master)
    assert account.configured_account() == master.lower()
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", "not-an-address")
    assert account.configured_account() == ""


def test_configured_account_never_raises(monkeypatch):
    import pathiel.client.hl_client as hl

    def boom():
        raise RuntimeError("registry down")
    monkeypatch.setattr(hl, "resolve_user_address", boom)
    assert account.configured_account() == ""


# ── venue ───────────────────────────────────────────────────────────────────

class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_post_info_posts_json_to_mainnet_info():
    seen = {}

    def opener(req, timeout):
        seen.update(url=req.full_url, body=json.loads(req.data.decode()), timeout=timeout,
                    method=req.get_method(), ctype=req.get_header("Content-type"))
        return _Reply(b'[{"name": "a"}]')

    payload = pool.extra_agents_request("0x" + "ab" * 20)
    assert venue.post_info(payload, opener=opener) == [{"name": "a"}]
    assert seen == {"url": runtime.MAINNET_API_URL + "/info", "body": payload, "timeout": venue.TIMEOUT_S,
                    "method": "POST", "ctype": "application/json"}


def test_post_info_is_mainnet_even_on_a_testnet_process(monkeypatch):
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")
    assert venue.info_url() == runtime.MAINNET_API_URL + "/info"
    assert "testnet" not in venue.info_url()


@pytest.mark.parametrize("errors,calls,ok", [
    ([urllib.error.URLError("down")], 2, True),
    ([urllib.error.HTTPError("u", 429, "slow", None, None)], 2, True),
    ([urllib.error.HTTPError("u", 502, "bad", None, None)] * 2, 2, False),
    ([urllib.error.HTTPError("u", 422, "no", None, None)], 1, False),
    ([OSError("reset"), OSError("reset")], 2, False),
])
def test_post_info_retries_once_then_raises(errors, calls, ok):
    queue, made, slept = list(errors), [], []

    def opener(req, timeout):
        made.append(1)
        if queue:
            raise queue.pop(0)
        return _Reply(b"{}")

    if ok:
        assert venue.post_info({"type": "userRole"}, opener=opener, sleep=slept.append) == {}
    else:
        with pytest.raises(venue.VenueError) as e:
            venue.post_info({"type": "userRole"}, opener=opener, sleep=slept.append)
        assert "userRole" in str(e.value)
    assert len(made) == calls
    assert slept == ([venue.RETRY_WAIT_S] if calls == 2 else [])


def test_a_reply_that_is_not_json_is_a_venue_error_not_an_empty_reply():
    with pytest.raises(venue.VenueError):
        venue.post_info({"type": "userRole"}, opener=lambda req, timeout: _Reply(b"<html>"),
                        sleep=lambda s: None)


# ── dispatcher ──────────────────────────────────────────────────────────────

def test_dispatch_hands_the_whole_argv_to_the_commands_module(monkeypatch):
    got = {}
    fake = types.ModuleType("services.pool.verify_cli")
    fake.main = lambda argv: got.setdefault("argv", argv) and 0
    monkeypatch.setitem(sys.modules, "services.pool.verify_cli", fake)
    assert cli.main(["verify", "--online", "--paths", "x"]) == 0
    assert got["argv"] == ["verify", "--online", "--paths", "x"]


def test_dispatch_passes_the_exit_code_through(monkeypatch):
    fake = types.ModuleType("services.pool.build_cli")
    fake.main = lambda argv: 3
    monkeypatch.setitem(sys.modules, "services.pool.build_cli", fake)
    assert cli.main(["build"]) == 3


def test_dispatch_unknown_and_help(capsys):
    assert cli.main(["trade"]) == 2
    assert "unknown command" in capsys.readouterr().err
    assert cli.main([]) == 2
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for command in pool.CLI_COMMANDS:
        assert command in out


def test_dispatch_says_so_when_a_command_module_is_missing(monkeypatch, capsys):
    monkeypatch.setitem(pool.CLI_COMMANDS, "status", "services.pool.no_such_module_cli")
    assert cli.main(["status"]) == 2
    assert "is not in this checkout" in capsys.readouterr().err


def test_dispatch_does_not_hide_a_broken_import_inside_a_command(monkeypatch):
    def boom(name):
        raise ModuleNotFoundError("No module named 'somethingelse'", name="somethingelse")
    monkeypatch.setattr(cli.importlib, "import_module", boom)
    with pytest.raises(ModuleNotFoundError):
        cli.main(["build"])
