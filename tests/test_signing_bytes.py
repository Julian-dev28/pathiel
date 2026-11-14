"""Order signing bytes do not move when msgpack moves.

The Hyperliquid SDK hashes every action as msgpack.packb(action) + nonce +
vault flag (hyperliquid/utils/signing.py) before signing it. A msgpack upgrade
that changed the encoding would silently break every order, cancel and
approval. These digests were taken on msgpack 1.2.2 and checked identical on
1.2.3 (2026-11-14, the GHSA-j586-36cw-2gc2 fix, a decoder-only memory leak);
a future bump that changes them fails here, in the pull request.
"""
from __future__ import annotations

import hashlib

import msgpack

NONCE = 1794400000000
ACTIONS = [
    {"type": "order", "orders": [{"a": 110003, "b": True, "p": "93.4", "s": "11.99", "r": False,
      "t": {"limit": {"tif": "Gtc"}}, "c": "0x" + "ab" * 16}], "grouping": "normalTpsl",
      "builder": {"b": "0x2c2e4eff574b3fd9879adef9c5cac6fd7c916985", "f": 10}},
    {"type": "order", "orders": [{"a": 0, "b": False, "p": "92.33", "s": "11.99", "r": True,
      "t": {"trigger": {"isMarket": True, "triggerPx": "92.33", "tpsl": "sl"}}}], "grouping": "na"},
    {"type": "cancel", "cancels": [{"a": 110003, "o": 604565189696}]},
    {"type": "updateLeverage", "asset": 110003, "isCross": False, "leverage": 20},
    {"type": "approveAgent", "agentAddress": "0x" + "12" * 20, "agentName": "pathiel-plugin", "nonce": NONCE},
    {"type": "batchModify", "modifies": [{"oid": 1, "order": {"a": 1, "b": True, "p": "1.5", "s": "0.0029",
                                                             "r": False, "t": {"limit": {"tif": "Ioc"}}}}]},
]
GOLDEN = [
    "3b362d72b0224cfb14525bc35df90263f4778d2e62a1c37a674bf368c986fe2e",
    "b8cd292d4bf6bd342a27b3d515966aecaaf324464cf27df554db8ffe62af60d4",
    "ce9d484509cb0cff67a422fda0950af9052265245318435f573d92eba7eac086",
    "ed75b98dd52a91c4e5c041f657b415af67f79a4cc50c6520e87e34a65bbbf455",
    "80901a1c37d929b67021ab3ec1388f6507e87fcbb60644bcc84a4d8b6141d8b6",
    "c1ced35ed7780276f2b8196a12928eae056b286beec654791f636dabbd16e384",
]


def _digest(action) -> str:
    return hashlib.sha256(msgpack.packb(action) + NONCE.to_bytes(8, "big") + b"\x00").hexdigest()


def test_signing_bytes_match_the_golden_digests():
    assert [_digest(a) for a in ACTIONS] == GOLDEN


def test_the_sdk_still_hashes_with_msgpack_packb():
    """If the SDK stops using packb this test, not production, finds out."""
    import inspect

    from hyperliquid.utils import signing
    src = inspect.getsource(signing.action_hash)
    assert "msgpack.packb(action)" in src
