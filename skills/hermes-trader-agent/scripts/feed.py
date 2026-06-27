#!/usr/bin/env python3
"""Live activity feed for hermes-trader.

The trading loop appends every scan / TA filter / research / execute / error
to `~/.hermes-trader-session-log.jsonl`. This script prints the last N events
in human-readable form, or follows the file in real time with --follow.

Usage:
    python3 feed.py                # last 20 events, one-shot
    python3 feed.py -n 50          # last 50 events
    python3 feed.py --follow       # tail -f style, never exits
    python3 feed.py --since 5m     # only events newer than 5 minutes
    python3 feed.py --filter execute,error    # only those event types

Designed for both interactive use (humans tailing what their bot is doing)
and cron delivery (one-shot, since=1h, no follow).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

SESSION_LOG = Path(os.environ.get(
    "SESSION_LOG_PATH",
    os.path.expanduser("~/.hermes-trader-session-log.jsonl"),
))


def _parse_duration(s: str) -> int:
    """Convert '5m' / '2h' / '30s' / '1d' to milliseconds."""
    m = re.fullmatch(r"(\d+)\s*([smhd])", s.strip().lower())
    if not m:
        raise ValueError(f"bad duration: {s!r} — use e.g. 5m, 2h, 30s, 1d")
    n = int(m.group(1))
    unit_ms = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}[m.group(2)]
    return n * unit_ms


def _fmt_ts(ts_ms: int) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts_ms / 1000))


def _fmt_event(e: dict) -> str:
    """Render one event as a compact human-readable line."""
    ts = _fmt_ts(int(e.get("ts", 0)))
    ev = e.get("event", "?")
    if ev == "loop_start":
        return f"[{ts}] ▶  loop_start  interval={e.get('scan_interval')}s min_score={e.get('min_score')}"
    if ev == "loop_stop":
        return f"[{ts}] ■  loop_stop"
    if ev == "loop_heartbeat":
        eq = e.get("equity", 0)
        av = e.get("available", 0)
        spot = e.get("spot_usdc", 0)
        pnl = e.get("daily_pnl", 0)
        op = e.get("open_positions", 0)
        line = (f"[{ts}] ♥  perp=${eq:.2f}  avail=${av:.2f}  spot=${spot:.2f}  "
                f"dailyPnL=${pnl:+.2f}  open={op}")
        if eq <= 0 and spot > 0:
            line += "  ⚠ funds in spot — transfer spot->perp to trade"
        return line
    if ev == "scan":
        n = e.get("triggers", 0)
        coins = e.get("coins") or []
        body = f"{n} triggers"
        if coins:
            body += " — " + ", ".join(coins[:8])
            if len(coins) > 8:
                body += f" (+{len(coins)-8})"
        return f"[{ts}] •  scan       {body}"
    if ev == "ta_skip":
        sig = e.get("signal")
        label = "ta_skip" if sig in {"WEAK", "REJECTED"} else "skip"
        reason = e.get("reason")
        body = f"{e.get('coin')} ({sig})"
        if reason:
            body += f" — {reason}"
        return f"[{ts}] ✗  {label:<9} {body}"
    if ev == "entry_preflight":
        reason = e.get("reason") or ""
        return f"[{ts}] ✗  preflight  {e.get('coin')} — {reason}"
    if ev == "research":
        v = e.get("verdict")
        c = e.get("confidence", 0)
        brain = e.get("ai_brain_provider")
        via = f" via {brain}" if brain else ""
        return f"[{ts}] ?  research   {e.get('coin')} → {v} (conf {c}{via})"
    if ev == "execute":
        ok = "✓" if e.get("executed") else "✗"
        return (f"[{ts}] {ok}  execute    {e.get('coin')} {e.get('side','?')}  "
                f"{e.get('detail','')}")
    if ev == "error":
        coin = e.get("coin") or "loop"
        return f"[{ts}] !  error      {coin}: {e.get('error','?')[:120]}"
    # Unknown event — dump compactly.
    rest = {k: v for k, v in e.items() if k not in ("ts", "event")}
    return f"[{ts}] ?  {ev}        {rest}"


def _read_all(path: Path):
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return


def _print_events(events, filt: Optional[set]) -> int:
    n = 0
    for e in events:
        if filt and e.get("event") not in filt:
            continue
        print(_fmt_event(e))
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--limit", type=int, default=20,
                    help="show last N events (default 20)")
    ap.add_argument("-f", "--follow", action="store_true",
                    help="tail -f style: print new events as they arrive")
    ap.add_argument("--since", default=None,
                    help="only events newer than e.g. 5m, 2h, 1d")
    ap.add_argument("--filter", default=None,
                    help="comma-separated event types: scan,research,execute,error")
    args = ap.parse_args()

    if not SESSION_LOG.is_file():
        print(f"(no activity yet — {SESSION_LOG} does not exist)")
        return 0

    filt = set(args.filter.split(",")) if args.filter else None
    cutoff = (int(time.time() * 1000) - _parse_duration(args.since)) if args.since else None

    events = list(_read_all(SESSION_LOG))
    if cutoff is not None:
        events = [e for e in events if int(e.get("ts", 0)) >= cutoff]
    if not args.follow and not args.since:
        events = events[-args.limit:]

    header = f"=== hermes-trader activity feed ({SESSION_LOG.name}) ==="
    print(header)
    shown = _print_events(events, filt)
    if shown == 0:
        print("(nothing in window)")

    if not args.follow:
        return 0

    # Follow mode — poll the file for new lines.
    print("--- following (Ctrl-C to stop) ---")
    last_size = SESSION_LOG.stat().st_size
    try:
        while True:
            time.sleep(1.0)
            size = SESSION_LOG.stat().st_size
            if size <= last_size:
                # File rotated or truncated; reset.
                if size < last_size:
                    last_size = 0
                continue
            with SESSION_LOG.open() as f:
                f.seek(last_size)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if filt and e.get("event") not in filt:
                        continue
                    print(_fmt_event(e))
            last_size = size
    except KeyboardInterrupt:
        print("\n(stopped)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
