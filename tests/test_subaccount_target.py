"""Sub-account trading: the opt-in account target in pathiel.client.exchange
and the pathiel.client.subaccount facade and reads.

The plumbing tests run the REAL hyperliquid SDK Exchange (signing included)
with its HTTP post replaced by a recorder and its metadata injected, so
what is asserted is the payload that would have gone to /exchange: the
`vaultAddress` field, and a signature that only verifies with that address
in the action hash. No network, a throwaway key.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest
from eth_account import Account
from hyperliquid.exchange import Exchange as SDKExchange
from hyperliquid.utils.signing import sign_l1_action

from pathiel.agents import bracket
from pathiel.client import exchange as ex
from pathiel.client import subaccount

SUB = "0x3d3b471f6f18f1e3018064112abb9bf6d46cc272"
KEY = "0x" + "11" * 32                       # a throwaway test key, never funded
MASTER = Account.from_key(KEY).address
META = {"universe": [{"name": "BTC", "szDecimals": 5, "maxLeverage": 50}]}
SPOT = {"universe": [], "tokens": []}


class RecordingExchange(SDKExchange):
    """The SDK's own Exchange: real action building and signing, no HTTP."""
    built: List[Dict[str, Any]] = []
    posts: List[Dict[str, Any]] = []

    def __init__(self, **kw: Any) -> None:
        RecordingExchange.built.append(dict(kw))
        kw.pop("perp_dexs", None)
        super().__init__(meta=META, spot_meta=SPOT, **kw)

    def post(self, url_path: str, payload: Any = None) -> Any:
        RecordingExchange.posts.append({"path": url_path, **payload})
        kind = payload["action"]["type"]
        if kind == "order":
            n = len(payload["action"]["orders"])
            return {"status": "ok", "response": {"type": "order", "data": {
                "statuses": [{"resting": {"oid": 100 + i}} for i in range(n)]}}}
        return {"status": "ok", "response": {"type": "default"}}


@pytest.fixture(autouse=True)
def sdk(monkeypatch):
    RecordingExchange.built, RecordingExchange.posts = [], []
    monkeypatch.setattr(ex, "Exchange", RecordingExchange)
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", KEY)
    monkeypatch.setattr(ex, "HL_ACCOUNT", MASTER)
    monkeypatch.setattr(ex, "HL_WALLET", MASTER)
    monkeypatch.setattr(ex, "IS_AGENT", False)
    monkeypatch.setattr(ex, "_vault_credentials", lambda: None)
    monkeypatch.setattr(ex, "_resolve_perp_dexs", lambda: None)
    monkeypatch.setattr(ex, "_exchange_instance", None)
    monkeypatch.setattr(ex, "_exchange_identity_key", None)
    monkeypatch.setattr(ex, "_target_exchanges", {})
    monkeypatch.setattr(ex, "get_coin_index", lambda coin: (0, 5, 1))
    monkeypatch.setattr(ex, "_is_isolated_only", lambda coin: False)
    monkeypatch.setattr(ex, "_main_user_address", lambda: MASTER)
    return RecordingExchange


# ── the default path is unchanged ─────────────────────────────────────────

def test_default_has_no_target_and_builds_the_sdk_client_without_a_vault(sdk):
    assert ex.current_account_target() is None
    assert ex.cancel_orders(7, "BTC") == {"ok": True}
    assert set(sdk.built[0]) == {"wallet", "base_url", "account_address", "perp_dexs", "timeout"}   # as before
    assert sdk.posts[0]["vaultAddress"] is None
    assert ex.resolve_user_address() == MASTER


def test_default_singleton_survives_a_targeted_call(sdk):
    first = ex._make_exchange()
    with ex.account_target(SUB):
        targeted = ex._make_exchange()
        assert targeted is not first and targeted.vault_address == SUB
        assert ex._make_exchange() is targeted                     # one client per target
    assert ex._make_exchange() is first and first.vault_address is None
    assert ex.current_account_target() is None                      # the block restored the default
    assert len(sdk.built) == 2


def test_target_is_cleared_when_the_block_raises():
    with pytest.raises(RuntimeError):
        with ex.account_target(SUB):
            raise RuntimeError("boom")
    assert ex.current_account_target() is None


# ── the target ────────────────────────────────────────────────────────────

def test_cancel_and_leverage_carry_the_vault_address(sdk):
    with ex.account_target(SUB):
        assert ex.cancel_orders(7, "BTC") == {"ok": True}
        assert ex.set_leverage("BTC", 20, is_cross=False, strict=True)["ok"] is True
    kinds = [(p["action"]["type"], p["vaultAddress"]) for p in sdk.posts]
    assert kinds == [("cancel", SUB), ("updateLeverage", SUB)]
    assert "vault_address" in sdk.built[0] and sdk.built[0]["vault_address"] == SUB


def test_the_signature_covers_the_vault_address(sdk):
    with ex.account_target(SUB):
        ex.cancel_orders(7, "BTC")
    p = sdk.posts[0]
    wallet = Account.from_key(KEY)
    with_vault = sign_l1_action(wallet, p["action"], SUB, p["nonce"], None, True)
    without = sign_l1_action(wallet, p["action"], None, p["nonce"], None, True)
    assert p["signature"] == with_vault and p["signature"] != without


def test_bad_targets_raise_before_anything_is_sent(sdk):
    for bad in ("", "0x123", "3d3b471f6f18f1e3018064112abb9bf6d46cc272", SUB + "00"):
        with pytest.raises(ValueError):
            with ex.account_target(bad):
                pass
    with ex.account_target(MASTER):                     # the signer itself is traded without a target
        assert ex.cancel_orders(7, "BTC")["ok"] is False
    assert sdk.posts == []
    with ex.account_target(None):
        assert ex.current_account_target() is None


def test_reads_inside_the_target_ask_for_the_sub_account(sdk, monkeypatch):
    asked: List[Dict[str, Any]] = []

    def post(path: str, payload: Dict[str, Any], timeout: int = 5) -> Any:
        asked.append(payload)
        if payload["type"] == "frontendOpenOrders":
            return [{"coin": "xyz:BRENTOIL", "oid": 5, "reduceOnly": False}]
        return {"assetPositions": [{"position": {"coin": "xyz:BRENTOIL", "szi": "-1.5"}}]}
    monkeypatch.setattr(ex, "_http_post", post)
    sub = subaccount.AccountExchange(SUB)
    user = sub.resolve_user_address()
    assert user == SUB and sub.address == SUB
    assert sub._position_size_on_coin(user, "xyz:BRENTOIL") == -1.5
    assert [o["oid"] for o in sub._resting_orders_for_coin(user, "xyz:BRENTOIL")] == [5]
    assert all(p["user"] == SUB and p["dex"] == "xyz" for p in asked)        # the coin's own dex
    assert ex.resolve_user_address() == MASTER                               # outside: the main account


def test_no_builder_fee_on_a_sub_account(monkeypatch):
    import contracts.runtime as runtime
    monkeypatch.setattr(runtime, "is_public", lambda: True)
    with ex.account_target(SUB):
        assert ex._order_builder() is None


# ── the facade, through the real bracket path ─────────────────────────────

def test_place_bracket_on_the_facade_sends_every_action_to_the_sub_account(sdk, monkeypatch):
    asked: List[Dict[str, Any]] = []

    def post(path: str, payload: Dict[str, Any], timeout: int = 5) -> Any:
        asked.append(payload)
        return [] if payload["type"] == "frontendOpenOrders" else {"assetPositions": []}
    monkeypatch.setattr(ex, "_http_post", post)
    res = bracket.place_bracket("BTC", entry_px=100.0, stop_px=99.0, tp_px=103.0, size=0.5, leverage=20,
                                coin_max_leverage=50, target_offset_frac=0.0, ex=subaccount.AccountExchange(SUB))
    assert res["ok"] and res["placed"]
    assert all(p["user"] == SUB for p in asked) and asked
    assert [(p["action"]["type"], p["vaultAddress"]) for p in sdk.posts] == [("updateLeverage", SUB), ("order", SUB)]
    order = sdk.posts[1]["action"]
    assert order["grouping"] == "normalTpsl" and len(order["orders"]) == 3     # entry, stop, target in one action
    assert [o["r"] for o in order["orders"]] == [False, True, True]
    lev = sdk.posts[0]["action"]
    assert lev["isCross"] is False and lev["leverage"] == 20


def test_the_same_bracket_without_a_facade_is_the_main_account(sdk, monkeypatch):
    monkeypatch.setattr(ex, "_http_post", lambda path, payload, timeout=5:
                        [] if payload["type"] == "frontendOpenOrders" else {"assetPositions": []})
    res = bracket.place_bracket("BTC", entry_px=100.0, stop_px=99.0, tp_px=103.0, size=0.5, leverage=20,
                                coin_max_leverage=50, target_offset_frac=0.0)
    assert res["placed"] and [p["vaultAddress"] for p in sdk.posts] == [None, None]


def test_read_only_facade_refuses_every_signing_call(sdk):
    ro = subaccount.AccountExchange(SUB, read_only=True)
    for name in sorted(subaccount.MUTATING):
        with pytest.raises(subaccount.ReadOnlyError):
            getattr(ro, name)("BTC", 1, 1, 1)
    assert sdk.posts == [] and sdk.built == []
    assert ro.resolve_user_address() == SUB and ro.MIN_ORDER_USD == ex.MIN_ORDER_USD     # reads pass through


def test_mutating_list_names_every_signing_function_in_exchange():
    signing = {n for n in dir(ex) if n.startswith(("place_hl_", "cancel_")) or n == "set_leverage"}
    assert signing <= subaccount.MUTATING


# ── reads ─────────────────────────────────────────────────────────────────

def _post(mode: str, spot: float, dex_value: float, positions=()):
    def post(path: str, payload: Dict[str, Any], timeout: int = 5) -> Any:
        assert payload["user"] == SUB
        t = payload["type"]
        if t == "userAbstraction":
            return mode
        if t == "spotClearinghouseState":
            return {"balances": [{"coin": "USDC", "total": str(spot)}, {"coin": "HYPE", "total": "3"}]}
        if payload.get("dex") == "xyz":
            return {"marginSummary": {"accountValue": str(dex_value)},
                    "assetPositions": [{"position": p} for p in positions]}
        return {"marginSummary": {"accountValue": "0.0"}, "assetPositions": []}
    return post


def test_equity_of_a_unified_account_is_its_spot_usdc():
    # the sub-account as the venue reported it on 2026-11-16: $20.06 spot, perp states all zero
    eq = subaccount.account_equity(SUB, "xyz:BRENTOIL", post=_post("unifiedAccount", 20.06, 0.0))
    assert eq["ok"] and eq["equity"] == 20.06 and eq["mode"] == "unifiedAccount" and eq["positions"] == []


def test_equity_of_a_unified_account_adds_unrealized_pnl():
    pos = [{"coin": "BRENTOIL", "szi": "2.5", "entryPx": "93.0", "unrealizedPnl": "-0.75", "marginUsed": "11.6",
            "leverage": {"type": "isolated", "value": 20}}]
    eq = subaccount.account_equity(SUB, "xyz:BRENTOIL", post=_post("unifiedAccount", 20.06, 11.0, pos))
    assert eq["equity"] == pytest.approx(19.31)
    assert eq["positions"] == [{"coin": "xyz:BRENTOIL", "szi": 2.5, "entry_px": 93.0, "unrealized_pnl": -0.75,
                                "margin_used": 11.6, "leverage": 20}]


def test_equity_in_standard_mode_is_the_coins_own_dex():
    eq = subaccount.account_equity(SUB, "xyz:BRENTOIL", post=_post("disabled", 500.0, 21.0))
    assert eq["equity"] == 21.0 and "xyz dex" in eq["source"]


def test_a_failed_read_is_an_error_never_a_zero_account():
    def boom(path: str, payload: Dict[str, Any], timeout: int = 5) -> Any:
        raise RuntimeError("429")
    eq = subaccount.account_equity(SUB, "xyz:BRENTOIL", post=boom)
    assert eq["ok"] is False and "429" in eq["error"] and "equity" not in eq
    none = subaccount.account_equity(SUB, "xyz:BRENTOIL", post=lambda path, payload, timeout=5: None)
    assert none["ok"] is False


def test_account_role():
    role = subaccount.account_role(SUB, post=lambda p, q, timeout=5: {"role": "subAccount",
                                                                    "data": {"master": MASTER}})
    assert role == {"role": "subAccount", "master": MASTER.lower()}
    assert subaccount.account_role(SUB, post=lambda p, q, timeout=5: {"role": "user"}) == {"role": "user"}
    assert subaccount.account_role(SUB, post=lambda p, q, timeout=5: None)["role"] is None
