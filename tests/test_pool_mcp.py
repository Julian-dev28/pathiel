"""The four research-pool MCP tools, wired into scripts/pathiel-mcp-server.py.

Only files named tests/test_pool_* may import contracts.pool
(contracts/pool/tests/test_thesis_boundary.py enforces it) -- this file is
one of them, alongside the pool's own tests.

What this file proves that no single unit's own tests could: the SHARED
server file now carries the pool's tools correctly, without disturbing the
operator's own tools/list or instructions (test_mcp_operator_unchanged.py
and test_mcp_overhaul.py pin those bytes directly; this file pins the pool
side of the same boundary).

Offline: reuses tests/test_eval_public_tiers.py's harness, which replaces
every network call with a fake venue.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

import contracts.pool as pool_contract

ROOT = Path(__file__).resolve().parents[1]
_KIT = Path(__file__).resolve().parent / "test_eval_public_tiers.py"
_spec = importlib.util.spec_from_file_location("pathiel_public_tiers_kit_pool_mcp", _KIT)
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)

M = E.load_server("pathiel_mcp_server_pool_mcp")

POOL_TOOL_NAMES = [t["name"] for t in pool_contract.MCP_TOOLS]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def no_key(monkeypatch, tmp_path, builder_jobs):
    """Fresh public install: testnet, no key, no address, no builder."""
    return E.install(monkeypatch, tmp_path, M, builder_jobs)


@pytest.fixture
def keyed(monkeypatch, tmp_path, builder_jobs):
    """Public, testnet key (fake SDK), builder address set, approval 10."""
    return E.install(monkeypatch, tmp_path, M, builder_jobs, key=True,
                     builder_address=E.BUILDER, max_builder_fee=10)


@pytest.fixture
def no_key_mainnet(monkeypatch, tmp_path, builder_jobs):
    """Public, no key, mainnet: the pool is mainnet only (contracts.pool
    NETWORK), so a testnet install answers 'testnet' before it ever gets to
    checking for a key. pool_join/pool_leave want this one."""
    return E.install(monkeypatch, tmp_path, M, builder_jobs, network="mainnet")


@pytest.fixture
def operator(monkeypatch, tmp_path, builder_jobs):
    """The maintainer's own checkout: PATHIEL_INSTALL unset, mainnet (the
    operator's own default), no key."""
    return E.install(monkeypatch, tmp_path, M, builder_jobs, public=False, network="mainnet")


@pytest.fixture
def operator_keyed(monkeypatch, tmp_path, builder_jobs):
    """The maintainer's own checkout with a REAL configured key: the exact
    HIGH-severity scenario (operator mode, mainnet, the key the live order
    path signs with). A fresh PATHIEL_STATE_DIR so a test can assert nothing
    was written to the pool's outbox."""
    h = E.install(monkeypatch, tmp_path, M, builder_jobs, public=False, network="mainnet", key=True)
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path / "state"))
    return h


# ── the pool tools exist exactly once and nowhere by accident ───────────────

def test_pool_tool_names_match_the_contract():
    assert POOL_TOOL_NAMES == ["pool_status", "pool_report", "pool_join", "pool_leave"]
    assert set(POOL_TOOL_NAMES) == set(pool_contract.MCP_HANDLERS)
    assert set(POOL_TOOL_NAMES) == set(pool_contract.MCP_TOOL_CLASS)


def test_pool_tools_never_in_the_operators_own_tools():
    """M.TOOLS is the operator's raw list; it never grows the four pool
    tools (that is what keeps it byte-identical -- see
    test_mcp_operator_unchanged.py and test_mcp_overhaul.py)."""
    operator_names = {t["name"] for t in M.TOOLS}
    assert operator_names.isdisjoint(POOL_TOOL_NAMES)
    assert len(M.TOOLS) == 60


def test_pool_tools_module_matches_the_contract_exactly():
    """M.POOL_TOOLS is a deepcopy of the contract's MCP_TOOLS (plus the
    annotations _annotate_tools adds); names, descriptions and input
    schemas are the contract's own, never rewritten."""
    by_name = {t["name"]: t for t in M.POOL_TOOLS}
    assert set(by_name) == set(POOL_TOOL_NAMES)
    for contract_tool in pool_contract.MCP_TOOLS:
        server_tool = by_name[contract_tool["name"]]
        assert server_tool["description"] == contract_tool["description"]
        assert server_tool["inputSchema"] == contract_tool["inputSchema"]
        assert "annotations" in server_tool


# ── classes and annotations ──────────────────────────────────────────────────

def test_pool_status_and_pool_report_are_read_only_hint():
    by_name = {t["name"]: t for t in M.POOL_TOOLS}
    for name in ("pool_status", "pool_report"):
        assert by_name[name]["annotations"].get("readOnlyHint") is True, name


def test_pool_join_and_pool_leave_are_not_destructive_and_not_live_money():
    by_name = {t["name"]: t for t in M.POOL_TOOLS}
    for name in ("pool_join", "pool_leave"):
        assert by_name[name]["annotations"].get("destructiveHint") is False, name
        assert name not in M.LIVE_MONEY_TOOLS, name
        assert name not in M.LIVE_CONFIG_TOOLS, name


def test_pool_tool_classes_match_the_contract():
    for name, cls in pool_contract.MCP_TOOL_CLASS.items():
        homes = [k for k, s in M.TOOL_CLASSES.items() if name in s]
        assert homes == [cls], (name, homes, cls)


def test_check_tool_classes_has_no_problems_with_the_pool_wired_in():
    assert M._check_tool_classes() == []


# ── public tools/list: 64, the pool's own descriptions ──────────────────────

def test_public_tools_list_has_64_names_with_pool_tools_last(no_key):
    listed = no_key.rpc("tools/list")["result"]["tools"]
    assert len(listed) == 64
    assert [t["name"] for t in listed][-4:] == POOL_TOOL_NAMES
    by_name = {t["name"]: t for t in listed}
    for contract_tool in pool_contract.MCP_TOOLS:
        listed_tool = by_name[contract_tool["name"]]
        assert listed_tool["description"] == contract_tool["description"]
        assert listed_tool["inputSchema"] == contract_tool["inputSchema"]


# ── operator byte-identity (the pool changes nothing on that path) ──────────

def test_operator_tools_list_and_instructions_hash_unchanged():
    assert E.tools_hash(M.TOOLS) == E.BASE_TOOLS_SHA256
    resp = M.dispatch_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert _sha(resp["result"]["instructions"]) == E.BASE_INSTRUCTIONS_SHA256
    assert M.server_tools() is M.TOOLS


# ── calling the tools ────────────────────────────────────────────────────────

def test_pool_report_with_no_account_returns_the_standard_no_account_payload(no_key):
    err, body = no_key.call("pool_report", {})
    assert err is True
    assert body == {"error": M.NO_ACCOUNT_MSG, "no_account": True}


def test_pool_join_dry_run_with_no_key_fails_cleanly(no_key_mainnet, monkeypatch):
    """No traceback, a stable code, and (with the launcher's own variables
    set) a master_command that carries PATHIEL_PYTHON -- the token the join
    unit added specifically because a bare 'python3' on a pasted line can
    resolve to the wrong interpreter."""
    monkeypatch.setenv("PATHIEL_ROOT", "/opt/pathiel-plugin")
    monkeypatch.setenv("PATHIEL_HOME", "/srv/pathiel-state")
    monkeypatch.setenv("PATHIEL_DATA", "/srv/pathiel-plugin-data")
    monkeypatch.setenv("PATHIEL_VENV", "/srv/pathiel-plugin-data/venv")
    err, body = no_key_mainnet.call("pool_join", {})
    assert err is True
    assert isinstance(body, dict)
    assert body.get("code") == "no_key"
    assert "error" in body
    assert "Traceback" not in json.dumps(body)
    assert "master_command" in body
    assert "PATHIEL_PYTHON=/srv/pathiel-plugin-data/venv/bin/python" in body["master_command"]
    assert "services.pool join --master-prompt --yes" in body["master_command"]


def test_pool_join_dry_run_without_the_launcher_variables_uses_sys_executable(no_key_mainnet, monkeypatch):
    """Never a BARE 'python3' (unqualified: whatever that resolves to on the
    user's own machine, e.g. macOS's /usr/bin/python3 at 3.9). sys.executable
    is itself a full, absolute path -- on some machines that path's own
    basename happens to BE 'python3' (a venv often names its binary that),
    which is fine: the token is the quoted interpreter that is actually
    running this test, never the bare, unqualified word."""
    import sys
    monkeypatch.delenv("PATHIEL_ROOT", raising=False)
    err, body = no_key_mainnet.call("pool_join", {})
    assert err is True
    assert body.get("code") == "no_key"
    assert body["master_command"].startswith("cd ")
    assert sys.executable in body["master_command"]
    token = body["master_command"].split(" && ", 1)[-1].split()[0]
    assert token != "python3"
    assert os.path.isabs(token)
    assert token == sys.executable


def test_pool_leave_dry_run_with_no_key_fails_cleanly(no_key_mainnet):
    err, body = no_key_mainnet.call("pool_leave", {})
    assert err is True
    assert body.get("code") == "no_key"
    assert "master_command" in body


def test_pool_status_reads_local_files_only_and_never_raises(no_key):
    err, body = no_key.call("pool_status", {})
    assert err is False
    assert body["ok"] is True
    assert body["state"] == "no_account"


# ── operator install: every pool tool refuses before it runs at all ────────
#
# HIGH finding (2026-11-13): _tool_handlers() wired every pool tool name to
# its real handler on EVERY install kind; only server_tools() (tools/list)
# differed by install kind. _call_tool() dispatched purely by name, with no
# check against the install kind for a pool tool, so a raw tools/call for
# pool_join on the operator's own mainnet checkout reached
# signing.sign_configured() -- the SAME key the live order path signs with
# -- and would have written a real signed join file for a pull request
# nobody asked for. The one test that sounded like it proved this
# (test_pool_tools_never_in_the_operators_own_tools, above) only checked
# tools/list; nothing called dispatch_message for a pool tool name on an
# operator-mode harness. These do.

def test_pool_tools_refuse_on_the_operators_own_install_through_dispatch_message(operator):
    """Every one of the four, through the exact path a client uses
    (M.dispatch_message -> M._call_tool -> M._tool_handlers()), not just a
    listing check."""
    for name in POOL_TOOL_NAMES:
        err, body = operator.call(name, {})
        assert err is True, (name, body)
        assert body == {"ok": False, "code": "operator_install", "error": body["error"]}, (name, body)
        assert "operator" in body["error"].lower()


def test_pool_join_and_pool_leave_confirm_on_operator_install_never_sign_or_write(operator_keyed):
    """The exact HIGH scenario: a raw tools/call for pool_join/pool_leave
    with confirm=true, a real configured mainnet key, on the operator's own
    checkout. No signature, no pr_url, no key byte anywhere in the answer,
    and nothing written to the pool's outbox."""
    import services.pool.paths as pool_paths

    for name, args in (("pool_join", {"confirm": True, "joined_at": "2026-11-13T00:00:00Z"}),
                       ("pool_leave", {"confirm": True, "left_at": "2026-11-13T00:00:00Z"})):
        err, body = operator_keyed.call(name, args)
        assert err is True, (name, body)
        assert body == {"ok": False, "code": "operator_install", "error": body["error"]}, (name, body)
        assert "signed" not in body and "pr_url" not in body and "signature" not in body
        assert E.KEY.removeprefix("0x").lower() not in json.dumps(body).lower()
    assert not os.path.exists(pool_paths.outbox_root())


def test_pool_tools_refuse_before_importing_services_pool_on_an_operator_install(operator_keyed, monkeypatch):
    """The refusal sits ahead of `importlib.import_module(module)` in
    contracts.pool.mcp_handler -- the exact line that used to reach
    services.pool.join_tools / services.pool.report_tool unconditionally.
    Patching import_module to explode proves it is never reached, for all
    four tools, with a real configured key."""
    def _boom(name):
        raise AssertionError(f"mcp_handler imported {name!r} despite the operator_install refusal")
    monkeypatch.setattr(pool_contract.importlib, "import_module", _boom)
    for name, args in (("pool_status", {}), ("pool_report", {}),
                       ("pool_join", {"confirm": True, "joined_at": "2026-11-13T00:00:00Z"}),
                       ("pool_leave", {"confirm": True, "left_at": "2026-11-13T00:00:00Z"})):
        err, body = operator_keyed.call(name, args)
        assert err is True, (name, body)
        assert body == {"ok": False, "code": "operator_install", "error": body["error"]}, (name, body)


# ── no pool tool is ever decorated with the live-money builder_fee block ────

def test_no_pool_tool_output_carries_builder_fee(keyed):
    keyed.warm_builder()
    for name, args in (("pool_status", {}), ("pool_report", {}), ("pool_join", {}),
                       ("pool_leave", {})):
        _err, body = keyed.call(name, args)
        assert isinstance(body, dict), (name, body)
        assert "builder_fee" not in body, name


def test_public_decorate_leaves_pool_tool_output_unchanged():
    for name in POOL_TOOL_NAMES:
        text = json.dumps({"ok": True, "code": "no_key"})
        assert M._public_decorate(name, {}, text) == text
