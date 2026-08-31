#!/usr/bin/env python3
"""Restart hermes processes that have died. Run by the scheduler, every 2 min.

Why this exists
---------------
`trading_loop.py` already self-heals when it HANGS: a watchdog thread re-execs
the process if no scan completes inside `HERMES_WATCHDOG_TIMEOUT_S`. That
covers a live-but-wedged loop. It cannot cover a loop that is GONE — OOM, a
SIGKILL, a closed terminal, the Mac sleeping. Nothing restarted those, and the
cost is on record: a full week of no trading in June, diagnosed after the fact
as "the Mac was asleep".

The obvious fix is launchd, and launchd does not work here. macOS TCC denies
launchd (and cron) access to ~/Documents, so a job defined there never runs and
never says why — which is exactly how the plist ended up renamed `.disabled`.
The scheduler is already a plain process started from a granted terminal, so it
already has the access launchd cannot get. Supervision belongs there.

Two rules keep this from being worse than the problem:

1. The operator always wins. `restart.sh stoploop` is the kill switch; a
   supervisor that restarts the loop two minutes later has silently deleted it.
   Every stop-and-stay-stopped action writes its component to
   `.state/supervisor_halt.json`, every start clears it, and a halted component
   is never restarted here.

2. A crash loop is a bug, not a thing to paper over. If a component needs more
   than MAX_RESTARTS inside RESTART_WINDOW_S it is left down and reported.
   Restarting a process that dies on startup forever would just hide the
   traceback that explains it.

The scheduler itself is deliberately NOT supervised: this runs as its child, so
restarting it would kill the process doing the restarting.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, ".state", "supervisor.json")
HALT_FILE = os.path.join(ROOT, ".state", "supervisor_halt.json")
RESTART_SH = os.path.join(ROOT, "scripts", "restart.sh")

MAX_RESTARTS = 3
RESTART_WINDOW_S = 3600.0

# component -> (ps pattern, restart.sh action, human name)
COMPONENTS: Dict[str, Tuple[str, str, str]] = {
    "loop":    ("scripts/trading_loop.py",   "loop",   "trading loop"),
    "server":  ("hermes_trader.server",      "server", "API server"),
    "rotator": ("log_rotate.py --daemon",    "rotate", "log rotator"),
}


def _read(path: str, default: Any) -> Any:
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def _write(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def halted() -> List[str]:
    """Components the operator deliberately stopped. Never restart these."""
    val = _read(HALT_FILE, {}).get("halted")
    return [str(x) for x in val] if isinstance(val, list) else []


def _ancestors() -> set:
    """Our own pid plus every ancestor, so we never mistake ourselves for the
    thing we are supervising."""
    out = set()
    pid = os.getpid()
    for _ in range(20):                      # bounded: no cycle can hang us
        if pid <= 1:
            break
        out.add(pid)
        try:
            r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                               capture_output=True, text=True, timeout=10)
            pid = int(r.stdout.strip())
        except Exception:
            break
    return out


def alive(pattern: str, exclude: set | None = None) -> bool:
    """Is a process matching `pattern` running, not counting ourselves?

    A plain substring test against `ps ax` looked right and was wrong: it
    matches ANY process whose command line merely mentions the pattern —
    including the shell that invoked this script, and including restart.sh
    while it is starting the very thing being checked. Caught 2026-08-31 when
    the supervisor reported the log rotator "running" one second after it had
    been SIGKILLed, because the test shell's own argv contained the pattern.

    `pgrep -f` gives pids instead of text, so the caller's process tree can be
    subtracted. restart.sh guards the same way for the same reason.
    """
    exclude = exclude if exclude is not None else _ancestors()
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True,
                           text=True, timeout=15)
    except Exception:
        return True          # cannot tell: assume alive, never restart blind
    pids = {int(x) for x in r.stdout.split() if x.strip().isdigit()}
    return bool(pids - exclude)


def _recent(history: List[float], now: float) -> List[float]:
    return [t for t in history if now - t < RESTART_WINDOW_S]


def decide(component: str, is_alive: bool, is_halted: bool,
           history: List[float], now: float) -> Tuple[str, str]:
    """Pure: what to do about one component. Returns (action, reason).

    Split out from the doing so the policy is testable without processes.
    """
    if is_alive:
        return "none", "running"
    if is_halted:
        return "none", "deliberately stopped by the operator"
    recent = _recent(history, now)
    if len(recent) >= MAX_RESTARTS:
        return "give_up", (
            f"down, but restarted {len(recent)}x in the last "
            f"{RESTART_WINDOW_S / 3600:.0f}h — crash loop, not a blip; "
            f"leaving it down so the failure stays visible")
    return "restart", f"down, restart {len(recent) + 1} of {MAX_RESTARTS}"


def main(argv: List[str] | None = None) -> int:
    dry = "--dry-run" in (argv if argv is not None else sys.argv[1:])
    mine = _ancestors()
    now = time.time()
    state = _read(STATE, {})
    hist_all: Dict[str, List[float]] = {
        k: [float(t) for t in v]
        for k, v in (state.get("restarts") or {}).items() if isinstance(v, list)
    }
    down = halted()
    report = []
    rc = 0

    for comp, (pattern, action, label) in COMPONENTS.items():
        history = hist_all.get(comp, [])
        what, why = decide(comp, alive(pattern, mine), comp in down, history, now)
        line = f"[supervisor] {label}: {why}"
        if what == "restart":
            if dry:
                line += " (dry-run, not restarting)"
            else:
                r = subprocess.run(["bash", RESTART_SH, action], cwd=ROOT,
                                   capture_output=True, text=True, timeout=180)
                ok = r.returncode == 0
                line += " — restarted" if ok else f" — RESTART FAILED rc={r.returncode}"
                if not ok:
                    rc = 1
                hist_all[comp] = _recent(history, now) + [now]
        elif what == "give_up":
            rc = 1
        report.append(line)
        print(line, flush=True)

    if not dry:
        _write(STATE, {"ts": now, "checked": sorted(COMPONENTS),
                       "halted": down, "restarts": hist_all,
                       "report": report})
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
