"""Gate tests for scripts/pathiel-mcp-server.py: protocol, schema honesty, handlers.

Deterministic, no network: every Hyperliquid call is monkeypatched. Each test
names the defect it pins. The live counterpart is tests/test_mcp_online_eval.py.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "pathiel-mcp-server.py"


def _load():
    spec = importlib.util.spec_from_file_location("pathiel_mcp_server_hardening", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _req(method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _call(name, args=None):
    resp = M.dispatch_message(_req("tools/call", {"name": name, "arguments": args or {}}))
    return resp["result"], json.loads(resp["result"]["content"][0]["text"])


# ── protocol ──────────────────────────────────────────────────────────────────
class TestProtocol:
    def test_ping_answers_empty_result(self):
        """MCP clients ping to check liveness. It used to get an error-shaped result."""
        assert M.dispatch_message(_req("ping")) == {"jsonrpc": "2.0", "id": 1, "result": {}}

    @pytest.mark.parametrize("method", ["notifications/initialized",
                                        "notifications/cancelled", "anything/else"])
    def test_notifications_never_get_a_response(self, method):
        """Answering a notification is a protocol violation. Unknown ones used to be answered."""
        assert M.dispatch_message({"jsonrpc": "2.0", "method": method}) is None

    def test_unknown_method_is_a_jsonrpc_error(self):
        resp = M.dispatch_message(_req("resources/list"))
        assert resp["error"]["code"] == -32601
        assert "result" not in resp

    def test_unknown_tool_is_invalid_params(self):
        resp = M.dispatch_message(_req("tools/call", {"name": "nope"}))
        assert resp["error"]["code"] == -32602
        assert "nope" in resp["error"]["message"]

    def test_tools_call_without_params_still_answers(self):
        """A missing params object used to raise inside the loop and the client hung."""
        resp = M.dispatch_message(_req("tools/call"))
        assert resp["id"] == 1 and "error" in resp

    def test_non_object_arguments_are_rejected(self):
        resp = M.dispatch_message(_req("tools/call", {"name": "state", "arguments": [1, 2]}))
        assert resp["result"]["isError"] is True

    @pytest.mark.parametrize("asked,expected", [
        ("2024-11-05", "2024-11-05"),
        ("2025-06-18", "2025-06-18"),
        ("1999-01-01", "2025-06-18"),
        (None, "2025-06-18"),
    ])
    def test_initialize_negotiates_protocol_version(self, asked, expected):
        params = {"capabilities": {}}
        if asked:
            params["protocolVersion"] = asked
        resp = M.dispatch_message(_req("initialize", params))
        assert resp["result"]["protocolVersion"] == expected
        assert resp["result"]["serverInfo"]["name"] == "pathiel"

    def test_initialize_records_sampling_capability(self):
        M.dispatch_message(_req("initialize", {"capabilities": {"sampling": {}}}))
        assert M._CLIENT_SUPPORTS_SAMPLING is True
        M.dispatch_message(_req("initialize", {"capabilities": {}}))
        assert M._CLIENT_SUPPORTS_SAMPLING is False

    def test_error_payload_sets_is_error(self, monkeypatch):
        """Handlers return {"error": ...}; that used to ship as isError:false."""
        monkeypatch.setitem(M._tool_handlers(), "state",
                            lambda p: json.dumps({"error": "boom"}))
        result, body = _call("state")
        assert result["isError"] is True and body["error"] == "boom"

    def test_status_error_payload_sets_is_error(self, monkeypatch):
        monkeypatch.setitem(M._tool_handlers(), "state",
                            lambda p: json.dumps({"status": "error", "error": "x"}))
        assert _call("state")[0]["isError"] is True

    def test_success_payload_is_not_error(self, monkeypatch):
        monkeypatch.setitem(M._tool_handlers(), "state", lambda p: json.dumps({"ok": 1}))
        assert _call("state")[0]["isError"] is False

    def test_handler_exception_becomes_error_result(self, monkeypatch):
        def boom(p):
            raise ValueError("bad")
        monkeypatch.setitem(M._tool_handlers(), "state", boom)
        result, body = _call("state")
        assert result["isError"] is True
        assert "ValueError" in body["error"]

    def test_run_loop_answers_parse_errors_and_survives(self, monkeypatch):
        stdin = io.StringIO("not json\n\n" + json.dumps(_req("ping", msg_id=7)) + "\n")
        out = io.StringIO()
        monkeypatch.setattr(M.sys, "stdin", stdin)
        monkeypatch.setattr(M.sys, "stdout", out)
        monkeypatch.setattr(M.sys, "stderr", io.StringIO())
        M.run()
        lines = [json.loads(x) for x in out.getvalue().splitlines()]
        assert lines[0]["error"]["code"] == -32700 and lines[0]["id"] is None
        assert lines[1] == {"jsonrpc": "2.0", "id": 7, "result": {}}
        assert len(lines) == 2  # the blank line produced nothing

    def test_run_loop_keeps_stray_prints_off_the_protocol_channel(self, monkeypatch):
        """A print() in an engine module used to splice text into the JSON-RPC stream."""
        def noisy(p):
            print("engine chatter")
            return json.dumps({"ok": True})
        monkeypatch.setitem(M._tool_handlers(), "state", noisy)
        stdin = io.StringIO(json.dumps(_req("tools/call", {"name": "state"})) + "\n")
        out, err = io.StringIO(), io.StringIO()
        monkeypatch.setattr(M.sys, "stdin", stdin)
        monkeypatch.setattr(M.sys, "stdout", out)
        monkeypatch.setattr(M.sys, "stderr", err)
        try:
            M.run()
        finally:
            M._PROTO_OUT = None
        lines = out.getvalue().splitlines()
        assert len(lines) == 1 and json.loads(lines[0])["result"]["isError"] is False
        assert "engine chatter" in err.getvalue()

    def test_server_request_answers_ping_while_parked(self, monkeypatch):
        """A client request that lands while sampling is pending still needs a reply."""
        replies = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 100, "method": "tools/list"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": "srv-1", "result": {"ok": 1}}) + "\n")
        out = io.StringIO()
        monkeypatch.setattr(M.sys, "stdin", replies)
        monkeypatch.setattr(M.sys, "stdout", out)
        monkeypatch.setattr(M.sys, "stderr", io.StringIO())
        M._server_request_seq = 0
        assert M._server_request("sampling/createMessage", {}) == {"ok": 1}
        sent = [json.loads(x) for x in out.getvalue().splitlines()]
        assert sent[1] == {"jsonrpc": "2.0", "id": 99, "result": {}}
        assert sent[2]["id"] == 100 and "error" in sent[2]


# ── schema honesty ────────────────────────────────────────────────────────────
def _keys_read_by(fn_name, fns, seen=None):
    """Every string constant reachable from a handler through module-level calls."""
    seen = seen if seen is not None else set()
    if fn_name in seen or fn_name not in fns:
        return set()
    seen.add(fn_name)
    out = set()
    for node in ast.walk(fns[fn_name]):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.add(node.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            out |= _keys_read_by(node.func.id, fns, seen)
    return out


class TestSchemas:
    FNS = {n.name: n for n in ast.walk(ast.parse(SERVER.read_text()))
           if isinstance(n, ast.FunctionDef)}

    @pytest.mark.parametrize("tool", M.TOOLS, ids=lambda t: t["name"])
    def test_every_advertised_param_is_read_by_its_handler(self, tool):
        """16 tools advertised params their handlers ignored (limit, user, is_buy,
        vault, withdrawal_id...). An agent passing one got silently ignored."""
        props = set(tool["inputSchema"].get("properties", {}))
        unread = props - _keys_read_by(f"handle_{tool['name']}", self.FNS)
        assert not unread, f"{tool['name']} advertises {sorted(unread)} but never reads them"

    @pytest.mark.parametrize("tool", M.TOOLS, ids=lambda t: t["name"])
    def test_required_params_are_declared(self, tool):
        schema = tool["inputSchema"]
        assert schema.get("type") == "object"
        assert set(schema.get("required", [])) <= set(schema.get("properties", {}))
        assert tool["description"].strip()

    def test_tool_names_are_unique(self):
        names = [t["name"] for t in M.TOOLS]
        assert len(names) == len(set(names))


# ── handlers ──────────────────────────────────────────────────────────────────
@pytest.fixture
def info(monkeypatch):
    """Fake /info: map type -> response (or callable(kw) -> response)."""
    table = {}
    calls = []

    def fake(typ, **kw):
        calls.append((typ, kw))
        v = table.get(typ)
        return v(kw) if callable(v) else v
    monkeypatch.setattr(M, "_info", fake)
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", "0x" + "a" * 40)
    table["__calls__"] = calls
    return table


BOOK = {"levels": [
    [{"px": "99", "sz": "10"}, {"px": "98", "sz": "10"}],
    [{"px": "101", "sz": "1"}, {"px": "102", "sz": "100"}],
]}


class TestPriceImpact:
    def test_size_is_coin_units_not_usd(self, info):
        """size=5 BTC used to be walked as a $5 order: zero impact, always."""
        info["allMids"] = {"BTC": "100"}
        info["l2Book"] = BOOK
        _, body = _call("get_price_impact", {"coin": "BTC", "size": 5})
        assert body["notional_usd"] == pytest.approx(500)
        assert body["worst_px"] == 102.0  # 1 coin at 101 is $101; the rest walks to 102
        assert body["size_coin"] == 5

    def test_notional_usd_overrides_size(self, info):
        info["l2Book"] = BOOK
        _, body = _call("get_slippage_estimate", {"coin": "BTC", "notionalUsd": 50, "size": 9})
        assert body["notional_usd"] == 50 and body["worst_px"] == 101.0

    def test_is_buy_false_hits_bids(self, info):
        info["allMids"] = {"BTC": "100"}
        info["l2Book"] = BOOK
        _, body = _call("get_price_impact", {"coin": "BTC", "size": 1, "is_buy": False})
        assert body["side"] == "sell" and body["best_px"] == 99.0

    def test_missing_mid_is_an_error(self, info):
        info["allMids"] = {}
        result, body = _call("get_price_impact", {"coin": "BTC", "size": 1})
        assert result["isError"] and "no mid" in body["error"]


class TestOrderLookups:
    def test_order_by_oid_defaults_user(self, info):
        """An omitted user went to HL as "" and 422'd."""
        info["orderStatus"] = {"status": "order"}
        _, body = _call("get_order_by_oid", {"oid": 5})
        assert body["user"] == "0x" + "a" * 40
        assert info["__calls__"][-1] == ("orderStatus", {"user": "0x" + "a" * 40, "oid": 5})

    def test_cloid_is_passed_as_string(self, info):
        """The SDK wrapper wanted a Cloid object and crashed on every string."""
        info["orderStatus"] = {"status": "unknownOid"}
        cloid = "0x" + "0" * 31 + "1"
        result, body = _call("query_order_by_cloid", {"cloid": cloid})
        assert not result["isError"]
        assert info["__calls__"][-1][1]["oid"] == cloid

    def test_explicit_user_wins(self, info):
        info["orderStatus"] = {}
        _call("get_order_status", {"oid": 1, "user": "0x" + "b" * 40})
        assert info["__calls__"][-1][1]["user"] == "0x" + "b" * 40


class TestLedgerAndFunding:
    ROWS = [{"hash": f"0x{i}", "delta": {"type": "withdraw" if i % 2 else "deposit"}}
            for i in range(10)]

    def test_ledger_honors_limit(self, info):
        info["userNonFundingLedgerUpdates"] = self.ROWS
        _, body = _call("get_deposits", {"limit": 2})
        assert body["count"] == 5 and [r["hash"] for r in body["rows"]] == ["0x6", "0x8"]

    def test_withdrawal_status_finds_by_hash(self, info):
        info["userNonFundingLedgerUpdates"] = self.ROWS
        _, hit = _call("get_withdrawal_status", {"withdrawal_id": "0x3"})
        _, miss = _call("get_withdrawal_status", {"withdrawal_id": "0x4"})
        assert hit["found"] is True and hit["status"] == "settled"
        assert miss["found"] is False

    def test_historical_funding_filters_coin(self, info):
        info["userFunding"] = [{"delta": {"coin": "BTC"}}, {"delta": {"coin": "ETH"}},
                               {"delta": {"coin": "BTC"}}]
        _, body = _call("get_historical_funding", {"coin": "btc", "limit": 1})
        assert body["count"] == 2 and len(body["rows"]) == 1

    def test_asset_contexts_failure_is_not_unknown_coin(self, info):
        """A 429 used to come back as "unknown coin: BTC"."""
        def fail(kw):
            raise RuntimeError("429")
        info["metaAndAssetCtxs"] = fail
        result, body = _call("get_funding_rate", {"coin": "BTC"})
        assert result["isError"] and "unknown coin" not in body["error"]


CLEARINGHOUSE = {
    "marginSummary": {"accountValue": "1000", "totalNtlPos": "3000", "totalMarginUsed": "300"},
    "crossMarginSummary": {"accountValue": "1000", "totalNtlPos": "3000"},
    "crossMaintenanceMarginUsed": "50",
    "withdrawable": "600",
    "assetPositions": [
        {"position": {"coin": "BTC", "szi": "0.03", "entryPx": "100000",
                      "liquidationPx": "70000", "unrealizedPnl": "12.5",
                      "returnOnEquity": "0.1", "leverage": {"value": 3}}},
        {"position": {"coin": "ETH", "szi": "0", "unrealizedPnl": "0"}},
    ],
}


class TestAccountTools:
    """summary / pnl / risk / asset_positions returned a hard-coded {} or []."""

    @pytest.fixture
    def acct(self, info, monkeypatch):
        info["clearinghouseState"] = CLEARINGHOUSE
        info["allMids"] = {"BTC": "100000"}
        info["portfolio"] = [["day", {"pnlHistory": [[1, "0"], [2, "4.5"]]}],
                             ["week", {"pnlHistory": []}]]
        monkeypatch.setattr(M, "fetch_account_state", lambda u, include_hip3=False: {
            "equity": 1010.0, "dex_equity": {"": 1000.0, "xyz": 10.0}, "available": 700.0,
            "total_ntl": 3000.0,
            "asset_positions": [CLEARINGHOUSE["assetPositions"][0]],
        })
        return info

    def test_account_summary(self, acct):
        _, body = _call("get_account_summary")
        s = body["summary"]
        assert s["equity"] == 1010.0 and s["withdrawable"] == 600.0
        assert s["position_count"] == 1 and s["equity_by_dex"]["xyz"] == 10.0

    def test_portfolio_pnl(self, acct):
        _, body = _call("get_portfolio_pnl")
        p = body["pnl"]
        assert p["unrealized_total"] == 12.5
        assert p["window_pnl"] == {"day": 4.5, "week": 0.0}

    def test_risk_metrics(self, acct):
        """Equity and leverage span every dex (2026-10-18: main-only read an
        all-xyz account as $0.0086); the cross margin ratio is main's own."""
        _, body = _call("get_risk_metrics")
        r = body["risk"]
        assert r["equity"] == 1010.0 and r["equity_by_dex"] == {"main": 1000.0, "xyz": 10.0}
        assert r["account_leverage"] == round(3000.0 / 1010.0, 4)
        assert r["main_margin_ratio"] == 0.05
        assert len(r["positions"]) == 1  # flat ETH excluded
        assert r["positions"][0]["liq_distance_pct"] == 30.0

    def test_asset_positions_filter(self, acct):
        _, body = _call("get_asset_positions", {"coin": "btc"})
        assert body["count"] == 1 and body["positions"][0]["coin"] == "BTC"
        _, body = _call("get_asset_positions", {"coin": "SOL"})
        assert body["count"] == 0

    def test_liquidation_price_reads_venue_for_open_position(self, acct):
        _, body = _call("get_liquidation_price", {"coin": "BTC"})
        assert body["source"] == "venue" and body["liquidation_price"] == "70000"


class TestLiquidationEstimate:
    @pytest.fixture(autouse=True)
    def _max_lev(self, monkeypatch, info):
        import pathiel.client.exchange as ex
        monkeypatch.setattr(ex, "get_max_leverage", lambda coin: 40)
        info["allMids"] = {"BTC": "100000"}

    def test_long_uses_maintenance_margin(self):
        """The old estimate entry*(1 - 1/lev) put liquidation too far away."""
        _, body = _call("get_liquidation_price", {"coin": "BTC", "leverage": 10, "is_long": True})
        # HL docs: price - side * margin_available / size / (1 - l * side), with
        # margin_available = initial - maintenance. 10x BTC (max 40x): l = 1/80.
        mmr = 1 / 80
        assert body["liquidation_price"] == pytest.approx(100000 - (10000 - 1250) / (1 - mmr))
        assert body["liquidation_price"] == pytest.approx(91139.24, abs=0.01)
        assert body["liquidation_price"] > 100000 * (1 - 1 / 10)  # closer than the old estimate

    def test_short(self):
        _, body = _call("get_liquidation_price", {"coin": "BTC", "leverage": 10, "is_long": False})
        assert body["liquidation_price"] == pytest.approx(100000 + (10000 - 1250) / (1 + 1 / 80))
        assert body["liquidation_price"] < 110000  # closer than the old 1/leverage estimate

    def test_over_max_leverage_rejected(self):
        result, body = _call("get_liquidation_price", {"coin": "BTC", "leverage": 50, "is_long": True})
        assert result["isError"] and "exceeds" in body["error"]


class TestPriceHistory:
    def test_window_is_honored_and_seconds_become_ms(self, info):
        """start_time/end_time were advertised and ignored."""
        info["candleSnapshot"] = [{"t": 1}]
        _, body = _call("get_price_history",
                        {"coin": "ETH", "start_time": 1_700_000_000, "end_time": 1_700_003_600})
        req = info["__calls__"][-1][1]["req"]
        assert req == {"coin": "ETH", "interval": "1h",
                       "startTime": 1_700_000_000_000, "endTime": 1_700_003_600_000}
        assert body["count"] == 1

    def test_inverted_window_rejected(self, info):
        result, _ = _call("get_price_history", {"coin": "ETH", "start_time": 2, "end_time": 1})
        assert result["isError"]


class TestScanResearchExecute:
    def test_scan_restores_max_markets_env(self, monkeypatch):
        """scan(maxMarkets=5) used to shrink every later scan in the process."""
        import pathiel.client.universe as uni
        seen = []
        monkeypatch.setattr(uni, "get_universe", lambda: [{"coin": "BTC"}])
        monkeypatch.setattr(M, "scan_once", lambda **kw: seen.append(
            os.environ.get("PATHIEL_MAX_MARKETS")) or [{"coin": "btc", "id": "p"}])
        monkeypatch.setenv("PATHIEL_MAX_MARKETS", "45")
        _call("scan", {"maxMarkets": 5})
        assert seen == ["5"] and os.environ["PATHIEL_MAX_MARKETS"] == "45"
        assert "BTC" in M._perception_cache  # keyed normalized

    def test_scan_rejects_bad_numbers(self):
        result, _ = _call("scan", {"minScore": "high"})
        assert result["isError"]

    def test_execute_finds_analysis_older_than_20(self, monkeypatch):
        """execute scanned only the newest 20 analyses."""
        from pathiel.agents import executor
        from pathiel.agents.memory import memory
        target = {"id": "old-one", "coin": "BTC", "verdict": "PASS"}
        memory.record_analysis(target)
        for i in range(30):
            memory.record_analysis({"id": f"n{i}", "coin": "ETH", "verdict": "PASS"})
        monkeypatch.setattr(executor, "route_verdict", lambda a: {"action": "skip"})
        _, body = _call("execute", {"analysisId": "old-one"})
        assert body["status"] == "skipped" and body["coin"] == "BTC"

    def test_research_unknown_coin_without_scan(self, monkeypatch):
        import pathiel.client.hl_client as hl
        monkeypatch.setattr(hl, "fetch_all_mids", lambda: {"BTC": "1"})
        M._perception_cache.pop("NOPE", None)
        result, body = _call("research", {"coin": "nope"})
        assert result["isError"] and "unknown coin NOPE" in body["error"]


class TestLeaderboardHandlers:
    def test_bad_time_frame_is_error_not_default(self, monkeypatch):
        from pathiel.agents import hyperfeed
        monkeypatch.setattr(hyperfeed, "_leaderboard_cache", ([], 1e18))
        result, body = _call("leaderboard_get_top_traders", {"time_frame": "YEARLY"})
        assert result["isError"] and "time_frame" in body["error"]


class TestRateLimitedInfo:
    def test_no_handler_uses_the_sdk_info_directly(self):
        """SDK Info posts outside the token bucket; a burst of those drew 429s."""
        src = SERVER.read_text()
        assert "_get_info(" not in src

    @pytest.mark.parametrize("method,args,typ,kw", [
        ("all_mids", (), "allMids", {}),
        ("l2_snapshot", ("BTC",), "l2Book", {"coin": "BTC"}),
        ("user_state", ("0x1",), "clearinghouseState", {"user": "0x1"}),
        ("query_referral_state", ("0x1",), "referral", {"user": "0x1"}),
        ("user_fills_by_time", ("0x1", 5), "userFillsByTime", {"user": "0x1", "startTime": 5}),
        ("user_funding_history", ("0x1", 5, 9), "userFunding",
         {"user": "0x1", "startTime": 5, "endTime": 9}),
        ("historical_orders", ("0x1",), "historicalOrders", {"user": "0x1"}),
    ])
    def test_facade_maps_to_info_types(self, info, method, args, typ, kw):
        info[typ] = {"ok": typ}
        assert getattr(M._limited_info(), method)(*args) == {"ok": typ}
        assert info["__calls__"][-1] == (typ, kw)


def test_audit_script_is_clean():
    """skills/pathiel-agent/scripts/audit_mcp_server.py is the wiring + schema
    gate agents run before editing this server. It must pass on HEAD."""
    import subprocess
    import sys
    r = subprocess.run([sys.executable, str(ROOT / "skills" / "pathiel-agent" / "scripts"
                                            / "audit_mcp_server.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert "RESULT: CLEAN" in r.stdout and "WARN" not in r.stdout


def test_wallet_account_counts_hip3_equity(monkeypatch):
    """A wallet whose margin sits on xyz: read as $0.0086 while `state` said $15.21."""
    seen = {}

    def fake(addr, include_hip3=False):
        seen["hip3"] = include_hip3
        return {"equity": 15.2, "dex_equity": {"": 0.01, "xyz": 15.19, "flx": 0.0},
                "asset_positions": []}
    monkeypatch.setattr(M, "fetch_account_state", fake)
    body = json.loads(M.handle_wallet_account({"address": "0x" + "c" * 40}))
    assert seen["hip3"] is True and body["equity"] == 15.2
    assert body["equity_by_dex"] == {"": 0.01, "xyz": 15.19}


def test_state_reports_equity_by_dex(monkeypatch):
    monkeypatch.setattr(M, "resolve_user_address", lambda: "0x" + "a" * 40)
    monkeypatch.setattr(M, "fetch_account_state", lambda u, include_hip3=False: {
        "equity": 15.2, "dex_equity": {"": 0.01, "xyz": 15.19, "km": 0.0}, "asset_positions": []})
    body = json.loads(M.handle_state({}))
    assert body["equity_by_dex"] == {"": 0.01, "xyz": 15.19}
