"""scripts/treasury.py refuses to run anywhere but the operator's install on
mainnet: its signer is hardcoded to mainnet, so on testnet it would read one
venue and sign transfers on another, and a public install has no business
moving the operator's money.

Every case is offline. The script's venue reads (_http_post,
fetch_account_state, resolve_user_address) and the SDK Exchange are replaced
with spies that fail the test if reached. No treasury subcommand runs on the
operator path; that path only checks that the guard returns None.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "treasury.py"
REFUSAL = "treasury.py is a maintainer tool for mainnet only"
_RUNTIME_KEYS = ("PATHIEL_INSTALL", "PATHIEL_NETWORK")


class _VenueReached(AssertionError):
    pass


@pytest.fixture
def treasury(monkeypatch):
    """load(**env) -> the script module under env, with every venue entry
    point replaced by a spy. load.calls lists what was reached."""
    before = dict(os.environ)
    calls: list = []

    def spy(name):
        def _fn(*args, **kwargs):
            calls.append(name)
            raise _VenueReached(f"treasury reached the venue through {name}")
        return _fn

    def load(**env):
        for key in _RUNTIME_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        spec = importlib.util.spec_from_file_location("treasury_guard_probe", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name in ("_http_post", "fetch_account_state", "resolve_user_address"):
            monkeypatch.setattr(mod, name, spy(name))
        import hyperliquid.exchange
        monkeypatch.setattr(hyperliquid.exchange, "Exchange", spy("Exchange"))
        return mod

    load.calls = calls  # type: ignore[attr-defined]
    yield load
    # treasury.py loads the checkout's .env.local with setdefault at import;
    # drop anything it added so no later test sees it.
    for key in set(os.environ) - set(before):
        os.environ.pop(key, None)


def _run_status(mod, monkeypatch) -> SystemExit:
    monkeypatch.setattr(sys, "argv", ["treasury.py", "status"])
    with pytest.raises(SystemExit) as exc:
        mod.main()
    return exc.value


def test_refuses_on_a_public_install(treasury, monkeypatch, capsys):
    mod = treasury(PATHIEL_INSTALL="public", PATHIEL_NETWORK="mainnet")
    exit_ = _run_status(mod, monkeypatch)
    assert exit_.code == 2
    out = capsys.readouterr().out
    assert REFUSAL in out
    assert "(install=public, network=mainnet); nothing was sent." in out
    assert treasury.calls == []


def test_refuses_on_a_public_install_with_no_network_set(treasury, monkeypatch, capsys):
    mod = treasury(PATHIEL_INSTALL="public")
    assert _run_status(mod, monkeypatch).code == 2
    assert "(install=public, network=testnet)" in capsys.readouterr().out
    assert treasury.calls == []


def test_refuses_on_testnet(treasury, monkeypatch, capsys):
    mod = treasury(PATHIEL_NETWORK="testnet")
    exit_ = _run_status(mod, monkeypatch)
    assert exit_.code == 2
    assert "(install=operator, network=testnet); nothing was sent." in capsys.readouterr().out
    assert treasury.calls == []


def test_guard_is_a_noop_for_operator_mainnet(treasury, capsys):
    mod = treasury()
    assert mod._refuse_unless_operator_mainnet() is None
    assert capsys.readouterr().out == ""
    assert treasury.calls == []


def test_exchange_factory_calls_the_guard_first(treasury, monkeypatch, capsys):
    """_exchange refuses before it reads the master key or builds the SDK
    client, so a caller that skips main() is still guarded. A dummy key is
    set so the old 'key missing' exit cannot pass for the guard."""
    mod = treasury(PATHIEL_NETWORK="testnet")
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", "0x" + "11" * 32)
    with pytest.raises(SystemExit) as exc:
        mod._exchange()
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert REFUSAL in out and "MASTER_PRIVATE_KEY" not in out
    assert treasury.calls == []
