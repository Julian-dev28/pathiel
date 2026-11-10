"""Approve an API wallet on pathiel's account, signed with the master key.

2026-11-09: the signing API wallet 0xD46C...eb5b dropped off the account
(services/accounts/signer_status.py found it) and the pathiel master wallet
is not in any browser wallet, so the Hyperliquid app could not re-approve it.
Operator, 2026-11-10: "a" (re-approve it with the master key by script).

    python3 scripts/approve_api_wallet.py --address 0x...            # dry run: prints the action
    python3 scripts/approve_api_wallet.py --address 0x... --send     # signs and sends it

An API wallet can trade the account, not withdraw. The key comes from
HYPERLIQUID_MASTER_PRIVATE_KEY in the env file and is never printed. The
signing is the installed SDK's own (hyperliquid.utils.signing.sign_agent),
the same call Exchange.approve_agent makes, but for an address we already
hold the key for instead of a freshly generated one.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable, Dict

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_SCRIPTS))
sys.path.insert(0, _SCRIPTS)


def build_action(agent_address: str, name: str, nonce_ms: int) -> Dict[str, Any]:
    return {"type": "approveAgent", "agentAddress": agent_address, "agentName": name, "nonce": nonce_ms}


def approve(master_key: str, agent_address: str, name: str, nonce_ms: int,
            post: Callable[[str, Dict[str, Any]], Any]) -> Any:
    """Sign approveAgent with `master_key` and post it. Returns the venue's reply."""
    from eth_account import Account
    from hyperliquid.utils.signing import sign_agent
    wallet = Account.from_key(master_key)
    action = build_action(agent_address, name, nonce_ms)
    signature = sign_agent(wallet, action, True)      # mainnet; adds signatureChainId/hyperliquidChain
    payload = {"action": action, "nonce": nonce_ms, "signature": signature,
               "vaultAddress": None, "expiresAfter": None}
    return post("/exchange", payload)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--address", required=True)
    ap.add_argument("--name", default="pathiel")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args(argv)
    import _state_env
    _state_env.load_env_local(os.path.dirname(_SCRIPTS))
    from eth_account import Account
    key = os.environ.get("HYPERLIQUID_MASTER_PRIVATE_KEY", "").strip()
    if not key:
        print(json.dumps({"ok": False, "error": "HYPERLIQUID_MASTER_PRIVATE_KEY is not set"}))
        return 1
    master = Account.from_key(key).address
    nonce = int(time.time() * 1000)
    plan = {"master": master, "approve": a.address, "name": a.name}
    if not a.send:
        print(json.dumps({"dry_run": True, **plan, "action": build_action(a.address, a.name, nonce)}))
        return 0
    from pathiel.client.hl_client import _http_post_strict
    reply = approve(key, a.address, a.name, nonce, _http_post_strict)
    ok = isinstance(reply, dict) and reply.get("status") == "ok"
    print(json.dumps({"ok": ok, **plan, "reply": reply}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
