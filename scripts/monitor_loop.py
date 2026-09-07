#!/usr/bin/env python3
"""Watch the live trading loop and emit only what is worth acting on.

WHY A SCRIPT AND NOT AN EYEBALL
Progress, ages, timeouts and P&L deltas are same-input-same-answer arithmetic,
so they belong in deterministic space. This script is the source of truth; the
reader's job is to judge what it reports, not to recompute it.

TWO OUTPUTS, DELIBERATELY DIFFERENT
  stdout        ONLY actionable events, because each line becomes a
                notification. Silence here means "nothing changed", never
                "nothing checked".
  progress.log  EVERY tick, timestamped, so a quiet stretch is still auditable
                after the fact.

SILENCE IS NOT SUCCESS
A monitor that only greps for good news is silent through a crash, and silence
looks identical to healthy. So the alert set covers the failure modes first:
the process dying, the log going stale (the loop wedged without exiting), a
kill-switch trip, a tracker outliving its timeout. A quiet loop and a dead loop
must never produce the same output.

    python scripts/monitor_loop.py --interval 300
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "trading_loop.log"
DSL = ROOT / ".dsl-state.json"
CFG = ROOT / ".agent-config.json"
JOB = Path("/tmp/pathia-loop")
PROGRESS = JOB / "progress.log"

# Lines worth waking someone for. Ordered most-severe first.
ALERT_PATTERNS = (
    ("CRITICAL", "Traceback"), ("CRITICAL", "CRITICAL"),
    ("KILL", "killswitch"), ("KILL", "Daily loss"), ("KILL", "flattening"),
    ("TRADE", "[dsl] Closing"), ("TRADE", "OPENED"), ("TRADE", "book_execute"),
    ("ERROR", "ERROR"),
    ("WATCHDOG", "watchdog"), ("WATCHDOG", "re-exec"),
    ("GUARD", "transfer race"), ("GUARD", "REFUSED"),
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def emit(line: str, alert: bool) -> None:
    """progress.log always; stdout only when it is worth a notification."""
    JOB.mkdir(parents=True, exist_ok=True)
    with PROGRESS.open("a") as fh:
        fh.write(f"{_now()}  {line}\n")
    if alert:
        print(line, flush=True)


def loop_pids() -> list[str]:
    r = subprocess.run(["pgrep", "-f", "scripts/trading_loop.py"],
                       capture_output=True, text=True)
    return [p for p in r.stdout.split() if p]


def read_state() -> dict:
    cfg = json.loads(CFG.read_text())
    try:
        pos = json.loads(DSL.read_text()).get("positions") or []
    except Exception:
        pos = []
    hard_h = float(cfg["dsl_exit"]["hard_timeout_minutes"]) / 60.0
    now = time.time()
    rows = []
    for t in pos:
        age = (now - float(t.get("entry_time", now))) / 3600.0
        rows.append({"coin": t.get("coin"), "side": t.get("side"),
                     "age_h": age, "left_h": hard_h - age,
                     "entry": t.get("entry_px")})
    return {"cfg": cfg, "rows": rows, "slots": int(cfg["max_concurrent"]),
            "hard_h": hard_h}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--stale-s", type=int, default=420,
                    help="log silence that means the loop is wedged")
    a = ap.parse_args()

    offset = LOG.stat().st_size if LOG.exists() else 0
    last = {"n": None, "pids": None}
    emit(f"MONITOR START — watching every {a.interval}s, "
         f"alerting on: trades, exits, errors, kill switch, process death, "
         f"log silence > {a.stale_s}s", True)

    while True:
        try:
            st = read_state()
            pids = loop_pids()

            # --- failure modes first -------------------------------------
            if not pids:
                emit("DEAD — trading_loop.py is NOT running. Restart: "
                     "bash scripts/restart.sh", True)
            elif last["pids"] and set(pids) != set(last["pids"]):
                emit(f"RESTARTED — pid {last['pids']} -> {pids} "
                     f"(watchdog re-exec or manual restart)", True)
            last["pids"] = pids

            age_s = time.time() - LOG.stat().st_mtime if LOG.exists() else 1e9
            if pids and age_s > a.stale_s:
                emit(f"STALE — loop alive (pid {pids[0]}) but log silent "
                     f"{age_s/60:.1f}m. Wedged, not working.", True)

            # --- new log lines -------------------------------------------
            if LOG.exists():
                size = LOG.stat().st_size
                if size < offset:          # rotated
                    offset = 0
                if size > offset:
                    with LOG.open() as fh:
                        fh.seek(offset)
                        chunk = fh.read()
                        offset = fh.tell()
                    for ln in chunk.splitlines():
                        for tag, pat in ALERT_PATTERNS:
                            if pat in ln:
                                emit(f"{tag} — {ln.strip()[:240]}", True)
                                break

            # --- position changes ----------------------------------------
            n = len(st["rows"])
            if last["n"] is not None and n != last["n"]:
                emit(f"POSITIONS {last['n']} -> {n}/{st['slots']}" +
                     ("  (slot freed — scan resumes next cycle)" if n < last["n"]
                      else "  (slots full — scan gated)"), True)
            last["n"] = n

            for r in st["rows"]:
                if 0 < r["left_h"] <= 1.0:
                    emit(f"TIMEOUT SOON — {r['coin']} is {r['age_h']:.1f}h old, "
                         f"hard timeout in {r['left_h']:.1f}h", True)
                elif r["left_h"] <= 0:
                    emit(f"OVERDUE — {r['coin']} is {r['age_h']:.1f}h old, past "
                         f"the {st['hard_h']:.0f}h hard timeout and still open", True)

            # --- routine tick (progress.log only) -------------------------
            desc = ", ".join(f"{r['coin']} {r['age_h']:.1f}h" for r in st["rows"])
            emit(f"ok — {n}/{st['slots']} open [{desc}]  loop pid {pids[0] if pids else '-'}"
                 f"  log {age_s:.0f}s ago", False)

        except Exception as e:                      # never die on a bad tick
            emit(f"MONITOR ERROR — {type(e).__name__}: {e}", True)
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
