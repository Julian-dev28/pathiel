"""I1: the operator's live stack behaves exactly as before the public plugin.

Julien launches through the repo .mcp.json (no env block), so PATHIEL_INSTALL
and PATHIEL_NETWORK are unset: operator, mainnet, his tracked 20x config, and
no builder on any order, even with a builder address configured and an
approval in the cache. The fake SDKs here have the pre-builder signatures:
a builder= keyword reaching them is a TypeError, not a silent pass.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path

import pytest

import contracts.runtime as rt
from pathiel.client import builder
from pathiel.client import exchange as ex
from pathiel.client import hl_client

ROOT = Path(__file__).resolve().parents[1]
ADDR = "0x" + "b1" * 20
ACCT = "0x" + "a1" * 20

FILLED = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"filled": {"oid": 11, "avgPx": "100.0", "totalSz": "1.0"}}]}}}
RESTING = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"resting": {"oid": 12}}]}}}
BULK_OK = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"resting": {"oid": 13}}, "waitingForFill", "waitingForFill"]}}}
BUILDER_ERR = {"status": "err", "response": "Builder fee has not been approved."}

# The operator's tracked .agent-config.json as of 2816df7, as a literal. The
# live file is never read here (I3: he has uncommitted edits to it).
OPERATOR_CONFIG = {
    "mode": "LIVE",
    "enable_hip3": True,
    "coin_allowlist": ["xyz:BRENTOIL", "xyz:CL", "xyz:SILVER"],
    "coin_blocklist": [],
    "hip3_dex_allowlist": ["xyz"],
    "hip3_dex_blocklist": [],
    "risk_profile": {"preset": "auto", "risk_per_trade_pct": 100, "daily_loss_limit_pct": 100,
                     "max_leverage": 20, "max_open_positions": 1,
                     "allow_min_order_bump": True, "max_bumped_risk_pct": 100},
    "active_desks": ["oil", "metals"],
    "desk_equity_share": {"oil": 0.9, "metals": 0.1},
}


class OldSdk:
    """hyperliquid-python-sdk's order/bulk_orders WITHOUT the builder parameter."""

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply

    def order(self, name, is_buy, sz, limit_px, order_type, reduce_only=False, cloid=None):
        self.calls.append(("order", name, is_buy, sz, limit_px, dict(order_type), reduce_only))
        if self.reply is not None:
            return self.reply
        return RESTING if "trigger" in order_type or order_type["limit"]["tif"] == "Gtc" else FILLED

    def bulk_orders(self, order_requests, grouping="na"):
        self.calls.append(("bulk_orders", len(order_requests), grouping))
        return self.reply if self.reply is not None else BULK_OK


PATHS = {
    "ioc": lambda: ex.place_hl_order(True, 1.0, 100.0, "BTC"),
    "close": lambda: ex.place_hl_order(False, 1.0, 100.0, "BTC", reduce_only=True),
    "trigger": lambda: ex.place_hl_trigger_order(True, 1.0, 95.0, "sl", "BTC"),
    "limit": lambda: ex.place_hl_limit_order("BTC", True, 1.0, 99.0),
    "bracket": lambda: ex.place_hl_bracket_order("BTC", True, 1.0, 99.0, 95.0, 110.0),
}


@pytest.fixture
def operator_with_a_warm_approval(monkeypatch, builder_jobs):
    """Worst case for I1: an address configured and an approval cached."""
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", ADDR)
    for net in ("mainnet", "testnet"):
        builder._cache[(net, ACCT, ADDR)] = {"value": 100, "fetched_at": builder._clock(),
                                             "error": None, "error_at": None}
    sdk = OldSdk()
    reads = []
    monkeypatch.setattr(ex, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(ex, "get_coin_index", lambda c: (0, 2, 0))
    monkeypatch.setattr(ex, "_ioc_cross_price", lambda coin, is_buy, mid: mid * 1.01)
    monkeypatch.setattr(ex, "_make_exchange", lambda: sdk)
    monkeypatch.setattr(ex, "_resolve_identity",
                        lambda: reads.append(1) or ("0x" + "11" * 32, ACCT, None, False))
    monkeypatch.setattr(builder, "_fetch", lambda *a: reads.append("fetch") or 100)
    return sdk, reads


def test_mcp_json_is_byte_identical():
    digest = hashlib.sha256((ROOT / ".mcp.json").read_bytes()).hexdigest()
    assert digest == "8edb54198025f4c1a99bbbf6907e5ef0d1e19d225e7fccb6eabd1f3b282c8ffc"


def test_operator_resolves_mainnet():
    for k in rt.RUNTIME_ENV_VARS:
        assert k not in os.environ
    assert rt.install_kind() == "operator" and rt.is_public() is False
    assert rt.network() == "mainnet" and rt.is_mainnet()
    assert hl_client.HL_API == "https://api.hyperliquid.xyz" == rt.MAINNET_API_URL
    assert ex._api() == rt.MAINNET_API_URL
    assert ex._order_builder() is None
    st = builder.builder_status(ACCT)
    assert st["enabled"] is False and st["reason_code"] == "operator_install"


@pytest.mark.parametrize("path", list(PATHS))
def test_no_builder_kwarg_no_job_one_sdk_call_on_any_order_path(path, operator_with_a_warm_approval,
                                                                builder_jobs):
    sdk, reads = operator_with_a_warm_approval
    out = PATHS[path]()
    assert out["ok"], out
    assert len(sdk.calls) == 1
    assert "builder" not in out, "operator result shape unchanged"
    assert builder_jobs == [] and reads == [], "no identity read, no approval read, no job"
    assert builder.builder_status(ACCT)["counters"] == {"with_builder": 0, "without_builder": 0,
                                                        "retried": 0}


def test_operator_sdk_call_arguments_are_the_old_ones(operator_with_a_warm_approval):
    sdk, _ = operator_with_a_warm_approval
    PATHS["ioc"]()
    PATHS["close"]()
    PATHS["trigger"]()
    PATHS["limit"]()
    PATHS["bracket"]()
    assert sdk.calls[0] == ("order", "BTC", True, 1.0, 101.0, {"limit": {"tif": "Ioc"}}, False)
    assert sdk.calls[1][6] is True                              # close stays reduce-only
    assert sdk.calls[2][5] == {"trigger": {"triggerPx": 95.0, "isMarket": True, "tpsl": "sl"}}
    assert sdk.calls[3] == ("order", "BTC", True, 1.0, 99.0, {"limit": {"tif": "Gtc"}}, False)
    assert sdk.calls[4] == ("bulk_orders", 3, "normalTpsl")


@pytest.mark.parametrize("path", list(PATHS))
def test_operator_reply_naming_builder_is_not_resent(path, operator_with_a_warm_approval):
    sdk, _ = operator_with_a_warm_approval
    sdk.reply = BUILDER_ERR
    out = PATHS[path]()
    assert len(sdk.calls) == 1, "operator: exactly one SDK call whatever the reply says"
    assert out["ok"] is False and "Builder fee" in out["error"]
    assert "builder" not in out


def test_send_order_returns_the_reply_object_unchanged():
    reply = {"status": "err", "response": "builder whatever"}
    seen = []
    result, note = ex._send_order(lambda b: seen.append(b) or reply)
    assert result is reply and note is None and seen == [None]


def test_operator_shaped_profile_resolves_20x_and_oil_share():
    from pathiel.agents.config_store import read_risk_profile
    from services.strategies.desk_share import desk_shares
    cfg = copy.deepcopy(OPERATOR_CONFIG)
    profile = read_risk_profile(cfg)
    assert profile.max_leverage == 20
    assert profile.risk_per_trade_pct == 100
    assert profile.max_open_positions == 1
    assert desk_shares(cfg) == {"oil": 0.9, "metals": 0.1}
    assert rt.install_kind() == "operator" and rt.network() == "mainnet"


def test_trading_block_reason_is_none_for_operator(monkeypatch):
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "")
    monkeypatch.setattr(ex, "_vault_credentials", lambda: None)
    assert ex.trading_block_reason() is None, "operator keeps his own per-function errors"
    out = ex.place_hl_order(True, 1.0, 100.0, "BTC")
    assert out == {"ok": False, "error": "HYPERLIQUID_PRIVATE_KEY not set"}
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    assert ex.trading_block_reason() == ex.READ_ONLY_NO_KEY
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "0x" + "11" * 32)
    assert ex.trading_block_reason() is None


def test_runtime_env_is_cleared_for_every_test_dir():
    for k in rt.RUNTIME_ENV_VARS:
        assert k not in os.environ, k
    # The pop lives in the ROOT conftest, which pytest loads for tests/,
    # services/ and contracts/ alike, before the .env.local loop.
    src = (ROOT / "conftest.py").read_text()
    pop_at = src.index("os.environ.pop(_k, None)")
    load_at = src.index("_ENV_FILE.read_text()")
    assert pop_at < load_at
    for k in rt.RUNTIME_ENV_VARS:
        assert f'"{k}"' in src
    assert "if _k in _RUNTIME_ENV:" in src
