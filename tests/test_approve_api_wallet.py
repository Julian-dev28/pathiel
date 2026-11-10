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
