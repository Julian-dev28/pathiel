"""The MCP server on a public install (PATHIEL_INSTALL=public).

What a stranger's Claude sees: a read-only tier with no key, account reads
that say "no account configured" instead of erroring, a 3x cap on
set_leverage, entries refused on coins this network does not list, the
network and the builder fee on every live-money output, no dashboard
wording, and a desk_refresh that fails fast without the claude CLI.

Offline: every venue read goes to tests/fixtures/public_info through the
harness in tests/test_eval_public_tiers.py; the stdio class runs the real
server in a subprocess with the pinned public_env (dead proxies, empty env
file, temp HOME).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from contracts.runtime import TESTNET_API_URL

_KIT = Path(__file__).resolve().parent / "test_eval_public_tiers.py"
_spec = importlib.util.spec_from_file_location("pathiel_public_tiers_kit_public", _KIT)
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)

M = E.load_server("pathiel_mcp_server_public_mode")

ACCOUNT_ALIASES = sorted(a for a, t in M.ALIASES.items() if t in M.ACCOUNT_READ_TOOLS)


@pytest.fixture
def no_key(monkeypatch, tmp_path, builder_jobs):
    """Fresh public install: testnet, no key, no address, no builder."""
    return E.install(monkeypatch, tmp_path, M, builder_jobs)


@pytest.fixture
def keyed(monkeypatch, tmp_path, builder_jobs):
    """Public, testnet key (fake SDK), builder address set, approval 10."""
    return E.install(monkeypatch, tmp_path, M, builder_jobs, key=True,
                     builder_address=E.BUILDER, max_builder_fee=10)


# ── classification ────────────────────────────────────────────────────────────

def test_every_tool_is_classified_exactly_once():
    names = [t["name"] for t in M.TOOLS]
    assert len(names) == 60
    for name in names:
        homes = [k for k, s in M.TOOL_CLASSES.items() if name in s]
        assert len(homes) == 1, (name, homes)
    assert set().union(*M.TOOL_CLASSES.values()) == set(names)


def test_every_alias_target_is_classified():
    names = {t["name"] for t in M.TOOLS}
    for alias, target in M.ALIASES.items():
        assert target in names, alias
        assert M._canonical(alias) == target
        assert any(target in s for s in M.TOOL_CLASSES.values())
    assert M._canonical("get_price") == "get_price"


def test_an_unclassified_tool_fails_the_import_check(monkeypatch):
    monkeypatch.setattr(M, "TOOLS", M.TOOLS + [{"name": "brand_new_tool", "description": "x",
                                                 "inputSchema": {"type": "object"}}])
    with pytest.raises(RuntimeError, match="brand_new_tool"):
        M._check_tool_classes()
    monkeypatch.setattr(M, "TOOLS", M.TOOLS[:-1])
    monkeypatch.setattr(M, "ALIASES", {**M.ALIASES, "old_name": "no_such_tool"})
    with pytest.raises(RuntimeError, match="old_name"):
        M._check_tool_classes()


# ── tools/list and instructions ───────────────────────────────────────────────

def test_public_tools_list_same_names_and_schemas(no_key):
    listed = no_key.rpc("tools/list")["result"]["tools"]
    assert listed is not M.TOOLS
    assert len(listed) == 60
    assert [t["name"] for t in listed] == [t["name"] for t in M.TOOLS]
    for pub, op in zip(listed, M.TOOLS):
        assert pub["inputSchema"] == op["inputSchema"]
        assert pub["annotations"] == op["annotations"]
        assert set(pub) == set(op)
    assert E.tools_hash(M.TOOLS) == E.BASE_TOOLS_SHA256   # the copy left TOOLS alone


def test_public_tools_list_has_no_dashboard_or_pause_wording(no_key):
    listed = no_key.rpc("tools/list")["result"]["tools"]
    for tool in listed:
        low = tool["description"].lower()
        hits = [w for w in E.FORBIDDEN_TOOL_WORDS if w in low]
        assert not hits, (tool["name"], hits)
    # every operator description with that wording has an override
    for tool in M.TOOLS:
        if any(w in tool["description"].lower() for w in E.FORBIDDEN_TOOL_WORDS):
            assert tool["name"] in M._PUBLIC_DESCRIPTION_OVERRIDES
    assert len(json.dumps(listed)) <= len(json.dumps(M.TOOLS)) + 200


@pytest.mark.parametrize("case", ["no_key", "testnet_key", "mainnet_key", "builder_set",
                                  "builder_unset"])
def test_public_instructions_content_and_length(case, monkeypatch, tmp_path, builder_jobs):
    kw = {"no_key": {}, "testnet_key": {"key": True}, "mainnet_key": {"key": True, "network": "mainnet"},
          "builder_set": {"key": True, "builder_address": E.BUILDER},
          "builder_unset": {"key": True, "builder_address": ""}}[case]
    h = E.install(monkeypatch, tmp_path, M, builder_jobs, **kw)
    text = h.rpc("initialize", {})["result"]["instructions"]
    low = text.lower()
    net = kw.get("network", "testnet")
    assert len(text) <= 2500
    assert text.endswith("write the local desk journal).")      # never truncated
    assert f"network: {net}" in low
    assert "not financial advice" in low
    assert "3x max leverage" in text and "1% of equity at risk at the stop" in text
    assert "one position" in text
    assert "set_leverage is capped" in text
    assert "testnet lists fewer live markets" in low
    assert ("trading: read-only" in low) == (case == "no_key")
    assert ("trading: enabled" in low) == (case != "no_key")
    if case == "builder_set":
        assert "0.01%" in text and E.BUILDER in text and "approve" in low
    else:
        assert "builder fee: not configured" in low
    for word in ("localhost", "desk page", "/api/", "dashboard", "20x"):
        assert word not in low, word
    if net == "mainnet":
        assert "real money" in low


# ── read-only tier ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool", sorted(M.LIVE_MONEY_TOOLS))
def test_write_tools_are_read_only_without_a_key(tool, no_key):
    from pathiel.client.exchange import READ_ONLY_NO_KEY
    err, body = no_key.call(tool, E.WRITE_ARGS[tool])
    assert err is True
    assert body == {"ok": False, "read_only": True, "network": "testnet", "tool": tool,
                    "error": READ_ONLY_NO_KEY}
    assert no_key.venue.urls == []          # refused before any read
    assert E.ExchangeSpy.constructed == 0


def test_dry_run_bracket_passes_the_gate(no_key):
    assert M._public_gate("place_bracket", {"coin": "BTC", "stop_px": 1, "tp_px": 2,
                                            "dry_run": True}) is None
    err, body = no_key.call("place_bracket", {"coin": "BTC", "stop_px": 72000, "tp_px": 77000,
                                              "dry_run": True})
    assert body.get("read_only") is not True
    assert body["network"] == "testnet"
    assert "Traceback" not in json.dumps(body)


def test_account_reads_without_an_address_say_so(no_key):
    for tool in sorted(M.ACCOUNT_READ_TOOLS):
        err, body = no_key.call(tool, E.ACCOUNT_ARGS.get(tool))
        assert err is True, tool
        assert body == {"error": M.NO_ACCOUNT_MSG, "no_account": True}, (tool, body)


@pytest.mark.parametrize("alias", ACCOUNT_ALIASES)
def test_account_read_aliases_get_the_same_answer(alias, no_key):
    err, body = no_key.call(alias, E.ACCOUNT_ARGS.get(M._canonical(alias)))
    assert err is True
    assert body == {"error": M.NO_ACCOUNT_MSG, "no_account": True}


def test_what_if_account_reads_need_no_account(no_key):
    err, body = no_key.call("risk_limits", {"equity_usd": 1000})
    assert not err and body["equity_usd"] == 1000
    err, body = no_key.call("risk_size_trade", {"coin": "BTC", "entry_px": 73000, "stop_px": 72000,
                                                "equity_usd": 1000})
    assert not err and body["coin"] == "BTC"
    err, body = no_key.call("get_liquidation_price", {"coin": "BTC", "leverage": 3, "is_long": True})
    assert not err and body["source"] == "estimate"
    err, body = no_key.call("wallet_account", {"address": E.ACCT})
    assert not err and body["address"] == E.ACCT


def test_account_reads_with_an_address_return_data(monkeypatch, tmp_path, builder_jobs):
    h = E.install(monkeypatch, tmp_path, M, builder_jobs, address=E.ACCT)
    for tool in ("get_account_summary", "get_asset_positions", "get_open_orders",
                 "get_user_fees", "account_overview", "risk_limits", "wallet_account"):
        err, body = h.call(tool, E.ACCOUNT_ARGS.get(tool))
        assert E.is_data(err, body), (tool, body)
    err, body = h.call("place_bracket", E.WRITE_ARGS["place_bracket"])
    assert err and body["read_only"] is True


def test_market_reads_need_nothing(no_key):
    for tool in sorted(M.MARKET_READ_TOOLS):
        err, body = no_key.call(tool, E.MARKET_ARGS.get(tool))
        assert E.is_data(err, body), (tool, body)
    assert no_key.venue.unknown == []
    assert no_key.venue.off_base() == []


# ── gates ─────────────────────────────────────────────────────────────────────

def test_set_leverage_above_profile_cap_is_refused(keyed, monkeypatch):
    from contracts.risk.models import RiskProfile
    from pathiel.agents import config_store
    monkeypatch.setattr(config_store, "read_risk_profile", lambda config=None: RiskProfile(
        preset="custom", risk_per_trade_pct=1.0, daily_loss_limit_pct=3.0, max_leverage=3))
    err, body = keyed.call("set_leverage", {"coin": "BTC", "leverage": 20})
    assert err is True
    assert body["max_leverage"] == 3 and "3x" in body["error"] and "20x" in body["error"]
    assert keyed.sdk.other == []
    err, body = keyed.call("set_leverage", {"coin": "BTC", "leverage": 3})
    assert not err, body
    assert keyed.sdk.other == ["update_leverage BTC 3"]


def test_auto_profile_cap_is_the_highest_auto_tier(keyed):
    assert M._profile_leverage_cap() == 10.0


def test_delisted_coin_is_refused_for_entries_not_for_closes(keyed):
    coin = "xyz:BRENTOIL"
    for tool, args in [("place_bracket", {"coin": coin, "stop_px": 95, "tp_px": 120}),
                       ("place_bracket", {"coin": coin, "stop_px": 95, "tp_px": 120, "dry_run": True}),
                       ("set_target", {"coin": coin, "tp_px": 120}),
                       ("set_leverage", {"coin": coin, "leverage": 2})]:
        err, body = keyed.call(tool, args)
        assert err is True, tool
        assert body["network_note"].startswith(f"{coin} is not tradable on testnet (delisted).")
        assert body["placed"] is False
    assert keyed.sdk.orders == [] and keyed.sdk.other == []
    for tool, args in [("close_position", {"coin": coin}), ("bracket_sweep", {"coin": coin}),
                       ("cancel_order", {"order_id": 7, "coin": coin})]:
        _err, body = keyed.call(tool, args)
        assert "network_note" not in body, (tool, body)
    assert "cancel xyz:BRENTOIL 7" in keyed.sdk.other


def test_unlisted_coin_is_refused(keyed):
    err, body = keyed.call("place_bracket", {"coin": "NOPE", "stop_px": 1, "tp_px": 3})
    assert err and "(not_listed)" in body["network_note"]
    err, body = keyed.call("place_bracket", {"coin": "zzz:NOPE", "stop_px": 1, "tp_px": 3})
    assert err and "(not_listed)" in body["network_note"]


def test_unknown_listing_does_not_refuse(keyed, monkeypatch):
    from pathiel.client import exchange
    monkeypatch.setattr(exchange, "coin_listing", lambda coin: "unknown")
    assert M._public_gate("place_bracket", {"coin": "BTC", "stop_px": 1, "tp_px": 2}) is None


def test_desk_brief_names_a_delisted_primary(no_key):
    err, body = no_key.call("desk_brief", {"desk": "oil"})
    assert not err
    assert body["network_note"].startswith("xyz:BRENTOIL is not tradable on testnet (delisted).")


def test_desk_refresh_without_claude_cli_fails_fast(no_key, monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_COMMAND", "pathiel-test-no-such-claude-binary")
    t0 = time.monotonic()
    err, body = no_key.call("desk_refresh", {"desk": "oil"})
    assert time.monotonic() - t0 < 1.0
    assert err is True
    assert body == {"error": ("desk_refresh needs the Claude Code CLI (claude) on PATH to write a "
                              "thesis and it was not found. Install Claude Code or set "
                              "CLAUDE_CLI_COMMAND. desk_brief still shows the last thesis."),
                    "desk": "oil"}
    assert no_key.facade.refresh_calls == []


def test_desk_refresh_with_the_cli_runs(no_key, monkeypatch):
    monkeypatch.setattr(M.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    err, body = no_key.call("desk_refresh", {"desk": "oil"})
    assert not err and no_key.facade.refresh_calls == [("oil", False)]


def test_a_broken_gate_fails_closed_for_writes_only(no_key, monkeypatch):
    def boom(name, args):
        raise ValueError("bad")
    monkeypatch.setattr(M, "_public_gate", boom)
    err, body = no_key.call("cancel_order", {"order_id": 1, "coin": "BTC"})
    assert err and "nothing was sent" in body["error"]
    err, body = no_key.call("get_price", {"coin": "BTC"})
    assert not err and body["price"] > 0


# ── builder fee in live-money output ──────────────────────────────────────────

def test_live_money_outputs_carry_network_and_builder_fee(keyed):
    keyed.warm_builder()
    err, body = keyed.call("place_bracket", E.WRITE_ARGS["place_bracket"])
    assert not err, body
    fee = body["builder_fee"]
    assert body["network"] == "testnet"
    assert fee["charged"] is True and fee["rate"] == "0.01%" and fee["address"] == E.BUILDER
    assert fee["reason"] == "approved"
    assert fee["est_usd"] == M._fee_estimate(body["sizing"]["notional_usd"], 10)
    E.seed_positions(keyed.venue)
    for tool in ("close_position", "set_target", "bracket_sweep"):
        before = len(keyed.sdk.orders)
        err, body = keyed.call(tool, E.WRITE_ARGS[tool])
        assert not err, (tool, body)
        assert body["network"] == "testnet"
        assert body["builder_fee"]["charged"] is True, (tool, body["builder_fee"])
        assert body["builder_fee"]["rate"] == "0.01%"
        assert all(o["builder"] == {"b": E.BUILDER, "f": 10} for o in keyed.sdk.orders[before:])
    for tool in ("set_leverage", "cancel_order"):
        _err, body = keyed.call(tool, E.WRITE_ARGS[tool])
        assert body["network"] == "testnet"
        assert body["builder_fee"]["charged"] is False
        assert body["builder_fee"]["reason"] == "not_an_order"
    assert all(u.startswith(TESTNET_API_URL) for u in keyed.venue.urls)


def test_cold_cache_output_says_not_charged(keyed):
    err, body = keyed.call("place_bracket", E.WRITE_ARGS["place_bracket"])
    assert not err, body
    assert body["builder_fee"]["charged"] is False
    assert body["builder_fee"]["reason"] == "cache_cold"
    assert body["builder_fee"]["est_usd"] == 0.0
    assert keyed.sdk.orders and all(o["builder"] is None for o in keyed.sdk.orders)


def test_not_approved_output_says_not_charged(monkeypatch, tmp_path, builder_jobs):
    h = E.install(monkeypatch, tmp_path, M, builder_jobs, key=True, builder_address=E.BUILDER,
                  max_builder_fee=5)
    h.warm_builder()
    E.seed_positions(h.venue)
    err, body = h.call("set_target", E.WRITE_ARGS["set_target"])
    assert not err, body
    assert body["builder_fee"]["charged"] is False
    assert body["builder_fee"]["reason"] == "not_approved"
    assert "approve_builder_fee.py" in body["builder_fee"]["detail"]


def test_live_money_call_warms_the_builder_cache(keyed):
    from pathiel.client import builder
    assert keyed.jobs == []
    keyed.call("set_leverage", {"coin": "BTC", "leverage": 2})
    assert len(keyed.jobs) >= 1                     # scheduled, not run inline
    assert builder.builder_status(E.ACCT)["reason_code"] == "cache_cold"
    keyed.run_builder_jobs()
    assert builder.builder_status(E.ACCT)["attaches"] is True
    assert keyed.venue.types.count("maxBuilderFee") == 1


def test_builder_fee_estimate_math():
    assert M._fee_estimate(250, 10) == 0.025
    assert M._fee_estimate(250, 0) == 0.0
    assert M._fee_estimate(1000, 100) == 1.0
    assert M._fee_estimate(3041.72, 10) == 0.3042


def test_dry_run_output_names_the_next_order(keyed):
    keyed.warm_builder()
    err, body = keyed.call("place_bracket", {**E.WRITE_ARGS["place_bracket"], "dry_run": True})
    assert body["builder_fee"]["charged"] is False
    assert body["builder_fee"]["reason"] == "no_order_placed"
    assert "approved" in body["builder_fee"]["detail"]
    assert keyed.sdk.orders == []


# ── state, risk_limits, wording ───────────────────────────────────────────────

def test_state_carries_the_install_block(keyed):
    keyed.warm_builder()
    err, body = keyed.call("state")
    assert not err
    install = body["install"]
    assert install["kind"] == "public" and install["network"] == "testnet"
    assert install["network_explicit"] is True
    assert install["trading"] == "enabled" and install["read_only_reason"] is None
    fee = install["builder_fee"]
    assert fee["address"] == E.BUILDER and fee["fee_rate"] == "0.01%" and fee["attaches"] is True


def test_state_install_block_without_a_key(no_key, monkeypatch):
    monkeypatch.delenv("PATHIEL_NETWORK")
    _err, body = no_key.call("state")
    assert body["install"]["trading"] == "read_only"
    assert body["install"]["network"] == "testnet" and body["install"]["network_explicit"] is False
    assert body["install"]["builder_fee"]["reason_code"] == "no_builder_address"


def test_risk_limits_replaces_the_daily_pause_note(no_key):
    err, body = no_key.call("risk_limits", {"equity_usd": 1000})
    assert not err
    text = json.dumps(body)
    assert "does not stop trading for you" in text
    assert "pause" not in text.lower()
    assert "stops for the day" not in text
    assert body["notes"][0] == (f"Daily loss figure: -${body['daily_loss_limit_usd']:,.2f}. "
                                "Informational only: pathiel does not stop trading for you.")
    assert sum("Daily loss figure:" in n for n in body["notes"]) == 1
    assert body["change_it"] == "the config tool (risk_profile), or edit ~/.pathiel/.agent-config.json."


def test_no_dashboard_wording_on_public_outputs(monkeypatch, tmp_path, builder_jobs):
    h = E.install(monkeypatch, tmp_path, M, builder_jobs, address=E.ACCT)
    for tool, args in [("accounts", {}), ("connected_wallets", {}), ("wallet_account", {}),
                       ("account_overview", {}), ("risk_limits", {"equity_usd": 500})]:
        _err, body = h.call(tool, args)
        text = json.dumps(body)
        for word in ("dashboard", "Account tab", "PUT /api/", "localhost"):
            assert word not in text, (tool, word, text[:300])
    rewritten = M._reword({"a": ["Connect one in the Account tab.", "signed in to the dashboard"],
                           "b": "Account tab in the dashboard (risk profile), or PUT /api/risk/profile."})
    assert "Account tab" not in json.dumps(rewritten) and "dashboard" not in json.dumps(rewritten)
    assert "PUT /api" not in json.dumps(rewritten)


def test_decorate_returns_non_json_unchanged(keyed):
    for text in ("not json", "[1, 2]", "42", '"a string"', ""):
        assert M._public_decorate("state", {}, text) == text
        assert M._public_decorate("place_bracket", {}, text) == text


# ── stdio, fresh HOME ─────────────────────────────────────────────────────────

class TestStdioFreshHome:
    """The real server process, launched the way the plugin launches it
    (PATHIEL_INSTALL=public, an empty env file, a temp HOME), with every
    proxy pointed at a dead port so nothing can reach a venue."""

    def _session(self, public_env, *calls):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}}}]
        msgs += [{"jsonrpc": "2.0", "id": i + 2, **c} for i, c in enumerate(calls)]
        proc = subprocess.run(
            [sys.executable, str(E.SERVER)],
            input="".join(json.dumps(m) + "\n" for m in msgs),
            capture_output=True, text=True, timeout=90, env=public_env(),
            cwd=str(public_env.env_file.parent))
        out = {}
        for line in proc.stdout.splitlines():
            msg = json.loads(line)
            out[msg.get("id")] = msg
        return proc, out

    def test_starts_and_lists_60_tools(self, public_env):
        proc, out = self._session(public_env, {"method": "tools/list"})
        assert proc.returncode == 0, proc.stderr[-2000:]
        init = out[1]["result"]
        assert init["serverInfo"]["name"] == "pathiel"
        assert "Network: testnet" in init["instructions"]
        assert "Trading: read-only" in init["instructions"]
        tools = out[2]["result"]["tools"]
        assert len(tools) == 60
        assert not any("dashboard" in t["description"].lower() for t in tools)
        assert "maintainer mode" not in proc.stderr

    def test_place_bracket_is_read_only(self, public_env):
        proc, out = self._session(public_env, {"method": "tools/call", "params": {
            "name": "place_bracket", "arguments": {"coin": "BTC", "stop_px": 1, "tp_px": 3}}})
        assert proc.returncode == 0, proc.stderr[-2000:]
        result = out[2]["result"]
        assert result["isError"] is True
        body = json.loads(result["content"][0]["text"])
        assert body["read_only"] is True and body["network"] == "testnet"
        assert body["tool"] == "place_bracket"
        assert "Traceback" not in proc.stderr
