"""No route ever returns key material, and no key material ever reaches a
log line. Every response body and every log record captured during a full
run of every flow is scanned for every private key the test ever generated
or supplied, in every case variant a careless `str()`, `repr()`, or f-string
could have produced.
"""
from __future__ import annotations

import logging

from eth_account import Account as EthAccount
from eth_account.messages import encode_typed_data

from services.accounts import agent_approval, hl_verify, registry, vault


def _needles(key_hex: str) -> list:
    raw = key_hex[2:] if key_hex.startswith("0x") else key_hex
    return [raw.lower(), raw.upper(), f"0x{raw.lower()}", f"0x{raw.upper()}"]


def _assert_clean(text: str, needles: list, where: str) -> None:
    lowered = text.lower()
    for needle in needles:
        assert needle.lower() not in lowered, f"key material leaked in {where}: matched {needle[:10]}..."


def test_no_route_response_or_log_line_ever_contains_a_private_key(clients, monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(agent_approval, "submit_approve_agent", lambda *a, **kw: {"status": "ok"})
    monkeypatch.setattr(hl_verify, "is_agent_approved", lambda *a, **kw: True)
    caplog.set_level(logging.DEBUG)

    c = clients.loopback()
    all_needles: list = []
    all_responses: list = []

    # 1. wallet_sign: the server generates the agent's private key itself.
    main_wallet = EthAccount.create()
    prep = c.post("/api/accounts/agent/prepare",
                  json={"main_address": main_wallet.address, "label": "A"})
    all_responses.append(prep)
    prep_body = prep.json()
    structured = encode_typed_data(full_message=prep_body["typed_data"])
    signature = "0x" + main_wallet.sign_message(structured).signature.hex()

    conf = c.post("/api/accounts/agent/confirm",
                  json={"request_id": prep_body["request_id"], "signature": signature})
    all_responses.append(conf)
    wallet_sign_account = conf.json()
    # The server-generated agent key is only recoverable by decrypting the
    # vault file directly -- that is the one legitimate reader.
    agent_key_hex = vault.load_key(wallet_sign_account["id"])
    assert agent_key_hex is not None
    all_needles += _needles(agent_key_hex)

    # 2. api_key import: the CALLER supplies a private key.
    main2 = EthAccount.create()
    agent2 = EthAccount.create()
    all_needles += _needles(agent2.key.hex())
    imp = c.post("/api/accounts/import", json={
        "main_address": main2.address, "agent_private_key": agent2.key.hex(), "label": "Imported",
    })
    all_responses.append(imp)

    # 3. Every other route in the surface, exercised for good measure.
    all_responses.append(c.get("/api/session"))
    all_responses.append(c.get("/api/accounts"))
    all_responses.append(c.get("/api/accounts/active/summary"))
    all_responses.append(c.post(f"/api/accounts/{wallet_sign_account['id']}/activate"))
    all_responses.append(c.patch(f"/api/accounts/{wallet_sign_account['id']}", json={"label": "Renamed"}))
    all_responses.append(c.post("/api/accounts/watch", json={"address": "0x" + "c3" * 20, "label": "Whale"}))
    all_responses.append(c.delete(f"/api/accounts/{imp.json()['id']}"))

    for resp in all_responses:
        _assert_clean(resp.text, all_needles, f"{resp.request.method} {resp.request.url.path} response body")

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    _assert_clean(log_text, all_needles, "log output")


def test_account_contract_model_has_no_key_shaped_field():
    """Belt-and-suspenders at the schema level, mirroring
    contracts/accounts/tests/test_contract.py's own
    test_private_key_shape_enforced_and_never_in_account."""
    from contracts.accounts.models import Account
    field_names = " ".join(Account.model_fields).lower()
    assert "private" not in field_names
    assert "key" not in field_names


def test_vault_secret_file_contents_never_appear_in_a_response(clients, tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    vault.store_key("acc_probe", "0x" + EthAccount.create().key.hex())
    from services.accounts import env
    secret = open(env.vault_secret_path()).read().strip()

    c = clients.loopback()
    r = c.get("/api/accounts")
    assert secret not in r.text


def test_registry_file_on_disk_never_contains_a_private_key(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    key_hex = "0x" + EthAccount.create().key.hex()
    stored = registry.add(label="A", main_address="0x" + "a1" * 20, agent_address="0x" + "b2" * 20,
                          mode="trade", source="wallet_sign", verified=True)
    vault.store_key(stored.id, key_hex)

    from services.accounts import env
    raw_registry = open(env.registry_path()).read()
    assert key_hex[2:].lower() not in raw_registry.lower()


def test_trading_credentials_repr_never_contains_the_key():
    """2026-10-17: an assertion printed TradingCredentials and leaked a real key."""
    from services.accounts.credentials import TradingCredentials
    key = "0x" + "ab" * 32
    c = TradingCredentials(private_key=key, main_address="0x" + "1" * 40,
                           agent_address="0x" + "2" * 40, source="env")
    assert key not in repr(c) and key[2:] not in repr(c) and key not in str(c)
