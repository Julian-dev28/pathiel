#!/usr/bin/env python3
"""Build the env file for a rented server that runs the desk loops.

The server gets its OWN Hyperliquid API wallet (trade only, no withdrawals),
never the Mac's key and never the main wallet's key. Everything else in the
Mac's env file is carried over: intel API keys, ports and flags. Mac paths
are rewritten to the server's paths, and any Mac path left over is dropped.

    .venv/bin/python scripts/vps/make_server_env.py --out FILE \
        --server-repo SERVER_REPO --server-state SERVER_STATE

It asks for the API wallet's private key with a hidden prompt, derives the
address, and prints names and public addresses only. It never prints a
secret value. The file it writes is mode 0600. scripts/vps/push.sh runs it.

Refuses when:
  - the key is malformed;
  - the key belongs to the main wallet (that key can withdraw);
  - the key is the one the Mac already uses (the server's key has to be
    revocable on its own after the trip);
  - the Mac env names no main account.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from typing import Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from _state_env import env_file_candidates  # noqa: E402

KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

# Never shipped as-is. The identity keys are re-added with the server's
# values; the master key never leaves the Mac.
DROP = frozenset({
    "HYPERLIQUID_PRIVATE_KEY",
    "HYPERLIQUID_WALLET_ADDRESS",
    "HYPERLIQUID_MASTER_ADDRESS",
    "HYPERLIQUID_MASTER_PRIVATE_KEY",
    "PATHIEL_STATE_DIR",
    "PATHIEL_ENV_FILE",
})


class EnvError(ValueError):
    """A refusal. The message never contains a secret value."""


def parse_env(text: str) -> List[Tuple[str, str]]:
    """KEY=VALUE pairs in file order, the same way _state_env reads them."""
    out: List[Tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out.append((key.strip(), value.strip()))
    return out


def derive_address(private_key: str) -> str:
    from eth_account import Account
    return Account.from_key(private_key).address


def build_server_env(
    pairs: List[Tuple[str, str]],
    *,
    agent_key: str,
    path_map: Dict[str, str],
    local_home: str,
    server_state: str,
    derive: Optional[Callable[[str], str]] = None,
) -> Tuple[List[Tuple[str, str]], Dict[str, object]]:
    """The server's env pairs and a report of names only.

    path_map maps a Mac path prefix to the server's (longest prefix wins).
    """
    if not KEY_RE.match(agent_key or ""):
        raise EnvError("the API wallet key must be 0x plus 64 hex characters")
    env = dict(pairs)
    master = (env.get("HYPERLIQUID_MASTER_ADDRESS")
              or env.get("HYPERLIQUID_WALLET_ADDRESS") or "").strip()
    if not master:
        raise EnvError("the Mac env file names no main account "
                       "(HYPERLIQUID_MASTER_ADDRESS or HYPERLIQUID_WALLET_ADDRESS)")
    agent = (derive or derive_address)(agent_key)
    if agent.lower() == master.lower():
        raise EnvError("that key is the main wallet's key, which can withdraw. "
                       "Make an API wallet on Hyperliquid and use its key")
    old_key = (env.get("HYPERLIQUID_PRIVATE_KEY") or "").strip()
    if old_key and old_key.lower() == agent_key.lower():
        raise EnvError("that is the key the Mac already uses. Make a separate API "
                       "wallet for the server so you can revoke it alone")

    prefixes = sorted(path_map, key=len, reverse=True)
    out: List[Tuple[str, str]] = []
    rewritten: List[str] = []
    dropped: List[str] = []
    for key, value in pairs:
        if key in DROP:
            continue
        hit = next((p for p in prefixes if value == p or value.startswith(p + "/")), None)
        if hit is not None:
            out.append((key, path_map[hit] + value[len(hit):]))
            rewritten.append(key)
        elif local_home and local_home in value:
            dropped.append(key)
        else:
            out.append((key, value))
    out += [
        ("HYPERLIQUID_MASTER_ADDRESS", master),
        ("HYPERLIQUID_WALLET_ADDRESS", agent),
        ("HYPERLIQUID_PRIVATE_KEY", agent_key),
        ("PATHIEL_STATE_DIR", server_state),
    ]
    report = {
        "master": master,
        "agent": agent,
        "kept": sorted(k for k, _ in out),
        "rewritten": sorted(rewritten),
        "dropped_mac_paths": sorted(dropped),
    }
    return out, report


def write_env(path: str, pairs: List[Tuple[str, str]]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("# pathiel server env, built by scripts/vps/make_server_env.py\n")
        for key, value in pairs:
            fh.write(f"{key}={value}\n")
    os.chmod(path, 0o600)


def local_env_path() -> Optional[str]:
    return next((p for p in env_file_candidates(ROOT) if os.path.isfile(p)), None)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="file to write (mode 0600)")
    ap.add_argument("--server-repo", required=True, help="repo path on the server")
    ap.add_argument("--server-state", required=True, help="state dir path on the server")
    args = ap.parse_args(argv)

    src = local_env_path()
    if not src:
        print("no env file found on this Mac", file=sys.stderr)
        return 2
    with open(src) as fh:
        pairs = parse_env(fh.read())
    local_state = dict(pairs).get("PATHIEL_STATE_DIR") or ROOT
    path_map = {os.path.realpath(local_state): args.server_state, local_state: args.server_state,
                os.path.realpath(ROOT): args.server_repo, ROOT: args.server_repo}

    key = getpass.getpass("Server API wallet private key (hidden): ").strip()
    try:
        out, report = build_server_env(pairs, agent_key=key, path_map=path_map,
                                       local_home=os.path.expanduser("~"),
                                       server_state=args.server_state)
    except EnvError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    write_env(args.out, out)
    print(f"main account   {report['master']}")
    print(f"server signer  {report['agent']}  (must match the API wallet address on Hyperliquid)")
    print(f"vars           {', '.join(report['kept'])}")
    if report["rewritten"]:
        print(f"paths moved    {', '.join(report['rewritten'])}")
    if report["dropped_mac_paths"]:
        print(f"dropped        {', '.join(report['dropped_mac_paths'])} (Mac-only paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
