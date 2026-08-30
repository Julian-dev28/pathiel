#!/usr/bin/env python3
"""Can this system trade right now, and if not, what is stopping it.

One command, no network writes, no orders. Every check names the file that
decides it, so a NO is actionable rather than mysterious.

    python scripts/preflight_live.py

Exit code is the number of blocking problems, so it can gate a start script.
Warnings do not affect the exit code.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# .env.local carries HERMES_STATE_DIR. Load it BEFORE importing anything that
# resolves a state path, or every book reads as empty from the wrong directory.
_env = ROOT / ".env.local"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

GREEN, RED, AMBER, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


class Report:
    def __init__(self) -> None:
        self.blocking: list[str] = []
        self.warnings: list[str] = []

    def ok(self, name: str, detail: str = "") -> None:
        print(f"  {GREEN}OK{OFF}    {name:<26} {DIM}{detail}{OFF}")

    def block(self, name: str, detail: str, fix: str) -> None:
        print(f"  {RED}BLOCK{OFF} {name:<26} {detail}")
        print(f"        {DIM}fix: {fix}{OFF}")
        self.blocking.append(name)

    def warn(self, name: str, detail: str) -> None:
        print(f"  {AMBER}WARN{OFF}  {name:<26} {detail}")
        self.warnings.append(name)


def check_secrets(r: Report) -> None:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "preflight_secrets.py")],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        r.ok("secrets", "preflight_secrets passes")
    else:
        bad = [ln for ln in proc.stdout.splitlines() if ln.startswith("[FAIL]")]
        r.block("secrets", f"{len(bad)} failing check(s)",
                "python scripts/preflight_secrets.py")


def check_brain(r: Report) -> None:
    from hermes_trader.agents.ai_brain import provider_readiness
    x = provider_readiness()
    if x.get("ready"):
        note = "" if x.get("deployable") else "local-only provider"
        r.ok("ai brain", f"{x['provider']} {note}")
    else:
        r.block("ai brain", x.get("reason", "not usable"),
                "set AI_BRAIN_PROVIDER=openrouter with OPENROUTER_API_KEY, "
                "or install the CLI binary")


def check_capital(r: Report) -> None:
    from hermes_trader.agents.executor import min_tradable_equity
    from hermes_trader.dashboard import _risk_payload
    x = _risk_payload()
    floor = min_tradable_equity({})
    eq = float(x.get("equity") or 0)
    if eq >= floor:
        r.ok("equity", f"${eq:.2f} clears the ${floor:.0f} floor")
    else:
        r.block("equity", f"${eq:.2f} is under the ${floor:.0f} structural floor",
                "fund the account; the executor refuses every order below it")
    if x.get("mode") != "LIVE":
        r.warn("mode", f"{x.get('mode')} — books will not trade until mode is LIVE")
    else:
        r.ok("mode", "LIVE")
    dd = x.get("max_drawdown_pct")
    if dd is not None and dd < -25:
        r.warn("drawdown", f"{dd}% max over {x.get('window_days')}d "
                           f"({x.get('drawdown_basis')} basis)")


def check_books(r: Report) -> None:
    import importlib
    import hermes_trader.dashboard as db
    spec = importlib.util.spec_from_file_location(
        "autonomous_cycle", ROOT / "scripts" / "autonomous_cycle.py")
    ac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ac)

    books = db._KNOWN_BOOK_NAMES
    if not books:
        r.block("books", "no books registered", "nothing can trade")
        return
    ungoverned = sorted(set(books) - set(ac._SWITCHES))
    if ungoverned:
        r.block("book switches", f"{ungoverned} cannot be demoted by the grader",
                "add a _SWITCHES entry in scripts/autonomous_cycle.py")
    else:
        r.ok("book switches", f"{len(books)} books, all demotable")

    # every live book must import — a broken import means the loop cannot start
    broken = []
    for mod in ("news_surge_short_live", "news_surge_multi",
                "social_trending_recorder", "unlock_short_live"):
        try:
            importlib.import_module(f"hermes_trader.agents.{mod}")
        except Exception as exc:
            broken.append(f"{mod}: {str(exc)[:60]}")
    if broken:
        r.block("book imports", "; ".join(broken),
                "the trading loop imports these at module scope and cannot start")
    else:
        r.ok("book imports", "all live books import")


def check_feed(r: Report) -> None:
    from hermes_trader.agents import perception
    st = perception.last_scan_integrity()
    if not st.get("ts"):
        r.ok("market feed", "no scan yet (cold start is not a fault)")
    elif perception.scan_is_trustworthy():
        r.ok("market feed", f"{st['gaps']}/{st['markets']} unreadable on the last scan")
    else:
        r.block("market feed",
                f"{st['gaps']}/{st['markets']} markets unreadable "
                f"({float(st['gap_frac']) * 100:.0f}%)",
                "entries are auto-blocked until it recovers; usually the exchange")


def check_disk(r: Report) -> None:
    from hermes_trader import log_setup
    g = log_setup.check_disk_guard()
    gb = g.free_bytes / 1e9
    if g.critical:
        r.block("disk", f"{gb:.1f}GB free is under the critical floor",
                "python scripts/log_rotate.py --once")
    elif g.warn:
        r.warn("disk", f"{gb:.1f}GB free, logs {g.log_dir_bytes / 1e6:.0f}MB")
    else:
        r.ok("disk", f"{gb:.1f}GB free, logs {g.log_dir_bytes / 1e6:.0f}MB")


def check_processes(r: Report) -> None:
    ps = subprocess.run(["ps", "ax"], capture_output=True, text=True).stdout
    for name, pattern, why in (
        ("trading loop", "trading_loop.py", "scripts/restart.sh"),
        ("scheduler", "scheduler.py", "scripts/restart.sh sched"),
        ("log rotator", "log_rotate.py --daemon", "scripts/restart.sh rotate"),
    ):
        if pattern in ps:
            r.ok(name, "running")
        else:
            r.warn(name, f"not running — start with {why}")


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    print(f"\n{DIM}hermes-trader — live readiness{OFF}\n")
    r = Report()
    for check in (check_secrets, check_brain, check_capital, check_books,
                  check_feed, check_disk, check_processes):
        try:
            check(r)
        except Exception as exc:                    # a broken check is a finding
            r.block(check.__name__, f"check itself failed: {str(exc)[:80]}",
                    "this is a bug in the preflight, not necessarily in the system")
    print()
    if r.blocking:
        print(f"{RED}NOT READY{OFF} — {len(r.blocking)} blocking: {', '.join(r.blocking)}")
    else:
        print(f"{GREEN}READY{OFF} — nothing is blocking a trade"
              + (f" ({len(r.warnings)} warning(s))" if r.warnings else ""))
    print()
    return len(r.blocking)


if __name__ == "__main__":
    sys.exit(main())
