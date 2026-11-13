"""Wallet connect contract: the plugin's "connect your Hyperliquid wallet" flow.

A public install generates an agent (API wallet) key on the user's machine,
the user approves it with one signature in their own browser wallet, and the
flow writes the agent key plus both addresses into the user's env file. The
main key never leaves the wallet; the agent key never leaves this machine and
is never printed, logged, returned or sent to the browser.

What lives here (stdlib only, imports contracts.runtime and nothing from
pathiel or services, so the MCP server can import it at start for free):

  * the MCP tool `wallet_connect`: its definition, its class in the server's
    TOOL_CLASSES, and the dotted handler path the server imports on first call
  * `mcp_handler()`, which refuses on the operator's own checkout
    (code `operator_install`) before any service code is imported
  * the result shape (`ConnectResult`), its states and error codes, and
    `result_problems()`, which a test runs over every result
  * the env file keys the flow writes and the page timeout

The flow itself is services/accounts/connect_wallet.py; the CLI is
scripts/connect_wallet.py. See README.md.
"""
from __future__ import annotations

import importlib
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict

from contracts import runtime

CONTRACT_VERSION = "1.2.0"   # 1.2.0: valid_until in the result. 1.1.0 (2026-11-13): AGENT_NAME "pathiel-plugin"

TOOL_NAME = "wallet_connect"
# scripts/pathiel-mcp-server.py TOOL_CLASSES key. "account_setup" tools write
# trading credentials to the user's env file: destructiveHint true, so Claude
# Code asks before each call, and never in LIVE_MONEY_TOOLS (no order, no
# transfer).
MCP_TOOL_CLASS = "account_setup"
MCP_HANDLER: Tuple[str, str] = ("services.accounts.connect_wallet", "handle_wallet_connect")

# The env file lines the flow writes. Every other line is kept as it was.
ENV_PRIVATE_KEY = "HYPERLIQUID_PRIVATE_KEY"        # the agent key
ENV_WALLET_ADDRESS = "HYPERLIQUID_WALLET_ADDRESS"  # the agent address
ENV_MASTER_ADDRESS = "HYPERLIQUID_MASTER_ADDRESS"  # the account that signed: holds the funds
ENV_KEYS = (ENV_PRIVATE_KEY, ENV_WALLET_ADDRESS, ENV_MASTER_ADDRESS)

# The agentName the approval carries (the app lists it). Not "pathiel": the
# maintainer's own live API wallet carries that name (scripts/approve_api_wallet.py
# --name default), and approving a new agent under a name the account already
# uses replaces that agent on Hyperliquid. A connect signed by the maintainer's
# master wallet must never knock out the live desk's signer (review, 2026-11-13).
# Reconnecting replaces the previous "pathiel-plugin" wallet, which is the point.
AGENT_NAME = "pathiel-plugin"
PAGE_TIMEOUT_S = 600              # the signing page closes after 10 minutes

STATES = ("none", "waiting", "working", "done", "failed", "cancelled")
FINAL_STATES = ("done", "failed", "cancelled")

ERROR_CODES = (
    "operator_install",   # the maintainer's checkout: refused before any file is touched
    "repo_env_file",      # the env file is a checkout's own .env.local: refused
    "network_mismatch",   # the env file names another network than this process runs
    "key_exists",         # a key is already set and replace was not asked for
    "busy",               # another connect flow holds the lock on this env file
    "bad_request",        # the page sent something that is not an address/signature/chain
    "signer_mismatch",    # the signature does not recover to the address the page reported
    "venue_refused",      # Hyperliquid answered with an error and the agent is not approved
    "not_confirmed",      # no error, but the agent never showed up in the /info reads
    "venue_unreachable",  # the /exchange post or every /info read failed on the network
    "write_failed",       # approved, but the env file could not be written
    "timeout",            # nobody signed within PAGE_TIMEOUT_S
    "cancelled",          # wallet_connect {cancel: true}, or Ctrl-C on the CLI
    "server_failed",      # the local page could not start
)

OPERATOR_INSTALL_MESSAGE = (
    "wallet_connect is for plugin installs only. This process is the maintainer's own "
    "checkout, whose .env.local holds the live trading key; the flow refuses here before "
    "touching any file. Approve API wallets in the Hyperliquid app or with "
    "scripts/approve_api_wallet.py.")

RECONNECT_NOTE = ("Reconnect pathiel in /mcp (or restart Claude Code) so the server loads the "
                  "new key. Until then it keeps running with the old one.")


class ConnectResult(TypedDict, total=False):
    """What wallet_connect, scripts/connect_wallet.py and the page's POST
    reply carry. Never a private key: `result_problems` checks that."""
    ok: bool
    state: str                    # one of STATES
    network: str                  # "mainnet" | "testnet"
    url: str                      # the one-shot page, while it waits
    agent_address: str            # the new API wallet (public)
    master_address: str           # the account that signed (public)
    env_file: str                 # where the key was written
    expires_in_s: int
    replaced: bool                # an earlier key was swapped out
    valid_until: Optional[str]    # UTC date Hyperliquid's approval ends (about 90 days), or None
    code: str                     # one of ERROR_CODES when ok is false
    error: str
    next: str                     # one plain sentence: what the user does now


RESULT_KEYS = tuple(ConnectResult.__annotations__)

_HEX64 = re.compile(r"(?<![0-9a-fA-F])(?:0x)?[0-9a-fA-F]{64}(?![0-9a-fA-F])")

MCP_TOOL: Dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Connect the user's Hyperliquid wallet: a new API wallet key is made on this machine, "
        "the user approves it with one signature on a local page in their browser wallet, and "
        "the key is saved to ~/.pathiel/.env.local. It can trade, never withdraw. Call only "
        "when the user asked. status=true: waiting, done or failed. cancel=true stops it."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "status": {"type": "boolean", "description": "report the current connect, start nothing"},
            "replace": {"type": "boolean",
                        "description": "swap out the key already set; only when the user said so"},
            "cancel": {"type": "boolean", "description": "stop a waiting connect"},
            "open_browser": {"type": "boolean", "description": "open the page (default true)"},
        },
    },
}


def result_problems(result: Any, secrets: Tuple[str, ...] = ()) -> List[str]:
    """Why `result` breaks this contract, or []. Checks the keys and the
    state, and that no value anywhere is a 64-hex-digit string (the shape of
    a private key) or contains one of `secrets`."""
    problems: List[str] = []
    if not isinstance(result, dict):
        return [f"not a dict: {type(result).__name__}"]
    extra = sorted(set(result) - set(RESULT_KEYS))
    if extra:
        problems.append(f"unknown keys {extra}")
    if "ok" not in result or not isinstance(result["ok"], bool):
        problems.append("ok must be a bool")
    if result.get("state") not in STATES:
        problems.append(f"state {result.get('state')!r} not in STATES")
    if result.get("ok") is False and result.get("code") not in ERROR_CODES:
        problems.append(f"code {result.get('code')!r} not in ERROR_CODES")
    text = json.dumps(result, default=str)
    if _HEX64.search(text):
        problems.append("a 64-hex-digit value (private key shape) is in the result")
    for secret in secrets:
        bare = str(secret or "").lower().removeprefix("0x")
        if bare and bare in text.lower():
            problems.append("a secret is in the result")
    return problems


def mcp_handler() -> Callable[[Dict[str, Any]], str]:
    """The wallet_connect handler, imported on first call.

    Refuses first, before importing services.accounts, whenever this process
    is not a public install (contracts.runtime.is_public()). tools/list hides
    the tool from the operator (server_tools() in scripts/pathiel-mcp-server.py),
    but a listing is not a lock: this wrapper is the only path the server
    wires the name to, so a raw tools/call on the maintainer's checkout can
    never reach the code that writes an env file. services/accounts/
    connect_wallet.py checks again on its own (two locks, one rule)."""
    module, func = MCP_HANDLER

    def call(params: Dict[str, Any]) -> str:
        if not runtime.is_public():
            return json.dumps({"ok": False, "state": "none", "code": "operator_install",
                               "error": OPERATOR_INSTALL_MESSAGE})
        return getattr(importlib.import_module(module), func)(params or {})

    call.__name__ = f"handle_{TOOL_NAME}"
    return call


def operator_refusal() -> Optional[ConnectResult]:
    """The refusal every entry point returns first on the operator's install,
    or None on a public one."""
    if runtime.is_public():
        return None
    return {"ok": False, "state": "none", "code": "operator_install",
            "error": OPERATOR_INSTALL_MESSAGE}


__all__ = [
    "CONTRACT_VERSION", "TOOL_NAME", "MCP_TOOL_CLASS", "MCP_HANDLER", "MCP_TOOL",
    "ENV_PRIVATE_KEY", "ENV_WALLET_ADDRESS", "ENV_MASTER_ADDRESS", "ENV_KEYS",
    "AGENT_NAME", "PAGE_TIMEOUT_S", "STATES", "FINAL_STATES", "ERROR_CODES",
    "OPERATOR_INSTALL_MESSAGE", "RECONNECT_NOTE", "ConnectResult", "RESULT_KEYS",
    "result_problems", "mcp_handler", "operator_refusal",
]
