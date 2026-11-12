"""The operator's MCP server is exactly what it was before the public plugin.

Julien launches scripts/pathiel-mcp-server.py through the repo .mcp.json with
no env block, so PATHIEL_INSTALL is unset and contracts.runtime says
'operator'. On that path the initialize instructions, tools/list and every
handler's output must be byte for byte what they were at 2816df7, and none of
the public hooks may run.

The two hashes below were taken from the untouched server under pytest before
this unit edited it (tests/test_eval_public_tiers.py pins the same values).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_KIT = Path(__file__).resolve().parent / "test_eval_public_tiers.py"
_spec = importlib.util.spec_from_file_location("pathiel_public_tiers_kit_operator", _KIT)
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)

M = E.load_server("pathiel_mcp_server_operator_unchanged")

BASE_INSTRUCTIONS_SHA256 = "8d82a135a7ac6113ae0a44c444d19dd1873f0a830cf0763f971b7eccffda5c1c"
BASE_INSTRUCTIONS_CHARS = 2491
BASE_TOOLS_SHA256 = "8ec89b2f6cfac71e9e8088068569b6272268b88e3f6d77fd29e189a89d039e4b"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def operator(monkeypatch, tmp_path, builder_jobs):
    """The operator's install against the offline venue: mainnet, a key, a
    builder address in the environment (it must still never attach)."""
    return E.install(monkeypatch, tmp_path, M, builder_jobs, public=False, network="mainnet",
                     key=True, builder_address=E.BUILDER, max_builder_fee=10)


def test_pins_agree_with_the_eval_kit():
    assert BASE_INSTRUCTIONS_SHA256 == E.BASE_INSTRUCTIONS_SHA256
    assert BASE_TOOLS_SHA256 == E.BASE_TOOLS_SHA256


def test_instructions_hash_is_the_base_value():
    resp = M.dispatch_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    text = resp["result"]["instructions"]
    assert len(text) == BASE_INSTRUCTIONS_CHARS
    assert _sha(text) == BASE_INSTRUCTIONS_SHA256
    assert M.server_instructions() is M.SERVER_INSTRUCTIONS
    assert _sha(M.SERVER_INSTRUCTIONS) == BASE_INSTRUCTIONS_SHA256


def test_tools_list_hash_is_the_base_value():
    assert _sha(json.dumps(M.TOOLS, sort_keys=True)) == BASE_TOOLS_SHA256
    resp = M.dispatch_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert _sha(json.dumps(resp["result"]["tools"], sort_keys=True)) == BASE_TOOLS_SHA256
    assert len(resp["result"]["tools"]) == 60


def test_server_tools_is_tools_for_the_operator():
    assert M.server_tools() is M.TOOLS


def test_operator_outputs_have_no_public_keys(operator):
    E.seed_positions(operator.venue)
    calls = [
        ("place_bracket", {"coin": "BTC", "stop_px": 72000, "tp_px": 77000, "dry_run": True}),
        ("close_position", {"coin": "ETH"}),
        ("state", {}),
        ("risk_limits", {"equity_usd": 1000}),
        ("set_target", {"coin": "ETH", "tp_px": 2100}),
        ("bracket_sweep", {"coin": "ETH"}),
    ]
    for name, args in calls:
        _err, body = operator.call(name, args)
        assert isinstance(body, dict), (name, body)
        leaked = [k for k in E.PUBLIC_KEYS if k in body]
        assert not leaked, (name, leaked, body)
    # the close and the stop fold reached the SDK, with no builder
    assert operator.sdk.orders
    assert all(o["builder"] is None for o in operator.sdk.orders)
    assert operator.venue.off_base() == []


def test_operator_risk_limits_note_is_unchanged(operator):
    _err, body = operator.call("risk_limits", {"equity_usd": 1000})
    assert any(n.startswith("Daily pause after") for n in body["notes"])
    assert body["change_it"] == "Account tab in the dashboard (risk profile), or PUT /api/risk/profile."
    _err, accounts = operator.call("accounts")
    assert accounts["manage"] == ("Account tab in the dashboard: connect a wallet, add a "
                                  "watch-only address, switch.")


def test_public_hooks_and_warm_are_never_called_on_the_operator_path(operator, monkeypatch):
    from pathiel.client import builder
    seen = []

    def spy(label, real=None):
        def f(*a, **k):
            seen.append(label)
            return real(*a, **k) if real else None
        return f

    monkeypatch.setattr(M, "_public_gate", spy("gate", M._public_gate))
    monkeypatch.setattr(M, "_public_decorate", spy("decorate", M._public_decorate))
    monkeypatch.setattr(M, "_public_instructions", spy("instructions", M._public_instructions))
    monkeypatch.setattr(builder, "warm", spy("warm", builder.warm))
    monkeypatch.setattr(builder, "builder_status", spy("status", builder.builder_status))
    monkeypatch.setattr(builder, "builder_for_order", spy("for_order", builder.builder_for_order))
    monkeypatch.setattr(M, "run", lambda: seen.append("run"))
    E.seed_positions(operator.venue)
    operator.rpc("initialize", {})
    operator.rpc("tools/list")
    for name, args in [("state", {}), ("close_position", {"coin": "ETH"}),
                       ("set_leverage", {"coin": "BTC", "leverage": 20}),
                       ("place_bracket", {"coin": "xyz:BRENTOIL", "stop_px": 1, "tp_px": 2,
                                          "dry_run": True}),
                       ("get_account_summary", {}), ("desk_brief", {"desk": "oil"})]:
        operator.call(name, args)
    M.main()
    assert seen == ["run"]
    assert operator.jobs == []


def test_operator_set_leverage_has_no_profile_cap(operator):
    """20x on the operator's install goes to the venue as before; the public
    3x cap never applies here."""
    _err, body = operator.call("set_leverage", {"coin": "BTC", "leverage": 20})
    assert "update_leverage BTC 20" in operator.sdk.other
    assert "max_leverage" not in body


def test_operator_desk_refresh_does_not_precheck_the_cli(operator, monkeypatch):
    calls = []
    monkeypatch.setattr(M.shutil, "which", lambda *a, **k: calls.append(a) or None)
    monkeypatch.setenv("CLAUDE_CLI_COMMAND", "definitely-not-a-claude-binary")
    err, body = operator.call("desk_refresh", {"desk": "oil"})
    assert calls == []
    assert operator.facade.refresh_calls == [("oil", False)]
    assert not err and body["job"]["state"] == "done"


def test_checkout_notice_is_none_with_an_env_file_and_set_without_one(tmp_path):
    with_env = tmp_path / "with"
    with_env.mkdir()
    (with_env / ".env.local").write_text("# operator\n")
    without = tmp_path / "without"
    without.mkdir()
    assert M._operator_checkout_notice(str(with_env), {}) is None
    notice = M._operator_checkout_notice(str(without), {})
    assert notice == ("pathiel: maintainer mode from a checkout (mainnet, the repo tracked config). "
                      "Public users: install the plugin or start scripts/pathiel-mcp.")
    assert M._operator_checkout_notice(str(without), {"PATHIEL_ENV_FILE": "/x/.env.local"}) is None
    assert M._operator_checkout_notice(str(without), {"PATHIEL_INSTALL": "public"}) is None


def test_checkout_notice_never_prints_for_the_operators_checkout():
    """The operator's checkout is the main repo, which holds .env.local. A
    worktree or CI checkout without one is not his live stack."""
    main_repo = ROOT
    if not (main_repo / ".env.local").is_file():
        pytest.skip("this checkout has no .env.local (a worktree or CI)")
    assert M._operator_checkout_notice(str(main_repo), {}) is None
