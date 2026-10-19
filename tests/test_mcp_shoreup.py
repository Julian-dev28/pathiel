"""Gate tests for the 2026-10-18 MCP shore-up (scripts/pathiel-mcp-server.py).

Pins, each against the defect it fixes:
  transport   a slow tools/call blocked every other call (single-threaded loop);
              a hung call never answered; cancel and progress were ignored;
              sampling replies were read off stdin by the handler itself.
  safety      no tool annotations and no server instructions: a client could
              not tell a live-money tool from a read.
  HIP-3       open orders, max trade size, risk metrics and user state read the
              MAIN dex only; this account's money and oil orders live on xyz.
  size        get_historical_orders returned 784 KB, get_spot_markets 150 KB.
  desks       desk_brief crashed on a desk with no thesis yet.
  new tools   account_overview, accounts, risk_limits, risk_size_trade.

Deterministic, no network: every venue read is monkeypatched.
"""
from __future__ import annotations

import importlib.util
import io
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contracts.accounts.models import Account, ActiveSummary
from contracts.risk.models import RiskProfile
from services.risk.derive import VenueRules

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "pathiel-mcp-server.py"


def _load():
    spec = importlib.util.spec_from_file_location("pathiel_mcp_server_shoreup", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()
ADDR = "0x" + "a1" * 20


def _req(method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _tool(msg_id, name, args=None, meta=None):
    params = {"name": name, "arguments": args or {}}
    if meta:
        params["_meta"] = meta
    return _req("tools/call", params, msg_id)


def _call(name, args=None):
    resp = M.dispatch_message(_tool(1, name, args))
    return resp["result"], json.loads(resp["result"]["content"][0]["text"])


def _run(monkeypatch, lines):
    """Drive the real run() loop over fake stdin/stdout; return the frames."""
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in lines))
    out = io.StringIO()
    monkeypatch.setattr(M.sys, "stdin", stdin)
    monkeypatch.setattr(M.sys, "stdout", out)
    monkeypatch.setattr(M.sys, "stderr", io.StringIO())
    try:
        M.run()
    finally:
        M._PROTO_OUT = None
    return [json.loads(x) for x in out.getvalue().splitlines()]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("PATHIEL_MCP_TOOL_TIMEOUT_S", raising=False)


@pytest.fixture
def info(monkeypatch):
    """Fake /info: type -> response, or callable(kw) -> response."""
    table, calls = {}, []

    def fake(typ, **kw):
        calls.append((typ, kw))
        v = table.get(typ)
        return v(kw) if callable(v) else v

    monkeypatch.setattr(M, "_info", fake)
    monkeypatch.setattr(M, "_user", lambda params=None: ADDR)
    monkeypatch.setattr(M, "resolve_user_address", lambda: ADDR)
    table["__calls__"] = calls
    return table


@pytest.fixture
def risk(monkeypatch):
    """Offline sizing inputs: starter profile (max 10x), coin max 20x, 3 size decimals."""
    monkeypatch.setattr(M, "_risk_inputs", lambda: (
        RiskProfile(preset="starter"), [], VenueRules(min_order_notional_usd=10.5)))
    monkeypatch.setattr(M, "_coin_specs", lambda coin: (20.0, 3))
    monkeypatch.setattr(M, "_live_equity", lambda: 15.21)


# ── transport ───────────────────────────────────────────────────────────────

class TestConcurrentTransport:
    def test_a_slow_call_does_not_block_a_fast_one(self, monkeypatch):
        fast_answered = threading.Event()
        real_send = M._send

        def send(msg):
            real_send(msg)
            if msg.get("id") == 2:
                fast_answered.set()

        def slow(p):
            assert fast_answered.wait(5), "the fast call never got through"
            return json.dumps({"who": "slow"})

        monkeypatch.setattr(M, "_send", send)
        monkeypatch.setitem(M._tool_handlers(), "state", slow)
        monkeypatch.setitem(M._tool_handlers(), "get_server_time",
                            lambda p: json.dumps({"who": "fast"}))
        out = _run(monkeypatch, [_tool(1, "state"), _tool(2, "get_server_time")])
        assert [m["id"] for m in out] == [2, 1]
        assert json.loads(out[1]["result"]["content"][0]["text"]) == {"who": "slow"}

    def test_a_call_past_its_deadline_is_answered_once_with_a_timeout(self, monkeypatch):
        monkeypatch.setattr(M, "DEFAULT_TOOL_TIMEOUT_S", 0.2)
        gate = threading.Event()
        sent = []
        real_send = M._send
        monkeypatch.setattr(M, "_send", lambda msg: (sent.append(msg), real_send(msg)))

        def stuck(p):
            gate.wait(5)
            return json.dumps({"late": True})

        monkeypatch.setitem(M._tool_handlers(), "state", stuck)
        out = _run(monkeypatch, [_tool(1, "state")])
        assert len(out) == 1
        body = json.loads(out[0]["result"]["content"][0]["text"])
        assert out[0]["result"]["isError"] is True and body["timed_out"] is True
        gate.set()                       # the stuck worker finishes late...
        time.sleep(0.2)
        assert [m.get("id") for m in sent] == [1]   # ...and never answers a second time

    def test_a_cancelled_call_gets_no_reply(self, monkeypatch):
        cancelled = threading.Event()
        real_cancel = M._cancel

        def cancel(request_id):
            real_cancel(request_id)
            cancelled.set()

        def slow(p):
            cancelled.wait(5)
            return json.dumps({"too": "late"})

        monkeypatch.setattr(M, "_cancel", cancel)
        monkeypatch.setitem(M._tool_handlers(), "state", slow)
        out = _run(monkeypatch, [
            _tool(1, "state"),
            {"jsonrpc": "2.0", "method": "notifications/cancelled",
             "params": {"requestId": 1, "reason": "user pressed esc"}},
        ])
        assert not [m for m in out if m.get("id") == 1]

    def test_progress_notifications_carry_the_clients_token(self, monkeypatch):
        def handler(p):
            M._report_progress("pulling news", total=2)
            M._report_progress("writing the thesis", total=2)
            return json.dumps({"ok": True})

        monkeypatch.setitem(M._tool_handlers(), "state", handler)
        out = _run(monkeypatch, [_tool(1, "state", meta={"progressToken": "tok-1"})])
        progress = [m for m in out if m.get("method") == "notifications/progress"]
        assert [p["params"]["progress"] for p in progress] == [1, 2]
        assert {p["params"]["progressToken"] for p in progress} == {"tok-1"}
        assert progress[1]["params"]["message"] == "writing the thesis"
        assert out[-1]["id"] == 1 and "result" in out[-1]

    def test_no_token_means_no_progress_frames(self, monkeypatch):
        monkeypatch.setitem(M._tool_handlers(), "state",
                            lambda p: (M._report_progress("x"), json.dumps({"ok": 1}))[1])
        out = _run(monkeypatch, [_tool(1, "state")])
        assert [m.get("method") for m in out] == [None]

    def test_sampling_reply_is_routed_and_other_requests_keep_flowing(self, monkeypatch):
        monkeypatch.setattr(M, "_server_request_seq", 0)
        monkeypatch.setitem(M._tool_handlers(), "state", lambda p: json.dumps(
            M._server_request("sampling/createMessage", {"x": 1}, timeout_s=5)))
        out = _run(monkeypatch, [
            _tool(1, "state"),
            _req("ping", msg_id=7),
            {"jsonrpc": "2.0", "id": "srv-1", "result": {"ok": 1}},
        ])
        by_id = {m.get("id"): m for m in out}
        assert by_id[7] == {"jsonrpc": "2.0", "id": 7, "result": {}}   # not "server busy"
        assert by_id["srv-1"]["method"] == "sampling/createMessage"
        assert json.loads(by_id[1]["result"]["content"][0]["text"]) == {"ok": 1}

    def test_unknown_tool_is_a_protocol_error_over_run(self, monkeypatch):
        out = _run(monkeypatch, [_tool(1, "nope")])
        assert out[0]["error"]["code"] == -32602


class TestOutputGuard:
    def test_an_oversized_result_is_cut_with_a_hint(self, monkeypatch):
        monkeypatch.setitem(M._tool_handlers(), "state",
                            lambda p: json.dumps({"blob": "x" * 100_000}))
        result, body = _call("state")
        assert result["isError"] is False
        assert body["truncated"] is True and body["original_chars"] > 100_000
        assert len(result["content"][0]["text"]) <= M.MAX_OUTPUT_CHARS


# ── safety surface ───────────────────────────────────────────────────────────

class TestAnnotationsAndInstructions:
    def test_every_tool_is_annotated(self):
        assert all("annotations" in t for t in M.TOOLS)

    def test_live_money_tools_are_destructive_and_say_so(self):
        tools = {t["name"]: t for t in M.TOOLS}
        for name in M.LIVE_MONEY_TOOLS:
            assert tools[name]["annotations"].get("destructiveHint") is True
            assert not tools[name]["annotations"].get("readOnlyHint")
            assert tools[name]["description"].startswith("LIVE MONEY")
        assert tools["config"]["annotations"].get("destructiveHint") is True

    def test_reads_are_read_only_and_writes_are_not(self):
        special = M.LIVE_MONEY_TOOLS | M.LIVE_CONFIG_TOOLS | M.LOCAL_WRITE_TOOLS
        for t in M.TOOLS:
            ro = t["annotations"].get("readOnlyHint") is True
            assert ro == (t["name"] not in special), t["name"]

    def test_the_buckets_name_real_tools(self):
        names = {t["name"] for t in M.TOOLS}
        assert (M.LIVE_MONEY_TOOLS | M.LIVE_CONFIG_TOOLS | M.LOCAL_WRITE_TOOLS) <= names

    def test_initialize_carries_short_instructions(self):
        resp = M.dispatch_message(_req("initialize", {"protocolVersion": "2025-06-18",
                                                      "capabilities": {}}))
        text = resp["result"]["instructions"]
        for must in ("LIVE MONEY", "account_overview", "risk_size_trade", "desk_brief", "xyz:CL"):
            assert must in text
        assert len(text) < 2500, "instructions ride in every session's context"


# ── HIP-3 correctness ────────────────────────────────────────────────────────

class TestHip3Reads:
    def test_open_orders_cover_every_dex_with_margin(self, info, monkeypatch):
        monkeypatch.setattr(M, "fetch_account_state", lambda u, include_hip3=False: {
            "dex_equity": {"": 0.0086, "xyz": 15.1783, "km": 0.004}})
        info["frontendOpenOrders"] = lambda kw: (
            [{"coin": "xyz:CL", "oid": 2}] if kw.get("dex") == "xyz" else [{"coin": "BTC", "oid": 1}])
        _, body = _call("get_open_orders")
        assert body["dexes"] == ["main", "xyz"]               # km holds dust: skipped
        assert sorted(o["coin"] for o in body["orders"]) == ["BTC", "xyz:CL"]
        asked = [kw.get("dex", "") for typ, kw in info["__calls__"] if typ == "frontendOpenOrders"]
        assert asked == ["", "xyz"]

    def test_open_orders_for_one_coin_ask_only_its_dex(self, info, monkeypatch):
        monkeypatch.setattr(M, "fetch_account_state",
                            lambda u, include_hip3=False: pytest.fail("no account read needed"))
        info["frontendOpenOrders"] = lambda kw: [{"coin": "xyz:CL", "oid": 2}]
        _, body = _call("get_open_orders", {"coin": "xyz:cl"})
        assert body["dexes"] == ["xyz"] and body["count"] == 1

    def test_max_trade_size_uses_the_coins_own_dex_and_real_caps(self, info, risk, monkeypatch):
        monkeypatch.setattr(M, "fetch_account_state", lambda u, include_hip3=False: {
            "equity": 15.21, "available": 0.0,
            "dex_equity": {"": 0.0086, "xyz": 15.18}, "dex_available": {"": 0.0, "xyz": 15.0}})
        info["l2Book"] = {"levels": [[{"px": "98", "sz": "100"}], [{"px": "99", "sz": "100"}]]}
        _, body = _call("get_max_trade_size", {"coin": "xyz:CL"})
        assert body["dex"] == "xyz" and body["margin_available"] == 15.0
        assert body["leverage_ceiling"] == 10.0              # starter 10x < coin 20x
        assert body["max_notional_by_margin"] == round(15.0 * 0.9 * 10.0, 2)

    def test_user_state_reads_the_dex_it_is_asked_for(self, info):
        info["clearinghouseState"] = lambda kw: {"marginSummary": {
            "accountValue": "15.18" if kw.get("dex") == "xyz" else "0.0086"}}
        _, body = _call("get_user_state", {"dex": "xyz"})
        assert body["dex"] == "xyz" and body["marginSummary"]["accountValue"] == "15.18"
        _, body = _call("get_user_state")
        assert body["dex"] == "main"

    def test_price_comes_from_the_coins_dex_and_unknown_is_an_error(self, info):
        info["allMids"] = lambda kw: {"xyz:CL": "98.85"} if kw.get("dex") == "xyz" else {"BTC": "1"}
        _, body = _call("get_price", {"coin": "xyz:CL"})
        assert body["price"] == 98.85
        result, body = _call("get_price", {"coin": "CL"})
        assert result["isError"] and "dex prefix" in body["error"]


# ── output narrowing ─────────────────────────────────────────────────────────

class TestNarrowing:
    def test_historical_orders_are_newest_first_and_limited(self, monkeypatch):
        rows = [{"order": {"coin": "xyz:CL" if i % 2 else "BTC"}, "status": "filled",
                 "statusTimestamp": i} for i in range(300)]

        class FakeInfo:
            def historical_orders(self, user):
                return list(rows)

        monkeypatch.setattr(M, "_limited_info", lambda: FakeInfo())
        monkeypatch.setattr(M, "_user", lambda params=None: ADDR)
        _, body = _call("get_historical_orders")
        assert body["count"] == 300 and len(body["orders"]) == 50
        assert body["orders"][0]["statusTimestamp"] == 299
        _, body = _call("get_historical_orders", {"coin": "xyz:cl", "limit": 5})
        assert body["count"] == 150 and len(body["orders"]) == 5
        assert {o["order"]["coin"] for o in body["orders"]} == {"xyz:CL"}

    def test_all_mids_drop_prediction_markets_unless_asked(self, monkeypatch):
        monkeypatch.setattr(M, "market_get_mids", lambda: {"#114220": "0.5", "BTC": "1", "xyz:CL": "98"})
        _, body = _call("get_all_mids")
        assert set(body["mids"]) == {"BTC", "xyz:CL"}
        _, body = _call("get_all_mids", {"include_outcomes": True})
        assert "#114220" in body["mids"]
        _, body = _call("get_all_mids", {"coins": ["xyz:cl"]})
        assert body["mids"] == {"xyz:CL": "98"}

    def test_portfolio_status_keeps_the_last_point_by_default(self, info):
        info["portfolio"] = [["day", {"accountValueHistory": [[1, "10"], [2, "15.21"]],
                                      "pnlHistory": [], "vlm": "0"}]]
        _, body = _call("get_portfolio_status")
        assert body[0][1]["accountValueHistory"] == {"last": [2, "15.21"], "points": 2}
        _, body = _call("get_portfolio_status", {"full": True})
        assert len(body[0][1]["accountValueHistory"]) == 2


# ── desks ────────────────────────────────────────────────────────────────────

class _Facade:
    def __init__(self, thesis=None):
        from contracts.strategies.models import DeskAnalytics, DeskSpec, Thesis
        fx = ROOT / "contracts" / "strategies" / "fixtures"
        self.spec = DeskSpec.model_validate_json((fx / "oil_spec.json").read_text())
        self.analytics = DeskAnalytics.model_validate_json((fx / "oil_analytics.json").read_text())
        self.thesis = Thesis.model_validate_json((fx / "oil_thesis.json").read_text()) if thesis else None
        self.kwargs = None
        self.stages = []

    def get_brief(self, desk_id, *, max_age_s=None, wait=False):
        from contracts.strategies.models import DeskBrief
        self.kwargs = {"max_age_s": max_age_s, "wait": wait}
        return DeskBrief(spec=self.spec, analytics=self.analytics, thesis=self.thesis,
                         history=[self.thesis] if self.thesis else [])

    def refresh(self, desk_id, force=False, on_stage=None):
        from contracts.strategies.models import RefreshJob
        for label in ("reading market data", "writing the thesis (about a minute)"):
            on_stage(label)
        return RefreshJob(id="j", desk_id=desk_id, state="done",
                          started_at=datetime.now(timezone.utc))


class TestDeskTools:
    def test_brief_of_a_desk_with_no_thesis_answers_instead_of_crashing(self, monkeypatch, risk):
        """Live, 2026-10-18: 'render failed: AttributeError: NoneType object has
        no attribute call' for every desk that had never been refreshed."""
        monkeypatch.setattr(M, "_strategies_facade", lambda: _Facade(thesis=None))
        result, body = _call("desk_brief", {"desk": "oil"})
        assert result["isError"] is False
        assert "no thesis yet" in body["markdown"] and body["your_size"] is None

    def test_brief_waits_for_fresh_enough_analytics(self, monkeypatch, risk):
        facade = _Facade(thesis=True)
        monkeypatch.setattr(M, "_strategies_facade", lambda: facade)
        _call("desk_brief", {"desk": "oil"})
        assert facade.kwargs == {"max_age_s": M.DESK_BRIEF_MAX_AGE_S, "wait": True}

    def test_brief_sizes_the_call_for_the_account(self, monkeypatch, risk):
        facade = _Facade(thesis=True)
        monkeypatch.setattr(M, "_strategies_facade", lambda: facade)
        _, body = _call("desk_brief", {"desk": "oil"})
        size = body["your_size"]
        assert size["can_trade"] is True and size["coin"] == facade.thesis.call.instrument
        assert "Your size: " in body["markdown"]
        assert size["notional_usd"] >= 10.5                  # never under the venue minimum

    def test_refresh_reports_each_stage_as_progress(self, monkeypatch):
        monkeypatch.setattr(M, "_strategies_facade", lambda: _Facade(thesis=True))
        out = _run(monkeypatch, [_tool(1, "desk_refresh", {"desk": "oil"},
                                       meta={"progressToken": 9})])
        msgs = [m["params"]["message"] for m in out if m.get("method") == "notifications/progress"]
        assert msgs == ["reading market data", "writing the thesis (about a minute)"]


# ── account + risk tools ─────────────────────────────────────────────────────

def _account(**kw):
    base = dict(id="a1", label="Main", main_address=ADDR, mode="trade", source="wallet_sign",
                is_active=True, created_at=datetime.now(timezone.utc),
                verified_at=datetime.now(timezone.utc))
    return Account(**{**base, **kw})


class TestAccountTools:
    def test_overview_shows_where_the_money_is(self, monkeypatch):
        import services.accounts.summary as summary
        state = {"dex_equity": {"": 0.0086, "xyz": 15.1783, "km": 0.004}, "spot_usdc": 0.0,
                 "asset_positions": [{"position": {"coin": "xyz:CL", "szi": "0.12", "entryPx": "98.9",
                                                   "positionValue": "11.87", "unrealizedPnl": "0.1",
                                                   "liquidationPx": "81.1",
                                                   "leverage": {"type": "isolated", "value": 3}}}]}
        monkeypatch.setattr(summary, "get_active_snapshot", lambda **kw: (ActiveSummary(
            account=_account(), equity_usd=15.21, withdrawable_usd=15.0, open_positions=1,
            can_trade=True), state))
        _, body = _call("account_overview")
        assert body["equity_usd"] == 15.21 and body["can_trade"] is True
        assert body["equity_by_dex"] == {"xyz": 15.1783}     # main and km are dust
        assert body["dust_usd"] == round(0.0086 + 0.004, 4)
        assert body["positions"] == [{"coin": "xyz:CL", "side": "long", "size": 0.12,
                                      "entry_px": 98.9, "value_usd": 11.87, "upnl_usd": 0.1,
                                      "liq_px": 81.1, "leverage": 3}]
        assert body["account"]["address"] == ADDR

    def test_overview_without_an_account_says_how_to_connect(self, monkeypatch):
        import services.accounts.summary as summary
        monkeypatch.setattr(summary, "get_active_snapshot", lambda **kw: (ActiveSummary(
            account=None, can_trade=False, blockers=["No account connected yet."]), None))
        _, body = _call("account_overview")
        assert body["account"] is None and body["blockers"]

    def test_accounts_lists_the_registry_and_the_trading_address(self, monkeypatch):
        import services.accounts.view as view
        monkeypatch.setattr(view, "list_all_accounts", lambda: [
            _account(), _account(id="a2", label="Whale", mode="watch", source="watch",
                                 is_active=False, verified_at=None,
                                 main_address="0x" + "c3" * 20)])
        monkeypatch.setattr(M, "resolve_user_address", lambda: ADDR)
        _, body = _call("accounts")
        assert body["count"] == 2 and body["bot_trades_with"] == ADDR
        assert [a["mode"] for a in body["accounts"]] == ["trade", "watch"]


class TestRiskTools:
    def test_risk_limits_for_the_live_account(self, risk):
        _, body = _call("risk_limits")
        assert body["equity_usd"] == 15.21 and body["preset"] == "starter"
        assert body["what_if"] is False and body["headline"]

    def test_risk_limits_what_if(self, risk):
        _, body = _call("risk_limits", {"equity_usd": 20_000, "preset": "standard"})
        assert body["what_if"] is True and body["preset"] == "standard"
        assert body["risk_per_trade_usd"] == 300.0

    def test_size_trade_reports_liquidation_and_payoffs(self, risk):
        _, body = _call("risk_size_trade", {"coin": "xyz:cl", "entry_px": 98.87, "stop_px": 88.22,
                                            "leverage": 5, "targets": [101.19, 105.55]})
        assert body["coin"] == "xyz:CL" and body["can_trade"] is True
        assert body["leverage"] == 5.0 and body["stop_before_liq"] is True
        assert body["liq_px"] < 88.22
        assert [t["px"] for t in body["targets"]] == [101.19, 105.55]

    def test_size_trade_warns_when_liquidation_comes_first(self, risk):
        _, body = _call("risk_size_trade", {"coin": "xyz:CL", "entry_px": 98.87,
                                            "stop_px": 88.22, "leverage": 20})
        assert body["leverage"] == 10.0                      # starter caps 20x at 10x
        assert body["stop_before_liq"] is False and "Liquidation" in body["warning"]

    @pytest.mark.parametrize("args", [{}, {"coin": "BTC"}, {"coin": "BTC", "entry_px": "x",
                                                            "stop_px": 1}])
    def test_size_trade_rejects_missing_inputs(self, risk, args):
        result, body = _call("risk_size_trade", args)
        assert result["isError"] and body["error"]


# ── docs ─────────────────────────────────────────────────────────────────────

def test_the_tool_table_doc_is_generated_from_the_server():
    """docs/overhaul/MCP.md is rendered from TOOLS/ALIASES; a new tool with no
    section, or a hand edit, fails here. Fix: .venv/bin/python scripts/mcp_tool_table.py"""
    spec = importlib.util.spec_from_file_location("mcp_tool_table", ROOT / "scripts" / "mcp_tool_table.py")
    table = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(table)
    assert (ROOT / "docs" / "overhaul" / "MCP.md").read_text() == table.render(M)


def test_full_brief_is_compacted_to_fit_a_model_context(monkeypatch, risk):
    """Live oil, 2026-10-18: the full brief was 99 KB (intel alone 70 KB) and
    hit the output guard, so mode=full returned nothing useful."""
    from contracts.strategies.models import Headline, IntelPacket, MarketOdds, PolicyItem
    facade = _Facade(thesis=True)
    now = datetime.now(timezone.utc)
    big = IntelPacket(
        desk_id="oil", as_of=now,
        headlines=[Headline(id=f"h{i}", title=f"headline {i} " + "x" * 200, source="wire",
                            url="https://example.com/" + "y" * 300, published_at=now,
                            relevance=1 - i / 100) for i in range(40)],
        policy=[PolicyItem(id=f"p{i}", title=f"bill {i}", url="https://example.com/p",
                           body="US Congress", summary="z" * 2000, status="proposed")
                for i in range(25)],
        odds=[MarketOdds(id=f"o{i}", venue="polymarket", question=f"q{i}", probability=0.5,
                         volume_usd=float(i), url="https://example.com/o") for i in range(79)],
    )
    real_get = facade.get_brief

    def get_brief(desk_id, **kw):
        return real_get(desk_id, **kw).model_copy(update={"intel": big})

    facade.get_brief = get_brief
    monkeypatch.setattr(M, "_strategies_facade", lambda: facade)
    result, body = _call("desk_brief", {"desk": "oil", "mode": "full"})
    assert "truncated" not in body
    intel = body["brief"]["intel"]
    assert len(intel["headlines"]) == 12 and intel["headlines_total"] == 40
    assert intel["headlines"][0]["id"] == "h0"                       # most relevant kept
    assert [o["id"] for o in intel["odds"][:2]] == ["o78", "o77"]   # biggest markets kept
    assert all(len(p["summary"]) <= M._POLICY_SUMMARY_CHARS + 3 for p in intel["policy"])
    assert set(body["brief"]["history"][0]) == {"id", "author", "created_at", "side",
                                                 "one_liner", "grade"}
    assert len(result["content"][0]["text"]) < M.MAX_OUTPUT_CHARS


class TestPayoffWarning:
    def test_targets_smaller_than_the_stop_are_flagged(self, risk):
        _, body = _call("risk_size_trade", {"coin": "xyz:CL", "entry_px": 98.87, "stop_px": 88.22,
                                            "leverage": 5, "targets": [101.19, 105.55]})
        assert any("0.63R" in w for w in body["warnings"])

    def test_a_target_worth_more_than_the_stop_is_not_flagged(self, risk):
        _, body = _call("risk_size_trade", {"coin": "xyz:CL", "entry_px": 98.87, "stop_px": 94.30,
                                            "leverage": 5, "targets": [105.55]})
        assert body["warnings"] == []

    def test_no_targets_no_warning(self, risk):
        _, body = _call("risk_size_trade", {"coin": "xyz:CL", "entry_px": 98.87, "stop_px": 88.22})
        assert body["warnings"] == []
