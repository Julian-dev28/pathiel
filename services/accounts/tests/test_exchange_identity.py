"""Proves pathiel.client.exchange's credential refactor:

1. resolve_env_identity() resolves to the SAME signer (and the SAME, now
   corrected, account_address) the pre-refactor code built — so the live
   trading loop keeps trading the same account purely off .env.local, with
   zero registry/vault involvement, exactly as before.
2. The accounts vault takes priority over the legacy env globals once a
   trade account is active, and _make_exchange() rebuilds its SDK singleton
   when that identity changes.
3. The old account_address bug (passing the agent's own address, not the
   funds address) is demonstrated and confirmed fixed.
"""
from __future__ import annotations

import pytest
from eth_account import Account as EthAccount

import pathiel.client.exchange as ex
from services.accounts import registry, vault


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Belt-and-suspenders alongside the Exchange/_resolve_perp_dexs mocks in
    the individual tests below — mirrors
    tests/test_mcp_overhaul.py::TestLiveMoneyToolsNeverTouchNetwork's own
    guard. Every test in this file either never reaches the SDK at all, or
    replaces hyperliquid.exchange.Exchange with a fake that cannot reach a
    socket either way; this makes a future edit that removes one of those
    mocks fail loudly instead of quietly hitting the real network."""
    import requests

    def _boom(self, method, url, *a, **kw):
        raise AssertionError(f"test tried to make a real {method} request to {url}")

    monkeypatch.setattr(requests.sessions.Session, "request", _boom)


def _fresh_key() -> str:
    return "0x" + EthAccount.create().key.hex()


# ── resolve_env_identity: the single source of truth for the legacy path ───────

def test_resolve_env_identity_none_when_no_key(monkeypatch):
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "")
    assert ex.resolve_env_identity() is None


def test_resolve_env_identity_agent_mode(monkeypatch):
    agent = EthAccount.create()
    master = EthAccount.create()
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "0x" + agent.key.hex())
    monkeypatch.setattr(ex, "HL_WALLET", agent.address)
    monkeypatch.setattr(ex, "HL_MASTER", master.address)
    monkeypatch.setattr(ex, "IS_AGENT", True)
    monkeypatch.setattr(ex, "HL_ACCOUNT", master.address)

    identity = ex.resolve_env_identity()
    assert identity["main_address"] == master.address
    assert identity["agent_address"] == agent.address
    assert identity["is_agent"] is True


def test_resolve_env_identity_direct_mode_no_separate_agent(monkeypatch):
    wallet = EthAccount.create()
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "0x" + wallet.key.hex())
    monkeypatch.setattr(ex, "HL_WALLET", wallet.address)
    monkeypatch.setattr(ex, "HL_MASTER", "")
    monkeypatch.setattr(ex, "IS_AGENT", False)
    monkeypatch.setattr(ex, "HL_ACCOUNT", wallet.address)

    identity = ex.resolve_env_identity()
    assert identity["main_address"] == wallet.address
    assert identity["agent_address"] is None
    assert identity["is_agent"] is False


def test_env_path_signer_matches_pre_refactor_behavior_with_fake_keys(monkeypatch):
    """The literal proof the task asked for: with fake keys, the signer
    _resolve_identity() hands _make_exchange() is IDENTICAL to what the
    pre-refactor code built directly from PRIVATE_KEY_HEX, and
    account_address is now the funds (main) address rather than the agent's
    own address -- a latent, inert bug (see this module's final report):
    nothing in this repo calls Exchange.market_close(), the only SDK method
    that ever reads account_address, so this was never reachable in live
    trading, but it is the wrong value for that method's documented use."""
    agent = EthAccount.create()
    master = EthAccount.create()
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "0x" + agent.key.hex())
    monkeypatch.setattr(ex, "HL_WALLET", agent.address)
    monkeypatch.setattr(ex, "HL_MASTER", master.address)
    monkeypatch.setattr(ex, "IS_AGENT", True)
    monkeypatch.setattr(ex, "HL_ACCOUNT", master.address)

    # Pre-refactor construction, reproduced literally from the original source.
    old_signer = EthAccount.from_key(ex.PRIVATE_KEY_HEX[2:]).address
    old_account_address = ex.HL_WALLET if ex.IS_AGENT else None

    # Post-refactor construction, via the new resolution path.
    key_hex, main_address, agent_address, is_agent = ex._resolve_identity()
    new_signer = EthAccount.from_key(key_hex[2:]).address
    new_account_address = main_address if is_agent else None

    assert new_signer == old_signer == agent.address, (
        "the signer -- what Hyperliquid actually authorizes trades by -- "
        "must be byte-identical to pre-refactor behavior")
    assert old_account_address == agent.address, "the bug: old code passed the agent's own address"
    assert new_account_address == master.address, "the fix: new code passes the funds address"
    assert old_account_address != new_account_address


# ── vault priority + _has_usable_credential ─────────────────────────────────────

def test_has_usable_credential_false_with_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "")
    assert ex._has_usable_credential() is False


def test_has_usable_credential_true_from_legacy_env_global(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "deadbeef")
    assert ex._has_usable_credential() is True


def test_has_usable_credential_true_from_vault_when_env_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "")
    key_hex = _fresh_key()
    stored = registry.add(label="Vault", main_address="0x" + "a1" * 20, agent_address="0x" + "b2" * 20,
                          mode="trade", source="wallet_sign", verified=True)
    vault.store_key(stored.id, key_hex)

    assert ex._has_usable_credential() is True


def test_resolve_identity_prefers_vault_over_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "0x" + EthAccount.create().key.hex())
    monkeypatch.setattr(ex, "HL_WALLET", "0x" + "9" * 40)
    monkeypatch.setattr(ex, "HL_MASTER", "0x" + "9" * 40)
    monkeypatch.setattr(ex, "IS_AGENT", False)
    monkeypatch.setattr(ex, "HL_ACCOUNT", "0x" + "9" * 40)

    key_hex = _fresh_key()
    main, agent = "0x" + "a1" * 20, "0x" + "b2" * 20
    stored = registry.add(label="Vault", main_address=main, agent_address=agent,
                          mode="trade", source="wallet_sign", verified=True)
    vault.store_key(stored.id, key_hex)

    key, resolved_main, resolved_agent, is_agent = ex._resolve_identity()
    assert resolved_main == main
    assert resolved_agent == agent
    assert key.lower() == key_hex.lower()
    assert is_agent is True


def test_resolve_identity_raises_with_nothing_usable(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "")
    import pytest
    with pytest.raises(RuntimeError, match="HYPERLIQUID_PRIVATE_KEY not set"):
        ex._resolve_identity()


# ── _make_exchange rebuilds on an identity change ───────────────────────────────
#
# The real hyperliquid.exchange.Exchange constructs an Info() internally,
# which fetches meta/spot_meta over the network whenever meta=None (its
# default) -- exactly this module's own real usage. A gate test must stay
# offline, so this swaps in a lightweight stand-in that records what it was
# built with instead of calling the SDK (the same reason
# tests/test_set_leverage_margin_mode.py monkeypatches _make_exchange itself
# wholesale -- here we are testing _make_exchange's OWN logic, so we fake one
# layer deeper: the Exchange class it calls).

class _FakeExchange:
    def __init__(self, wallet, base_url=None, account_address=None, perp_dexs=None):
        self.wallet = wallet
        self.base_url = base_url
        self.account_address = account_address
        self.perp_dexs = perp_dexs


def test_make_exchange_rebuilds_when_identity_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ex, "_exchange_instance", None)
    monkeypatch.setattr(ex, "_exchange_identity_key", None)
    monkeypatch.setattr(ex, "Exchange", _FakeExchange)
    # _resolve_perp_dexs() reads .agent-config.json and, when enable_hip3 is
    # on (the checked-in default), calls out to list the live HIP-3 dex
    # registry -- a gate test must stay offline regardless of that file's
    # ambient state.
    monkeypatch.setattr(ex, "_resolve_perp_dexs", lambda: None)

    key_a, key_b = EthAccount.create(), EthAccount.create()
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "0x" + key_a.key.hex())
    monkeypatch.setattr(ex, "HL_WALLET", key_a.address)
    monkeypatch.setattr(ex, "HL_MASTER", "")
    monkeypatch.setattr(ex, "IS_AGENT", False)
    monkeypatch.setattr(ex, "HL_ACCOUNT", key_a.address)

    first = ex._make_exchange()
    assert first.wallet.address == key_a.address
    cached = ex._make_exchange()
    assert cached is first  # same identity -> same singleton, not rebuilt

    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "0x" + key_b.key.hex())
    monkeypatch.setattr(ex, "HL_WALLET", key_b.address)
    monkeypatch.setattr(ex, "HL_ACCOUNT", key_b.address)

    second = ex._make_exchange()
    assert second is not first
    assert second.wallet.address == key_b.address


def test_make_exchange_passes_funds_address_as_account_address_for_a_vault_agent(monkeypatch, tmp_path):
    """Direct regression test for the account_address fix: a vault-resolved
    agent identity must build the Exchange with account_address == the
    funds (main) address, not the agent's own address."""
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ex, "_exchange_instance", None)
    monkeypatch.setattr(ex, "_exchange_identity_key", None)
    monkeypatch.setattr(ex, "Exchange", _FakeExchange)
    monkeypatch.setattr(ex, "_resolve_perp_dexs", lambda: None)
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "")

    key_hex = _fresh_key()
    main, agent = "0x" + "a1" * 20, "0x" + "b2" * 20
    stored = registry.add(label="Vault", main_address=main, agent_address=agent,
                          mode="trade", source="wallet_sign", verified=True)
    vault.store_key(stored.id, key_hex)

    built = ex._make_exchange()
    assert built.account_address == main
    assert built.account_address != agent
