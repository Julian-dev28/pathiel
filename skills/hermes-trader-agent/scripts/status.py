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


def _process_running(pattern: str):
    blocked = False
    try:
        out = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and bool(out.stdout.strip()):
            return True
        if "sysmond" in (out.stderr or "") or "Cannot get process list" in (out.stderr or ""):
            blocked = True
    except (OSError, subprocess.SubprocessError):
        blocked = True
    try:
        out = subprocess.run(
            ["ps", "ax", "-o", "command="], capture_output=True, text=True, timeout=5
        )
        return out.returncode == 0 and pattern in out.stdout
    except (OSError, subprocess.SubprocessError):
        return None if blocked else False


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
        return fetch_account_state(user, include_hip3=True)
    except Exception as e:
        return {"_error": f"HL fetch failed: {e}"}


def _active_claim_books(repo_root: Path) -> set[str]:
    sys.path.insert(0, str(repo_root))
    try:
        from hermes_trader.agents.rebalancer_owned import active_claim_books
        return active_claim_books()
    except Exception:
        return {"xs_momentum", "rally_exhaustion"}


def _print_claims_audit(repo_root: Path, live_positions=None) -> None:
    """Surface stale/mismatched book claims before they suppress strategy books."""
    active_books = _active_claim_books(repo_root)
    data = _load_json(repo_root / ".rebalancer_claims.json")
    if data is None:
        print("claims       : none yet")
        return
    if isinstance(data, dict) and "_error" in data:
        print(f"claims       : unreadable — {data['_error']}")
        return
    raw = data.get("claims") if isinstance(data, dict) else {}
    claims = {str(k): str(v) for k, v in (raw or {}).items()}
    owners = sorted(set(claims.values()))
    print(f"claims       : {len(claims)} coin(s), owners={owners or 'none'}")

    stale = {coin: owner for coin, owner in claims.items() if owner not in active_books}
    if stale:
        sample = ", ".join(f"{c}={b}" for c, b in sorted(stale.items())[:10])
        more = f" (+{len(stale) - 10} more)" if len(stale) > 10 else ""
        print(f"  ⚠ stale claims block live books until scrubbed: {sample}{more}")

    xs_state = _load_json(repo_root / ".xs_momentum_positions.json") or {}
    if isinstance(xs_state, dict) and "_error" in xs_state:
        print(f"  ⚠ xs_momentum state unreadable — {xs_state['_error']}")
        return
    xs_owned = set(xs_state.get("longs") or []) | set(xs_state.get("shorts") or [])
    xs_claimed = {coin for coin, owner in claims.items() if owner == "xs_momentum"}
    missing_claims = sorted(xs_owned - xs_claimed)
    orphan_claims = sorted(xs_claimed - xs_owned)
    if missing_claims:
        print(f"  ⚠ xs_momentum owns without claims: {missing_claims}")
    if orphan_claims:
        print(f"  ⚠ xs_momentum claims without owned state: {orphan_claims}")

    if live_positions is not None and xs_owned:
        live_coins = set()
        for ap in live_positions or []:
            p = ap.get("position", {}) if isinstance(ap, dict) else {}
            coin = p.get("coin")
            try:
                szi = float(p.get("szi", 0) or 0)
            except (TypeError, ValueError):
                szi = 0.0
            if coin and szi != 0:
                live_coins.add(coin)
        vanished = sorted(xs_owned - live_coins)
        if vanished:
            print(f"  ⚠ xs_momentum state has non-live coins; next rebalance should prune: {vanished}")


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
        sig = e.get("signal")
        label = "ta-skip" if sig in {"WEAK", "REJECTED"} else "skip"
        reason = e.get("reason")
        body = f"{e.get('coin')} ({sig})"
        if reason:
            body += f" — {reason}"
        return f"{when}  {label:<9} {body}"
    if ev == "entry_preflight":
        return f"{when}  preflight {e.get('coin')} — {e.get('reason')}"
    if ev == "research":
        brain = e.get("ai_brain_provider")
        via = f" via {brain}" if brain else ""
        return (f"{when}  research  {e.get('coin')} -> {e.get('verdict')} "
                f"(conf {e.get('confidence')}{via})")
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
    live_positions_for_claim_audit = None

    config = _load_json(ROOT / ".agent-config.json")
    if config is None:
        print("config: .agent-config.json not found (defaults: mode OFF + tuned strategy)")
    elif "_error" in config:
        print(f"config: unreadable — {config['_error']}")
    else:
        print(f"mode  : {config.get('mode', 'OFF')}")
        ai_brain = config.get("ai_brain") or {}
        provider = os.environ.get("AI_BRAIN_PROVIDER") or ai_brain.get("provider", "openrouter")
        source = "env" if os.environ.get("AI_BRAIN_PROVIDER") else "config"
        print(f"brain : {provider} ({source})")
        caps = {k: config[k] for k in (
            "max_trade_notional_usd",
            "max_concurrent",
            "min_ai_confidence",
            "leverage", "max_daily_loss_usd") if k in config}
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
        spot_usdc = float(live.get("spot_usdc", 0) or 0)
        total_ntl = float(live.get("total_ntl", 0) or 0)
        positions = live.get("asset_positions", []) or []
        live_positions_for_claim_audit = positions
        print(f"LIVE perp     : equity ${live_equity:.2f}   "
              f"available ${available:.2f}   notional ${total_ntl:.2f}")
        dex_equity = live.get("dex_equity") or {}
        if dex_equity:
            parts = []
            for dex, val in dex_equity.items():
                label = dex or "main"
                try:
                    parts.append(f"{label}:${float(val):.2f}")
                except (TypeError, ValueError):
                    parts.append(f"{label}:{val}")
            print("LIVE dex      : " + "  ".join(parts))
        print(f"LIVE spot     : ${spot_usdc:.2f} USDC   "
              f"(total controlled ${live_equity + spot_usdc:.2f})")
        if live_equity <= 0 and spot_usdc > 0:
            print(f"  ⚠ ${spot_usdc:.2f} sits in SPOT — the bot trades perps and "
                  f"cannot use it until you transfer USDC spot -> perp.")
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

    _print_claims_audit(ROOT, live_positions_for_claim_audit)

    loop = _process_running("trading_loop.py")
    mcp = _process_running("hermes-mcp-server.py")
    loop_label = "RUNNING" if loop is True else "stopped" if loop is False else "unknown (process list unavailable)"
    mcp_label = "RUNNING" if mcp is True else "stopped (Hermes spawns it on demand)" if mcp is False else "unknown (process list unavailable)"
    print(f"trading loop : {loop_label}")
    print(f"MCP server   : {mcp_label}")

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
