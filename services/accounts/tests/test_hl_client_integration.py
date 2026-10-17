"""pathiel.client.hl_client's accounts-aware pieces:
resolve_user_address()'s new "active account first" priority, and
fetch_account_state()'s new withdrawable/withdrawable_aggregated fields.

tests/test_cleanup.py::test_resolve_user_address already pins the pure env
fallback (no registry involved); this file covers the new registry-aware
behavior layered in front of it.
"""
from __future__ import annotations

from eth_account import Account as EthAccount

from pathiel.client import hl_client
from services.accounts import registry, vault


def _fresh_key() -> str:
    return "0x" + EthAccount.create().key.hex()


def test_resolve_user_address_prefers_active_trade_account(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", "0xENVMASTER")
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", "0xENVWALLET")

    key_hex = _fresh_key()
    main, agent = "0x" + "a1" * 20, "0x" + "b2" * 20
    stored = registry.add(label="Vault", main_address=main, agent_address=agent,
                          mode="trade", source="wallet_sign", verified=True)
    vault.store_key(stored.id, key_hex)

    assert hl_client.resolve_user_address() == main


def test_resolve_user_address_ignores_a_watch_mode_active_account(monkeypatch, tmp_path):
    """A watch account must never redirect fill/order lookups away from
    whoever is actually trading -- see resolve_user_address's own
    docstring for why."""
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", "0xENVMASTER")
    monkeypatch.delenv("HYPERLIQUID_WALLET_ADDRESS", raising=False)

    registry.add(label="Whale", main_address="0x" + "c3" * 20, agent_address=None,
                mode="watch", source="watch")

    assert hl_client.resolve_user_address() == "0xENVMASTER"


def test_resolve_user_address_falls_back_to_env_with_empty_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", "0xMASTER")
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", "0xWALLET")
    assert hl_client.resolve_user_address() == "0xMASTER"
    monkeypatch.delenv("HYPERLIQUID_MASTER_ADDRESS")
    assert hl_client.resolve_user_address() == "0xWALLET"


def test_fetch_account_state_exposes_withdrawable(monkeypatch):
    def fake_post(path, payload, timeout=5):
        if payload.get("type") == "clearinghouseState":
            return {"marginSummary": {"accountValue": "100.0", "totalNtlPos": "0",
                                      "totalMarginUsed": "10.0"},
                   "withdrawable": "42.5", "assetPositions": []}
        if payload.get("type") == "spotClearinghouseState":
            return {"balances": []}
        return None

    monkeypatch.setattr(hl_client, "_http_post", fake_post)

    state = hl_client.fetch_account_state("0xUSER", include_hip3=False)

    assert state["withdrawable"] == 42.5
    assert state["withdrawable_aggregated"] == 42.5
    assert state["available"] == 90.0  # unaffected, still equity - margin_used
