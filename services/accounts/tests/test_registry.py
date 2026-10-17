"""services.accounts.registry — the accounts.json CRUD layer."""
from __future__ import annotations

import os
import stat

import pytest

from services.accounts import env, registry


def test_empty_registry_has_no_accounts_and_no_active(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    assert registry.list_accounts() == []
    assert registry.get_active() is None
    assert registry.get_active_id() is None
    assert registry.rev() == 0


def test_add_creates_and_activates_the_first_account(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    stored = registry.add(label="Main", main_address="0x" + "A" * 40, agent_address="0x" + "B" * 40,
                          mode="trade", source="wallet_sign", agent_name="pathiel", verified=True)
    assert stored.id.startswith("acc_")
    assert stored.main_address == "0x" + "a" * 40      # lowercased on write
    assert stored.agent_address == "0x" + "b" * 40
    assert registry.get_active_id() == stored.id
    assert registry.get_active() == stored


def test_registry_file_is_chmod_600(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    registry.add(label="Main", main_address="0x" + "1" * 40, agent_address=None,
                 mode="watch", source="watch")
    mode = stat.S_IMODE(os.stat(env.registry_path()).st_mode)
    assert mode == 0o600


def test_second_account_does_not_steal_activation_unless_asked(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    first = registry.add(label="A", main_address="0x" + "1" * 40, agent_address=None,
                         mode="watch", source="watch", activate=False)
    second = registry.add(label="B", main_address="0x" + "2" * 40, agent_address=None,
                          mode="watch", source="watch", activate=False)
    # Neither asked to activate, but the registry started empty, so the
    # FIRST one ever added still becomes active -- there must always be an
    # active account once at least one exists, unless explicitly cleared.
    assert registry.get_active_id() == first.id
    assert registry.get(second.id) is not None


def test_add_default_never_steals_activation_from_an_existing_account(monkeypatch, tmp_path):
    """The actual default (no activate= kwarg at all) — what every real
    caller in services.accounts.api relies on. A second, third, ... account
    added the normal way must never switch away from whatever is already
    active, matching wallet-ui's own "only the very first one auto-activates"
    rule."""
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    first = registry.add(label="First", main_address="0x" + "1" * 40, agent_address=None,
                         mode="watch", source="watch")
    assert registry.get_active_id() == first.id

    second = registry.add(label="Second", main_address="0x" + "2" * 40, agent_address=None,
                          mode="trade", source="wallet_sign", agent_name="pathiel", verified=True)
    third = registry.add(label="Third", main_address="0x" + "3" * 40, agent_address=None,
                         mode="watch", source="watch")

    assert registry.get_active_id() == first.id
    assert registry.get(second.id) is not None
    assert registry.get(third.id) is not None


def test_add_default_activates_when_nothing_else_is_active(monkeypatch, tmp_path):
    """After every account is removed, the NEXT one added should become
    active again -- "nothing active" triggers it, not literally "the first
    one ever"."""
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    first = registry.add(label="First", main_address="0x" + "1" * 40, agent_address=None,
                         mode="watch", source="watch")
    registry.delete(first.id)
    assert registry.get_active_id() is None

    second = registry.add(label="Second", main_address="0x" + "2" * 40, agent_address=None,
                          mode="watch", source="watch")
    assert registry.get_active_id() == second.id


def test_add_with_activate_true_switches_the_active_account(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    first = registry.add(label="A", main_address="0x" + "1" * 40, agent_address=None,
                         mode="watch", source="watch")
    second = registry.add(label="B", main_address="0x" + "2" * 40, agent_address=None,
                          mode="watch", source="watch", activate=True)
    assert registry.get_active_id() == second.id
    assert first.id != second.id


def test_set_active_switches_and_unknown_id_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    a = registry.add(label="A", main_address="0x" + "1" * 40, agent_address=None,
                     mode="watch", source="watch")
    b = registry.add(label="B", main_address="0x" + "2" * 40, agent_address=None,
                     mode="watch", source="watch", activate=False)
    assert registry.get_active_id() == a.id
    registry.set_active(b.id)
    assert registry.get_active_id() == b.id
    with pytest.raises(KeyError):
        registry.set_active("acc_does_not_exist")


def test_clear_active_leaves_accounts_but_nothing_active(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    registry.add(label="A", main_address="0x" + "1" * 40, agent_address=None,
                mode="watch", source="watch")
    registry.clear_active()
    assert registry.get_active_id() is None
    assert len(registry.list_accounts()) == 1


def test_rename_updates_label_and_truncates_to_48_chars(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    acct = registry.add(label="Old", main_address="0x" + "1" * 40, agent_address=None,
                        mode="watch", source="watch")
    long_label = "x" * 100
    updated = registry.rename(acct.id, long_label)
    assert updated.label == "x" * 48
    assert registry.get(acct.id).label == "x" * 48


def test_rename_unknown_id_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    with pytest.raises(KeyError):
        registry.rename("acc_nope", "New label")


def test_delete_removes_account_and_its_vault_key(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    from eth_account import Account
    from services.accounts import vault
    key_hex = "0x" + Account.create().key.hex()
    acct = registry.add(label="A", main_address="0x" + "1" * 40, agent_address="0x" + "2" * 40,
                        mode="trade", source="wallet_sign", verified=True)
    vault.store_key(acct.id, key_hex)
    assert vault.load_key(acct.id) is not None

    registry.delete(acct.id)

    assert registry.get(acct.id) is None
    assert vault.load_key(acct.id) is None


def test_deleting_the_active_account_promotes_the_next_one(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    a = registry.add(label="A", main_address="0x" + "1" * 40, agent_address=None,
                     mode="watch", source="watch")
    b = registry.add(label="B", main_address="0x" + "2" * 40, agent_address=None,
                     mode="watch", source="watch", activate=False)
    assert registry.get_active_id() == a.id

    registry.delete(a.id)

    assert registry.get_active_id() == b.id


def test_deleting_the_last_account_leaves_nothing_active(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    a = registry.add(label="A", main_address="0x" + "1" * 40, agent_address=None,
                     mode="watch", source="watch")
    registry.delete(a.id)
    assert registry.get_active_id() is None
    assert registry.list_accounts() == []


def test_delete_unknown_id_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    with pytest.raises(KeyError):
        registry.delete("acc_nope")


def test_generation_bumps_on_every_mutation(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    g0 = registry.generation()
    acct = registry.add(label="A", main_address="0x" + "1" * 40, agent_address=None,
                        mode="watch", source="watch")
    g1 = registry.generation()
    registry.rename(acct.id, "B")
    g2 = registry.generation()
    registry.delete(acct.id)
    g3 = registry.generation()
    assert g0 < g1 < g2 < g3


def test_rev_increments_in_the_persisted_file(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    assert registry.rev() == 0
    registry.add(label="A", main_address="0x" + "1" * 40, agent_address=None,
                mode="watch", source="watch")
    assert registry.rev() == 1


def test_malformed_registry_file_is_treated_as_empty_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    os.makedirs(env.accounts_dir(), exist_ok=True)
    with open(env.registry_path(), "w") as fh:
        fh.write("{ this is not valid json")
    assert registry.list_accounts() == []
    assert registry.get_active() is None


def test_list_accounts_skips_an_unreadable_entry_without_crashing(monkeypatch, tmp_path):
    import json
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    os.makedirs(env.accounts_dir(), exist_ok=True)
    with open(env.registry_path(), "w") as fh:
        json.dump({"version": 1, "rev": 1, "active_id": None,
                  "accounts": [{"id": "acc_bad", "unexpected_shape": True}]}, fh)
    assert registry.list_accounts() == []
