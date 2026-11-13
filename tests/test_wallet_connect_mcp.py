"""wallet_connect in the MCP server and scripts/connect_wallet.py.

The tool is listed on a public install only and refuses on the operator's
checkout through the real dispatch path, before any file is touched. The
CLI prints one JSON result and never a key. Offline: the venue is
services/accounts/tests/wallet_fakes.FakeVenue, loaded by path.
"""
from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import contracts.wallet_connect as contract

ROOT = Path(__file__).resolve().parents[1]
_KIT = Path(__file__).resolve().parent / "test_eval_public_tiers.py"
_spec = importlib.util.spec_from_file_location("pathiel_public_tiers_kit_wallet", _KIT)
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)
M = E.load_server("pathiel_mcp_server_wallet_connect")

_fspec = importlib.util.spec_from_file_location(
    "pathiel_wallet_fakes_mcp", ROOT / "services" / "accounts" / "tests" / "wallet_fakes.py")
F = importlib.util.module_from_spec(_fspec)
_fspec.loader.exec_module(F)

_cspec = importlib.util.spec_from_file_location("pathiel_connect_wallet_cli",
                                                ROOT / "scripts" / "connect_wallet.py")
CLI = importlib.util.module_from_spec(_cspec)
_cspec.loader.exec_module(CLI)

TEMPLATE = "# keys\nHYPERLIQUID_WALLET_ADDRESS=\nHYPERLIQUID_PRIVATE_KEY=\nHYPERLIQUID_MASTER_ADDRESS=\n"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from services.accounts import connect_wallet as cw
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("PATHIEL_NETWORK_SOURCE", raising=False)
    cw.reset_for_tests()
    yield
    cw.reset_for_tests()


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    p = tmp_path / ".pathiel" / ".env.local"
    p.parent.mkdir(parents=True)
    p.write_text(TEMPLATE)
    monkeypatch.setenv("PATHIEL_ENV_FILE", str(p))
    return p


def _snap(p: Path):
    st = p.stat()
    return p.read_bytes(), st.st_mtime_ns


# ── listing and classes ──────────────────────────────────────────────────────

def test_wallet_connect_is_never_in_the_operators_tools():
    assert "wallet_connect" not in {t["name"] for t in M.TOOLS}
    assert len(M.TOOLS) == 60
    assert E.tools_hash(M.TOOLS) == E.BASE_TOOLS_SHA256


def test_public_lists_it_once_before_the_pool_tools(monkeypatch, tmp_path, builder_jobs):
    h = E.install(monkeypatch, tmp_path, M, builder_jobs)
    names = [t["name"] for t in h.rpc("tools/list")["result"]["tools"]]
    assert len(names) == 65 and names.count("wallet_connect") == 1
    assert names[60] == "wallet_connect" and names[61:] == [
        "pool_status", "pool_report", "pool_join", "pool_leave"]


def test_operator_tools_list_does_not_show_it(monkeypatch, tmp_path, builder_jobs):
    h = E.install(monkeypatch, tmp_path, M, builder_jobs, public=False, network="mainnet")
    names = [t["name"] for t in h.rpc("tools/list")["result"]["tools"]]
    assert "wallet_connect" not in names and len(names) == 60


def test_it_is_destructive_account_setup_never_live_money():
    tool = M.WALLET_CONNECT_TOOLS[0]
    assert tool["annotations"] == {"destructiveHint": True}
    assert tool["description"] == contract.MCP_TOOL["description"]
    assert tool["inputSchema"] == contract.MCP_TOOL["inputSchema"]
    assert "wallet_connect" not in M.LIVE_MONEY_TOOLS and "wallet_connect" not in M.LIVE_CONFIG_TOOLS
    assert [k for k, s in M.TOOL_CLASSES.items() if "wallet_connect" in s] == ["account_setup"]
    assert M._check_tool_classes() == []


def test_the_tool_description_has_no_banned_style(copy_lint):
    text = contract.MCP_TOOL["description"] + json.dumps(contract.MCP_TOOL["inputSchema"])
    assert copy_lint.style_hits(text) == []
    assert copy_lint.claim_hits(text) == []


# ── calling it ───────────────────────────────────────────────────────────────

def test_operator_dispatch_refuses_before_touching_the_env_file(monkeypatch, tmp_path, builder_jobs,
                                                                env_file):
    h = E.install(monkeypatch, tmp_path, M, builder_jobs, public=False, network="mainnet", key=True)
    monkeypatch.setenv("PATHIEL_ENV_FILE", str(env_file))
    before = _snap(env_file)
    for args in ({}, {"replace": True}, {"status": True}):
        _err, body = h.call("wallet_connect", args)
        assert body["ok"] is False and body["code"] == "operator_install", body
    assert _snap(env_file) == before
    assert sorted(p.name for p in env_file.parent.iterdir()) == [".env.local"]


def test_public_dispatch_runs_the_flow_and_never_returns_the_key(monkeypatch, tmp_path, builder_jobs,
                                                                 env_file):
    from services.accounts import connect_wallet as cw
    h = E.install(monkeypatch, tmp_path, M, builder_jobs, network="testnet")
    monkeypatch.setenv("PATHIEL_ENV_FILE", str(env_file))
    env_file.write_text("PATHIEL_NETWORK=testnet\n" + TEMPLATE)     # the user's testnet opt-in
    venue = F.FakeVenue(network="testnet")
    monkeypatch.setattr(cw, "_VENUE_FACTORY", lambda: venue)
    monkeypatch.setattr(cw, "_OPENER", lambda url: True)
    monkeypatch.setattr(cw, "CONFIRM_WAIT_S", 0)
    _err, started = h.call("wallet_connect", {})
    assert started["state"] == "waiting" and started["network"] == "testnet", started
    from eth_account import Account
    code, reply, _page = F.browser_connect(started["url"], Account.create())
    assert reply["ok"] is True, reply
    for _ in range(300):
        _err, status = h.call("wallet_connect", {"status": True})
        if status["state"] == "done":
            break
    assert status["state"] == "done"
    key = cw.env_values(env_file.read_text())["HYPERLIQUID_PRIVATE_KEY"]
    assert len(key) == 66
    for blob in (started, reply, status):
        assert not F.contains_key(json.dumps(blob), key)
        assert contract.result_problems({k: v for k, v in blob.items()
                                         if k in contract.RESULT_KEYS}, (key,)) == []
    assert venue.exchange_posts[0]["action"]["hyperliquidChain"] == "Testnet"


# ── the CLI ──────────────────────────────────────────────────────────────────

def test_cli_full_run_prints_one_json_line_and_no_key(monkeypatch, env_file, capfd):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    monkeypatch.setenv("PATHIEL_NETWORK", "mainnet")
    from eth_account import Account
    venue = F.FakeVenue()
    replies = []

    def opener(url):
        threading.Thread(target=lambda: replies.append(F.browser_connect(url, Account.create())),
                         daemon=True).start()
        return True

    from services.accounts import connect_wallet as cw
    monkeypatch.setattr(cw, "CONFIRM_WAIT_S", 0)
    rc = CLI.main([], venue=venue, opener=opener)
    out, err = capfd.readouterr()
    key = cw.env_values(env_file.read_text())["HYPERLIQUID_PRIVATE_KEY"]
    assert rc == 0
    lines = out.strip().splitlines()
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert result["ok"] is True and result["state"] == "done"
    assert "MAINNET (REAL MONEY)" in err and "http://127.0.0.1:" in err
    for text in (out, err, json.dumps(replies)):
        assert not F.contains_key(text, key)
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_cli_existing_key_refuses_without_a_page(monkeypatch, env_file, capfd):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    monkeypatch.setenv("PATHIEL_NETWORK", "mainnet")
    env_file.write_text(TEMPLATE.replace("PRIVATE_KEY=", "PRIVATE_KEY=0x" + "3" * 64))
    rc = CLI.main([], venue=F.FakeVenue(), opener=lambda u: pytest.fail("opened a page"))
    out, err = capfd.readouterr()
    assert rc == 1 and json.loads(out)["code"] == "key_exists"
    assert "3" * 64 not in out + err


def _run_cli(public_env, *args, **env):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "connect_wallet.py"), *args],
                          capture_output=True, text=True, timeout=60, env=public_env(**env),
                          cwd=str(public_env.env_file.parent))


def test_cli_on_the_operator_install_refuses_before_any_file(public_env):
    env_file = public_env.env_file
    before = _snap(env_file)
    proc = _run_cli(public_env, PATHIEL_INSTALL=None)
    assert proc.returncode == 1, proc.stderr
    assert json.loads(proc.stdout)["code"] == "operator_install"
    assert _snap(env_file) == before
    assert sorted(p.name for p in env_file.parent.iterdir()) == [".env.local"]


def test_cli_never_echoes_an_unknown_argument(public_env):
    pasted = "0x" + "9" * 64
    proc = _run_cli(public_env, pasted)
    assert proc.returncode == 2
    assert pasted not in proc.stdout + proc.stderr and "9" * 64 not in proc.stdout + proc.stderr


def test_cli_subprocess_refuses_an_existing_key_and_prints_no_key(public_env):
    secret = "0x" + "4" * 64
    public_env.env_file.write_text(f"PATHIEL_NETWORK=testnet\nHYPERLIQUID_PRIVATE_KEY={secret}\n")
    proc = _run_cli(public_env, "--no-browser", PATHIEL_NETWORK="testnet")
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert out["code"] == "key_exists"
    assert "4" * 64 not in proc.stdout + proc.stderr


def test_cli_subprocess_names_a_network_mismatch(public_env):
    """The file says testnet, the process was started on mainnet: refuse,
    and say how to fix it."""
    public_env.env_file.write_text("PATHIEL_NETWORK=testnet\n")
    proc = _run_cli(public_env, "--no-browser", PATHIEL_NETWORK="mainnet",
                    PATHIEL_NETWORK_SOURCE="env file")
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["code"] == "network_mismatch"
