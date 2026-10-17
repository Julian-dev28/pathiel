"""services.accounts.vault — encrypted-at-rest key storage.

PATHIEL_VAULT_FAST_KDF=1 is set for the whole suite by conftest.py's
autouse _isolated_state fixture, so every encrypt/decrypt here is a real
Account.encrypt/Account.decrypt round trip through a cheap KDF rather than
the slow scrypt default — see vault.py's module docstring.
"""
from __future__ import annotations

import json
import os
import stat

import pytest
from eth_account import Account

from services.accounts import env, vault


def _fresh_key() -> str:
    return "0x" + Account.create().key.hex()


def test_store_and_load_round_trips(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    key_hex = _fresh_key()
    vault.store_key("acc_1", key_hex)
    loaded = vault.load_key("acc_1")
    assert loaded is not None
    assert loaded.lower() == key_hex.lower()


def test_load_missing_account_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    assert vault.load_key("does_not_exist") is None


def test_key_file_is_chmod_600(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    vault.store_key("acc_perm", _fresh_key())
    mode = vault.key_file_mode("acc_perm")
    assert mode == 0o600


def test_vault_secret_file_is_chmod_600_and_created_once(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("PATHIEL_VAULT_PASSPHRASE", raising=False)
    vault.store_key("acc_a", _fresh_key())
    secret_path = env.vault_secret_path()
    assert os.path.exists(secret_path)
    mode = stat.S_IMODE(os.stat(secret_path).st_mode)
    assert mode == 0o600
    first = open(secret_path).read()

    # A second key, same process: the secret is created ONCE and reused, so
    # both keys stay decryptable with the same passphrase.
    vault.store_key("acc_b", _fresh_key())
    second = open(secret_path).read()
    assert first == second


def test_decrypting_without_the_right_passphrase_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PATHIEL_VAULT_PASSPHRASE", "correct-horse")
    key_hex = _fresh_key()
    vault.store_key("acc_wrongpw", key_hex)

    monkeypatch.setenv("PATHIEL_VAULT_PASSPHRASE", "totally-different")
    with pytest.raises(Exception):
        vault.load_key("acc_wrongpw")


def test_explicit_passphrase_env_var_overrides_machine_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PATHIEL_VAULT_PASSPHRASE", "my-explicit-passphrase")
    key_hex = _fresh_key()
    vault.store_key("acc_explicit", key_hex)
    # No machine secret file should have been created at all.
    assert not os.path.exists(env.vault_secret_path())
    assert vault.load_key("acc_explicit").lower() == key_hex.lower()


def test_store_key_overwrites_existing_key_file(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    first_key = _fresh_key()
    second_key = _fresh_key()
    vault.store_key("acc_overwrite", first_key)
    vault.store_key("acc_overwrite", second_key)
    loaded = vault.load_key("acc_overwrite")
    assert loaded.lower() == second_key.lower()
    assert loaded.lower() != first_key.lower()


def test_delete_key_removes_the_file_and_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    vault.store_key("acc_del", _fresh_key())
    assert vault.load_key("acc_del") is not None
    vault.delete_key("acc_del")
    assert vault.load_key("acc_del") is None
    vault.delete_key("acc_del")  # must not raise on an already-gone key


def test_stored_file_is_keystore_json_never_plaintext(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    key_hex = _fresh_key()
    vault.store_key("acc_shape", key_hex)
    raw = open(vault.key_file_path("acc_shape")).read()
    assert key_hex[2:].lower() not in raw.lower()
    data = json.loads(raw)
    assert "crypto" in data and "ciphertext" in data["crypto"]
