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
# State lives where every other component's state lives. The canonical rule is
# hermes_trader.agents.rebalancer_owned.state_file(): HERMES_STATE_DIR, else the
# project root. Replicated (not imported) so this stays a dependency-free script
# the scheduler can run without loading the agents package — and pinned by
# tests/test_supervisor.py so the two can never drift. They were briefly
# hardcoded to <root>/.state, which agreed with the live value by coincidence
# and disagreed everywhere else: the metrics reader looked in one place while
# these wrote to another, and both sides looked fine in isolation.
STATE_DIR = os.environ.get("HERMES_STATE_DIR") or ROOT
STATE = os.path.join(STATE_DIR, "supervisor.json")
HALT_FILE = os.path.join(STATE_DIR, "supervisor_halt.json")
RESTART_SH = os.path.join(ROOT, "scripts", "restart.sh")

MAX_RESTARTS = 3
RESTART_WINDOW_S = 3600.0

# What each managed process actually IS, not a string that appears near it.
#   kind "script": python running <root>/<target>
#   kind "module": python -m <target>
# `args` are extra flags the real process must carry (the rotator's --daemon).
COMPONENTS: Dict[str, Dict[str, Any]] = {
    "loop": {"kind": "script", "target": "scripts/trading_loop.py",
             "action": "loop", "label": "trading loop"},
    "server": {"kind": "module", "target": "hermes_trader.server",
               "action": "server", "label": "API server"},
    "rotator": {"kind": "script", "target": "scripts/log_rotate.py",
                "args": ["--daemon"], "action": "rotate", "label": "log rotator"},
    "scheduler": {"kind": "script", "target": "scripts/scheduler.py",
                  "action": "sched", "label": "scheduler"},
}

# What a default run supervises. The scheduler is excluded because this script
# normally runs AS the scheduler's child: restarting it would kill the process
# doing the restarting.
#
# But leaving it unsupervised put the whole watch on one process — the
# scheduler runs both this and the alert evaluator, so its death would silently
# end supervision AND alerting at once. The trading loop is the other
# long-lived process and it is independent, so it calls this with
# `--components scheduler`. Scheduler watches loop, loop watches scheduler.
DEFAULT_COMPONENTS = ("loop", "server", "rotator")


def _command_lines() -> List[str]:
    try:
        r = subprocess.run(["ps", "-Ao", "command="], capture_output=True,
                           text=True, timeout=20)
        return [ln for ln in r.stdout.splitlines() if ln.strip()]
    except Exception:
        return []


def is_the_process(cmd: str, spec: Dict[str, Any]) -> bool:
    """Is this command line the managed process ITSELF?

    Not "does the pattern appear in it". The substring version reported the log
    rotator running one second after SIGKILL, because the invoking shell's argv
    contained the pattern. Excluding our own process tree fixed that case and
    introduced a worse one: when the trading loop starts the scheduler, the loop
    is transiently the scheduler's ancestor, so the scheduler's first supervisor
    pass subtracted the loop's pid, concluded the loop was DOWN, and restarted a
    perfectly healthy trading loop mid-scan (2026-08-31 08:22:56, on record in
    logs/supervisor.log).

    Both bugs came from matching text near a process instead of identifying it.
    A python daemon has an exact shape: interpreter, then either the script path
    or `-m module`. Nothing that merely mentions the name can forge that.
    """
    tokens = cmd.split()
    if len(tokens) < 2:
        return False
    exe = os.path.basename(tokens[0]).lower()
    if not exe.startswith("python"):
        return False
    if spec["kind"] == "module":
        return len(tokens) >= 3 and tokens[1] == "-m" and tokens[2] == spec["target"]
    if tokens[1].startswith("-"):
        return False                       # `-m`, `-c`, flags: not our script
    if not tokens[1].endswith(spec["target"]):
        return False
    return all(a in tokens[2:] for a in spec.get("args", []))


def alive(spec: Dict[str, Any], commands: Optional[List[str]] = None) -> bool:
    """Is the managed process running? Unknown reads as alive: if we cannot
    tell, restarting is the dangerous guess — it can start a SECOND trading
    loop."""
    lines = commands if commands is not None else _command_lines()
    if not lines:
        return True
    return any(is_the_process(ln, spec) for ln in lines)


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


def selected(argv: List[str]) -> List[str]:
    """Which components this invocation supervises. Unknown names are an error,
    not a silent empty run — a typo'd `--components schedular` that supervised
    nothing would look exactly like a healthy pass."""
    if "--components" not in argv:
        return list(DEFAULT_COMPONENTS)
    names = [n for n in argv[argv.index("--components") + 1].split(",") if n]
    unknown = [n for n in names if n not in COMPONENTS]
    if unknown or not names:
        raise SystemExit(
            f"unknown component(s) {unknown or '<none given>'}; "
            f"known: {', '.join(sorted(COMPONENTS))}")
    return names


def main(argv: List[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    dry = "--dry-run" in argv
    want = selected(argv)
    commands = _command_lines()
    now = time.time()

    state_path = (STATE if want == list(DEFAULT_COMPONENTS)
                  else STATE.replace(".json", f"_{'-'.join(sorted(want))}.json"))
    state = _read(state_path, {})
    hist_all: Dict[str, List[float]] = {
        k: [float(t) for t in v]
        for k, v in (state.get("restarts") or {}).items() if isinstance(v, list)
    }
    down = halted()
    report = []
    rc = 0

    for comp in want:
        spec = COMPONENTS[comp]
        label = spec["label"]
        history = hist_all.get(comp, [])
        what, why = decide(comp, alive(spec, commands), comp in down, history, now)
        line = f"[supervisor] {label}: {why}"
        if what == "restart":
            if dry:
                line += " (dry-run, not restarting)"
            else:
                r = subprocess.run(["bash", RESTART_SH, spec["action"]], cwd=ROOT,
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
        _write(state_path, {"ts": now, "checked": sorted(want),
                            "halted": down, "restarts": hist_all,
                            "report": report})
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
