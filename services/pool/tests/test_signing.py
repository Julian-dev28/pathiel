"""services/pool/signing.py: EIP-191 signatures and identity, offline, with
keys made in the test and dropped. No test key's hex text may appear in any
return value, printed output or log record."""
from __future__ import annotations

import logging

import pytest

import contracts.pool as pool
from services.pool import signing

MESSAGE = pool.statement_text("join", "0x" + "ab" * 20, "0x" + "ab" * 20, "2026-11-12T12:00:00Z")


def _key_hex(acct) -> str:
    return acct.key.hex()


# ── sign_message / recover_signer round trip ────────────────────────────────

def test_sign_message_round_trips_through_recover_signer(new_account):
    acct = new_account()
    sig = signing.sign_message(MESSAGE, acct.key)
    assert sig.startswith("0x") and len(sig) == 132 and sig[2:] == sig[2:].lower()
    assert pool.recover_signer(MESSAGE, sig) == acct.address.lower()


def test_sign_message_accepts_the_key_with_or_without_0x(new_account):
    acct = new_account()
    key_hex = _key_hex(acct)
    with_0x = key_hex if key_hex.startswith("0x") else "0x" + key_hex
    without_0x = key_hex.removeprefix("0x")
    assert signing.sign_message(MESSAGE, with_0x) == signing.sign_message(MESSAGE, without_0x)


# ── identity() ───────────────────────────────────────────────────────────────

def _patch_resolve_identity(monkeypatch, key_hex, main, agent, is_agent):
    import pathiel.client.exchange as exchange

    def fake():
        return key_hex, main, agent, is_agent
    monkeypatch.setattr(exchange, "_resolve_identity", fake)


def test_identity_master_setup(monkeypatch, new_account):
    acct = new_account()
    _patch_resolve_identity(monkeypatch, _key_hex(acct), acct.address, None, False)
    account, signer = signing.identity()
    assert account == acct.address.lower() and signer == acct.address.lower()


def test_identity_agent_setup(monkeypatch, new_account):
    main, agent = new_account(), new_account()
    _patch_resolve_identity(monkeypatch, _key_hex(agent), main.address, agent.address, True)
    account, signer = signing.identity()
    assert account == main.address.lower() and signer == agent.address.lower()
    assert account != signer


def test_identity_no_main_address_falls_back_to_the_signer(monkeypatch, new_account):
    acct = new_account()
    _patch_resolve_identity(monkeypatch, _key_hex(acct), "", None, False)
    account, signer = signing.identity()
    assert account == signer == acct.address.lower()


def test_identity_raises_no_key_on_runtime_error(monkeypatch):
    import pathiel.client.exchange as exchange

    def boom():
        raise RuntimeError("HYPERLIQUID_PRIVATE_KEY not set")
    monkeypatch.setattr(exchange, "_resolve_identity", boom)
    with pytest.raises(signing.NoKey):
        signing.identity()


def test_identity_raises_no_key_on_an_empty_key(monkeypatch):
    import pathiel.client.exchange as exchange
    monkeypatch.setattr(exchange, "_resolve_identity", lambda: ("", "0x" + "ab" * 20, None, False))
    with pytest.raises(signing.NoKey):
        signing.identity()


def test_identity_never_leaks_the_key(monkeypatch, new_account, caplog):
    acct = new_account()
    key_hex = _key_hex(acct)
    _patch_resolve_identity(monkeypatch, key_hex, acct.address, None, False)
    with caplog.at_level(logging.DEBUG):
        account, signer = signing.identity()
    needle = key_hex.removeprefix("0x").lower()
    assert needle not in repr((account, signer)).lower()
    assert all(needle not in r.getMessage().lower() for r in caplog.records)


# ── sign_configured() ────────────────────────────────────────────────────────

def test_sign_configured_signs_with_the_resolved_key(monkeypatch, new_account):
    acct = new_account()
    _patch_resolve_identity(monkeypatch, _key_hex(acct), acct.address, None, False)
    signer, signature = signing.sign_configured(MESSAGE)
    assert signer == acct.address.lower()
    assert pool.recover_signer(MESSAGE, signature) == signer


def test_sign_configured_raises_no_key(monkeypatch):
    import pathiel.client.exchange as exchange
    monkeypatch.setattr(exchange, "_resolve_identity", lambda: ("", "", None, False))
    with pytest.raises(signing.NoKey):
        signing.sign_configured(MESSAGE)


# ── sign_prompted() ───────────────────────────────────────────────────────────

def test_sign_prompted_refuses_a_non_tty(capsys):
    with pytest.raises(pool.PoolError) as e:
        signing.sign_prompted(MESSAGE, "0x" + "ab" * 20, getpass_fn=lambda prompt: "should not be called",
                              isatty=lambda: False)
    assert e.value.code == "no_tty"
    assert capsys.readouterr() == ("", "")   # never calls print


def test_sign_prompted_refuses_nothing_typed(capsys):
    with pytest.raises(pool.PoolError) as e:
        signing.sign_prompted(MESSAGE, "0x" + "ab" * 20, getpass_fn=lambda prompt: "", isatty=lambda: True)
    assert e.value.code == "no_tty"
    assert capsys.readouterr() == ("", "")


def test_sign_prompted_refuses_garbage_input(new_account, capsys):
    acct = new_account()
    with pytest.raises(pool.PoolError) as e:
        signing.sign_prompted(MESSAGE, acct.address, getpass_fn=lambda prompt: "not-a-key",
                              isatty=lambda: True)
    assert e.value.code == "wrong_key"
    assert capsys.readouterr() == ("", "")


def test_sign_prompted_refuses_the_wrong_key(new_account, capsys):
    acct, other = new_account(), new_account()
    seen_prompts = []

    def fake_getpass(prompt):
        seen_prompts.append(prompt)
        return _key_hex(other)
    with pytest.raises(pool.PoolError) as e:
        signing.sign_prompted(MESSAGE, acct.address, getpass_fn=fake_getpass, isatty=lambda: True)
    assert e.value.code == "wrong_key"
    assert seen_prompts == [signing.HIDDEN_PROMPT]
    assert capsys.readouterr() == ("", "")


def test_sign_prompted_signs_with_the_matching_master_key(new_account, capsys):
    acct = new_account()
    signer, signature = signing.sign_prompted(MESSAGE, acct.address, getpass_fn=lambda prompt: _key_hex(acct),
                                              isatty=lambda: True)
    assert signer == acct.address.lower()
    assert pool.recover_signer(MESSAGE, signature) == signer
    assert capsys.readouterr() == ("", "")


def test_sign_prompted_never_leaks_the_key(new_account, capsys, caplog):
    acct = new_account()
    key_hex = _key_hex(acct)
    with caplog.at_level(logging.DEBUG):
        signer, signature = signing.sign_prompted(MESSAGE, acct.address, getpass_fn=lambda prompt: key_hex,
                                                  isatty=lambda: True)
    needle = key_hex.removeprefix("0x").lower()
    out, err = capsys.readouterr()
    assert needle not in (out + err + signer + signature).lower()
    assert all(needle not in r.getMessage().lower() for r in caplog.records)
