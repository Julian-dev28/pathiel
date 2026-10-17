"""services.accounts.credentials — get_trading_credentials()'s priority
(active trade account, else env fallback) and its cache invalidation."""
from __future__ import annotations

from eth_account import Account as EthAccount

from services.accounts import credentials, registry, vault


def _fresh_key() -> str:
    return "0x" + EthAccount.create().key.hex()


def test_returns_none_with_nothing_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("HYPERLIQUID_PRIVATE_KEY", raising=False)
    credentials.invalidate_cache()
    assert credentials.get_trading_credentials() is None


def test_active_trade_account_wins_over_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    # A legacy env identity is ALSO present...
    monkeypatch.setattr("pathiel.client.exchange.PRIVATE_KEY_HEX", _fresh_key())
    monkeypatch.setattr("pathiel.client.exchange.HL_WALLET", "0x" + "9" * 40)
    monkeypatch.setattr("pathiel.client.exchange.HL_MASTER", "0x" + "9" * 40)
    monkeypatch.setattr("pathiel.client.exchange.IS_AGENT", False)
    monkeypatch.setattr("pathiel.client.exchange.HL_ACCOUNT", "0x" + "9" * 40)

    # ...but a vault trade account is active, and must win.
    key_hex = _fresh_key()
    main = "0x" + "a1" * 20
    agent = "0x" + "b2" * 20
    stored = registry.add(label="Vault", main_address=main, agent_address=agent,
                          mode="trade", source="wallet_sign", agent_name="pathiel", verified=True)
    vault.store_key(stored.id, key_hex)
    credentials.invalidate_cache()

    creds = credentials.get_trading_credentials()
    assert creds is not None
    assert creds.source == "wallet_sign"
    assert creds.main_address == main
    assert creds.agent_address == agent
    assert creds.private_key.lower() == key_hex.lower()


def test_watch_mode_active_account_falls_back_to_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    env_key = _fresh_key()
    monkeypatch.setattr("pathiel.client.exchange.PRIVATE_KEY_HEX", env_key)
    monkeypatch.setattr("pathiel.client.exchange.HL_WALLET", "0x" + "9" * 40)
    monkeypatch.setattr("pathiel.client.exchange.HL_MASTER", "")
    monkeypatch.setattr("pathiel.client.exchange.IS_AGENT", False)
    monkeypatch.setattr("pathiel.client.exchange.HL_ACCOUNT", "0x" + "9" * 40)

    registry.add(label="Whale I follow", main_address="0x" + "c3" * 20, agent_address=None,
                mode="watch", source="watch")
    credentials.invalidate_cache()

    creds = credentials.get_trading_credentials()
    assert creds is not None
    assert creds.source == "env"
    assert creds.main_address == "0x" + "9" * 40
    assert creds.private_key == env_key


def test_trade_account_with_missing_vault_key_falls_back_to_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    env_key = _fresh_key()
    monkeypatch.setattr("pathiel.client.exchange.PRIVATE_KEY_HEX", env_key)
    monkeypatch.setattr("pathiel.client.exchange.HL_WALLET", "0x" + "9" * 40)
    monkeypatch.setattr("pathiel.client.exchange.HL_MASTER", "")
    monkeypatch.setattr("pathiel.client.exchange.IS_AGENT", False)
    monkeypatch.setattr("pathiel.client.exchange.HL_ACCOUNT", "0x" + "9" * 40)

    # Registered as trade, but never actually given a vault key (corrupt or
    # manually edited registry) -- must degrade to env, not crash or silently
    # "succeed" with no key.
    registry.add(label="Broken", main_address="0x" + "d4" * 20, agent_address="0x" + "e5" * 20,
                mode="trade", source="wallet_sign", agent_name="pathiel", verified=True)
    credentials.invalidate_cache()

    creds = credentials.get_trading_credentials()
    assert creds is not None
    assert creds.source == "env"


def test_cache_invalidates_when_registry_mutates(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("HYPERLIQUID_PRIVATE_KEY", raising=False)
    monkeypatch.setattr("pathiel.client.exchange.PRIVATE_KEY_HEX", "")
    credentials.invalidate_cache()

    assert credentials.get_trading_credentials() is None

    key_hex = _fresh_key()
    main, agent = "0x" + "a1" * 20, "0x" + "b2" * 20
    stored = registry.add(label="New", main_address=main, agent_address=agent,
                          mode="trade", source="wallet_sign", verified=True)
    vault.store_key(stored.id, key_hex)

    # No explicit invalidate_cache() call -- the registry write's own mtime
    # change must be enough.
    creds = credentials.get_trading_credentials()
    assert creds is not None
    assert creds.main_address == main


def test_cache_invalidates_when_active_account_switches(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("pathiel.client.exchange.PRIVATE_KEY_HEX", "")
    credentials.invalidate_cache()

    key_a, key_b = _fresh_key(), _fresh_key()
    acc_a = registry.add(label="A", main_address="0x" + "a1" * 20, agent_address="0x" + "b2" * 20,
                         mode="trade", source="wallet_sign", verified=True)
    vault.store_key(acc_a.id, key_a)
    acc_b = registry.add(label="B", main_address="0x" + "c3" * 20, agent_address="0x" + "d4" * 20,
                         mode="trade", source="wallet_sign", verified=True, activate=False)
    vault.store_key(acc_b.id, key_b)

    first = credentials.get_trading_credentials()
    assert first.main_address == "0x" + "a1" * 20

    registry.set_active(acc_b.id)

    second = credentials.get_trading_credentials()
    assert second.main_address == "0x" + "c3" * 20
    assert second.private_key.lower() == key_b.lower()
