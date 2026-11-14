"""The public MCP toolset (contracts/mcp_public): 30 scoped tools a plugin
install lists, how each reaches the old handlers, and that nothing old broke.

Why: directory graders (Glama TDQS, 2026-11-13) scored the old 65-tool public
list 3.6/5 for overlapping tools, mixed naming and thin descriptions. These
gate tests pin the structure that fixes it; tests/test_eval_tdqs.py (paid,
marked live) scores the descriptions with the same rubric.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

import contracts.mcp_public as P

_KIT = Path(__file__).resolve().parent / "test_eval_public_tiers.py"
_spec = importlib.util.spec_from_file_location("pathiel_public_tiers_kit_toolset", _KIT)
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)
M = E.load_server("pathiel_mcp_server_toolset")


# ── the contract ─────────────────────────────────────────────────────────────

def test_thirty_unique_verb_noun_names():
    assert len(P.NAMES) == 30 and len(set(P.NAMES)) == 30
    for name in P.NAMES:
        assert P.NAME_RE.match(name), name
    assert set(P.KIND.values()) <= set(P.KINDS)


def test_every_name_has_exactly_one_route():
    for name in P.NAMES:
        routes = [name in P.RENAMES, name in P.COMPOSITE_INNER, name in P.OWN_HANDLER]
        assert routes.count(True) == 1, name


def test_descriptions_follow_the_rubric(copy_lint):
    """Purpose, a sibling to use instead, what it returns: the three things
    the low-scoring old definitions lacked. No 'also answers' alias lists."""
    for name, _kind, desc, _schema in P.TOOLS:
        assert 300 <= len(desc) <= 1400, (name, len(desc))
        assert "Returns" in desc, name
        others = [n for n in P.NAMES if n != name and re.search(rf"\b{n}\b", desc)]
        assert others, f"{name} names no sibling tool"
        assert "also answers" not in desc.lower(), name
        assert copy_lint.style_hits(desc) == [], (name, copy_lint.style_hits(desc))
        assert copy_lint.claim_hits(desc) == [], name


def test_live_money_descriptions_say_they_need_a_key_and_what_is_refused():
    for name, kind, desc, _ in P.TOOLS:
        if kind == "live_money":
            assert "Needs a trading key" in desc, name
            low = desc.lower()
            assert "refus" in low or "{error}" in low or name == "sweep_brackets", name


def test_built_tools_document_every_parameter_and_keep_old_arguments():
    old = {t["name"]: t for t in M.TOOLS + M.PUBLIC_ONLY_TOOLS}
    built = P.build_tools(lambda n: json.loads(json.dumps(old[n]["inputSchema"])))
    for tool in built:
        for key, prop in tool["inputSchema"].get("properties", {}).items():
            assert prop.get("description"), (tool["name"], key)
        if tool["name"] in P.RENAMES and P.TOOLS[P.NAMES.index(tool["name"])][3] is None:
            want = old[P.RENAMES[tool["name"]]]["inputSchema"]
            assert set(tool["inputSchema"]["properties"]) == set(want["properties"])
            assert tool["inputSchema"].get("required") == want.get("required")
        assert tool["annotations"] == P.annotations(P.KIND[tool["name"]])
        if P.KIND[tool["name"]] == "live_money":
            assert tool["description"].startswith("LIVE MONEY")


# ── composite routing (pure) ─────────────────────────────────────────────────

ROUTES = [
    ("list_markets", {}, ["market_list_instruments"]),
    ("list_markets", {"view": "mids", "coins": ["BTC"]}, ["get_all_mids"]),
    ("list_markets", {"view": "funding"}, ["get_funding_rate"]),
    ("list_markets", {"view": "open_interest"}, ["get_open_interest"]),
    ("list_markets", {"view": "assets"}, ["get_assets"]),
    ("get_candles", {"coin": "BTC"}, ["get_candles_by_count"]),
    ("get_candles", {"coin": "BTC", "start_time": 1}, ["get_price_history"]),
    ("get_order_book", {"coin": "BTC"}, ["get_l2_book"]),
    ("get_order_book", {"coin": "BTC", "size": 1}, ["get_l2_book", "get_price_impact"]),
    ("get_account", {}, ["account_overview", "get_risk_metrics"]),
    ("get_account", {"detail": "full"}, ["account_overview", "get_risk_metrics", "get_portfolio_status",
                                         "get_user_fees", "get_api_rate_limits"]),
    ("get_account", {"address": "0x" + "1" * 40, "detail": "full"}, ["wallet_account", "get_risk_metrics"]),
    ("get_positions", {}, ["get_asset_positions"]),
    ("get_positions", {"coin": "ETH"}, ["get_asset_positions", "get_liquidation_price"]),
    ("get_positions", {"coin": "ETH", "address": "0x" + "1" * 40}, ["get_asset_positions"]),
    ("get_orders", {}, ["get_open_orders"]),
    ("get_orders", {"status": "history"}, ["get_historical_orders"]),
    ("get_orders", {"oid": 5, "status": "history"}, ["get_order_by_oid"]),
    ("get_orders", {"cloid": "0x" + "0" * 32}, ["query_order_by_cloid"]),
    ("get_history", {}, ["get_trade_history"]),
    ("get_history", {"start_time": 1}, ["get_user_fills_by_time"]),
    ("get_history", {"start_time": 1, "address": "0x" + "1" * 40}, ["get_trade_history"]),
    ("get_history", {"kind": "funding"}, ["get_historical_funding"]),
    ("get_history", {"kind": "deposits"}, ["get_deposits"]),
    ("get_history", {"kind": "withdrawals"}, ["get_withdrawals"]),
    ("get_history", {"kind": "withdrawals", "withdrawal_id": "0xab"}, ["get_withdrawal_status"]),
    ("get_history", {"kind": "transfers"}, ["get_transfers"]),
    ("size_trade", {"coin": "BTC", "entry_px": 100, "stop_px": 95}, ["risk_size_trade", "get_max_trade_size"]),
    ("size_trade", {"coin": "BTC", "entry_px": 100, "stop_px": 95, "equity_usd": 500}, ["risk_size_trade"]),
    ("get_status", {}, ["state", "accounts"]),
    ("get_config", {"mode": "LIVE"}, ["config"]),
    ("get_desk_record", {"desk": "oil"}, ["desk_track_record"]),
    ("get_desk_record", {"desk": "oil", "grade": True}, ["desk_grade", "desk_track_record"]),
    ("get_pool_status", {}, ["pool_status"]),
    ("get_pool_status", {"report": True}, ["pool_status", "pool_report"]),
    ("get_market", {"coin": "BTC"}, ["get_price", "get_asset_context", "get_predicted_funding", "get_max_leverage"]),
]


@pytest.mark.parametrize("name,args,want", ROUTES)
def test_plan_routes_to_the_right_old_tools(name, args, want):
    assert [old for _k, old, _a in P.plan(name, args)] == want
    for _k, old, _a in P.plan(name, args):
        assert old in P.COMPOSITE_INNER[name]


def test_plan_translates_argument_names():
    (_, _, impact) = P.plan("get_order_book", {"coin": "BTC", "notional_usd": 500, "is_buy": False})[1]
    assert impact == {"coin": "BTC", "notionalUsd": 500, "is_buy": False}
    (_, _, funding), = P.plan("get_history", {"kind": "funding", "start_time": 1, "end_time": 2,
                                              "address": "0xa"})
    assert funding == {"startTime": 1, "endTime": 2, "user": "0xa"}
    (_, _, cfg), = P.plan("get_config", {"mode": "LIVE"})
    assert cfg == {}, "get_config never passes arguments through to the writer"
    (_, _, mx) = P.plan("size_trade", {"coin": "BTC", "entry_px": 90, "stop_px": 95})[1]
    assert mx == {"coin": "BTC", "is_buy": False}, "stop above entry is a short"


def test_every_composite_and_view_has_a_route():
    for name in P.COMPOSITE_INNER:
        assert P.plan(name, {"coin": "BTC", "desk": "oil", "entry_px": 1, "stop_px": 0.9})
    assert P.plan("get_history", {"kind": "nonsense"}) == []


# ── run(): passthrough, merge, errors ────────────────────────────────────────

def _fake(answers):
    calls = []

    def call(old, args):
        calls.append((old, args))
        return answers[old]
    return call, calls


def test_single_route_returns_the_old_answer_unchanged():
    call, calls = _fake({"get_open_orders": ('{"count": 0, "orders": []}', False)})
    assert P.run("get_orders", {}, call) == '{"count": 0, "orders": []}'
    assert calls == [("get_open_orders", {})]


def test_merge_keeps_each_part_and_one_failing_part_is_not_a_failure():
    call, _ = _fake({"get_price": ('{"price": 1}', False), "get_asset_context": ('{"error": "x"}', True),
                     "get_predicted_funding": ('{"venues": []}', False),
                     "get_max_leverage": ('{"max_leverage": 40}', False)})
    out = json.loads(P.run("get_market", {"coin": "BTC"}, call))
    assert out["coin"] == "BTC" and out["mid"] == {"price": 1} and out["context"] == {"error": "x"}
    assert "error" not in out


def test_every_part_failing_is_an_error_and_no_account_wins():
    call, _ = _fake({k: ('{"error": "down"}', True) for k in P.COMPOSITE_INNER["get_market"]})
    assert json.loads(P.run("get_market", {"coin": "BTC"}, call))["error"] == "down"
    no_acct = json.dumps({"error": "no account configured", "no_account": True})
    call, _ = _fake({"account_overview": (no_acct, True), "get_risk_metrics": (no_acct, True)})
    assert json.loads(P.run("get_account", {}, call)) == json.loads(no_acct)


def test_update_config_refuses_an_empty_change():
    assert "get_config" in json.loads(P.validate_update_config({}))["error"]
    assert P.validate_update_config({"mode": None}) is not None
    assert P.validate_update_config({"enable_hip3": True}) is None


# ── the server ───────────────────────────────────────────────────────────────

def test_operator_install_is_unchanged():
    assert E.tools_hash(M.TOOLS) == E.BASE_TOOLS_SHA256
    handlers = M._tool_handlers()
    assert handlers["get_candles"] is handlers["get_candles_aggregated"], "operator get_candles is its own"
    assert M._check_public_toolset() == [] and M._check_tool_classes() == []


def test_renames_are_their_old_tool_for_the_gate():
    for new, old in P.RENAMES.items():
        assert M._canonical(new) == old
    assert M._canonical("sweep_brackets") in M.LIVE_MONEY_TOOLS
    assert M._canonical("get_candles_by_count") == "get_candles"


@pytest.fixture
def keyed(monkeypatch, tmp_path, builder_jobs):
    h = E.install(monkeypatch, tmp_path, M, builder_jobs, key=True)
    E.seed_positions(h.venue)
    return h


@pytest.fixture
def no_key(monkeypatch, tmp_path, builder_jobs):
    return E.install(monkeypatch, tmp_path, M, builder_jobs)


CALLS = {
    "get_market": {"coin": "BTC"}, "list_markets": {"limit": 3}, "get_candles": {"coin": "BTC", "count": 3},
    "get_order_book": {"coin": "BTC", "notional_usd": 1000}, "get_funding_regime": {},
    "get_account": {"detail": "full"}, "get_positions": {"coin": "ETH"}, "get_orders": {},
    "get_history": {}, "get_risk_limits": {}, "size_trade": {"coin": "BTC", "entry_px": 75000, "stop_px": 74000},
    "list_desks": {}, "get_desk_brief": {"desk": "oil"}, "get_desk_record": {"desk": "oil"},
    "get_status": {}, "get_config": {}, "get_pool_status": {},
}


def test_every_read_answers_offline_with_data(keyed):
    listed = [t["name"] for t in keyed.rpc("tools/list")["result"]["tools"]]
    assert listed == P.NAMES
    for name, args in CALLS.items():
        err, body = keyed.call(name, args)
        assert not err, (name, body)
        text = json.dumps(body)
        assert "Traceback" not in text and "KeyError" not in text, name
    # accounts (inside get_status) asks the venue which API wallets the account
    # approved (extraAgents); the offline venue does not model it and answers
    # nothing, which accounts reports as none. Anything else unknown is a bug.
    assert [r for r in keyed.venue.unknown if r.get("type") != "extraAgents"] == []


def test_market_reads_need_no_key(no_key):
    for name in ("get_market", "list_markets", "get_candles", "get_order_book", "get_funding_regime"):
        err, body = no_key.call(name, CALLS.get(name, {}))
        assert E.is_data(err, body), (name, body)


@pytest.mark.parametrize("name,args", [("set_stop", {"coin": "ETH", "stop_px": 1900}),
                                       ("sweep_brackets", {"coin": "ETH"}),
                                       ("set_target", {"coin": "ETH", "tp_px": 2100}),
                                       ("close_position", {"coin": "ETH"})])
def test_live_money_tools_are_read_only_without_a_key(no_key, name, args):
    from pathiel.client.exchange import READ_ONLY_NO_KEY
    err, body = no_key.call(name, args)
    assert err is True and body["read_only"] is True and body["error"] == READ_ONLY_NO_KEY
    assert no_key.venue.urls == []


def test_set_stop_tightens_the_seeded_long_through_the_server(keyed):
    """ETH long 0.5 at 1900 with two stops at 1800 (seed_positions): 1850 is
    tighter, goes on first, both old stops go."""
    err, body = keyed.call("set_stop", {"coin": "ETH", "stop_px": 1850})
    assert not err, body
    assert body["ok"] is True and body["stop_px"] == 1850 and sorted(body["replaced"]) == ["1800.0", "1800.0"]
    assert body["network"] == "testnet" and "builder_fee" in body
    err, body = keyed.call("set_stop", {"coin": "ETH", "stop_px": 1700})
    assert err is True and "not tighter" in body["error"]


def test_old_public_names_still_answer(keyed):
    for old in ("get_price", "state", "desk_list", "risk_size_trade", "wallet_account", "pool_status"):
        args = {"coin": "BTC", "entry_px": 75000, "stop_px": 74000} if old == "risk_size_trade" else (
            {"coin": "BTC"} if old == "get_price" else {})
        if old == "wallet_account":
            args = {"address": E.ACCT}
        err, body = keyed.call(old, args)
        assert not err, (old, body)


# ── history keeps the newest rows (found live, 2026-11-13) ───────────────────

@pytest.mark.parametrize("handler,info_type,key", [
    ("handle_get_trade_history", "userFills", "fills"),
    ("handle_get_historical_funding", "userFunding", "rows"),
])
def test_history_keeps_the_newest_rows_whatever_order_the_venue_sends(monkeypatch, handler, info_type, key):
    """A real Claude Code session asked for a wallet's last 3 fills and got
    June's: the venue sends userFills newest first and the handler kept
    rows[-limit:]. Rows are now sorted oldest first before the limit."""
    newest_first = [{"time": t, "coin": "BTC", "delta": {"coin": "BTC"}} for t in (500, 400, 300, 200, 100)]
    monkeypatch.setattr(M, "_info", lambda typ, **kw: list(newest_first) if typ == info_type else [])
    monkeypatch.setattr(M, "_user", lambda params=None: "0x" + "1" * 40)
    out = json.loads(getattr(M, handler)({"limit": 2, "startTime": 0}))
    assert [r["time"] for r in out[key]] == [400, 500]


def test_ledger_keeps_the_newest_rows(monkeypatch):
    rows = [{"time": t, "delta": {"type": "deposit", "usdc": "1"}} for t in (300, 100, 200)]
    monkeypatch.setattr(M, "_info", lambda typ, **kw: list(rows))
    monkeypatch.setattr(M, "_user", lambda params=None: "0x" + "1" * 40)
    out = json.loads(M.handle_get_deposits({"limit": 2}))
    assert [r["time"] for r in out["rows"]] == [200, 300]


def test_another_wallets_fills_honour_the_time_window():
    """Live 2026-11-13: get_history(address, start_time) ignored the window.
    The venue cannot time-filter another wallet's fills, so the composite
    takes its newest 2000 and cuts the window itself, newest kept."""
    fills = [{"time": t, "coin": "BTC"} for t in range(100, 1100, 100)]   # oldest first, 10 rows
    call, calls = _fake({"get_trade_history": (json.dumps({"count": 10, "fills": fills}), False)})
    out = json.loads(P.run("get_history", {"address": "0xa", "start_time": 300, "end_time": 800,
                                           "limit": 2}, call))
    assert calls == [("get_trade_history", {"limit": 2000, "user": "0xa"})]
    assert [r["time"] for r in out["fills"]] == [700, 800] and out["window"]["matched"] == 6
    assert "note" not in out["window"]
    full = [{"time": t} for t in range(5000, 7000)]   # 2000 rows, all after the window start
    call, _ = _fake({"get_trade_history": (json.dumps({"fills": full}), False)})
    out = json.loads(P.run("get_history", {"address": "0xa", "start_time": 1}, call))
    assert "newest 2000" in out["window"]["note"]


# ── the operator's install on the scoped toolset (PATHIEL_TOOLSET=v2) ────────

def _fresh_server(name):
    return E.load_server(name)


def test_operator_default_is_unchanged_without_the_switch(monkeypatch):
    monkeypatch.delenv("PATHIEL_INSTALL", raising=False)
    monkeypatch.delenv("PATHIEL_TOOLSET", raising=False)
    m = _fresh_server("srv_v2_off")
    assert m.server_tools() is m.TOOLS and not m.operator_v2()
    assert m.server_instructions() == m.SERVER_INSTRUCTIONS


def test_operator_v2_lists_the_scoped_toolset_without_public_only_tools(monkeypatch, tmp_path, builder_jobs):
    monkeypatch.setenv("PATHIEL_TOOLSET", "v2")
    m = _fresh_server("srv_v2_on")
    h = E.install(monkeypatch, tmp_path, m, builder_jobs, public=False, network="mainnet", key=True)
    monkeypatch.setenv("PATHIEL_TOOLSET", "v2")
    names = [t["name"] for t in h.rpc("tools/list")["result"]["tools"]]
    assert names == P.operator_names() and len(names) == 26
    assert not set(names) & P.OPERATOR_EXCLUDED
    init = h.rpc("initialize", {})["result"]["instructions"]
    assert init == m.OPERATOR_V2_INSTRUCTIONS
    for old in ("bracket_sweep", "desk_brief", "risk_size_trade", "account_overview"):
        assert not re.search(rf"(?<![a-z_]){old}\b", init), old
    for new in ("sweep_brackets", "get_desk_brief", "size_trade", "get_account", "set_stop"):
        assert new in init, new
    assert E.tools_hash(m.TOOLS) == E.BASE_TOOLS_SHA256, "the 60 definitions are untouched"
    # composites answer on the operator's path, and the old names still do too
    E.seed_positions(h.venue)
    for name, args in (("get_market", {"coin": "BTC"}), ("get_candles", {"coin": "BTC", "count": 3}),
                       ("get_positions", {"coin": "ETH"}), ("get_account", {}), ("get_status", {}),
                       ("bracket_sweep", {"coin": "ETH"}), ("desk_brief", {"desk": "oil"})):
        err, body = h.call(name, args)
        assert not err, (name, body)
        assert "Traceback" not in json.dumps(body), name
    # operator path: no public decoration on live-money output
    err, body = h.call("sweep_brackets", {"coin": "ETH"})
    assert not err and "builder_fee" not in body and "network" not in body


def test_operator_v2_instructions_name_only_listed_tools(monkeypatch):
    monkeypatch.setenv("PATHIEL_TOOLSET", "v2")
    m = _fresh_server("srv_v2_words")
    listed = set(P.operator_names())
    named = set(re.findall(r"\b([a-z]+_[a-z_]+)\b", m.OPERATOR_V2_INSTRUCTIONS))
    tools_named = {n for n in named if n in set(P.NAMES) | {t["name"] for t in m.TOOLS}}
    assert tools_named <= listed, tools_named - listed
