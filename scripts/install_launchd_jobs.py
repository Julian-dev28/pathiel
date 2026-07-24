#!/usr/bin/env python3
"""Move this repo's scheduled jobs off cron and onto launchd.

WHY (measured 2026-07-25): every crontab entry for this repo was failing with

    python: realpath: .venv/bin/: Operation not permitted

macOS `cron` runs outside the user's TCC context and has no access to
`~/Documents`, so it cannot even resolve the venv interpreter. It fails silently
into a log nobody reads. `logs/autonomous_cycle.log` held 3 lines, all this
error: the autonomous evidence loop **never ran once** between its 2026-07-20
install and 2026-07-25 — five days of believing books were being auto-graded.

A LaunchAgent runs inside the user's GUI session and inherits the user's TCC
permissions, so the same command works. It also survives reboot, logs where you
point it, and can be kicked manually to prove it works.

    python scripts/install_launchd_jobs.py            # write + load + verify
    python scripts/install_launchd_jobs.py --dry-run  # print the plists only
    python scripts/install_launchd_jobs.py --uninstall
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "bin", "python")
AGENT_DIR = os.path.expanduser("~/Library/LaunchAgents")

# label -> (module args, schedule, log). Schedule is a list of
# StartCalendarInterval dicts; {"Minute": 5} alone means "every hour at :05".
JOBS: Dict[str, Dict[str, Any]] = {
    "com.hermes.polymarket-board": {
        "args": ["-m", "services.polymarket_scout.daily", "--board-only"],
        "schedule": [{"Minute": 5}],
        "log": "logs/polymarket_scout.log",
        "why": "hourly board cache refresh so /predictions is never stale (no LLM)",
    },
    "com.hermes.polymarket-daily": {
        "args": ["-m", "services.polymarket_scout.daily"],
        "schedule": [{"Hour": 9, "Minute": 45}],
        "log": "logs/polymarket_scout.log",
        "why": "both forecast lanes + grade resolved paper trades (spends tokens)",
    },
    "com.hermes.autonomous-cycle": {
        "args": [os.path.join(ROOT, "scripts", "autonomous_cycle.py")],
        "schedule": [{"Hour": 9, "Minute": 15}],
        "log": "logs/autonomous_cycle.log",
        "why": "grade every book, auto-demote refuted, auto-promote validated",
    },
}


def plist_for(label: str, job: Dict[str, Any], root: str = ROOT,
              py: str = PY) -> Dict[str, Any]:
    """The plist body. `RunAtLoad` is deliberately False: these jobs hit live
    APIs and one of them spends tokens, so loading the agent must not fire it."""
    log = os.path.join(root, job["log"])
    return {
        "Label": label,
        "ProgramArguments": [py] + list(job["args"]),
        "WorkingDirectory": root,
        "StartCalendarInterval": list(job["schedule"]),
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "RunAtLoad": False,
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin"},
        "ProcessType": "Background",
    }


def plist_path(label: str, agent_dir: str = AGENT_DIR) -> str:
    return os.path.join(agent_dir, f"{label}.plist")


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def install(labels: Optional[List[str]] = None, agent_dir: str = AGENT_DIR,
            dry_run: bool = False) -> int:
    os.makedirs(agent_dir, exist_ok=True)
    uid = os.getuid()
    for label in (labels or list(JOBS)):
        job = JOBS[label]
        body = plist_for(label, job)
        path = plist_path(label, agent_dir)
        if dry_run:
            print(f"--- {path}\n{plistlib.dumps(body).decode()}")
            continue
        with open(path, "wb") as fh:
            plistlib.dump(body, fh)
        # bootout first so a re-install replaces cleanly; ignore "not loaded"
        _run(["launchctl", "bootout", f"gui/{uid}/{label}"])
        r = _run(["launchctl", "bootstrap", f"gui/{uid}", path])
        ok = r.returncode == 0
        print(f"[launchd] {'loaded ' if ok else 'FAILED  '}{label:<32} {job['why']}")
        if not ok:
            print(f"          {(r.stderr or r.stdout).strip()[:200]}")
    return 0


def uninstall(labels: Optional[List[str]] = None, agent_dir: str = AGENT_DIR) -> int:
    uid = os.getuid()
    for label in (labels or list(JOBS)):
        _run(["launchctl", "bootout", f"gui/{uid}/{label}"])
        path = plist_path(label, agent_dir)
        if os.path.exists(path):
            os.remove(path)
        print(f"[launchd] removed {label}")
    return 0


def status(agent_dir: str = AGENT_DIR) -> List[Dict[str, Any]]:
    uid = os.getuid()
    out = []
    for label in JOBS:
        r = _run(["launchctl", "print", f"gui/{uid}/{label}"])
        loaded = r.returncode == 0
        state = ""
        for line in (r.stdout or "").splitlines():
            if "last exit code" in line or line.strip().startswith("state ="):
                state += line.strip() + "  "
        out.append({"label": label, "loaded": loaded,
                    "plist": os.path.exists(plist_path(label, agent_dir)),
                    "detail": state.strip()})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    args = ap.parse_args()
    if sys.platform != "darwin":
        print("launchd is macOS-only; on Linux use systemd timers", file=sys.stderr)
        return 2
    labels = args.only or None
    if args.status:
        for row in status():
            print(f"{row['label']:<32} loaded={row['loaded']} plist={row['plist']} "
                  f"{row['detail']}")
        return 0
    if args.uninstall:
        return uninstall(labels)
    rc = install(labels, dry_run=args.dry_run)
    if not args.dry_run:
        print("\n# verify:  python scripts/install_launchd_jobs.py --status")
        print("# kick one: launchctl kickstart -k gui/$UID/com.hermes.polymarket-board")
        print("# REMOVE the matching crontab lines — cron cannot run these "
              "(TCC blocks ~/Documents).")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
