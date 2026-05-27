#!/usr/bin/env python3
"""hermes — autonomous multi-market trading agent for Hyperliquid.

Usage:
  hermes scan               Scan all markets for triggers
  hermes research <COIN>    AI analysis on a coin
  hermes execute            Execute trade from last analysis
  hermes status             Show agent state, positions, config
  hermes trades             Show trade history
  hermes account            Show HL account state
  hermes config [K=V ...]   Show or set config values
  hermes start              Run autonomous scanning loop
  hermes stop               Stop autonomous loop
  hermes version            Show version
"""

from __future__ import annotations

import os
import signal
import sys

from hermes_trader import __version__


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


def _import_memory():
    from hermes_trader.agents.memory import memory
    memory.load()
    return memory


# ── Commands ──────────────────────────────────────────────────────────

def cmd_scan():
    """Scan all markets for triggers."""
    from hermes_trader.agents.perception import scan_once
    from hermes_trader.client.universe import get_universe

    print_banner()
    print("  Scanning all markets for trigger signals...\n")

    universe = get_universe()
    perceptions = scan_once(universe=universe, min_score=75)

    if not perceptions:
        print("  No triggers fired above threshold (score >= 75).\n")
        return

    print(f"  {len(perceptions)} trigger(s) detected:\n")
    for i, p in enumerate(perceptions[:20], 1):
        score = p.get("composite_score", 0)
        coin = p.get("coin", "?")
        mid = p.get("mid", 0)
        fired = [t.get("name", "?") for t in p.get("triggers", []) if t.get("fired")]
        print(f"  [{i:>2}] {coin:<12} score={score:>6.1f}  mid={mid:>12.2f}  triggers={', '.join(fired)}")
    print()


def cmd_research(coin: str):
    """Run AI research on a coin."""
    from hermes_trader.agents.research import research
    from hermes_trader.agents.memory import memory
    from hermes_trader.client.hl_client import fetch_all_mids

    print_banner()
    coin = coin.upper()

    # Look up perception from memory
    memory.load()
    perception = None
    for p in reversed(memory.get_recent_perceptions(100)):
        if p.get("coin") == coin:
            perception = p
            break

    if not perception:
        # Fallback: create minimal perception from live mids
        mids = fetch_all_mids()
        perception = {"coin": coin, "mid": float(mids.get(coin, "0")), "type": "perp"}

    print(f"  Running AI research on {coin}...\n")
    analysis = research(coin=coin, perception=perception)

    verdict = analysis.get("verdict", "?")
    conf = analysis.get("confidence", 0)
    side = analysis.get("side") or "—"
    entry = analysis.get("entry_px", 0)
    stop = analysis.get("stop_px", 0)
    tp = analysis.get("tp_px", 0)
    reason = analysis.get("reasoning", "")[:250]

    if verdict == "PASS":
        v = GRAY(verdict)
    elif verdict == "LONG":
        v = GREEN(verdict)
    elif verdict == "SHORT":
        v = RED(verdict)
    else:
        v = YELLOW(verdict)

    print(f"  {BOLD('Coin:')}     {coin}")
    print(f"  {BOLD('Verdict:')}  {v}")
    print(f"  {BOLD('Conf:')}     {conf:.2f}")
    print(f"  {BOLD('Side:')}     {side}")
    if entry:
        print(f"  {BOLD('Entry:')}    {entry:,.2f}")
    if stop:
        print(f"  {BOLD('Stop:')}     {stop:,.2f}")
    if tp:
        print(f"  {BOLD('TP:')}       {tp:,.2f}")
    if reason:
        print(f"  {BOLD('Reason:')}   {reason}")
    print(f"  {BOLD('ID:')}       {analysis.get('id', '?')}\n")


def cmd_execute():
    """Execute trade from last confirmed analysis."""
    from hermes_trader.agents.executor import maybe_execute

    print_banner()
    memory = _import_memory()

    analyses = memory.get_all_analyses()
    for a in reversed(analyses):
        if a.get("verdict") in ("LONG", "SHORT", "CLOSE") and a.get("confidence", 0) >= 0.5:
            print(f"  Executing {a['coin']} ({a['verdict']}, conf={a['confidence']:.2f})...\n")
            result = maybe_execute(a)

            if result.get("executed"):
                print(GREEN(f"  ✅ {a['coin']} executed @ {result.get('entry_px', 0):,.2f} (${result.get('size_usd', 0):,.0f})"))
            else:
                reason = result.get("reason") or result.get("blocked_by")
                if isinstance(reason, list):
                    print(YELLOW(f"  ✗ Blocked: {', '.join(reason)}"))
                else:
                    print(YELLOW(f"  ✗ Blocked: {reason}"))
            return

    print("  No CONFIRMED analysis to execute.\n")


def cmd_status():
    """Show full agent state."""
    from hermes_trader.agents.config_store import read_agent_config

    print_banner()
    memory = _import_memory()
    config = read_agent_config()
    state = memory.get_full_state()

    wr = state.get("win_rate", {})
    print(f"  {BOLD('Mode:')}         {config.get('mode', 'OFF')}")
    print(f"  {BOLD('Equity:')}       ${state.get('equity', 0):,.2f}")
    print(f"  {BOLD('Win Rate:')}     {wr.get('rate', 0):.0%} ({wr.get('wins', 0)}/{wr.get('total', 0)})")
    print(f"  {BOLD('Daily PnL:')}    ${state.get('daily_pnl', 0):,.2f}")
    print(f"  {BOLD('Analyses:')}     {len(state.get('recent_analyses', []))}")
    print(f"  {BOLD('Perceptions:')}  {len(state.get('recent_perceptions', []))}")

    positions = state.get("open_positions", [])
    if positions:
        print(f"\n  {BOLD('Open Positions:')}")
        for p in positions:
            szi = p.get("szi", 0)
            coin = p.get("position", {}).get("coin", "?")
            side = "long" if float(szi) > 0 else "short"
            entry = p.get("position", {}).get("entryPx", 0)
            pnl = p.get("unrealizedPnl", 0)
            print(f"    {coin:<12} {side:>6}  szi={float(szi):>12.4f}  entry={float(entry):>12.2f}  pnl={float(pnl):>+10,.2f}")
    else:
        print(f"\n  {BOLD('Open Positions:')}  none")
    print()


def cmd_trades():
    """Show trade history."""
    print_banner()
    memory = _import_memory()
    trades = memory.get_all_trades()

    if not trades:
        print("  No trades yet.\n")
        return

    print(f"  {len(trades)} trade(s):\n")
    for i, t in enumerate(reversed(trades), 1):
        coin = t.get("coin", "?")
        side = t.get("side", "?")
        entry = t.get("entry_px", 0)
        size = t.get("size_usd", 0)
        pnl = t.get("pnl")
        exit_px = t.get("exitPx")

        pnl_str = f"${pnl:+,.2f}" if pnl is not None else "—"
        extra = f"  exit={float(exit_px):,.2f}" if exit_px else ""

        verdict = t.get("verdict")
        if verdict == "PASS":
            verdict_str = GRAY("PASS")
        elif verdict == "LONG":
            verdict_str = GREEN("LONG")
        elif verdict == "SHORT":
            verdict_str = RED("SHORT")
        else:
            verdict_str = YELLOW(str(verdict))

        print(f"  [{i:>2}] {verdict_str}  {coin:<12} {side:>6}  entry={float(entry):>12.2f}  size=${float(size):>8,.0f}  {pnl_str:>12}{extra}")
    print()


def cmd_account():
    """Show HL account state."""
    from hermes_trader.client.hl_client import fetch_account_state, resolve_user_address

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
    from hermes_trader.agents.config_store import read_agent_config, write_agent_config

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

            config[key] = val
            print(f"  {key}: {old!r:12} → {val!r}")

        write_agent_config(config)
        print("\n  ✓ Config updated.\n")


def cmd_start():
    """Start autonomous scanning loop in background."""
    import subprocess

    pid_file = os.path.expanduser("~/.hermes.pid")
    if os.path.exists(pid_file):
        old_pid = open(pid_file).read().strip()
        try:
            os.kill(int(old_pid), 0)
            print(f"  Scanner already running (PID {old_pid}). Run 'hermes stop' first.\n")
            return
        except (OSError, ValueError):
            pass

    print("  Starting autonomous scanner...\n")
    env = os.environ.copy()
    proc = subprocess.Popen(
        [sys.executable, "-m", "hermes_trader"],
        env=env,
        start_new_session=True,
    )

    open(pid_file, "w").write(str(proc.pid))
    print(f"  Scanner running (PID {proc.pid}).\n")
    print("  To stop:  hermes stop")
    print("  Monitor:  hermes status\n")


def cmd_stop():
    """Stop autonomous scanning loop."""
    pid_file = os.path.expanduser("~/.hermes.pid")
    if not os.path.exists(pid_file):
        print("  Scanner not running.\n")
        return

    pid = open(pid_file).read().strip()
    try:
        os.kill(int(pid), signal.SIGTERM)
        print(f"  Scanner stopped (PID {pid}).\n")
    except (OSError, ValueError):
        print("  Scanner not running (stale PID).\n")
    finally:
        try:
            os.remove(pid_file)
        except OSError:
            pass


def cmd_version():
    print_banner()
    print(f"  Hermes-Trader v{__version__}\n")


# ── Main ──────────────────────────────────────────────────────────────

COMMANDS = {
    "scan": cmd_scan,
    "research": cmd_research,
    "execute": cmd_execute,
    "status": cmd_status,
    "trades": cmd_trades,
    "account": cmd_account,
    "config": cmd_config,
    "start": cmd_start,
    "stop": cmd_stop,
    "version": cmd_version,
}


def main():
    if len(sys.argv) < 2:
        print_banner()
        print("  Usage: hermes <command> [args]")
        print()
        print("  Commands:")
        print("    scan               Scan all markets for triggers")
        print("    research <COIN>    AI analysis on a coin")
        print("    execute            Execute trade from last analysis")
        print("    status             Show agent state, positions, config")
        print("    trades             Show trade history")
        print("    account            Show HL account state")
        print("    config [K=V ...]   Show or set config values")
        print("    start              Start autonomous scanning loop")
        print("    stop               Stop autonomous scanning loop")
        print("    version            Show version")
        print()
        return

    cmd = sys.argv[1].lower()
    handler = COMMANDS.get(cmd)

    if not handler:
        print(f"  Unknown command: {cmd}")
        print("  Run 'hermes' for available commands.\n")
        return

    handler(*sys.argv[2:])


if __name__ == "__main__":
    main()
