#!/usr/bin/env python3
"""In-process job scheduler — the only scheduler on this box that actually runs.

WHY NOT CRON, WHY NOT LAUNCHD (both measured 2026-07-25):

    cron:     python: realpath: .venv/bin/: Operation not permitted
    launchd:  PermissionError: [Errno 1] Operation not permitted:
              '.../.venv/pyvenv.cfg'

macOS TCC gates `~/Documents` on the *responsible* process. Neither `cron` nor
`launchd` has that grant, so both fail before Python even boots, silently, into
a log nobody reads. `logs/autonomous_cycle.log` held three lines — all this error
— meaning the autonomous evidence loop **never ran once** between its 2026-07-20
install and 2026-07-25.

This process is started by `scripts/restart.sh`, from the operator's terminal,
which DOES hold the grant. Children inherit it. No GUI permission prompt, no
sudo, nothing to click.

CATCH-UP IS THE POINT. This Mac sleeps (194.8 dark hours in 15 days, per the
2026-07-10 audit). A scheduler that only fires when the clock is exactly on the
mark loses every job that came due while the lid was shut. Each job stores its
own `last_run`; on every tick a job is due when its most recent scheduled
occurrence is newer than its last run. Wake at 14:00 with a 09:15 job unfired
and it fires at 14:00.

    python scripts/scheduler.py              # run forever (restart.sh does this)
    python scripts/scheduler.py --once       # one pass, fire what is due, exit
    python scripts/scheduler.py --status     # what ran, what is due next
    python scripts/scheduler.py --force poly-board
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "bin", "python")
STATE = os.path.join(ROOT, ".state", "scheduler.json")
TICK_S = 60.0

# name -> job. `hour=None` means every hour at `minute`.
JOBS: Dict[str, Dict[str, Any]] = {
    "poly-board": {
        "args": [PY, "-m", "services.polymarket_scout.daily", "--board-only"],
        "hour": None, "minute": 5,
        "log": "logs/polymarket_scout.log",
        "why": "hourly Polymarket board cache refresh (no LLM, no capital)",
    },
    "poly-judgment": {
        "args": [PY, "-m", "services.polymarket_scout.daily", "--lanes",
                 "judgment,trending,sports", "--judgment-limit", "16",
                 "--trending-limit", "14"],
        "interval_min": 240,          # every 4h — the judgment lanes are the real edge
        "log": "logs/polymarket_scout.log",
        "why": "judgment/trending/sports forecasts (web-search edge) + grade; every 4h",
    },
    "autonomous-cycle": {
        "args": [PY, os.path.join(ROOT, "scripts", "autonomous_cycle.py")],
        "hour": 9, "minute": 15,
        "log": "logs/autonomous_cycle.log",
        "why": "grade every book, auto-demote refuted, auto-promote validated",
    },
    "trends-price": {
        "args": [PY, "-m", "services.trend_engine.run", "--refresh-all",
                 "--lanes", "hl,updown,politics"],
        "interval_min": 30,
        "log": "logs/trend_engine.log",
        "why": "/trends price lanes — HL 7d scan, BTC 5m base rates, political drift. "
               "No LLM, no capital; the tab reads this cache and never fetches itself",
    },
    "trends-recorders": {
        "args": [PY, "-m", "services.trend_engine.run", "--refresh-all",
                 "--lanes", "recorders"],
        "interval_min": 360,          # every 6h — forward-grading is minutes of candles
        "log": "logs/trend_engine.log",
        "why": "/trends P&L lane — forward-grade every shadow book + the Polymarket "
               "paper ledger. Slow on purpose, so it runs on its own clock",
    },
    # updown-5m REMOVED 2026-07-29: proven no edge (backtest 74d0846 — the venue
    # prices the momentum; live ledger -18.8% Kelly). It spammed 898 coin-flip
    # reads that dragged the scoreboard's Brier below the market. The reader +
    # panel + on-demand "Analyze now" still work by hand; only the auto-spam is off.
}
# updown-5m spends a model call every 5 min. TIMEOUT_S below (3600) is generous
# for it; the read is short (no web search) so it finishes in seconds.
# A job that hangs must not wedge the scheduler. Generous, because poly-daily
# makes several multi-minute web-search LLM calls.
TIMEOUT_S = 3600.0


# ── schedule math (pure) ─────────────────────────────────────────────────────
def last_occurrence(job: Dict[str, Any], now: float) -> float:
    """Epoch of the most recent time this job was scheduled to run, at or before
    `now`. `interval_min` jobs recur on a fixed grid; hourly jobs look back at
    most one hour; daily jobs at most one day."""
    interval = job.get("interval_min")
    if interval:
        step = int(interval) * 60
        return (int(now) // step) * step
    lt = time.localtime(now)
    minute = int(job.get("minute", 0))
    hour = job.get("hour")
    if hour is None:
        occ = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, minute, 0,
                           lt.tm_wday, lt.tm_yday, -1))
        return occ if occ <= now else occ - 3600.0
    occ = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(hour), minute, 0,
                       lt.tm_wday, lt.tm_yday, -1))
    return occ if occ <= now else occ - 86400.0


def is_due(job: Dict[str, Any], last_run: Optional[float], now: float) -> bool:
    """Due when the newest scheduled occurrence has not been run yet. A job that
    has never run is due immediately — that is what makes a fresh install (or a
    long sleep) self-heal instead of waiting for tomorrow."""
    if last_run is None:
        return True
    return last_run < last_occurrence(job, now)


def next_occurrence(job: Dict[str, Any], now: float) -> float:
    if job.get("interval_min"):
        step = int(job["interval_min"]) * 60
    elif job.get("hour") is None:
        step = 3600.0
    else:
        step = 86400.0
    return last_occurrence(job, now) + step


# ── state ────────────────────────────────────────────────────────────────────
def load_state(path: str = STATE) -> Dict[str, Any]:
    try:
        with open(path) as fh:
            s = json.load(fh)
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def save_state(state: Dict[str, Any], path: str = STATE) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass


# ── running ──────────────────────────────────────────────────────────────────
def run_job(name: str, job: Dict[str, Any],
            runner: Optional[Callable[[List[str], str], int]] = None) -> Dict[str, Any]:
    """Run one job, append its output to its own log, never raise. The scheduler
    surviving a failing job is the whole reason this is not a shell loop."""
    started = time.time()
    log = os.path.join(ROOT, job["log"])
    if runner is not None:
        rc = runner(job["args"], log)
    else:
        try:
            os.makedirs(os.path.dirname(log), exist_ok=True)
            with open(log, "a") as fh:
                fh.write(f"\n[scheduler] {time.strftime('%Y-%m-%d %H:%M:%S')} "
                         f"start {name}\n")
                fh.flush()
                rc = subprocess.run(job["args"], cwd=ROOT, stdout=fh, stderr=fh,
                                    timeout=TIMEOUT_S).returncode
        except subprocess.TimeoutExpired:
            rc = -9
        except Exception:
            rc = -1
    return {"name": name, "rc": rc, "started": started,
            "elapsed_s": round(time.time() - started, 1)}


def tick(now: Optional[float] = None, state: Optional[Dict[str, Any]] = None,
         jobs: Optional[Dict[str, Dict[str, Any]]] = None,
         runner: Optional[Callable[[List[str], str], int]] = None,
         state_path: str = STATE,
         printer: Callable[[str], None] = print) -> List[Dict[str, Any]]:
    """One pass: fire everything that is due, record it, return the results."""
    now = time.time() if now is None else now
    jobs = jobs if jobs is not None else JOBS
    own_state = state is None
    state = load_state(state_path) if own_state else state
    fired: List[Dict[str, Any]] = []
    for name, job in jobs.items():
        entry = state.get(name) or {}
        if not is_due(job, entry.get("last_run"), now):
            continue
        printer(f"[scheduler] firing {name} — {job['why']}")
        res = run_job(name, job, runner=runner)
        state[name] = {"last_run": now, "last_rc": res["rc"],
                       "last_elapsed_s": res["elapsed_s"],
                       "last_run_iso": time.strftime("%Y-%m-%d %H:%M:%S",
                                                     time.localtime(now))}
        save_state(state, state_path)
        printer(f"[scheduler] {name} rc={res['rc']} in {res['elapsed_s']}s"
                + ("  <<< FAILED" if res["rc"] != 0 else ""))
        fired.append(res)
    return fired


def status(now: Optional[float] = None, state_path: str = STATE) -> List[Dict[str, Any]]:
    now = time.time() if now is None else now
    state = load_state(state_path)
    out = []
    for name, job in JOBS.items():
        e = state.get(name) or {}
        out.append({
            "name": name,
            "last_run": e.get("last_run_iso") or "never",
            "last_rc": e.get("last_rc"),
            "due_now": is_due(job, e.get("last_run"), now),
            "next": time.strftime("%Y-%m-%d %H:%M",
                                  time.localtime(next_occurrence(job, now))),
            "why": job["why"],
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="append", default=[],
                    help="run this job now regardless of schedule (repeatable)")
    args = ap.parse_args()

    if args.status:
        for r in status():
            flag = "DUE" if r["due_now"] else "   "
            print(f"{flag} {r['name']:<20} last={r['last_run']:<20} rc={r['last_rc']} "
                  f"next={r['next']}  {r['why']}")
        return 0
    if args.force:
        for name in args.force:
            if name not in JOBS:
                print(f"unknown job {name!r}; known: {', '.join(JOBS)}", file=sys.stderr)
                return 2
            res = run_job(name, JOBS[name])
            st = load_state()
            st[name] = {"last_run": time.time(), "last_rc": res["rc"],
                        "last_elapsed_s": res["elapsed_s"],
                        "last_run_iso": time.strftime("%Y-%m-%d %H:%M:%S")}
            save_state(st)
            print(f"[scheduler] forced {name} rc={res['rc']} in {res['elapsed_s']}s")
        return 0
    if args.once:
        fired = tick()
        print(f"[scheduler] one pass: fired {len(fired)}")
        return 0

    print(f"[scheduler] up — {len(JOBS)} jobs, {TICK_S:.0f}s tick, catch-up on wake",
          flush=True)
    while True:
        try:
            tick(printer=lambda s: print(s, flush=True))
        except Exception as exc:                 # a bad tick must not end the loop
            print(f"[scheduler] tick error: {exc}", flush=True)
        time.sleep(TICK_S)


if __name__ == "__main__":
    raise SystemExit(main())
