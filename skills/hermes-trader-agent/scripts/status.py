#!/usr/bin/env python3
"""Plain-text status snapshot of the hermes-trader system.

Run from anywhere:

    python3 skills/hermes-trader-agent/scripts/status.py

Local-only: reads the project state files and checks running processes. No
network, no credentials. Safe to run any time (e.g. from a cron report job).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SESSION_LOG = Path.home() / ".hermes-trader-session-log.jsonl"


def _load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as e:
        return {"_error": str(e)}


def _process_running(pattern: str) -> bool:
    try:
        out = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5
        )
        return out.returncode == 0 and bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def main() -> int:
    print("=== hermes-trader status ===")
    print(f"repo: {ROOT}")

    config = _load_json(ROOT / ".agent-config.json")
    if config is None:
        print("config: .agent-config.json not found (defaults: mode OFF)")
    elif "_error" in config:
        print(f"config: unreadable — {config['_error']}")
    else:
        print(f"mode  : {config.get('mode', 'OFF')}")
        caps = {k: config[k] for k in (
            "maxTradeNotionalUsd", "max_trade_notional_usd",
            "maxConcurrent", "minAiConfidence") if k in config}
        if caps:
            print(f"caps  : {caps}")

    mem = _load_json(ROOT / ".agent-memory.json")
    if mem is None:
        print("memory: .agent-memory.json not found (no trade history yet)")
    elif "_error" in mem:
        print(f"memory: unreadable — {mem['_error']}")
    else:
        trades = mem.get("trades", [])
        analyses = mem.get("analyses", [])
        closed = [t for t in trades if t.get("pnl") is not None]
        wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        print(f"equity: ${float(mem.get('equity', 0)):.2f}   "
              f"daily PnL: ${float(mem.get('dailyPnl', 0)):.2f}")
        print(f"trades: {len(trades)} recorded, {len(closed)} closed, "
              f"win rate {wins}/{len(closed) or 0}")
        print(f"analyses: {len(analyses)} recorded")
        open_pos = mem.get("openPositions", [])
        print(f"open positions: {len(open_pos)}")

    loop = _process_running("trading_loop.py")
    mcp = _process_running("hermes-mcp-server.py")
    print(f"trading loop : {'RUNNING' if loop else 'stopped'}")
    print(f"MCP server   : {'RUNNING' if mcp else 'stopped (Hermes spawns it on demand)'}")

    if SESSION_LOG.is_file():
        lines = [ln for ln in SESSION_LOG.read_text().splitlines() if ln.strip()]
        print(f"session log  : {len(lines)} entries — last {min(3, len(lines))}:")
        for ln in lines[-3:]:
            try:
                e = json.loads(ln)
                print(f"  {e.get('event', '?')}: "
                      f"{ {k: v for k, v in e.items() if k != 'event'} }")
            except json.JSONDecodeError:
                print(f"  (unparseable) {ln[:80]}")
    else:
        print("session log  : none yet")

    return 0


if __name__ == "__main__":
    sys.exit(main())
