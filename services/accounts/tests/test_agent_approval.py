"""services.accounts.agent_approval — the EIP-712 approveAgent flow.

The whole point of this module is to be byte-for-byte interoperable with the
installed hyperliquid SDK (`hyperliquid.utils.signing`), so every test here
calls the REAL SDK functions as the reference, never a hand-rolled
reimplementation of what the SDK "should" do.
"""
from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_hex
from hyperliquid.utils.signing import (recover_user_from_user_signed_action,
                                       sign_agent, user_signed_payload)

from services.accounts import agent_approval as aa

AGENT_NAME = "pathiel"
SDK_APPROVE_AGENT_FIELDS = [
    {"name": "hyperliquidChain", "type": "string"},
    {"name": "agentAddress", "type": "address"},
    {"name": "agentName", "type": "string"},
    {"name": "nonce", "type": "uint64"},
]


def _sdk_payload(agent_address: str, nonce_ms: int, agent_name: str = AGENT_NAME) -> dict:
    """The exact typed-data payload hyperliquid.utils.signing.sign_agent
    hashes internally — reproduces its own in-place action mutation
    (sign_user_signed_action) without signing, so the comparison is against
    the SDK's real structure, not a description of it."""
    action = {"agentAddress": agent_address, "agentName": agent_name, "nonce": nonce_ms}
    action["signatureChainId"] = aa.SIGNATURE_CHAIN_ID_HEX
    action["hyperliquidChain"] = "Mainnet"
    return user_signed_payload(aa.PRIMARY_TYPE, SDK_APPROVE_AGENT_FIELDS, action)


# ── typed data == the SDK's typed data, hash-for-hash ───────────────────────────

def test_typed_data_hashes_identically_to_the_installed_sdk():
    agent_address = Account.create().address
    nonce_ms = 1792238400000

    ours = aa.build_typed_data(agent_address, nonce_ms, AGENT_NAME)
    sdk = _sdk_payload(agent_address, nonce_ms, AGENT_NAME)

    our_structured = encode_typed_data(full_message=ours)
    sdk_structured = encode_typed_data(full_message=sdk)

    assert our_structured.header == sdk_structured.header
    assert our_structured.body == sdk_structured.body


def test_typed_data_shape_matches_the_contract_fixture_fields():
    typed_data = aa.build_typed_data("0x" + "b2" * 20, 1792238400000, "pathiel")
    assert typed_data["primaryType"] == "HyperliquidTransaction:ApproveAgent"
    assert typed_data["domain"]["chainId"] == int("0x66eee", 16)
    assert typed_data["message"] == {
        "hyperliquidChain": "Mainnet",
        "agentAddress": "0x" + "b2" * 20,
        "agentName": "pathiel",
        "nonce": 1792238400000,
    }


def test_different_nonce_or_agent_produces_a_different_hash():
    a1 = aa.build_typed_data("0x" + "11" * 20, 1000, "pathiel")
    a2 = aa.build_typed_data("0x" + "11" * 20, 1001, "pathiel")
    a3 = aa.build_typed_data("0x" + "22" * 20, 1000, "pathiel")
    h1 = encode_typed_data(full_message=a1).body
    h2 = encode_typed_data(full_message=a2).body
    h3 = encode_typed_data(full_message=a3).body
    assert h1 != h2 != h3 != h1


# ── signing + recovery interop, both directions ──────────────────────────────────

def test_sdk_signed_signature_recovers_correctly_through_our_function():
    """The SDK signs (sign_agent); our recover_signer must name the same
    wallet that signed."""
    wallet = Account.create()
    agent_address = Account.create().address
    nonce_ms = aa.new_nonce_ms()
    action = {"agentAddress": agent_address, "agentName": AGENT_NAME, "nonce": nonce_ms}
    sig = sign_agent(wallet, action, is_mainnet=True)
    # sign_agent (via sign_inner) already returns r/s as "0x..."-prefixed hex
    # strings, not raw ints -- strip the prefix and zero-pad to a full
    # 65-byte signature rather than re-hex-encoding an already-hex string.
    sig_hex = "0x" + sig["r"][2:].zfill(64) + sig["s"][2:].zfill(64) + format(sig["v"], "02x")

    typed_data = aa.build_typed_data(agent_address, nonce_ms, AGENT_NAME)
    recovered = aa.recover_signer(typed_data, sig_hex)

    assert recovered.lower() == wallet.address.lower()


def test_our_signed_signature_recovers_correctly_through_the_sdks_own_function():
    """We build typed data and sign with a fresh wallet (as a browser wallet
    would); the SDK's OWN recover_user_from_user_signed_action must name the
    same wallet."""
    wallet = Account.create()
    agent_address = Account.create().address
    nonce_ms = aa.new_nonce_ms()

    typed_data = aa.build_typed_data(agent_address, nonce_ms, AGENT_NAME)
    structured = encode_typed_data(full_message=typed_data)
    signed = wallet.sign_message(structured)

    # recover_user_from_user_signed_action sets hyperliquidChain itself but,
    # unlike sign_user_signed_action, does NOT set signatureChainId -- it
    # expects the caller's action to already carry it.
    action = {"agentAddress": agent_address, "agentName": AGENT_NAME, "nonce": nonce_ms,
             "signatureChainId": aa.SIGNATURE_CHAIN_ID_HEX}
    sdk_signature = {"r": to_hex(signed["r"]), "s": to_hex(signed["s"]), "v": signed["v"]}
    recovered = recover_user_from_user_signed_action(
        action, sdk_signature, SDK_APPROVE_AGENT_FIELDS, aa.PRIMARY_TYPE, is_mainnet=True)

    assert recovered.lower() == wallet.address.lower()


def test_recover_signer_rejects_a_signature_from_the_wrong_wallet():
    real_wallet = Account.create()
    impostor_wallet = Account.create()
    agent_address = Account.create().address
    nonce_ms = aa.new_nonce_ms()

    typed_data = aa.build_typed_data(agent_address, nonce_ms, AGENT_NAME)
    structured = encode_typed_data(full_message=typed_data)
    signed = impostor_wallet.sign_message(structured)
    sig_hex = to_hex(signed.signature)

    recovered = aa.recover_signer(typed_data, sig_hex)

    assert recovered.lower() != real_wallet.address.lower()
    assert recovered.lower() == impostor_wallet.address.lower()


# ── signature splitting ─────────────────────────────────────────────────────────

def test_split_signature_matches_the_sdks_own_rsv_as_integers():
    wallet = Account.create()
    agent_address = Account.create().address
    nonce_ms = aa.new_nonce_ms()
    typed_data = aa.build_typed_data(agent_address, nonce_ms, AGENT_NAME)
    structured = encode_typed_data(full_message=typed_data)
    signed = wallet.sign_message(structured)

    sdk_triple = {"r": to_hex(signed["r"]), "s": to_hex(signed["s"]), "v": signed["v"]}
    ours = aa._split_signature(to_hex(signed.signature))

    assert int(ours["r"], 16) == int(sdk_triple["r"], 16)
    assert int(ours["s"], 16) == int(sdk_triple["s"], 16)
    assert ours["v"] == sdk_triple["v"]


def test_split_signature_rejects_the_wrong_length():
    with pytest.raises(ValueError):
        aa._split_signature("0x" + "ab" * 10)


def test_split_signature_normalizes_low_v_to_27_28():
    wallet = Account.create()
    agent_address = Account.create().address
    nonce_ms = aa.new_nonce_ms()
    typed_data = aa.build_typed_data(agent_address, nonce_ms, AGENT_NAME)
    structured = encode_typed_data(full_message=typed_data)
    signed = wallet.sign_message(structured)

    raw = bytearray(bytes(signed.signature))
    raw[64] = raw[64] - 27 if raw[64] >= 27 else raw[64]  # force a 0/1-style v
    low_v_hex = "0x" + bytes(raw).hex()

    result = aa._split_signature(low_v_hex)
    assert result["v"] in (27, 28)


# ── submit_approve_agent: the real /exchange payload, never actually sent ──────

def test_submit_approve_agent_payload_matches_the_sdks_wire_format(monkeypatch):
    captured = {}

    def fake_post_strict(path, payload, timeout=5):
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok", "response": {"type": "default"}}

    monkeypatch.setattr(aa, "_http_post_strict", fake_post_strict)

    agent_address = Account.create().address
    nonce_ms = 1792238400000
    sig_hex = "0x" + "11" * 65  # shape only -- never actually submitted

    result = aa.submit_approve_agent(agent_address, AGENT_NAME, nonce_ms, sig_hex)

    assert result == {"status": "ok", "response": {"type": "default"}}
    assert captured["path"] == "/exchange"
    payload = captured["payload"]
    assert payload["action"] == {
        "type": "approveAgent",
        "agentAddress": agent_address,
        "agentName": AGENT_NAME,
        "nonce": nonce_ms,
        "signatureChainId": "0x66eee",
        "hyperliquidChain": "Mainnet",
    }
    assert payload["nonce"] == nonce_ms
    assert set(payload["signature"].keys()) == {"r", "s", "v"}
    assert payload["vaultAddress"] is None
    assert payload["expiresAfter"] is None


def test_submit_approve_agent_propagates_network_failure(monkeypatch):
    from pathiel.client.hl_client import HLRequestError

    def boom(path, payload, timeout=5):
        raise HLRequestError("simulated network failure")

    monkeypatch.setattr(aa, "_http_post_strict", boom)

    with pytest.raises(HLRequestError):
        aa.submit_approve_agent(Account.create().address, AGENT_NAME, 123, "0x" + "11" * 65)


# ── signing on the wallet's own chain (integrator, 2026-10-17) ────────────────

def test_typed_data_uses_the_requested_signing_chain():
    from services.accounts import agent_approval as aa
    td = aa.build_typed_data("0x" + "1" * 40, 1, "pathiel", signature_chain_id=42161)
    assert td["domain"]["chainId"] == 42161
    assert aa.build_typed_data("0x" + "1" * 40, 1, "pathiel")["domain"]["chainId"] == 0x66EEE


def test_submission_carries_the_same_chain_the_wallet_signed(monkeypatch):
    """Hyperliquid recovers the signer with the action's signatureChainId; it
    must equal the domain chainId the wallet signed or the approval fails."""
    from services.accounts import agent_approval as aa
    sent = {}
    monkeypatch.setattr(aa, "_http_post_strict", lambda path, payload: sent.update(payload) or {"status": "ok"})
    aa.submit_approve_agent("0x" + "2" * 40, "pathiel", 7, "0x" + "ab" * 65, signature_chain_id=42161)
    assert sent["action"]["signatureChainId"] == "0xa4b1"
    aa.submit_approve_agent("0x" + "2" * 40, "pathiel", 7, "0x" + "ab" * 65)
    assert sent["action"]["signatureChainId"] == "0x66eee"


def test_a_wallet_signature_on_arbitrum_one_recovers_the_main_address():
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from services.accounts import agent_approval as aa
    wallet = Account.create()
    td = aa.build_typed_data("0x" + "3" * 40, 99, "pathiel", signature_chain_id=42161)
    sig = Account.sign_message(encode_typed_data(full_message=td), private_key=wallet.key).signature.hex()
    assert aa.recover_signer(td, "0x" + sig.removeprefix("0x")).lower() == wallet.address.lower()
