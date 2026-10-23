#!/usr/bin/env python3
"""pathiel — CLI for the headless Hyperliquid desk.

The autonomous scan/research/execute engine is retired (2026-10-23, wave 2):
Claude places every trade through the MCP server
(`scripts/pathiel-mcp-server.py`), so the commands that drove it (`scan`,
`research`, `execute`, `status`, `trades`) are gone with it. The server
itself runs as `python -m pathiel.server`, not through this CLI.

Usage:
  pathiel account             Show HL account state
  pathiel config [K=V ...]    Show or set config values
  pathiel version             Show version
"""

from __future__ import annotations

import sys

from pathiel import __version__


def print_banner():
    print("""
╔═══════════════════════════════════════════════════╗
║        🦅  H E R M E S  —  Autonomous Agent      ║
║      Multi-Market Trading for Hyperliquid        ║
╚═══════════════════════════════════════════════════╝
""")


# ── Color helpers ─────────────────────────────────────────────────────

def color(text: str, code: str) -> str:
    return f"{code}{text}\033[0m"


GREEN = lambda t: color(t, "\033[32m")
RED = lambda t: color(t, "\033[31m")
YELLOW = lambda t: color(t, "\033[33m")
GRAY = lambda t: color(t, "\033[90m")
BOLD = lambda t: color(t, "\033[1m")


# ── Commands ──────────────────────────────────────────────────────────

def cmd_account():
    """Show HL account state."""
    from pathiel.client.hl_client import fetch_account_state, resolve_user_address

    print_banner()
    user = resolve_user_address()
    if not user:
        print("  HL wallet not configured. Set HYPERLIQUID_WALLET_ADDRESS.\n")
        return

    print("  Fetching account state...\n")
    try:
        # include_hip3=True so the CLI shows xyz/vntl/km positions and
        # aggregates their equity into the total.
        state = fetch_account_state(user, include_hip3=True)
        equity = state.get("equity", 0)
        notional = state.get("total_ntl", 0)

        print(f"  {BOLD('Equity:')}     ${equity:>12,.2f}")
        print(f"  {BOLD('Notional:')}   ${notional:>12,.2f}")

        positions = state.get("asset_positions", [])
        if positions:
            print(f"\n  {BOLD('Positions:')}")
            for p in positions:
                pos = p.get("position", {})
                szi = float(pos.get("szi", "0"))
                coin = pos.get("coin", "?")
                side = "long" if szi > 0 else "short"
                entry = float(pos.get("entryPx", "0"))
                unimpl = float(pos.get("unrealizedPnl", "0"))
                print(f"    {coin:<12} {side:>6}  szi={abs(szi):>12.4f}  entry={entry:>12.2f}  pnl={unimpl:+,.2f}")
        else:
            print("\n  No open positions.\n")
    except Exception as e:
        print(f"  Error: {e}\n")


def cmd_config(*args):
    """Show or set config."""
    from pathiel.agents.config_store import read_agent_config, update_agent_config

    print_banner()
    config = read_agent_config()

    if not args:
        print("  Current config:\n")
        for key, val in sorted(config.items()):
            if isinstance(val, list) and not val:
                print(f"    {key}: [] (empty = all)")
            elif isinstance(val, str) and len(val) > 30:
                print(f"    {key}: {val[:27]}...")
            else:
                print(f"    {key}: {val}")
        print()
    else:
        print("  Updating config:\n")
        updates = {}
        for arg in args:
            if "=" not in arg:
                print(f"  Invalid: {arg} (use KEY=VALUE)")
                continue
            key, val = arg.split("=", 1)
            key = key.strip()
            val = val.strip()
            old = config.get(key, "✗")

            # Type coercion
            if val.lower() == "true":
                val = True
            elif val.lower() == "false":
                val = False
            else:
                try:
                    val = int(val)
                except ValueError:
                    try:
                        val = float(val)
                    except ValueError:
                        pass

            updates[key] = val
            print(f"  {key}: {old!r:12} → {val!r}")

        update_agent_config(updates)
        print("\n  ✓ Config updated.\n")


def cmd_version():
    print_banner()
    print(f"  Pathiel-Trader v{__version__}\n")


# ── Main ──────────────────────────────────────────────────────────────

COMMANDS = {
    "account": cmd_account,
    "config": cmd_config,
    "version": cmd_version,
}


def main():
    if len(sys.argv) < 2:
        print_banner()
        print("  Usage: pathiel <command> [args]")
        print()
        print("  Commands:")
        print("    account            Show HL account state")
        print("    config [K=V ...]   Show or set config values")
        print("    version            Show version")
        print()
        print("  The server runs separately: python -m pathiel.server")
        print()
        return

    cmd = sys.argv[1].lower()
    handler = COMMANDS.get(cmd)

    if not handler:
        print(f"  Unknown command: {cmd}")
        print("  Run 'pathiel' for available commands.\n")
        return

    handler(*sys.argv[2:])


if __name__ == "__main__":
    main()
