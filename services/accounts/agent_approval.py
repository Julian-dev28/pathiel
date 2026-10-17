"""The approveAgent EIP-712 flow.

Typed-data construction mirrors `hyperliquid.utils.signing.sign_agent` /
`sign_user_signed_action` / `user_signed_payload` from the installed SDK
(`.venv/lib/python3.13/site-packages/hyperliquid/utils/signing.py`) exactly —
same domain, same `HyperliquidTransaction:ApproveAgent` type, same
`signatureChainId`, same mainnet `hyperliquidChain`. Mainnet only: this
product trades mainnet, never testnet, so there is no network switch to get
wrong here.

Two things never happen outside a mocked test, per
docs/overhaul/SWARM_RULES.md section 10 (this wave's extra live-money rule):
`submit_approve_agent` is fully implemented but this agent's own work never
calls it against the real network, and the agent's own prepare/confirm flow
never signs anything itself — the signature always comes from the user's own
wallet, over the typed data `build_typed_data` returns.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_hex

from pathiel.client.hl_client import _http_post_strict

AGENT_NAME = "pathiel"
EXPIRY_MINUTES = 10

# hyperliquid.utils.signing hardcodes this literal for EVERY user-signed
# action (approveAgent, usdSend, withdraw, ...) — it is not derived from the
# real chain the transaction executes on; Hyperliquid accepts it as a fixed
# constant for this class of action. Matching it exactly (rather than, say,
# the real mainnet chain id) is what makes the signature recoverable the same
# way the SDK's own `recover_user_from_user_signed_action` would recover it.
SIGNATURE_CHAIN_ID_HEX = "0x66eee"

PRIMARY_TYPE = "HyperliquidTransaction:ApproveAgent"

# The SDK's sign_agent() passes exactly these 4 fields as the typed struct.
APPROVE_AGENT_FIELDS = [
    {"name": "hyperliquidChain", "type": "string"},
    {"name": "agentAddress", "type": "address"},
    {"name": "agentName", "type": "string"},
    {"name": "nonce", "type": "uint64"},
]

_EIP712_DOMAIN_FIELDS = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def new_nonce_ms() -> int:
    return int(time.time() * 1000)


def build_typed_data(agent_address: str, nonce_ms: int, agent_name: str = AGENT_NAME) -> Dict[str, Any]:
    """The exact object the connected wallet signs with `eth_signTypedData_v4`.

    The SDK's own in-memory action dict carries two extra keys at the point it
    signs (`type`, `signatureChainId` — added by `sign_user_signed_action`'s
    in-place mutation before hashing) that are not part of the declared
    `HyperliquidTransaction:ApproveAgent` struct. EIP-712 struct hashing reads
    fields by name from the type's own field list, so a key that is not in
    that list is never hashed — confirmed empirically (encode_typed_data
    produces an identical signable payload with and without those two extra
    keys present; see services/accounts/tests/test_agent_approval.py). Leaving
    them out here is therefore not a divergence from the SDK's signature at
    all, and it keeps what the wallet shows the user to exactly the 4 fields
    that matter: chain, agent address, agent name, nonce.
    """
    chain_id = int(SIGNATURE_CHAIN_ID_HEX, 16)
    return {
        "domain": {
            "name": "HyperliquidSignTransaction",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": _ZERO_ADDRESS,
        },
        "types": {
            PRIMARY_TYPE: APPROVE_AGENT_FIELDS,
            "EIP712Domain": _EIP712_DOMAIN_FIELDS,
        },
        "primaryType": PRIMARY_TYPE,
        "message": {
            "hyperliquidChain": "Mainnet",
            "agentAddress": agent_address,
            "agentName": agent_name,
            "nonce": nonce_ms,
        },
    }


def recover_signer(typed_data: Dict[str, Any], signature_hex: str) -> str:
    """The checksummed address that produced `signature_hex` over
    `typed_data`. Raises (eth_account's own exception types) on a malformed
    signature — callers must not treat that as "wrong signer", which is a
    distinct, expected case."""
    structured = encode_typed_data(full_message=typed_data)
    return Account.recover_message(structured, signature=signature_hex)


def _split_signature(signature_hex: str) -> Dict[str, Any]:
    """`0x` + 130 hex chars (65 bytes: r, s, v) -> the {r, s, v} triple
    Hyperliquid's /exchange expects — the exact shape
    `hyperliquid.utils.signing.sign_inner` produces (`to_hex` on the raw
    integers, not zero-padded fixed-width hex; HL parses r/s as integers, so
    padding is cosmetic and this matches the SDK byte-for-byte in the only
    way that matters: the integer values)."""
    raw = bytes.fromhex(signature_hex[2:] if signature_hex.startswith("0x") else signature_hex)
    if len(raw) != 65:
        raise ValueError(f"expected a 65-byte signature, got {len(raw)} bytes")
    r_int = int.from_bytes(raw[0:32], "big")
    s_int = int.from_bytes(raw[32:64], "big")
    v_int = raw[64]
    if v_int < 27:
        v_int += 27
    return {"r": to_hex(r_int), "s": to_hex(s_int), "v": v_int}


def submit_approve_agent(agent_address: str, agent_name: str, nonce_ms: int,
                          signature_hex: str) -> Dict[str, Any]:
    """Submit the ALREADY-SIGNED approveAgent action to Hyperliquid's
    `/exchange`. This function signs nothing — the signature was produced by
    the user's own wallet, over exactly this (agent_address, agent_name,
    nonce_ms) via `build_typed_data`; this only relays it.

    The submitted action includes `signatureChainId`/`hyperliquidChain`
    (absent from `build_typed_data`'s message, see its docstring) because
    those ARE exactly what the installed SDK's `approve_agent()` sends in the
    wire payload — `sign_user_signed_action` mutates its action dict in place
    before `_post_action` is called, so the SDK's real `/exchange` payload
    carries both keys. Matching the SDK's wire format here, rather than
    relying on what is cryptographically redundant, is the conservative
    choice for the one call that actually reaches Hyperliquid's servers.

    REAL network call — never invoked by this agent's own work.
    docs/overhaul/SWARM_RULES.md section 10 bans ever submitting an
    `/exchange` action this wave; every test mocks this function (or the HTTP
    layer beneath it via `pathiel.client.hl_client._http_post_strict`).
    """
    action = {
        "type": "approveAgent",
        "agentAddress": agent_address,
        "agentName": agent_name,
        "nonce": nonce_ms,
        "signatureChainId": SIGNATURE_CHAIN_ID_HEX,
        "hyperliquidChain": "Mainnet",
    }
    payload = {
        "action": action,
        "nonce": nonce_ms,
        "signature": _split_signature(signature_hex),
        "vaultAddress": None,
        "expiresAfter": None,
    }
    return _http_post_strict("/exchange", payload)
