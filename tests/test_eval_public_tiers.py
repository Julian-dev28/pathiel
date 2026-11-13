"""Eval: what a stranger's Claude sees from the MCP server, by install tier.

Three scenarios, run through dispatch_message (the server's JSON-RPC entry)
against an offline venue built from recorded testnet /info answers
(tests/fixtures/public_info/). One scorecard; every row must pass.

  A  public, fresh HOME, no key, no address: market reads return data, account
     reads say "no account configured", the six live-money tools say
     read-only, no traceback or dashboard wording anywhere, the SDK Exchange
     is never built, tools/list carries no operator wording.
  B  public, testnet key, builder approved and the approval cache warm, fake
     SDK: place_bracket, close_position, set_target and bracket_sweep reach
     the SDK with the builder and their outputs carry network and builder_fee;
     no request goes to mainnet.
  C  operator: instructions and tools/list hash to the pinned base values and
     no output carries a public key.
  D  final review: a testnet with no HIP-3 dex, a fresh approval, the
     launcher's default network.
  E  decisions of 2026-11-12, mainnet: no PATHIEL_NETWORK means mainnet and
     the state notice says real money; the shipped builder address is the
     default and never charges its own account; the oil desk is Brent.
  W  decisions of 2026-11-12, testnet: the oil desk trades xyz:CL (WTI) as
     the Brent stand-in and the brief says so first; Brent entries are
     refused, WTI entries are not; analytics sends no candle read for a coin
     testnet does not list.

Run: .venv/bin/python -m pytest -q -s tests/test_eval_public_tiers.py

This file also holds the offline harness (FakeVenue, FakeInfo, FakeSdk,
FakeFacade, install). tests/test_mcp_public_mode.py and
tests/test_mcp_operator_unchanged.py load it with importlib, because tests/
has no __init__.py.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from contracts.runtime import MAINNET_API_URL, TESTNET_API_URL

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "pathiel-mcp-server.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "public_info"
STRATEGY_FIXTURES = ROOT / "contracts" / "strategies" / "fixtures"

ACCT = "0x" + "a1" * 20
BUILDER = "0x" + "b1" * 20
SHIPPED_BUILDER = "0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985"   # contracts/runtime default
WTI_LINE = ("Testnet: this desk trades WTI (xyz:CL) as the stand-in for Brent (xyz:BRENTOIL), "
            "which is delisted on testnet.")
KEY = "0x" + "11" * 32

# Base values, pinned on the untouched server (2816df7) before this unit
# edited it. tests/test_mcp_operator_unchanged.py asserts the same two.
BASE_INSTRUCTIONS_SHA256 = "8d82a135a7ac6113ae0a44c444d19dd1873f0a830cf0763f971b7eccffda5c1c"
BASE_TOOLS_SHA256 = "8ec89b2f6cfac71e9e8088068569b6272268b88e3f6d77fd29e189a89d039e4b"

PUBLIC_KEYS = ("network", "builder_fee", "install", "read_only", "network_note")
FORBIDDEN_OUTPUT = ("Traceback", "NoneType", "KeyError", "localhost", "Account tab", "dashboard")
FORBIDDEN_TOOL_WORDS = ("dashboard", "account tab", "desk page", "localhost", "daily pause")

# A sensible call for every tool. Market reads use BTC, which the testnet
# fixtures list.
MARKET_ARGS: Dict[str, Dict[str, Any]] = {
    "market_get_asset_data": {"asset": "BTC", "intervals": ["1h"], "count": 5},
    "market_get_funding_regime": {},
    "market_list_instruments": {"limit": 5},
    "get_price": {"coin": "BTC"},
    "get_candles": {"coin": "BTC", "interval": "1h", "count": 10},
    "get_l2_book": {"coin": "BTC"},
    "get_predicted_funding": {"coin": "BTC"},
    "get_asset_context": {"coin": "BTC"},
    "get_price_history": {"coin": "BTC", "interval": "1h"},
    "get_assets": {},
    "get_coin_info": {"coin": "BTC"},
    "get_all_mids": {},
    "get_24h_stats": {"coin": "BTC"},
    "get_funding_rate": {"coin": "BTC"},
    "get_open_interest": {"coin": "BTC"},
    "get_server_time": {},
    "get_price_impact": {"coin": "BTC", "notionalUsd": 1000},
    "get_max_leverage": {"coin": "BTC"},
}
ACCOUNT_ARGS: Dict[str, Dict[str, Any]] = {
    "get_order_by_oid": {"oid": 1},
    "query_order_by_cloid": {"cloid": "0x" + "0" * 32},
    "get_user_fills_by_time": {"start_time": 0},
    "get_max_trade_size": {"coin": "BTC"},
    "get_liquidation_price": {"coin": "BTC"},
    "risk_size_trade": {"coin": "BTC", "entry_px": 100, "stop_px": 95},
}
WRITE_ARGS: Dict[str, Dict[str, Any]] = {
    "place_bracket": {"coin": "BTC", "stop_px": 72000, "tp_px": 77000},
    "bracket_sweep": {"coin": "ETH"},
    "set_target": {"coin": "ETH", "tp_px": 2100},
    "close_position": {"coin": "ETH"},
    "set_leverage": {"coin": "BTC", "leverage": 3},
    "cancel_order": {"order_id": 1, "coin": "BTC"},
}


def load_server(name: str):
    spec = importlib.util.spec_from_file_location(name, SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def tools_hash(tools: List[Dict[str, Any]]) -> str:
    return sha256(json.dumps(tools, sort_keys=True))


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())["response"]


# ── the offline venue ─────────────────────────────────────────────────────────

class FakeResponse:
    status_code = 200

    def __init__(self, body: Any):
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return copy.deepcopy(self._body)


class FakeVenue:
    """Answers /info by payload type from tests/fixtures/public_info and
    records every URL. Positions and resting orders are settable per dex.
    An /exchange post or an unknown type is recorded and fails the eval."""

    def __init__(self, base_url: str):
        self.base = base_url
        self.urls: List[str] = []
        self.types: List[str] = []
        self.unknown: List[Any] = []
        self.positions: Dict[str, List[Dict[str, Any]]] = {}
        self.open_orders: Dict[str, List[Dict[str, Any]]] = {}
        self.max_builder_fee = 0
        # dexes this venue serves; perp_dexs, when set, replaces the recorded
        # perpDexs reply (scenario D: a network with no HIP-3 dex)
        self.dexes = ("", "xyz")
        self.perp_dexs: Optional[List[Any]] = None

    def answer(self, payload: Dict[str, Any]) -> Any:
        typ = payload.get("type")
        dex = payload.get("dex") or ""
        self.types.append(typ)
        if typ == "perpDexs" and self.perp_dexs is not None:
            return copy.deepcopy(self.perp_dexs)
        if typ in ("meta", "metaAndAssetCtxs", "allMids"):
            if dex in self.dexes:
                return fixture(f"{typ}_{dex or 'main'}")
            return {"universe": []} if typ == "meta" else ([{"universe": []}, []]
                                                            if typ == "metaAndAssetCtxs" else {})
        if typ == "l2Book":
            # the recorded BTC book, scaled to the coin's own mid
            book = fixture("l2Book")
            coin = str(payload.get("coin") or "")
            mids = fixture("allMids_xyz" if ":" in coin else "allMids_main")
            scale = float(mids.get(coin) or 0) / float(fixture("allMids_main")["BTC"]) or 1.0
            for side in book["levels"]:
                for lv in side:
                    lv["px"] = f"{float(lv['px']) * scale:.6g}"
            book["coin"] = coin
            return book
        if typ == "candleSnapshot":
            coin = (payload.get("req") or {}).get("coin")
            return [{**c, "s": coin} for c in fixture("candleSnapshot")]
        if typ == "clearinghouseState":
            state = fixture("clearinghouseState")
            if dex:   # the recorded account holds its money on the main dex only
                for k in ("marginSummary", "crossMarginSummary"):
                    state[k] = {f: "0.0" for f in state[k]}
                state["withdrawable"] = "0.0"
            state["assetPositions"] = copy.deepcopy(self.positions.get(dex, []))
            return state
        if typ in ("frontendOpenOrders", "openOrders"):
            return copy.deepcopy(self.open_orders.get(dex, []))
        if typ == "maxBuilderFee":
            return self.max_builder_fee
        if (FIXTURES / f"{typ}.json").is_file():
            return fixture(typ)
        self.unknown.append(payload)
        return None

    def post(self, url: str, json: Any = None, timeout: Any = None, **_kw: Any) -> FakeResponse:
        self.urls.append(url)
        if not url.endswith("/info"):
            self.unknown.append({"url": url, "payload": json})
            raise AssertionError(f"offline venue: {url} is not /info")
        return FakeResponse(self.answer(json or {}))

    def mainnet_requests(self) -> int:
        return sum(1 for u in self.urls if u.startswith(MAINNET_API_URL))

    def off_base(self) -> List[str]:
        return [u for u in self.urls if not u.startswith(self.base)]


class FakeSession:
    def __init__(self, venue: FakeVenue):
        self.venue = venue

    def post(self, url: str, **kw: Any) -> FakeResponse:
        return self.venue.post(url, **kw)


class FakeInfo:
    """The SDK Info methods pathiel.client.exchange calls, on the fake venue."""

    def __init__(self, venue: FakeVenue):
        self.venue = venue

    def _q(self, payload: Dict[str, Any]) -> Any:
        return self.venue.post(f"{self.venue.base}/info", json=payload).json()

    def meta(self, dex: str = "") -> Any:
        return self._q({"type": "meta", **({"dex": dex} if dex else {})})

    def all_mids(self, dex: str = "") -> Any:
        return self._q({"type": "allMids", **({"dex": dex} if dex else {})})

    def l2_snapshot(self, name: str) -> Any:
        return self._q({"type": "l2Book", "coin": name})

    def user_state(self, address: str, dex: str = "") -> Any:
        return self._q({"type": "clearinghouseState", "user": address, **({"dex": dex} if dex else {})})

    def frontend_open_orders(self, address: str, dex: str = "") -> Any:
        return self._q({"type": "frontendOpenOrders", "user": address, **({"dex": dex} if dex else {})})

    def post(self, path: str, payload: Dict[str, Any]) -> Any:
        return self._q(payload)


FILLED = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"filled": {"oid": 11, "avgPx": "1979.75", "totalSz": "0.5"}}]}}}
RESTING = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"resting": {"oid": 12}}]}}}


class FakeSdk:
    """hyperliquid Exchange, the calls pathiel makes. Records each order's
    builder argument (None when absent). Never signs, never posts."""

    def __init__(self):
        self.orders: List[Dict[str, Any]] = []
        self.other: List[str] = []

    def order(self, name, is_buy, sz, limit_px, order_type, reduce_only=False, cloid=None,
              builder=None):
        self.orders.append({"coin": name, "is_buy": is_buy, "sz": sz, "px": limit_px,
                            "type": order_type, "builder": None if builder is None else dict(builder)})
        if "trigger" in order_type:
            return copy.deepcopy(RESTING)
        out = copy.deepcopy(FILLED)
        out["response"]["data"]["statuses"][0]["filled"].update(
            {"avgPx": str(limit_px), "totalSz": str(sz)})
        return out

    def bulk_orders(self, order_requests, builder=None, grouping="na"):
        self.orders.append({"bulk": len(order_requests), "builder": None if builder is None else dict(builder)})
        return {"status": "ok", "response": {"type": "order", "data": {"statuses": [
            {"resting": {"oid": 13}}, "waitingForFill", "waitingForFill"]}}}

    def update_leverage(self, leverage, name, is_cross=True):
        self.other.append(f"update_leverage {name} {leverage}")
        return {"status": "ok", "response": {"type": "default"}}

    def cancel(self, name, oid):
        self.other.append(f"cancel {name} {oid}")
        return {"status": "ok", "response": {"type": "cancel", "data": {"statuses": ["success"]}}}


class ExchangeSpy:
    """Stands in for hyperliquid.exchange.Exchange: counts constructions."""
    constructed = 0

    def __init__(self, *a: Any, **kw: Any):
        type(self).constructed += 1
        raise AssertionError("the SDK Exchange was constructed")


def _strategy_fixture(name: str) -> Dict[str, Any]:
    return json.loads((STRATEGY_FIXTURES / f"{name}.json").read_text())


class FakeFacade:
    """services.strategies' facade, built from the contract fixtures (same
    pattern as _FakeFacade in tests/test_mcp_overhaul.py)."""

    def __init__(self):
        from contracts.strategies.models import (
            DeskAnalytics, DeskBrief, DeskCard, DeskSpec, IntelPacket, RefreshJob, Thesis,
            TrackRecord,
        )
        self._DeskBrief, self._DeskCard = DeskBrief, DeskCard
        self._RefreshJob, self._TrackRecord = RefreshJob, TrackRecord
        self.spec = DeskSpec.model_validate(_strategy_fixture("oil_spec"))
        self.analytics = DeskAnalytics.model_validate(_strategy_fixture("oil_analytics"))
        self.intel = IntelPacket.model_validate(_strategy_fixture("oil_intel"))
        self.thesis = Thesis.model_validate(_strategy_fixture("oil_thesis"))
        self.refresh_calls: List[Any] = []

    def list_cards(self):
        primary = next(i for i in self.spec.instruments if i.role == "primary")
        snap = next(s for s in self.analytics.instruments if s.coin == primary.coin)
        return [self._DeskCard(
            id=self.spec.id, name=self.spec.name, tagline=self.spec.tagline,
            primary_coin=primary.coin, primary_label=primary.label, mid=snap.mid,
            ret_1d=snap.ret_1d, one_liner=self.thesis.call.one_liner,
            call_side=self.thesis.call.side, thesis_age_min=5.0)]

    def get_brief(self, desk_id: str, *, max_age_s=None, wait=False):
        if desk_id != self.spec.id:
            raise KeyError(f"no such desk: {desk_id}")
        return self._DeskBrief(spec=self.spec, analytics=self.analytics, intel=self.intel,
                               thesis=self.thesis, history=[self.thesis])

    def refresh(self, desk_id: str, force: bool = False, on_stage=None):
        self.refresh_calls.append((desk_id, force))
        return self._RefreshJob(id="job-1", desk_id=desk_id, state="done",
                                started_at=self.thesis.created_at,
                                finished_at=self.thesis.created_at)

    def track_record(self, desk_id: str):
        return self._TrackRecord(n_graded=3, direction_hit_rate=0.67, mean_brier=0.2,
                                 mean_ret_pct=0.05)

    def grade_due(self):
        return 0

    def grade_due_trades(self, desk_id=None):
        return 0


class Harness:
    def __init__(self, server, venue: FakeVenue, sdk: Optional[FakeSdk], facade: FakeFacade,
                 jobs: List[Any]):
        self.server, self.venue, self.sdk, self.facade, self.jobs = server, venue, sdk, facade, jobs
        self.outputs: List[str] = []
        self._id = 0

    def rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._id += 1
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        return self.server.dispatch_message(msg)

    def call(self, name: str, args: Optional[Dict[str, Any]] = None):
        """(isError, parsed body or raw text) for one tools/call."""
        resp = self.rpc("tools/call", {"name": name, "arguments": args or {}})
        result = resp["result"]
        text = result["content"][0]["text"]
        self.outputs.append(text)
        try:
            body = json.loads(text)
        except ValueError:
            body = text
        return result["isError"], body

    def run_builder_jobs(self) -> int:
        """Run the background approval reads the code scheduled (the root
        conftest's builder isolation queues them instead of starting threads)."""
        n = 0
        while self.jobs:
            self.jobs.pop(0)()
            n += 1
        return n

    def warm_builder(self, account: str = ACCT) -> None:
        from pathiel.client import builder
        builder.warm(account)
        self.run_builder_jobs()


def install(monkeypatch, tmp_path, server, jobs, *, public: bool = True,
            network: str = "testnet", key: bool = False, address: Optional[str] = None,
            builder_address: Optional[str] = "", max_builder_fee: int = 0) -> Harness:
    """Point one loaded server module at the offline venue, as one install.

    Never reaches the network: the shared session, requests.post, the SDK
    Info and the SDK Exchange are all replaced, and builder approval reads go
    to the fake venue through builder._fetch.

    builder_address: "" means no builder (the env off switch "none"; the
    shipped default is set since 2026-11-12), None means the shipped
    default, anything else is the override."""
    import requests

    from pathiel.client import builder
    from pathiel.client import exchange as ex
    from pathiel.client import hl_client, universe
    from pathiel.client.rate_limit import HL_LIMITER

    base = TESTNET_API_URL if network == "testnet" else MAINNET_API_URL
    if public:
        monkeypatch.setenv("PATHIEL_INSTALL", "public")
    else:
        monkeypatch.delenv("PATHIEL_INSTALL", raising=False)
    if public or network != "mainnet":
        monkeypatch.setenv("PATHIEL_NETWORK", network)
    else:
        monkeypatch.delenv("PATHIEL_NETWORK", raising=False)
    if builder_address is None:
        monkeypatch.delenv("PATHIEL_BUILDER_ADDRESS", raising=False)
    else:
        monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", builder_address or "none")
    monkeypatch.delenv("PATHIEL_BUILDER_FEE_TENTHS_BP", raising=False)
    monkeypatch.setattr(hl_client, "HL_API", base)

    venue = FakeVenue(base)
    venue.max_builder_fee = max_builder_fee
    monkeypatch.setattr(hl_client, "_get_session", lambda: FakeSession(venue))
    monkeypatch.setattr(requests, "post", venue.post)
    fake_info = FakeInfo(venue)
    monkeypatch.setattr(ex, "_get_info", lambda: fake_info)
    monkeypatch.setattr(hl_client, "get_info", lambda: fake_info)
    monkeypatch.setattr(HL_LIMITER, "acquire", lambda *a, **k: True)
    monkeypatch.setattr(builder, "_fetch", lambda user, b: venue.post(
        f"{hl_client.HL_API}/info", json={"type": "maxBuilderFee", "user": user, "builder": b}).json())

    cache = tmp_path / "universe"
    cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(universe, "_CACHE_DIR", cache)
    monkeypatch.setattr(universe, "_UNIVERSE_CACHE_PATH", cache / "meta.json")
    monkeypatch.setattr(universe, "_SPOT_CACHE_PATH", cache / "spot_meta.json")
    monkeypatch.setattr(universe, "_PERP_DEXS_CACHE_PATH", cache / "perp_dexs.json")
    monkeypatch.setattr(ex, "_META_CACHE", {})
    if hasattr(hl_client, "_CANDLE_CACHE"):
        monkeypatch.setattr(hl_client, "_CANDLE_CACHE", {})

    # identity: the exchange module's env globals and the read address
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", KEY if key else "")
    monkeypatch.setattr(ex, "HL_WALLET", ACCT if key else "")
    monkeypatch.setattr(ex, "HL_MASTER", "")
    monkeypatch.setattr(ex, "IS_AGENT", False)
    monkeypatch.setattr(ex, "HL_ACCOUNT", ACCT if key else "")
    read_address = address if address is not None else (ACCT if key else "")
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", read_address)
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", "")
    monkeypatch.setenv("HYPERLIQUID_PRIVATE_KEY", "")
    ExchangeSpy.constructed = 0
    monkeypatch.setattr(ex, "Exchange", ExchangeSpy)
    sdk = FakeSdk() if key else None
    if key:
        monkeypatch.setattr(ex, "_make_exchange", lambda: sdk)

    facade = FakeFacade()
    monkeypatch.setattr(server, "_strategies_facade", lambda: facade)
    monkeypatch.setattr(server, "_live_rule", lambda desk_id, brief: {
        "next": None, "line": "Live rule: (offline eval)."})
    monkeypatch.setattr(server, "_PUBLIC_TOOLS", None)
    return Harness(server, venue, sdk, facade, jobs)


def is_data(is_error: bool, body: Any) -> bool:
    """Data-shaped: not an error, and a JSON object or list that does not
    carry an error key."""
    if is_error:
        return False
    if isinstance(body, dict):
        return body.get("error") in (None, "", False) and body.get("status") != "error"
    return isinstance(body, list)


def scan(texts: List[str], words) -> List[str]:
    return [f"{w} in {t[:120]!r}" for t in texts for w in words if w in t]


# ── the scenarios ─────────────────────────────────────────────────────────────

def scenario_a(h: Harness) -> List[Dict[str, Any]]:
    from pathiel.client.exchange import READ_ONLY_NO_KEY
    s = h.server
    rows: List[Dict[str, Any]] = []
    market_ok = [n for n in sorted(s.MARKET_READ_TOOLS) if is_data(*h.call(n, MARKET_ARGS.get(n)))]
    rows.append({"check": "A market reads return data", "got": len(market_ok),
                 "want": len(s.MARKET_READ_TOOLS), "ok": len(market_ok) == len(s.MARKET_READ_TOOLS)})
    names = sorted(s.ACCOUNT_READ_TOOLS) + sorted(a for a, t in s.ALIASES.items()
                                                  if t in s.ACCOUNT_READ_TOOLS)
    clean = 0
    for n in names:
        err, body = h.call(n, ACCOUNT_ARGS.get(s._canonical(n)))
        if err and isinstance(body, dict) and body == {"error": s.NO_ACCOUNT_MSG, "no_account": True}:
            clean += 1
    rows.append({"check": "A account reads + aliases say no account", "got": clean,
                 "want": len(names), "ok": clean == len(names)})
    ro = 0
    for n in sorted(s.LIVE_MONEY_TOOLS):
        err, body = h.call(n, WRITE_ARGS[n])
        if (err and isinstance(body, dict) and body.get("read_only") is True
                and body.get("error") == READ_ONLY_NO_KEY and body.get("network") == "testnet"):
            ro += 1
    rows.append({"check": "A writes are read-only", "got": ro, "want": 6, "ok": ro == 6})
    for n in sorted(s.LOCAL_READ_TOOLS):
        h.call(n, {"desk": "oil"} if n.startswith("desk_") and n != "desk_list" else {})
    bad = scan(h.outputs, FORBIDDEN_OUTPUT)
    rows.append({"check": "A tracebacks or operator wording in outputs", "got": len(bad),
                 "want": 0, "ok": not bad})
    rows.append({"check": "A SDK Exchange constructions", "got": ExchangeSpy.constructed,
                 "want": 0, "ok": ExchangeSpy.constructed == 0})
    listed = h.rpc("tools/list")["result"]["tools"]
    words = sum(1 for t in listed for w in FORBIDDEN_TOOL_WORDS if w in t["description"].lower())
    rows.append({"check": "A operator words in tools/list", "got": words, "want": 0, "ok": words == 0})
    rows.append({"check": "A unknown venue requests", "got": len(h.venue.unknown), "want": 0,
                 "ok": not h.venue.unknown})
    return rows


def seed_positions(venue: FakeVenue) -> None:
    """ETH long 0.5 with two stops at one price (bracket_sweep folds them)."""
    venue.positions[""] = [{"type": "oneWay", "position": {
        "coin": "ETH", "szi": "0.5", "entryPx": "1900.0", "positionValue": "989.875",
        "unrealizedPnl": "39.875", "liquidationPx": "1500.0", "marginUsed": "200.0",
        "leverage": {"type": "isolated", "value": 5}, "maxLeverage": 25}}]
    venue.open_orders[""] = [
        {"coin": "ETH", "oid": 101, "side": "A", "sz": "0.25", "origSz": "0.25", "limitPx": "1800.0",
         "triggerPx": "1800.0", "isTrigger": True, "reduceOnly": True, "orderType": "Stop Market",
         "children": [], "timestamp": 1},
        {"coin": "ETH", "oid": 102, "side": "A", "sz": "0.25", "origSz": "0.25", "limitPx": "1800.0",
         "triggerPx": "1800.0", "isTrigger": True, "reduceOnly": True, "orderType": "Stop Market",
         "children": [], "timestamp": 2},
    ]


def scenario_b(h: Harness) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    h.warm_builder()
    reached = 0
    decorated = 0
    for n in ("place_bracket", "close_position", "set_target", "bracket_sweep"):
        if n == "close_position":
            seed_positions(h.venue)    # place_bracket ran flat; the rest act on an ETH long
        before = len(h.sdk.orders)
        err, body = h.call(n, WRITE_ARGS[n])
        sent = h.sdk.orders[before:]
        if sent and all(o["builder"] == {"b": BUILDER, "f": 10} for o in sent):
            reached += 1
        fee = body.get("builder_fee") if isinstance(body, dict) else None
        if (isinstance(body, dict) and body.get("network") == "testnet" and isinstance(fee, dict)
                and fee.get("charged") is True and fee.get("rate") == "0.01%"):
            decorated += 1
    rows.append({"check": "B write tools reach the SDK with the builder", "got": reached,
                 "want": 4, "ok": reached == 4})
    rows.append({"check": "B outputs carry network and builder_fee", "got": decorated, "want": 4,
                 "ok": decorated == 4})
    rows.append({"check": "B mainnet requests", "got": h.venue.mainnet_requests(), "want": 0,
                 "ok": h.venue.mainnet_requests() == 0 and not h.venue.off_base()})
    rows.append({"check": "B SDK Exchange constructions", "got": ExchangeSpy.constructed,
                 "want": 0, "ok": ExchangeSpy.constructed == 0})
    return rows


def scenario_d(h: Harness, monkeypatch) -> List[Dict[str, Any]]:
    """Final review, 2026-11-12: a testnet with no HIP-3 dex, an approval
    signed while the server runs, and the launcher's default network."""
    from pathiel.client import builder
    rows: List[Dict[str, Any]] = []
    h.venue.perp_dexs = [None]
    h.venue.dexes = ("",)
    _err, body = h.call("get_price", {"coin": "xyz:CL"})
    named = isinstance(body, dict) and "not tradable on testnet (not_listed)" in str(body.get("error"))
    rows.append({"check": "D get_price on a dex this network lacks names the network",
                 "got": named, "want": True, "ok": named})
    _err, body = h.call("place_bracket", {"coin": "xyz:CL", "stop_px": 58, "tp_px": 66,
                                          "dry_run": True})
    refused = isinstance(body, dict) and "not_listed" in str(body.get("network_note"))
    rows.append({"check": "D place_bracket on it is refused with the network note",
                 "got": refused, "want": True, "ok": refused})
    h.venue.max_builder_fee = 0
    h.warm_builder()
    before = builder.builder_status(ACCT)["reason_code"]
    h.jobs.clear()
    h.venue.max_builder_fee = 10           # the user ran approve-sign
    _err, body = h.call("state")
    fee = ((body or {}).get("install") or {}).get("builder_fee") or {}
    seen = (before, fee.get("attaches"))
    rows.append({"check": "D state sees a new approval at once", "got": seen,
                 "want": ("not_approved", True), "ok": seen == ("not_approved", True)})
    monkeypatch.setenv("PATHIEL_NETWORK_SOURCE", "default")
    _err, body = h.call("state")
    explicit = ((body or {}).get("install") or {}).get("network_explicit")
    rows.append({"check": "D state: launcher default network is not a choice", "got": explicit,
                 "want": False, "ok": explicit is False})
    rows.append({"check": "D unknown venue requests", "got": len(h.venue.unknown), "want": 0,
                 "ok": not h.venue.unknown})
    rows.append({"check": "D mainnet requests", "got": h.venue.mainnet_requests(), "want": 0,
                 "ok": h.venue.mainnet_requests() == 0})
    return rows


def _row(check: str, got: Any, want: Any) -> Dict[str, Any]:
    return {"check": check, "got": got, "want": want, "ok": got == want}


def scenario_e(h: Harness, monkeypatch) -> List[Dict[str, Any]]:
    """Public install on mainnet by default, with the shipped builder."""
    from pathiel.client import builder
    from services.strategies.desks import get_spec
    monkeypatch.delenv("PATHIEL_NETWORK", raising=False)
    monkeypatch.setenv("PATHIEL_NETWORK_SOURCE", "default")
    rows: List[Dict[str, Any]] = []
    _err, body = h.call("state")
    install = (body or {}).get("install") or {}
    rows.append(_row("E no PATHIEL_NETWORK: network", install.get("network"), "mainnet"))
    rows.append(_row("E no PATHIEL_NETWORK: not marked as chosen", install.get("network_explicit"), False))
    notice = str(install.get("network_notice"))
    rows.append(_row("E state notice says MAINNET and real money",
                     "MAINNET" in notice and "real money" in notice, True))
    init = h.rpc("initialize", {})["result"]["instructions"]
    rows.append(_row("E instructions open on mainnet REAL MONEY", "Network: mainnet. REAL MONEY" in init, True))
    fee = install.get("builder_fee") or {}
    rows.append(_row("E builder address is the shipped one", fee.get("address"), SHIPPED_BUILDER.lower()))
    rows.append(_row("E shipped builder never charges its own account",
                     (builder.builder_for_order(SHIPPED_BUILDER),
                      builder.last_decision(SHIPPED_BUILDER).get("reason_code")),
                     (None, "self_trade")))
    _err, body = h.call("desk_brief", {"desk": "oil"})
    primary = next(i.coin for i in get_spec("oil").instruments if i.role == "primary")
    rows.append(_row("E mainnet oil desk primary", primary, "xyz:BRENTOIL"))
    rows.append(_row("E mainnet brief has no stand-in line",
                     "testnet_substitute" in body or "Testnet:" in str(body.get("markdown")), False))
    return rows


def scenario_w(h: Harness) -> List[Dict[str, Any]]:
    """Public install that opted into testnet."""
    from services.strategies import analytics
    from services.strategies.desks import get_spec
    rows: List[Dict[str, Any]] = []
    spec = get_spec("oil")
    primary = next(i for i in spec.instruments if i.role == "primary")
    rows.append(_row("W testnet oil desk primary", (primary.coin, primary.label), ("xyz:CL", "WTI")))
    rows.append(_row("W xyz:CL once, Brent gone, no WTI-WTI pair",
                     ([i.coin for i in spec.instruments].count("xyz:CL"),
                      "xyz:BRENTOIL" in [i.coin for i in spec.instruments], spec.pairs),
                     (1, False, [])))
    _err, body = h.call("desk_brief", {"desk": "oil"})
    md = str((body or {}).get("markdown"))
    rows.append(_row("W brief opens with the WTI stand-in line", md.startswith("**" + WTI_LINE), True))
    rows.append(_row("W brief has no cannot-trade note", "network_note" in (body or {}), False))
    _err, body = h.call("place_bracket", {"coin": "xyz:CL", "stop_px": 58, "tp_px": 66, "dry_run": True})
    rows.append(_row("W WTI entry is not refused for listing", "network_note" in (body or {}), False))
    _err, body = h.call("place_bracket", {"coin": "xyz:BRENTOIL", "stop_px": 58, "tp_px": 66,
                                          "dry_run": True})
    rows.append(_row("W Brent entry is refused (delisted)",
                     "(delisted)" in str((body or {}).get("network_note")), True))
    before = h.venue.types.count("candleSnapshot")
    got = analytics.default_candle_fetcher("xyz:ARM", "1d", 30)
    rows.append(_row("W no candle read for a coin testnet does not list",
                     (list(got), h.venue.types.count("candleSnapshot") - before), ([], 0)))
    got = analytics.default_candle_fetcher("xyz:CL", "1d", 30)
    rows.append(_row("W listed coin still reads candles", len(got) > 0, True))
    rows.append(_row("W mainnet requests", h.venue.mainnet_requests(), 0))
    rows.append(_row("W unknown venue requests", len(h.venue.unknown), 0))
    return rows


def scenario_c(h: Harness) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    init = h.rpc("initialize", {})["result"]["instructions"]
    listed = h.rpc("tools/list")["result"]["tools"]
    rows.append({"check": "C instructions hash is the base value", "got": sha256(init)[:12],
                 "want": BASE_INSTRUCTIONS_SHA256[:12], "ok": sha256(init) == BASE_INSTRUCTIONS_SHA256})
    rows.append({"check": "C tools/list hash is the base value", "got": tools_hash(listed)[:12],
                 "want": BASE_TOOLS_SHA256[:12], "ok": tools_hash(listed) == BASE_TOOLS_SHA256})
    seed_positions(h.venue)
    calls = [("state", {}), ("risk_limits", {"equity_usd": 1000}),
             ("place_bracket", {**WRITE_ARGS["place_bracket"], "dry_run": True}),
             ("close_position", {"coin": "ETH"}), ("set_target", WRITE_ARGS["set_target"]),
             ("bracket_sweep", {"coin": "ETH"}), ("desk_brief", {"desk": "oil"})]
    leaks = 0
    for n, a in calls:
        _err, body = h.call(n, a)
        if isinstance(body, dict):
            leaks += sum(1 for k in PUBLIC_KEYS if k in body)
    rows.append({"check": "C public keys in operator outputs", "got": leaks, "want": 0,
                 "ok": leaks == 0})
    attached = sum(1 for o in (h.sdk.orders if h.sdk else []) if o["builder"] is not None)
    rows.append({"check": "C operator orders with a builder", "got": attached, "want": 0,
                 "ok": attached == 0 and bool(h.sdk and h.sdk.orders)})
    return rows


SERVER_MODULE = None


def _server():
    global SERVER_MODULE
    if SERVER_MODULE is None:
        SERVER_MODULE = load_server("pathiel_mcp_server_eval_tiers")
    return SERVER_MODULE


def test_eval_public_tiers(monkeypatch, tmp_path, builder_jobs, scorecard):
    s = _server()
    rows: List[Dict[str, Any]] = []
    with monkeypatch.context() as m:
        h = install(m, tmp_path / "a", s, builder_jobs)
        rows += scenario_a(h)
    builder_jobs.clear()
    from pathiel.client import builder
    builder.reset()
    with monkeypatch.context() as m:
        h = install(m, tmp_path / "b", s, builder_jobs, key=True, builder_address=BUILDER,
                    max_builder_fee=10)
        rows += scenario_b(h)
    builder_jobs.clear()
    builder.reset()
    with monkeypatch.context() as m:
        h = install(m, tmp_path / "d", s, builder_jobs, key=True, builder_address=BUILDER)
        rows += scenario_d(h, m)
    builder_jobs.clear()
    builder.reset()
    with monkeypatch.context() as m:
        h = install(m, tmp_path / "e", s, builder_jobs, network="mainnet", builder_address=None)
        rows += scenario_e(h, m)
    builder_jobs.clear()
    builder.reset()
    with monkeypatch.context() as m:
        h = install(m, tmp_path / "w", s, builder_jobs, network="testnet", key=True)
        rows += scenario_w(h)
    builder_jobs.clear()
    builder.reset()
    with monkeypatch.context() as m:
        h = install(m, tmp_path / "c", s, builder_jobs, public=False, network="mainnet", key=True,
                    builder_address=BUILDER, max_builder_fee=10)
        rows += scenario_c(h)
    card = scorecard("public_tiers", rows)
    failed = [r for r in card["rows"] if not r["ok"]]
    assert not failed, f"{len(failed)} row(s) failed: {failed}"


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(pytest.main(["-q", "-s", __file__]))
