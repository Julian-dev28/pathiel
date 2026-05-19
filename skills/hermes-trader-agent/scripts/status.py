#!/usr/bin/env python3
"""Plain-text status snapshot of the hermes-trader system.

Run from anywhere:

    python3 skills/hermes-trader-agent/scripts/status.py

Shows BOTH local cached state (.agent-memory.json — what the loop last
persisted) and LIVE state pulled directly from Hyperliquid. The live read
is the source of truth; the cached read tells you if the loop is keeping
its memory fresh. A large gap between them = loop heartbeat is broken.

Live read needs HYPERLIQUID_MASTER_ADDRESS or HYPERLIQUID_WALLET_ADDRESS
in the environment (loaded from .env.local in the repo root). If neither
is set, only the cached view is printed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
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


def _load_env_local(repo_root: Path) -> None:
    """Best-effort: hydrate os.environ from .env.local so we can talk to HL."""
    env_path = repo_root / ".env.local"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        # Don't clobber values the user already exported.
        os.environ.setdefault(key.strip(), val.strip())


def _process_running(pattern: str) -> bool:
    try:
        out = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5
        )
        return out.returncode == 0 and bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def _fetch_live_state(repo_root: Path):
    """Pull live equity + positions from Hyperliquid. Returns dict or None."""
    _load_env_local(repo_root)
    if not (os.environ.get("HYPERLIQUID_MASTER_ADDRESS")
            or os.environ.get("HYPERLIQUID_WALLET_ADDRESS")):
        return None
    # Lazy import so the script still works (cache-only) outside the repo.
    sys.path.insert(0, str(repo_root))
    try:
        from hermes_trader.client.hl_client import (
            fetch_account_state, resolve_user_address,
        )
    except Exception as e:
        return {"_error": f"import failed: {e}"}
    try:
        user = resolve_user_address()
        if not user:
            return None
        return fetch_account_state(user)
    except Exception as e:
        return {"_error": f"HL fetch failed: {e}"}


def _clock(ts_ms) -> str:
    """Render an epoch-ms timestamp as local HH:MM:SS, or '--:--:--'."""
    try:
        return time.strftime("%H:%M:%S", time.localtime(int(ts_ms) / 1000))
    except (TypeError, ValueError, OSError):
        return "--:--:--"


def _fmt_event(e: dict) -> str:
    """One-line human rendering of a session-log event."""
    ev = e.get("event", "?")
    when = _clock(e.get("ts"))
    if ev == "scan":
        n = e.get("triggers", e.get("perceptions", 0))
        coins = ", ".join(e.get("coins", [])) or "none"
        return f"{when}  scan      {n} trigger(s): {coins}"
    if ev == "ta_skip":
        return f"{when}  ta-skip   {e.get('coin')} ({e.get('signal')})"
    if ev == "research":
        return (f"{when}  research  {e.get('coin')} -> {e.get('verdict')} "
                f"(conf {e.get('confidence')})")
    if ev == "execute":
        ok = "EXECUTED" if e.get("executed") else "not executed"
        return (f"{when}  execute   {e.get('coin')} {e.get('side', '')} "
                f"{ok} — {e.get('detail')}")
    if ev == "loop_heartbeat":
        return (f"{when}  heartbeat equity ${float(e.get('equity', 0)):.2f}  "
                f"avail ${float(e.get('available', 0)):.2f}  "
                f"pnl ${float(e.get('daily_pnl', 0)):.2f}  "
                f"pos {e.get('open_positions', 0)}")
    if ev in ("loop_start", "loop_stop"):
        return f"{when}  {ev}"
    if ev == "error":
        return f"{when}  error     {e.get('coin', '')} {e.get('error', '')}".rstrip()
    rest = {k: v for k, v in e.items() if k not in ("event", "ts")}
    return f"{when}  {ev}  {rest}"


def _print_session_tail(n: int = 8) -> None:
    if not SESSION_LOG.is_file():
        print("session log  : none yet — start the trading loop to populate it")
        return
    lines = [ln for ln in SESSION_LOG.read_text().splitlines() if ln.strip()]
    events = []
    for ln in lines[-n:]:
        try:
            events.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    if not events:
        print("session log  : present but empty")
        return
    last_ts = events[-1].get("ts")
    age = ""
    if last_ts:
        age = f", newest {(time.time() * 1000 - int(last_ts)) / 60_000:.0f} min ago"
    print(f"session log  : {len(lines)} events{age} — last {len(events)}:")
    for e in events:
        print(f"  {_fmt_event(e)}")


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
    cached_equity = 0.0
    if mem is None:
        print("memory: .agent-memory.json not found (no trade history yet)")
    elif "_error" in mem:
        print(f"memory: unreadable — {mem['_error']}")
    else:
        trades = mem.get("trades", [])
        analyses = mem.get("analyses", [])
        closed = [t for t in trades if t.get("pnl") is not None]
        wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        cached_equity = float(mem.get("equity", 0) or 0)
        open_pos = mem.get("openPositions", [])
        print(f"cached equity : ${cached_equity:.2f}   "
              f"daily PnL: ${float(mem.get('dailyPnl', 0)):.2f}  "
              f"open: {len(open_pos)}")
        print(f"trades   : {len(trades)} recorded, {len(closed)} closed, "
              f"win rate {wins}/{len(closed) or 0}")
        print(f"analyses : {len(analyses)} recorded")

    # ── Live HL state (source of truth) ──────────────────────────────────────
    live = _fetch_live_state(ROOT)
    if live is None:
        print("live HL state : (no wallet env var — cached view only)")
    elif "_error" in live:
        print(f"live HL state : ERROR — {live['_error']}")
    else:
        live_equity = float(live.get("equity", 0) or 0)
        available = float(live.get("available", 0) or 0)
        total_ntl = float(live.get("total_ntl", 0) or 0)
        positions = live.get("asset_positions", []) or []
        print(f"LIVE equity   : ${live_equity:.2f}   "
              f"available: ${available:.2f}   notional: ${total_ntl:.2f}")
        print(f"LIVE positions: {len(positions)} open")
        for ap in positions:
            p = ap.get("position", {}) if isinstance(ap, dict) else {}
            coin = p.get("coin", "?")
            szi = float(p.get("szi", 0) or 0)
            side = "LONG " if szi > 0 else "SHORT"
            entry = float(p.get("entryPx", 0) or 0)
            upnl = float(p.get("unrealizedPnl", 0) or 0)
            print(f"  {side} {coin:<6} sz={szi:+.4f} entry={entry:.4f} uPnL=${upnl:+.2f}")
        drift = live_equity - cached_equity
        if abs(drift) > 0.5 and cached_equity == 0:
            print("  ⚠ cached equity is 0 — loop heartbeat hasn't fired yet "
                  "(restart loop or wait one cycle).")
        elif abs(drift) > max(1.0, 0.05 * live_equity):
            print(f"  ⚠ cached ↔ live drift = ${drift:+.2f} — heartbeat is stale.")

    loop = _process_running("trading_loop.py")
    mcp = _process_running("hermes-mcp-server.py")
    print(f"trading loop : {'RUNNING' if loop else 'stopped'}")
    print(f"MCP server   : {'RUNNING' if mcp else 'stopped (Hermes spawns it on demand)'}")

    _print_session_tail()

    # ── TA verdict distribution (last 30 scans) ─────────────────────────────
    ta_counts = {"CONFIRMED": 0, "WEAK": 0, "REJECTED": 0, "missing": 0}
    confs = []
    for p in (mem.get("perceptions") or [])[-30:]:
        sig = p.get("taSignal")
        if sig in ta_counts:
            ta_counts[sig] += 1
        else:
            ta_counts["missing"] += 1
        if p.get("composite_score"):
            confs.append(p.get("composite_score"))
    print(f"TA verdicts  : CONFIRMED={ta_counts['CONFIRMED']} WEAK={ta_counts['WEAK']} "
          f"REJECTED={ta_counts['REJECTED']} (of last 30)")
    if confs:
        print(f"avg comp score (last 30): {sum(confs)/len(confs):.1f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
