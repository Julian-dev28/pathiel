"""scripts/public_status.py: the check /pathiel:setup runs after the env file
is filled in. Names only, never a value; exit 1 on any FAIL.

Every run is a subprocess with env=public_env(...). Keys are random,
generated per test, and never belong to any account. Offline: the one
--online case has no builder address, so it makes no read.

    .venv/bin/python -m pytest -q tests/test_public_status.py
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "scripts" / "public_status.py"
LINE = re.compile(r"^(PASS|WARN|FAIL) ([^:]+): (.+)$")
BUILDER = "0x" + "ab" * 20


def throwaway_key() -> Tuple[str, str]:
    """(private key, its address), random, never funded."""
    from eth_account import Account
    key = "0x" + secrets.token_hex(32)
    return key, Account.from_key(key).address


def other_address() -> str:
    return "0x" + secrets.token_hex(20)


def run(env, *args) -> Tuple[int, str, str]:
    proc = subprocess.run([sys.executable, str(STATUS), *args], env=env, capture_output=True,
                          text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def lines(stdout: str) -> List[Tuple[str, str, str]]:
    out = []
    for line in stdout.splitlines():
        m = LINE.match(line)
        assert m, f"not a 'STATUS name: message' line: {line!r}"
        out.append(m.groups())
    return out


def by_name(stdout: str, name: str) -> List[Tuple[str, str]]:
    return [(s, msg) for s, n, msg in lines(stdout) if n == name]


def test_keyless_install_is_all_pass_or_warn(public_env):
    code, out, err = run(public_env())
    assert code == 0, out + err
    parsed = lines(out)
    assert parsed and all(status in ("PASS", "WARN") for status, _n, _m in parsed), out
    assert by_name(out, "trading") == [("WARN", "read-only (no key)")]
    assert by_name(out, "network")[0][0] == "PASS" and "testnet" in by_name(out, "network")[0][1]
    assert by_name(out, "install")[0][0] == "PASS"


@pytest.mark.parametrize("shape", ["wallet_equals_master", "key_is_the_master_key"])
def test_master_key_as_signing_key_fails(public_env, shape):
    key, address = throwaway_key()
    if shape == "wallet_equals_master":
        env = public_env(HYPERLIQUID_PRIVATE_KEY=key, HYPERLIQUID_WALLET_ADDRESS=address,
                         HYPERLIQUID_MASTER_ADDRESS=address)
    else:   # the wallet field names another address, but the key signs as the master
        env = public_env(HYPERLIQUID_PRIVATE_KEY=key, HYPERLIQUID_WALLET_ADDRESS=other_address(),
                         HYPERLIQUID_MASTER_ADDRESS=address)
    code, out, _err = run(env)
    assert code == 1
    status, msg = by_name(out, "agent key")[0]
    assert status == "FAIL" and "main account" in msg


def test_an_api_wallet_key_passes(public_env):
    key, address = throwaway_key()
    env = public_env(HYPERLIQUID_PRIVATE_KEY=key, HYPERLIQUID_WALLET_ADDRESS=address,
                     HYPERLIQUID_MASTER_ADDRESS=other_address())
    code, out, err = run(env)
    assert code == 0, out + err
    assert by_name(out, "agent key")[0][0] == "PASS"
    assert by_name(out, "key")[0][0] == "PASS"
    assert [s for s, _m in by_name(out, "address")] == ["PASS", "PASS"]


def test_a_key_without_addresses_fails(public_env):
    key, _address = throwaway_key()
    code, out, _err = run(public_env(HYPERLIQUID_PRIVATE_KEY=key))
    assert code == 1
    assert [s for s, _m in by_name(out, "address")] == ["FAIL", "FAIL"]


def test_a_malformed_key_fails_without_echoing_it(public_env):
    bad = "0x" + "zz" * 32
    code, out, err = run(public_env(HYPERLIQUID_PRIVATE_KEY=bad))
    assert code == 1
    assert by_name(out, "key")[0][0] == "FAIL"
    assert bad not in out + err


def test_master_private_key_present_fails_on_public(public_env):
    master_key, _ = throwaway_key()
    code, out, _err = run(public_env(HYPERLIQUID_MASTER_PRIVATE_KEY=master_key))
    assert code == 1
    status, msg = by_name(out, "master key")[0]
    assert status == "FAIL"
    assert "remove it from the env file; pathiel never needs it there" in msg

    # The operator's install keeps it (scripts/treasury.py reads it there).
    code, out, _err = run(public_env(HYPERLIQUID_MASTER_PRIVATE_KEY=master_key, PATHIEL_INSTALL=None))
    assert by_name(out, "master key")[0][0] == "WARN"
    assert all(s != "FAIL" for s, _n, _m in lines(out)), out


def test_never_prints_a_key_value(public_env):
    key, address = throwaway_key()
    master_key, _ = throwaway_key()
    token = "tok_" + secrets.token_hex(16)
    make = public_env
    # Through the env file (the real path), not the process env.
    make.env_file.write_text(
        f"HYPERLIQUID_PRIVATE_KEY={key}\nHYPERLIQUID_WALLET_ADDRESS={address}\n"
        f"HYPERLIQUID_MASTER_ADDRESS={other_address()}\n"
        f"HYPERLIQUID_MASTER_PRIVATE_KEY={master_key}\nSOME_API_TOKEN={token}\n")
    env = make(HYPERLIQUID_PRIVATE_KEY=None, HYPERLIQUID_MASTER_PRIVATE_KEY=None,
               HYPERLIQUID_WALLET_ADDRESS=None, HYPERLIQUID_MASTER_ADDRESS=None)
    for args in ((), ("--json",)):
        code, out, err = run(env, *args)
        assert code == 1        # the master key is present on a public install
        text = out + err
        for value in (key, master_key, token):
            assert value not in text
            assert value.removeprefix("0x") not in text
            assert value.lower() not in text.lower()
    code, out, _err = run(env)
    assert by_name(out, "key")[0][0] == "PASS", "the key in the env file was loaded"


def test_json_output_shape(public_env):
    code, out, _err = run(public_env(), "--json")
    assert code == 0
    payload = json.loads(out)
    assert set(payload) == {"ok", "install", "network", "network_explicit", "trading_mode",
                            "env_file", "checks"}
    assert payload["ok"] is True and payload["install"] == "public"
    assert payload["network"] == "testnet" and payload["network_explicit"] is False
    assert payload["trading_mode"] == "read-only (no key)"
    assert payload["env_file"] == str(public_env.env_file)
    names = [c["name"] for c in payload["checks"]]
    for want in ("install", "network", "state dir", "config", "key", "address", "agent key",
                 "master key", "trading", "builder fee", "claude CLI"):
        assert want in names, want
    for c in payload["checks"]:
        assert set(c) == {"status", "name", "message"} and c["status"] in ("PASS", "WARN", "FAIL")

    failing = json.loads(run(public_env(HYPERLIQUID_MASTER_PRIVATE_KEY="0x" + "1" * 64), "--json")[1])
    assert failing["ok"] is False


@pytest.mark.parametrize("network,explicit,mode,level", [
    (None, False, "testnet trading", "PASS"),
    ("testnet", True, "testnet trading", "PASS"),
    ("mainnet", True, "MAINNET trading: real money", "WARN"),
])
def test_reports_network_and_trading_mode(public_env, network, explicit, mode, level):
    key, address = throwaway_key()
    env = public_env(HYPERLIQUID_PRIVATE_KEY=key, HYPERLIQUID_WALLET_ADDRESS=address,
                     HYPERLIQUID_MASTER_ADDRESS=other_address(), PATHIEL_NETWORK=network,
                     PATHIEL_STATE_DIR=None)
    code, out, err = run(env)
    assert code == 0, out + err
    assert by_name(out, "trading") == [(level, mode)]
    net_status, net_msg = by_name(out, "network")[0]
    assert net_msg.startswith(network or "testnet")
    assert ("set by PATHIEL_NETWORK" in net_msg) is explicit
    assert net_status == ("WARN" if network == "mainnet" else "PASS")
    payload = json.loads(run(env, "--json")[1])
    assert payload["trading_mode"] == mode and payload["network_explicit"] is explicit


def test_state_dir_must_match_the_network(public_env, tmp_path):
    mismatch = public_env(PATHIEL_NETWORK="testnet", PATHIEL_STATE_DIR=str(tmp_path / "state"))
    assert "does not say testnet" in by_name(run(mismatch)[1], "state dir")[0][1]
    good = tmp_path / "state-testnet"
    good.mkdir()
    assert by_name(run(public_env(PATHIEL_STATE_DIR=str(good)))[1], "state dir")[0][0] == "PASS"


def test_builder_line_for_unset_and_set_address(public_env):
    code, out, _err = run(public_env(PATHIEL_BUILDER_ADDRESS=""))
    assert code == 0
    assert by_name(out, "builder fee") == [
        ("PASS", "no builder address configured: orders carry no builder fee")]

    code, out, _err = run(public_env(PATHIEL_BUILDER_ADDRESS=BUILDER))
    status, msg = by_name(out, "builder fee")[0]
    assert status == "PASS"
    assert "0.01% of notional per fill" in msg and BUILDER in msg
    assert "charged only after you approve it" in msg and "--online" in msg

    # --online with no address set: nothing to read, so no network either way.
    code, out, _err = run(public_env(PATHIEL_BUILDER_ADDRESS=""), "--online")
    assert code == 0 and "no builder address configured" in by_name(out, "builder fee")[0][1]

    # The operator's install never carries a builder fee.
    code, out, _err = run(public_env(PATHIEL_BUILDER_ADDRESS=BUILDER, PATHIEL_INSTALL=None))
    assert by_name(out, "builder fee")[0][1].startswith("operator install")


def test_config_line_flags_a_profile_looser_than_the_public_default(public_env, tmp_path):
    cfg = tmp_path / "home" / ".pathiel" / ".agent-config.json"
    cfg.write_text(json.dumps({"risk_profile": {"preset": "auto", "max_leverage": 20}}))
    status, msg = by_name(run(public_env())[1], "config")[0]
    assert status == "WARN" and "looser than the public default" in msg

    cfg.write_text((ROOT / "config" / "public" / "agent-config.json").read_text())
    status, msg = by_name(run(public_env())[1], "config")[0]
    assert status == "PASS" and "max leverage 3x" in msg and "1 position at a time" in msg


def test_claude_cli_missing_is_a_warning(public_env, tmp_path):
    # A PATH without claude on it; python itself runs by absolute path.
    code, out, _err = run(public_env(PATH="/usr/bin:/bin"))
    status, msg = by_name(out, "claude CLI")[0]
    assert code == 0 and status == "WARN" and "desk_refresh" in msg


def test_the_launchers_default_is_not_reported_as_a_choice(public_env):
    """The launcher always exports PATHIEL_NETWORK; PATHIEL_NETWORK_SOURCE
    tells the default from a line the user wrote."""
    default = public_env(PATHIEL_NETWORK="testnet", PATHIEL_NETWORK_SOURCE="default")
    assert "(the default)" in by_name(run(default)[1], "network")[0][1]
    assert json.loads(run(default, "--json")[1])["network_explicit"] is False
    chosen = public_env(PATHIEL_NETWORK="testnet", PATHIEL_NETWORK_SOURCE="env file")
    assert "set by PATHIEL_NETWORK" in by_name(run(chosen)[1], "network")[0][1]
