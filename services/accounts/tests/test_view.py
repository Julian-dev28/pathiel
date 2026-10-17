"""services.accounts.view — contract-shape conversions and env-account
synthesis."""
from __future__ import annotations

from services.accounts import registry, view


def _clear_env_identity(monkeypatch):
    monkeypatch.setattr("pathiel.client.exchange.PRIVATE_KEY_HEX", "")


def _set_env_identity(monkeypatch, wallet: str, master: str = ""):
    monkeypatch.setattr("pathiel.client.exchange.PRIVATE_KEY_HEX", "deadbeef")
    monkeypatch.setattr("pathiel.client.exchange.HL_WALLET", wallet)
    monkeypatch.setattr("pathiel.client.exchange.HL_MASTER", master)
    is_agent = bool(master and wallet.lower() != master.lower())
    monkeypatch.setattr("pathiel.client.exchange.IS_AGENT", is_agent)
    monkeypatch.setattr("pathiel.client.exchange.HL_ACCOUNT", master if is_agent else wallet)


def test_synthesize_env_account_none_without_env_creds(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _clear_env_identity(monkeypatch)
    assert view.synthesize_env_account(active_id=None) is None


def test_synthesize_env_account_present_and_active_when_nothing_else_is(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _set_env_identity(monkeypatch, wallet="0x" + "9" * 40)
    acct = view.synthesize_env_account(active_id=None)
    assert acct is not None
    assert acct.id == "env"
    assert acct.source == "env"
    assert acct.mode == "trade"
    assert acct.main_address == "0x" + "9" * 40
    assert acct.agent_address is None
    assert acct.is_active is True


def test_synthesize_env_account_not_active_when_a_real_account_is(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _set_env_identity(monkeypatch, wallet="0x" + "9" * 40)
    acct = view.synthesize_env_account(active_id="acc_real")
    assert acct.is_active is False


def test_synthesize_env_account_reflects_agent_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _set_env_identity(monkeypatch, wallet="0x" + "9" * 40, master="0x" + "8" * 40)
    acct = view.synthesize_env_account(active_id=None)
    assert acct.main_address == "0x" + "8" * 40
    assert acct.agent_address == "0x" + "9" * 40


def test_to_contract_account_sets_is_active_from_active_id(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    stored = registry.add(label="Main", main_address="0x" + "1" * 40, agent_address="0x" + "2" * 40,
                          mode="trade", source="wallet_sign", verified=True)
    active = view.to_contract_account(stored, active_id=stored.id)
    inactive = view.to_contract_account(stored, active_id="acc_someone_else")
    assert active.is_active is True
    assert inactive.is_active is False


def test_list_all_accounts_includes_stored_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _set_env_identity(monkeypatch, wallet="0x" + "9" * 40)
    registry.add(label="Stored", main_address="0x" + "1" * 40, agent_address=None,
                mode="watch", source="watch")
    accounts = view.list_all_accounts()
    ids = {a.id for a in accounts}
    assert "env" in ids
    assert any(a.label == "Stored" for a in accounts)


def test_list_all_accounts_omits_env_when_no_env_creds(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _clear_env_identity(monkeypatch)
    registry.add(label="Stored", main_address="0x" + "1" * 40, agent_address=None,
                mode="watch", source="watch")
    accounts = view.list_all_accounts()
    assert all(a.id != "env" for a in accounts)


def test_get_account_or_env_resolves_both_kinds(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _set_env_identity(monkeypatch, wallet="0x" + "9" * 40)
    stored = registry.add(label="Stored", main_address="0x" + "1" * 40, agent_address=None,
                          mode="watch", source="watch")
    assert view.get_account_or_env(stored.id).label == "Stored"
    assert view.get_account_or_env("env").source == "env"
    assert view.get_account_or_env("acc_nope") is None


def test_get_effective_active_account_prefers_real_account(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _set_env_identity(monkeypatch, wallet="0x" + "9" * 40)
    stored = registry.add(label="Real", main_address="0x" + "1" * 40, agent_address=None,
                          mode="watch", source="watch")
    effective = view.get_effective_active_account()
    assert effective.id == stored.id


def test_get_effective_active_account_falls_back_to_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _set_env_identity(monkeypatch, wallet="0x" + "9" * 40)
    effective = view.get_effective_active_account()
    assert effective is not None
    assert effective.source == "env"


def test_get_effective_active_account_none_with_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _clear_env_identity(monkeypatch)
    assert view.get_effective_active_account() is None
