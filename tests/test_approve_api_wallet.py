"""scripts/approve_api_wallet.py: the approval is signed by the master key,
names the agent, and posts the SDK's wire shape. No network."""
import importlib.util
import json
from pathlib import Path

from eth_account import Account
from hyperliquid.utils import signing

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("approve_api_wallet", ROOT / "scripts" / "approve_api_wallet.py")
aw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aw)

AGENT = "0xD46C20732c480f41f4603aEdBB0F73F6AF78eb5b"


def test_signed_by_master_and_posts_sdk_shape():
    master = Account.create()
    sent = []
    reply = aw.approve(master.key.hex(), AGENT, "pathiel", 1_794_288_221_486,
                       lambda path, payload: sent.append((path, payload)) or {"status": "ok"})
    assert reply == {"status": "ok"}
    path, p = sent[0]
    assert path == "/exchange" and p["nonce"] == 1_794_288_221_486 and p["vaultAddress"] is None
    a = p["action"]
    assert a["type"] == "approveAgent" and a["agentAddress"] == AGENT and a["agentName"] == "pathiel"
    assert a["hyperliquidChain"] == "Mainnet" and a["signatureChainId"] == "0x66eee"
    who = signing.recover_user_from_user_signed_action(
        dict(a), p["signature"],
        [{"name": "hyperliquidChain", "type": "string"}, {"name": "agentAddress", "type": "address"},
         {"name": "agentName", "type": "string"}, {"name": "nonce", "type": "uint64"}],
        "HyperliquidTransaction:ApproveAgent", True)
    assert who.lower() == master.address.lower()
    assert master.key.hex().removeprefix("0x") not in json.dumps(p)


def test_dry_run_never_posts(monkeypatch, capsys):
    master = Account.create()
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", master.key.hex())
    import _state_env  # noqa: F401  (scripts/ is on sys.path via the script import)
    monkeypatch.setattr("_state_env.load_env_local", lambda *a, **k: None)
    monkeypatch.setattr(aw, "approve", lambda *a, **k: (_ for _ in ()).throw(AssertionError("posted")))
    assert aw.main(["--address", AGENT]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["master"] == master.address
    assert master.key.hex().removeprefix("0x") not in json.dumps(out)


# ── network-correct signing (2026-11-12, public plugin) ───────────────────────

import copy  # noqa: E402

import pytest  # noqa: E402

import contracts.runtime as runtime  # noqa: E402
from pathiel.client import hl_client  # noqa: E402

_TYPES = [{"name": "hyperliquidChain", "type": "string"}, {"name": "agentAddress", "type": "address"},
          {"name": "agentName", "type": "string"}, {"name": "nonce", "type": "uint64"}]
_PRIMARY = "HyperliquidTransaction:ApproveAgent"


class _Post:
    def __init__(self):
        self.calls = []

    def __call__(self, path, payload):
        self.calls.append((path, copy.deepcopy(payload)))
        return {"status": "ok", "response": {"type": "default"}}


@pytest.fixture
def operator_env(monkeypatch):
    """Operator install on mainnet, a throwaway master key in the env, no env file read."""
    master = Account.create()
    import _state_env  # noqa: F401
    monkeypatch.setattr("_state_env.load_env_local", lambda *a, **k: None)
    for k in runtime.RUNTIME_ENV_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", master.key.hex())
    monkeypatch.setattr(hl_client, "HL_API", runtime.MAINNET_API_URL)
    return master


def test_default_is_mainnet_signature():
    """approve() without is_mainnet signs exactly what it signed before the
    parameter existed: sign_agent(wallet, action, True). ECDSA here is
    deterministic (RFC 6979), so the signatures are equal byte for byte."""
    master = Account.create()
    post = _Post()
    aw.approve(master.key.hex(), AGENT, "pathiel", 1_794_288_221_486, post)
    today = aw.build_action(AGENT, "pathiel", 1_794_288_221_486)
    sig_today = signing.sign_agent(master, today, True)
    p = post.calls[0][1]
    assert p["signature"] == sig_today
    assert p["action"] == today and p["action"]["hyperliquidChain"] == "Mainnet"


def test_testnet_network_signs_testnet_chain(operator_env, monkeypatch, capsys):
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")
    monkeypatch.setattr(hl_client, "HL_API", runtime.TESTNET_API_URL)
    post = _Post()
    assert aw.main(["--address", AGENT, "--send"], post=post) == 0
    path, p = post.calls[0]
    assert path == "/exchange" and p["action"]["hyperliquidChain"] == "Testnet"
    who = signing.recover_user_from_user_signed_action(dict(p["action"]), p["signature"],
                                                       _TYPES, _PRIMARY, False)
    assert who.lower() == operator_env.address.lower()
    capsys.readouterr()


def test_refuses_on_a_public_install(operator_env, monkeypatch, capsys):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    post = _Post()
    for argv in (["--address", AGENT, "--send"], ["--address", AGENT]):
        assert aw.main(argv, post=post) == 1
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is False and "Hyperliquid app" in out["error"]
        assert "app.hyperliquid-testnet.xyz" in out["error"]
    assert post.calls == []


def test_refuses_on_network_drift(operator_env, monkeypatch, capsys):
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")       # HL_API stays on mainnet
    post = _Post()
    assert aw.main(["--address", AGENT, "--send"], post=post) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "network drift" in out["error"]
    assert post.calls == []
    assert operator_env.key.hex().removeprefix("0x") not in json.dumps(out)


def test_operator_output_keys_unchanged(operator_env, capsys):
    assert aw.main(["--address", AGENT]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert set(dry) == {"dry_run", "master", "approve", "name", "action"}
    post = _Post()
    assert aw.main(["--address", AGENT, "--send"], post=post) == 0
    sent = json.loads(capsys.readouterr().out)
    assert set(sent) == {"ok", "master", "approve", "name", "reply"}
    assert sent["ok"] is True and len(post.calls) == 1
    assert post.calls[0][1]["action"]["hyperliquidChain"] == "Mainnet"
