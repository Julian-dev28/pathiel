"""Connect your Hyperliquid wallet to a pathiel plugin install.

Makes a new API wallet (agent) key on this machine, opens a one-shot local
page where your browser wallet approves it with one signature, then writes
the key and both addresses to ~/.pathiel/.env.local (mode 600). The key can
trade and never withdraw; your main key never leaves your wallet.

    "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/connect_wallet.py
    "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/connect_wallet.py --replace
    "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/connect_wallet.py --no-browser

It prints the page URL on stderr, waits up to 10 minutes for the signature,
and prints one JSON object on stdout: the result, never a key. Exit 0 when
the wallet is connected, 1 otherwise. Ctrl-C cancels; nothing is written.

Plugin installs only: on the maintainer's checkout it refuses before reading
or writing any file. The flow is services/accounts/connect_wallet.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, List, Optional

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_SCRIPTS))


def _parse_args(argv: Optional[List[str]]) -> Optional[argparse.Namespace]:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--replace", action="store_true",
                    help="swap out a key that is already in the env file")
    ap.add_argument("--no-browser", action="store_true", help="print the URL, open nothing")
    # parse_known_args: argparse echoes unknown arguments in its error, and a
    # pasted key must never be echoed back.
    args, unknown = ap.parse_known_args(argv)
    if unknown:
        print(f"error: {len(unknown)} unrecognized argument(s), not shown. This script takes "
              "no key; your wallet signs in the browser.", file=sys.stderr)
        return None
    return args


def main(argv: Optional[List[str]] = None, *, venue: Any = None,
         opener: Optional[Callable[[str], Any]] = None, timeout_s: Optional[float] = None) -> int:
    args = _parse_args(argv)
    if args is None:
        return 2
    import contracts.wallet_connect as contract
    refusal = contract.operator_refusal()      # first: reads and writes nothing
    if refusal is not None:
        print(json.dumps(refusal))
        return 1

    from services.accounts import connect_wallet as cw
    env_file = cw.resolve_env_file()
    cw.load_network_from_env_file(env_file)
    kwargs: dict = {"replace": args.replace, "venue": venue}
    if timeout_s is not None:
        kwargs["timeout_s"] = timeout_s
    try:
        flow = cw.ConnectFlow(env_file, **kwargs)
        url = flow.start()
    except cw.ConnectError as e:
        print(json.dumps({"ok": False, "state": "failed", "code": e.code, "error": e.message,
                          "next": cw._next_for(e.code)}))
        return 1

    net = flow.network.upper()
    print(f"pathiel wallet connect on {net}"
          + (" (REAL MONEY)" if flow.network == "mainnet" else " (test funds)"), file=sys.stderr)
    print(f"New API wallet: {flow.agent_address}", file=sys.stderr)
    print(f"Open this page in the browser that has your wallet (it works once and closes in "
          f"10 minutes):\n  {url}", file=sys.stderr)
    sys.stderr.flush()
    if not args.no_browser:
        if opener is None:
            import webbrowser
            opener = webbrowser.open
        try:
            opener(url)
        except Exception:   # noqa: BLE001 - the URL is printed; opening is a convenience
            pass
    try:
        result = flow.serve()
    except KeyboardInterrupt:
        flow.cancel()
        result = flow.serve()
    print(json.dumps(result))
    return 0 if result.get("ok") and result.get("state") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
