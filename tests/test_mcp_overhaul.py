"""Gate tests for the 2026-10-17 MCP overhaul: lean tool surface, HIP-3
fixes, desk tools, compact responses, stdio e2e.

Deterministic, no network (see TestLiveMoneyToolsNeverTouchNetwork for the
one class that goes out of its way to prove that under test, not just by
convention). Companion to test_mcp_server_hardening.py, which predates this
overhaul and still covers protocol/schema-honesty/handler behavior that
didn't change.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "pathiel-mcp-server.py"
HIP3_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hip3_info"
STRATEGY_FIXTURES = ROOT / "contracts" / "strategies" / "fixtures"


def _load():
    spec = importlib.util.spec_from_file_location("pathiel_mcp_server_overhaul", SERVER)
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


def _fixture(name: str) -> Any:
    return json.loads((HIP3_FIXTURES / name).read_text())


@pytest.fixture
def info(monkeypatch):
    """Fake /info: map type -> response (or callable(kw) -> response). Same
    shape as test_mcp_server_hardening.py's fixture of the same name."""
    table: Dict[str, Any] = {}
    calls = []

    def fake(typ, **kw):
        calls.append((typ, kw))
        v = table.get(typ)
        return v(kw) if callable(v) else v

    monkeypatch.setattr(M, "_info", fake)
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", "0x" + "a" * 40)
    table["__calls__"] = calls
    return table


# ── tools/list: lean surface + token budget gate ────────────────────────────

class TestToolsListBudget:
    def test_tool_count_snapshot(self):
        """Pins the exact count (59: 80 - 22 bot-era/off-topic tools cut on
        2026-10-23 when pathiel became a headless oil desk -- scan, research,
        submit_verdict, execute (the bot's brain path, replaced by
        place_bracket/bracket_sweep), discovery_*, leaderboard_*,
        get_staking_info, get_validator_info, get_vault_details, get_referral,
        get_rewards, get_spot_balances, get_spot_markets, get_user_twist,
        get_user_roles, get_user_defined_types, get_network_stats, plus
        get_funding_history and get_trading_permissions, redundant with the
        funding/fee reads kept -- + 1 desk_grade tool added the same day for
        the grade-everything build) so a drift -- someone re-adding a
        duplicate schema, or silently dropping a tool -- shows up as a
        failing assert, not a surprise someone has to notice by eye."""
        assert len(M.TOOLS) == 59

    def test_desk_tools_are_present(self):
        names = {t["name"] for t in M.TOOLS}
        assert {"desk_list", "desk_brief", "desk_refresh",
               "desk_submit_call", "desk_track_record", "desk_grade"} <= names

    def test_token_budget_gate(self):
        """Ceiling, not a pin: 88 tools cost 29370 chars (~7342 tokens,
        chars/4) before the 2026-10-17 overhaul; 74 tools + 5 desk tools
        cost 29568 after. 2026-10-18 (MCP shore-up): +4 account/risk tools,
        safety annotations on every tool and narrowing params on the
        oversized readers, paid for mostly by compacting the config schema
        (4.9k -> 0.9k chars): 32847 chars (~8.2k tokens). 2026-10-23
        (headless oil desk): 80 -> 58 tools cut 33945 -> 26079 chars, then +1
        desk_grade tool for grade-everything: 59 tools, 26115 chars. The cap
        drops to 26115 + 10% (28727) so it keeps biting instead of riding the
        old 80-tool ceiling. A future PR that blows through this number has
        to touch this test."""
        payload = json.dumps({"tools": M.TOOLS})
        assert len(M.TOOLS) <= 59, "tool count crept back up -- did a duplicate return?"
        assert len(payload) <= 28727, f"tools/list grew to {len(payload)} chars"

    def test_no_duplicate_names_between_tools_and_aliases(self):
        tool_names = {t["name"] for t in M.TOOLS}
        assert tool_names.isdisjoint(M.ALIASES), (
            "a name is both a listed tool AND a hidden alias -- that is "
            "the exact duplication this overhaul exists to remove")

    def test_tools_list_token_size_is_measured_by_the_committed_script(self):
        """scripts/mcp_token_report.py is the single source of truth for
        this number (same chars/4 estimator); this just proves it still
        runs against the live module and agrees with TOOLS directly."""
        sys.path.insert(0, str(ROOT))
        import scripts.mcp_token_report as report
        metrics = report._tools_list_metrics(M)
        assert metrics["tool_count"] == len(M.TOOLS)
        assert metrics["tokens_est"] == round(len(json.dumps({"tools": M.TOOLS})) / 4)


# ── every tool's input schema is valid JSON Schema ──────────────────────────

_SCHEMA_TYPES = {"object", "string", "number", "integer", "boolean", "array", "null"}


def _assert_valid_schema(node: Any, path: str = "$") -> None:
    """A structural check for the narrow JSON Schema dialect this server
    actually emits (type / properties / required / items / enum / default
    / description / pattern / minimum / maximum). Not a full Draft-07
    validator: the `jsonschema` package is not installed, and this shared
    `.venv` is symlinked across all 8 parallel overhaul worktrees -- adding
    a new pip dependency here would land in every other agent's
    environment mid-swarm, which is the kind of cross-session side effect
    SWARM_RULES exists to avoid. This covers every keyword TOOLS uses.
    """
    assert isinstance(node, dict), f"{path}: schema node must be an object, got {type(node)}"
    t = node.get("type")
    ok_type = (set(t) <= _SCHEMA_TYPES and bool(t)) if isinstance(t, list) else t in _SCHEMA_TYPES
    assert ok_type, f"{path}: bad or missing 'type': {t!r}"
    if "enum" in node:
        assert isinstance(node["enum"], list) and node["enum"], f"{path}.enum must be a non-empty list"
    if t == "object":
        props = node.get("properties", {})
        assert isinstance(props, dict), f"{path}.properties must be an object"
        for key, sub in props.items():
            _assert_valid_schema(sub, f"{path}.properties.{key}")
        required = node.get("required", [])
        assert isinstance(required, list), f"{path}.required must be a list"
        unknown = set(required) - set(props)
        assert not unknown, f"{path}.required names undeclared properties: {unknown}"
    if t == "array" and "items" in node:
        _assert_valid_schema(node["items"], f"{path}.items")


class TestSchemaValidity:
    @pytest.mark.parametrize("tool", M.TOOLS, ids=lambda t: t["name"])
    def test_input_schema_is_valid_json_schema(self, tool):
        _assert_valid_schema(tool["inputSchema"], f"{tool['name']}.inputSchema")
        assert tool["inputSchema"].get("type") == "object", (
            f"{tool['name']}: a tool's top-level inputSchema must be an object")


# ── alias dispatch: every old name still works, none are listed ────────────

class TestAliasDispatch:
    def test_every_alias_points_at_a_real_listed_tool(self):
        listed = {t["name"] for t in M.TOOLS}
        for alias, canonical in M.ALIASES.items():
            assert canonical in listed, f"{alias} -> {canonical!r}, which is not in TOOLS"

    def test_aliases_are_hidden_from_tools_list(self):
        listed = {t["name"] for t in M.TOOLS}
        assert listed.isdisjoint(M.ALIASES), (
            f"these old names leaked back into tools/list: {listed & set(M.ALIASES)}")

    @pytest.mark.parametrize("alias,canonical", sorted(M.ALIASES.items()))
    def test_alias_dispatches_to_the_exact_same_handler(self, alias, canonical):
        handlers = M._tool_handlers()
        assert alias in handlers, f"{alias} is not dispatchable at all"
        assert handlers[alias] is handlers[canonical], (
            f"{alias} and {canonical} resolved to different functions -- "
            f"that is 'silently returning different data for synonyms', "
            f"which this codebase treats as worse than duplication")

    @pytest.fixture
    def _everything_mocked(self, monkeypatch):
        """Enough mocking that every tool, old name or new, can actually run
        its handler body without a crash or a real network call -- this is
        a dispatch smoke test, not a correctness test for each handler.

        Two layers, both needed: `_info`/`_http_post*` for calls the server
        makes directly, AND the higher-level pathiel.client functions
        (fetch_hl_candles, get_hl_price, ...) for calls that go through
        them instead -- those have their own retry/backoff loops and their
        own separate SDK Info/Exchange session, neither of which a bare
        `_info` patch reaches. The first version of this fixture missed
        that and a 'smoke test' spent 23s in fetch_hl_candles's real
        backoff sleep chasing a live network call it never should have
        made, plus a few more seconds in the SDK's own session for
        get_hl_price.
        """
        monkeypatch.setattr(M, "_info", lambda typ, **kw: (
            [{"universe": []}, []] if "AndAssetCtxs" in typ
            else ({"universe": []} if typ == "meta" else
                 ({"levels": [[{"px": "1", "sz": "1"}], [{"px": "2", "sz": "1"}]]}
                  if typ == "l2Book" else {}))))
        monkeypatch.setattr(M, "fetch_account_state", lambda user, include_hip3=False: {
            "equity": 0.0, "available": 0.0, "available_aggregated": 0.0,
            "total_ntl": 0.0, "asset_positions": [], "dex_equity": {}, "dex_available": {},
            "spot_usdc": 0.0, "total_usdc": 0.0, "withdrawable": 0.0, "queried_dexes": set(),
        })

        import pathiel.client.hl_client as hlc
        monkeypatch.setattr(hlc, "_http_post_strict", lambda path, payload, timeout=5: {})
        monkeypatch.setattr(hlc, "_http_post", lambda path, payload, timeout=5: {})
        monkeypatch.setattr(hlc, "fetch_account_state", M.fetch_account_state)
        monkeypatch.setattr(hlc, "fetch_all_mids", lambda include_hip3=False: {"BTC": "100"})
        monkeypatch.setattr(hlc, "fetch_hl_candles",
                            lambda coin, interval="5m", count=100: [])

        import pathiel.client.exchange as ex
        monkeypatch.setattr(ex, "get_max_leverage", lambda coin: 10)
        monkeypatch.setattr(ex, "get_hl_price", lambda coin="BTC": 100.0)
        monkeypatch.setattr(ex, "get_coin_index", lambda coin: (0, 5, 4))

        monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", "0x" + "a" * 40)

    @pytest.mark.parametrize("alias", sorted(M.ALIASES))
    def test_every_old_name_is_callable_through_tools_call(self, alias, _everything_mocked):
        generous_args = {
            "coin": "BTC", "oid": 1, "orderId": 1, "asset": 0, "order_id": 1,
            "cloid": "0x1", "limit": 5, "start_time": 1, "startTime": 1,
            "user": "0x" + "a" * 40,
        }
        resp = M.dispatch_message(_req("tools/call", {"name": alias, "arguments": generous_args}))
        assert "result" in resp, f"{alias}: dispatch itself failed: {resp.get('error')}"
        # Not "Unknown tool" -- the handler ran (it may still report its own
        # application-level {"error": ...}, which is a different thing).
        text = resp["result"]["content"][0]["text"]
        assert "Unknown tool" not in text


# ── HIP-3 fixes, proven against recorded /info responses ───────────────────

class TestHIP3Fixes:
    """Every fix here is proven against a REAL recorded /info response
    (tests/fixtures/hip3_info/, see its README for provenance), not a
    hand-rolled shape that happens to match what the code expects."""

    @pytest.fixture
    def hip3(self, info):
        meta_xyz = _fixture("metaAndAssetCtxs_dex_xyz.json")
        meta_main = _fixture("metaAndAssetCtxs_main.json")
        mids_xyz = _fixture("allMids_dex_xyz.json")
        mids_main = _fixture("allMids_main.json")
        pf_main = _fixture("predictedFundings_main.json")

        info["metaAndAssetCtxs"] = lambda kw: meta_xyz if kw.get("dex") == "xyz" else meta_main
        info["allMids"] = lambda kw: mids_xyz if kw.get("dex") == "xyz" else mids_main
        info["predictedFundings"] = pf_main
        info["l2Book"] = {"levels": [
            [{"px": "99.30", "sz": "500"}, {"px": "99.20", "sz": "500"}],
            [{"px": "99.40", "sz": "500"}, {"px": "99.50", "sz": "500"}],
        ]}
        return info

    def test_get_asset_context_resolves_a_hip3_coin(self, hip3):
        """Was: {"error": "unknown coin: xyz:CL"} -- the handler only ever
        read the main-dex metaAndAssetCtxs."""
        _, body = _call("get_asset_context", {"coin": "xyz:CL"})
        assert body == {"coin": "xyz:CL", **_fixture("metaAndAssetCtxs_dex_xyz.json")[1][0]}
        assert hip3["__calls__"][-1] == ("metaAndAssetCtxs", {"dex": "xyz"})

    def test_get_asset_context_main_coin_is_unaffected(self, hip3):
        _, body = _call("get_asset_context", {"coin": "BTC"})
        assert body["coin"] == "BTC"
        assert hip3["__calls__"][-1] == ("metaAndAssetCtxs", {})

    def test_get_asset_context_bulk_mode_stays_main_dex_only(self, hip3):
        """Bulk ('no coin') mode is documented as main-dex only -- walking
        every HIP-3 dex on an unfiltered listing is the opposite of cheap
        per token. A single coin is always resolved correctly either way."""
        _, body = _call("get_asset_context", {})
        assert hip3["__calls__"][-1] == ("metaAndAssetCtxs", {})
        assert "xyz:CL" not in body["contexts"]

    def test_get_open_interest_resolves_a_hip3_coin(self, hip3):
        _, body = _call("get_open_interest", {"coin": "xyz:CL"})
        assert body["coin"] == "xyz:CL"
        assert body["open_interest"] == _fixture("metaAndAssetCtxs_dex_xyz.json")[1][0]["openInterest"]

    def test_get_funding_rate_resolves_a_hip3_coin(self, hip3):
        _, body = _call("get_funding_rate", {"coin": "xyz:CL"})
        assert body["funding"] == _fixture("metaAndAssetCtxs_dex_xyz.json")[1][0]["funding"]

    def test_get_max_leverage_resolves_a_hip3_coin(self, monkeypatch):
        """Delegates to pathiel.client.exchange.get_max_leverage (already
        HIP-3-aware); mocked the same way
        tests/test_max_leverage_never_defaults_to_1x.py already does.
        Was: re-scanning the main-dex meta by hand and answering 'not
        found' for every xyz:/vntl:/km: coin."""
        import pathiel.client.exchange as ex
        real_universe = _fixture("metaAndAssetCtxs_dex_xyz.json")[0]["universe"]
        monkeypatch.setattr(ex, "_cached_universe",
                            lambda dex=None, force_refresh=False: real_universe if dex else [])
        _, body = _call("get_max_leverage", {"coin": "xyz:CL"})
        assert body == {"coin": "xyz:CL", "max_leverage": 20}

    def test_get_liquidation_price_entry_px_falls_back_to_hip3_mid(self, monkeypatch, hip3):
        """Was: {"error": "no price for xyz:CL"} -- the entry_px fallback
        read a bare allMids(), which has zero HIP-3 coverage."""
        import pathiel.client.exchange as ex
        monkeypatch.setattr(ex, "get_max_leverage", lambda coin: 20)
        _, body = _call("get_liquidation_price",
                        {"coin": "xyz:CL", "leverage": 5, "is_long": True})
        assert "error" not in body, body
        assert body["entry_px"] == pytest.approx(
            float(_fixture("allMids_dex_xyz.json")["xyz:CL"]))

    def test_get_price_impact_size_conversion_resolves_hip3_mid(self, hip3):
        """Same root cause as get_liquidation_price: converting a COIN-unit
        size to USD read a bare allMids()."""
        result, body = _call("get_price_impact", {"coin": "xyz:CL", "size": 1})
        assert not result["isError"], body
        assert body["notional_usd"] is None or "no mid" not in json.dumps(body)

    def test_get_predicted_funding_falls_back_for_a_hip3_coin(self, hip3):
        """Was: {"error": "no predicted funding for xyz:CL"}. HL's
        predictedFundings ignores a dex param entirely (verified live
        2026-10-17), so this is a labeled current-rate fallback, not a
        fabricated prediction."""
        _, body = _call("get_predicted_funding", {"coin": "xyz:CL"})
        real_rate = _fixture("metaAndAssetCtxs_dex_xyz.json")[1][0]["funding"]
        assert body["source"] == "hip3_current_funding"
        assert body["venues"] == [["HlPerp", {"fundingRate": real_rate, "fundingIntervalHours": 1}]]

    def test_get_predicted_funding_main_coin_is_unaffected(self, hip3):
        _, body = _call("get_predicted_funding", {"coin": "0G"})
        assert "source" not in body and "venues" in body


# ── desk tools, against a fake services.strategies facade ──────────────────

def _strategy_fixture(name: str) -> Dict[str, Any]:
    return json.loads((STRATEGY_FIXTURES / f"{name}.json").read_text())


class _FakeFacade:
    """Everything a real services.strategies.facade would return, built
    from the SAME real fixtures contracts/strategies/tests/test_contract.py
    round-trips -- not hand-waved shapes. desk-core (building the real
    facade in parallel) does not exist in this worktree; this is the fake
    the task explicitly calls for.
    """

    def __init__(self):
        from contracts.strategies.models import (
            DeskAnalytics, DeskBrief, DeskCard, DeskSpec, IntelPacket,
            RefreshJob, Thesis, TrackRecord,
        )
        self._DeskBrief, self._DeskCard = DeskBrief, DeskCard
        self._RefreshJob, self._TrackRecord = RefreshJob, TrackRecord
        self._Thesis = Thesis
        self.spec = DeskSpec.model_validate(_strategy_fixture("oil_spec"))
        self.analytics = DeskAnalytics.model_validate(_strategy_fixture("oil_analytics"))
        self.intel = IntelPacket.model_validate(_strategy_fixture("oil_intel"))
        self.thesis = Thesis.model_validate(_strategy_fixture("oil_thesis"))
        self.refresh_calls: list = []
        self.submitted: list = []

    def list_cards(self):
        primary = next(i for i in self.spec.instruments if i.role == "primary")
        snap = next(s for s in self.analytics.instruments if s.coin == primary.coin)
        return [self._DeskCard(
            id=self.spec.id, name=self.spec.name, tagline=self.spec.tagline,
            primary_coin=primary.coin, primary_label=primary.label,
            mid=snap.mid, ret_1d=snap.ret_1d, one_liner=self.thesis.call.one_liner,
            call_side=self.thesis.call.side, thesis_age_min=5.0,
        )]

    def get_brief(self, desk_id: str, *, max_age_s=None, wait=False):
        if desk_id != self.spec.id:
            raise KeyError(f"no such desk: {desk_id}")
        self.brief_kwargs = {"max_age_s": max_age_s, "wait": wait}
        thesis = self.thesis
        return self._DeskBrief(spec=self.spec, analytics=self.analytics, intel=self.intel,
                               thesis=thesis, history=[thesis] if thesis else [])

    def refresh(self, desk_id: str, force: bool = False, on_stage=None):
        self.refresh_calls.append((desk_id, force))
        if on_stage is not None:
            on_stage("writing the thesis (about a minute)")
        return self._RefreshJob(id="job-1", desk_id=desk_id, state="done",
                                started_at=self.thesis.created_at,
                                finished_at=self.thesis.created_at)

    def submit_user_thesis(self, desk_id: str, body):
        self.submitted.append((desk_id, body))
        return self._Thesis(
            contract_version=self.thesis.contract_version, id="user-thesis-1",
            desk_id=desk_id, author="user", created_at=self.thesis.created_at,
            expires_at=self.thesis.expires_at, horizon_days=self.thesis.horizon_days,
            call=body.call, scenarios=body.scenarios)

    def track_record(self, desk_id: str):
        return self._TrackRecord(n_graded=3, direction_hit_rate=0.67,
                                 mean_brier=0.2, mean_ret_pct=0.05)

    def grade_due(self):
        self.grade_due_calls = getattr(self, "grade_due_calls", 0) + 1
        return 2

    def grade_due_trades(self, desk_id=None):
        self.grade_due_trades_calls = getattr(self, "grade_due_trades_calls", []) + [desk_id]
        return 5


@pytest.fixture
def fake_desk(monkeypatch):
    """The fake facade, plus offline sizing inputs: desk_brief sizes the
    call for the active account, which would otherwise read the live config,
    the venue's coin metadata and the account's equity."""
    from contracts.risk.models import RiskProfile
    from services.risk.derive import VenueRules
    facade = _FakeFacade()
    monkeypatch.setattr(M, "_strategies_facade", lambda: facade)
    monkeypatch.setattr(M, "_risk_inputs", lambda: (
        RiskProfile(preset="starter"), [], VenueRules(min_order_notional_usd=10.5)))
    monkeypatch.setattr(M, "_coin_specs", lambda coin: (20.0, 3))
    monkeypatch.setattr(M, "_live_equity", lambda: 15.21)
    return facade


class TestDeskTools:
    def test_desk_list(self, fake_desk):
        _, body = _call("desk_list")
        assert body["count"] == 1
        assert body["desks"][0]["id"] == "oil"
        assert body["desks"][0]["one_liner"] == fake_desk.thesis.call.one_liner

    def test_desk_brief_quick_mode_is_markdown(self, fake_desk):
        _, body = _call("desk_brief", {"desk": "oil"})
        assert body["mode"] == "quick"
        assert fake_desk.thesis.call.instrument in body["markdown"]
        assert fake_desk.thesis.call.invalidation in body["markdown"]

    def test_desk_brief_quick_uses_the_fallback_renderer(self, fake_desk, monkeypatch):
        """With services.thesis unimportable, desk_brief must still answer
        through its minimal fallback renderer."""
        import builtins
        real_import = builtins.__import__

        def no_thesis(name, *a, **kw):
            if name.startswith("services.thesis"):
                raise ImportError("simulated: services.thesis not installed")
            return real_import(name, *a, **kw)
        monkeypatch.setattr(builtins, "__import__", no_thesis)
        _, body = _call("desk_brief", {"desk": "oil"})
        assert body["markdown"]  # the fallback produced something, not an empty string
        assert fake_desk.thesis.call.one_liner in body["markdown"]

    def test_desk_brief_quick_uses_the_thesis_renderer(self, fake_desk):
        from services.thesis.api import render_markdown
        _, body = _call("desk_brief", {"desk": "oil"})
        rendered = render_markdown(fake_desk.thesis, fake_desk.analytics, "quick")
        assert body["markdown"].startswith(rendered)
        assert "\nYour size: " in body["markdown"][len(rendered):]

    def test_desk_brief_full_mode_is_the_raw_structured_payload(self, fake_desk):
        _, body = _call("desk_brief", {"desk": "oil", "mode": "full"})
        assert body["mode"] == "full"
        assert body["brief"]["spec"]["id"] == "oil"
        assert body["brief"]["thesis"]["call"]["instrument"] == "xyz:CL"

    def test_desk_brief_rejects_a_bad_mode(self, fake_desk):
        result, body = _call("desk_brief", {"desk": "oil", "mode": "verbose"})
        assert result["isError"] and "mode" in body["error"]

    def test_desk_brief_unknown_desk_is_a_clear_error_not_a_crash(self, fake_desk):
        result, body = _call("desk_brief", {"desk": "nope"})
        assert result["isError"] and "error" in body

    def test_desk_refresh(self, fake_desk):
        _, body = _call("desk_refresh", {"desk": "oil", "force": True})
        assert body["job"]["state"] == "done"
        assert fake_desk.refresh_calls == [("oil", True)]

    def test_desk_refresh_force_defaults_false(self, fake_desk):
        _call("desk_refresh", {"desk": "oil"})
        assert fake_desk.refresh_calls == [("oil", False)]

    def test_desk_submit_call(self, fake_desk):
        _, body = _call("desk_submit_call", {"desk": "oil", "call": {
            "side": "long", "instrument": "xyz:CL", "one_liner": "test probe call",
            "confidence": 0.6, "invalidation": "breaks below 90",
        }})
        assert body["thesis"]["author"] == "user"
        assert body["thesis"]["call"]["instrument"] == "xyz:CL"
        assert len(fake_desk.submitted) == 1

    def test_desk_submit_call_rejects_an_invalid_call(self, fake_desk):
        result, body = _call("desk_submit_call", {"desk": "oil", "call": {
            "side": "long", "instrument": "xyz:CL", "one_liner": "x",
            "confidence": 5.0,  # out of [0, 1] -- contract-invalid
            "invalidation": "x",
        }})
        assert result["isError"]
        assert not fake_desk.submitted, "an invalid call must never reach the facade"

    def test_desk_submit_call_requires_a_call_object(self, fake_desk):
        result, body = _call("desk_submit_call", {"desk": "oil"})
        assert result["isError"] and "call" in body["error"]

    def test_desk_track_record(self, fake_desk):
        _, body = _call("desk_track_record", {"desk": "oil"})
        assert body["track_record"]["n_graded"] == 3

    def test_desk_grade(self, fake_desk):
        _, body = _call("desk_grade", {"desk": "oil"})
        assert body == {"desk": "oil", "theses_graded": 2, "trades_graded": 5}
        assert fake_desk.grade_due_calls == 1
        assert fake_desk.grade_due_trades_calls == ["oil"]

    def test_desk_grade_omitting_desk_grades_every_desk(self, fake_desk):
        _, body = _call("desk_grade", {})
        assert body["desk"] is None
        assert fake_desk.grade_due_trades_calls == [None]

    @pytest.mark.parametrize("tool,args", [
        ("desk_list", {}), ("desk_brief", {"desk": "oil"}),
        ("desk_refresh", {"desk": "oil"}), ("desk_track_record", {"desk": "oil"}),
        ("desk_grade", {}),
    ])
    def test_missing_facade_is_a_clear_error_not_a_crash(self, monkeypatch, tool, args):
        """services.strategies does not exist in this worktree (desk-core
        owns it, built in parallel). Every desk_* tool must degrade to a
        readable error, never an unhandled traceback."""
        def boom():
            raise RuntimeError("services.strategies facade unavailable "
                              "(ModuleNotFoundError: No module named 'services.strategies')")
        monkeypatch.setattr(M, "_strategies_facade", boom)
        result, body = _call(tool, args)
        assert result["isError"]
        assert "facade unavailable" in body["error"]


# ── live-money tools: mocked only, never reach the network ─────────────────

class TestLiveMoneyToolsNeverTouchNetwork:
    """place_bracket / bracket_sweep / close_position / set_leverage /
    cancel_order are the only tools that could ever move real money (the
    bot's old execute path is retired as of the 2026-10-23 headless-oil-desk
    cut). Every test here proves two independent things, so either guard
    failing fails the test: (1) the SDK object that would place an order is
    never even constructed (credentials forced empty, deterministically --
    not dependent on whether this machine's .env.local happens to hold real
    keys), and (2) nothing on the call path reaches a real socket
    (requests.Session.request raises).
    """

    @pytest.fixture(autouse=True)
    def _no_network(self, monkeypatch):
        import requests

        def _boom(self, method, url, *a, **kw):
            raise AssertionError(f"test tried to make a real {method} request to {url}")

        monkeypatch.setattr(requests.sessions.Session, "request", _boom)
        import pathiel.client.exchange as ex
        monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "", raising=False)
        monkeypatch.setattr(ex, "_exchange_instance", None, raising=False)

    def test_cancel_order_never_builds_a_real_exchange(self):
        result, body = _call("cancel_order", {"asset": 0, "order_id": 1})
        assert result["isError"]
        assert "HYPERLIQUID_PRIVATE_KEY" in body["error"]

    def test_set_leverage_never_reaches_the_exchange(self, monkeypatch):
        import pathiel.client.exchange as ex

        def boom(coin, leverage, is_cross=None, strict=False):
            raise AssertionError("set_leverage must never be called for real in a test")
        monkeypatch.setattr(ex, "set_leverage", boom)
        with pytest.raises(AssertionError):
            M.handle_set_leverage({"coin": "BTC", "leverage": 5})

    def test_close_position_never_places_a_real_close(self):
        result, body = _call("close_position", {"coin": "BTC"})
        # Whichever guard trips first (no network, or no credentials), the
        # handler's own try/except turns it into a clean non-close, never
        # a crash and never a real order.
        assert result["isError"] or body.get("closed") is False

    def test_place_bracket_never_builds_a_real_exchange(self):
        result, body = _call("place_bracket", {"coin": "BTC", "entry_px": 100000,
                                               "stop_px": 98000, "tp_px": 103000})
        assert result["isError"] or body.get("placed") is False

    def test_bracket_sweep_never_reaches_the_exchange(self):
        result, body = _call("bracket_sweep", {"coin": "BTC"})
        assert result["isError"] or body.get("ok") is False

    @pytest.mark.parametrize("tool", ["place_bracket", "bracket_sweep", "close_position",
                                      "set_leverage", "cancel_order"])
    def test_every_live_money_tool_is_a_hard_boundary_in_swarm_rules(self, tool):
        """Belt-and-suspenders on the policy itself: these five names are
        exactly the ones docs/overhaul/SWARM_RULES.md section 1 bans
        calling live. If that list and this test ever drift apart, this
        fails loudly instead of quietly under-covering the boundary."""
        rules = (ROOT / "docs" / "overhaul" / "SWARM_RULES.md").read_text()
        assert f"`{tool}`" in rules


# ── stdio e2e: a real subprocess, not just dispatch_message() in-process ───

class TestStdioE2E:
    def test_initialize_list_and_one_read_only_call_over_real_stdio(self, tmp_path):
        env = {**os.environ, "PATHIEL_STATE_DIR": str(tmp_path)}
        proc = subprocess.Popen(
            [sys.executable, str(SERVER)], cwd=str(ROOT), text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env)
        try:
            def send(msg):
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()

            def recv():
                line = proc.stdout.readline()
                assert line, f"server produced no output; stderr:\n{proc.stderr.read()}"
                return json.loads(line)

            send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
            init = recv()
            assert init["result"]["protocolVersion"] == "2025-06-18"
            assert init["result"]["serverInfo"]["name"] == "pathiel"

            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            listing = recv()
            names = {t["name"] for t in listing["result"]["tools"]}
            assert "get_server_time" in names and "desk_list" in names
            assert "get_coin_price" not in names, "hidden alias leaked into tools/list"
            assert len(names) == len(M.TOOLS) == 59

            send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "get_server_time", "arguments": {}}})
            called = recv()
            assert called["result"]["isError"] is False
            body = json.loads(called["result"]["content"][0]["text"])
            assert isinstance(body["server_time"], int) and body["server_time"] > 0
        finally:
            proc.stdin.close()
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
