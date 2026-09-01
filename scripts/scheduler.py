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
    python scripts/scheduler.py --force trends-price
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "bin", "python")
STATE = os.path.join(ROOT, ".state", "scheduler.json")
TICK_S = 60.0
# Jobs run CONCURRENTLY, one run per job at a time. Serially was the bug: a
# single long job ran 3612s on 2026-08-03 (right into TIMEOUT_S) and
# `trends-price` — a 55s cache refresh on a 30-minute cadence — could not fire
# for 11 hours, so the /trends tab served an 11h-old read with no sign anything
# was wrong.
# Capped so a catch-up burst after the lid opens cannot put every scan on the
# HL/Gamma rate limit at once.
MAX_CONCURRENT_JOBS = 3

# name -> job. `hour=None` means every hour at `minute`.
JOBS: Dict[str, Dict[str, Any]] = {
    "capital-flows": {
        "args": [PY, os.path.join(ROOT, "scripts", "record_capital_flows.py"),
                 "--since-days", "7"],
        "interval_min": 360,          # every 6h — flows are rare, misses are cheap
        "log": "logs/capital_flows.log",
        "why": "record deposits/withdrawals so the drawdown is flow-neutral and "
               "a withdrawal stops reading as a trading loss",
    },
    "autonomous-cycle": {
        "args": [PY, os.path.join(ROOT, "scripts", "autonomous_cycle.py")],
        "hour": 9, "minute": 15,
        "log": "logs/autonomous_cycle.log",
        "why": "grade every book, auto-demote refuted, auto-promote validated",
    },
    "trends-price": {
        "args": [PY, "-m", "services.trend_engine.run", "--refresh-all",
                 "--lanes", "hl"],
        "interval_min": 30,
        "log": "logs/trend_engine.log",
        "why": "/trends price lane — HL 7d scan. No LLM, no capital; the tab "
               "reads this cache and never fetches itself",
    },
    "alerts": {
        "args": [PY, os.path.join(ROOT, "scripts", "alert_eval.py")],
        "interval_min": 2,            # one HTTP GET; cheap enough to be frequent
        "log": "logs/alerts_eval.log",
        "why": "evaluate k8s/prometheusrule.yaml locally and deliver. The rules "
               "were written for the Prometheus Operator; nothing here runs "
               "Kubernetes, so until this job existed all seven alerts were "
               "documentation and every silent failure stayed silent",
    },
    "backup-state": {
        "args": [PY, os.path.join(ROOT, "scripts", "backup_state.py")],
        "hour": 4, "minute": 30,       # daily, off the trading path
        "log": "logs/backup_state.log",
        "why": "snapshot the state that cannot be recreated — trade memory, the "
               "shadow-ledger evidence base, capital flows. All gitignored, all "
               "on one laptop, and until this job none had a copy",
    },
    "supervisor": {
        "args": [PY, os.path.join(ROOT, "scripts", "supervise_processes.py")],
        "interval_min": 2,            # a dead loop should cost minutes, not days
        "log": "logs/supervisor.log",
        "why": "restart the loop/server/rotator if they die. The loop self-heals "
               "when HUNG; nothing covered GONE, and a sleeping Mac once cost a "
               "full week of no trading. launchd cannot do this — TCC blocks it "
               "from ~/Documents — but the scheduler already has the access",
    },
}
# A job that hangs must not wedge the scheduler. Generous, because
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


_RUNNING: Dict[str, threading.Thread] = {}
_RUN_LOCK = threading.Lock()


def running_jobs() -> List[str]:
    with _RUN_LOCK:
        return sorted(n for n, t in _RUNNING.items() if t.is_alive())


def prune_ghosts(state: Dict[str, Any],
                 jobs: Optional[Dict[str, Dict[str, Any]]] = None
                 ) -> Dict[str, Any]:
    """Drop entries for jobs that no longer exist.

    A deleted job leaves its last_run behind forever. `scheduler.py status`
    then lists poly-board as a job with a timestamp, which is a job that will
    never run again wearing the appearance of one that just did.
    """
    known = jobs if jobs is not None else JOBS
    return {k: v for k, v in state.items() if k in known}


def _stamp(name: str, now: float, res: Optional[Dict[str, Any]],
           state_path: str,
           jobs: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
    """Write one job's outcome, re-reading state so concurrent jobs do not
    clobber each other's entries."""
    with _RUN_LOCK:
        # Prune against the job set THIS tick is running, not the module
        # global: `tick` accepts a custom `jobs` dict, and pruning against
        # JOBS there would delete the entries of every job in that run. The
        # job being stamped is by definition real, so it is always kept.
        known = {**(jobs if jobs is not None else JOBS), name: {}}
        state = prune_ghosts(load_state(state_path), known)
        entry = dict(state.get(name) or {})
        entry["last_run"] = now
        entry["last_run_iso"] = time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime(now))
        if res is not None:
            entry["last_rc"] = res["rc"]
            entry["last_elapsed_s"] = res["elapsed_s"]
            # `last_run` moves on every dispatch, success or not, so a job that
            # runs daily and FAILS daily looks perfectly fresh by that field
            # alone. autonomous-cycle hit its deadline on every run from
            # 2026-08-23 to 08-31 — eight days with no book graded, and every
            # surface reported it as having "run today". Track the last time it
            # actually succeeded, separately.
            if res["rc"] == 0:
                entry["last_ok"] = now
                entry["last_ok_iso"] = entry["last_run_iso"]
        state[name] = entry
        save_state(state, state_path)


def tick(now: Optional[float] = None, state: Optional[Dict[str, Any]] = None,
         jobs: Optional[Dict[str, Dict[str, Any]]] = None,
         runner: Optional[Callable[[List[str], str], int]] = None,
         state_path: str = STATE,
         printer: Callable[[str], None] = print,
         join: bool = False) -> List[Dict[str, Any]]:
    """One pass: start everything that is due, on its own thread.

    Concurrent because serial starved the cheap jobs: one hour-long LLM lane
    blocked an 11-hour backlog of 55-second cache refreshes. Each job still runs
    at most one copy at a time — a slow job is skipped, not stacked — and no
    more than `MAX_CONCURRENT_JOBS` run together.

    `last_run` is stamped when the job STARTS, so a job that takes longer than
    its own interval keeps its cadence instead of firing again the moment it
    finishes. `join=True` waits for this pass (used by `--once` and the tests).
    """
    now = time.time() if now is None else now
    jobs = jobs if jobs is not None else JOBS
    state = load_state(state_path) if state is None else state
    started: List[Dict[str, Any]] = []
    threads: List[threading.Thread] = []
    for name, job in jobs.items():
        entry = state.get(name) or {}
        if not is_due(job, entry.get("last_run"), now):
            continue
        with _RUN_LOCK:
            live = [n for n, t in _RUNNING.items() if t.is_alive()]
            if name in live:
                printer(f"[scheduler] {name} still running from the last pass — skipped")
                continue
            if len(live) >= MAX_CONCURRENT_JOBS:
                printer(f"[scheduler] {name} due but {len(live)} jobs already "
                        f"running — next tick")
                continue
        printer(f"[scheduler] firing {name} — {job['why']}")
        _stamp(name, now, None, state_path, jobs)  # claim the slot before running
        state.setdefault(name, {})["last_run"] = now

        def _work(name: str = name, job: Dict[str, Any] = job) -> None:
            res = run_job(name, job, runner=runner)
            _stamp(name, now, res, state_path, jobs)
            printer(f"[scheduler] {name} rc={res['rc']} in {res['elapsed_s']}s"
                    + ("  <<< FAILED" if res["rc"] != 0 else ""))
            started.append(res)

        t = threading.Thread(target=_work, name=f"job-{name}", daemon=True)
        with _RUN_LOCK:
            _RUNNING[name] = t
        threads.append(t)
        t.start()
    if join:
        for t in threads:
            t.join()
    return started


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
        fired = tick(join=True)
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
