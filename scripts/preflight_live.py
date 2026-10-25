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
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# .env.local carries PATHIEL_STATE_DIR. Load it BEFORE importing anything that
# resolves a state path, or every state read comes back empty from the wrong directory.
sys.path.insert(0, str(ROOT / "scripts"))
import _state_env  # noqa: E402

_state_env.load_env_local(str(ROOT))

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


def _live_limits():
    """Live equity of the ACTIVE account (HIP-3 dexes included) and the limits
    the executor derives from it. The dashboard's risk payload is a snapshot of
    the loop's last heartbeat, stale whenever the loop is down (it read $1.37
    while the account held $15.21, 2026-10-18)."""
    from pathiel.agents.config_store import read_agent_config, read_risk_profile
    from services.accounts.summary import get_active_summary
    from services.risk import books as risk_books
    from services.risk.api import _VENUE
    from services.risk.derive import derive_limits
    summary = get_active_summary()
    eq = float(summary.equity_usd or 0.0)
    cfg = read_agent_config()
    return cfg, eq, derive_limits(eq, read_risk_profile(cfg), risk_books.all_book_specs(cfg), _VENUE)


def check_capital(r: Report) -> None:
    # Per-trade feasibility (services/risk): can the one tradeable path —
    # bookless main-engine / MCP-submitted entries, the only trader on the
    # wallet since the strategy books were retired 2026-10-23 — place a
    # trade right now at this equity.
    cfg, eq, lim = _live_limits()
    if lim.can_trade:
        r.ok("equity", f"${eq:.2f}: {lim.headline}")
    else:
        r.block("equity", f"${eq:.2f}: {lim.headline}",
                "fund the account, or pick a different risk preset on /account")
    if cfg.get("mode") != "LIVE":
        r.warn("mode", f"{cfg.get('mode')} — entries blocked until mode is LIVE "
                        "(exits still monitored)")
    else:
        r.ok("mode", "LIVE")


def check_margin_headroom(r: Report) -> None:
    """Can the main-engine entry path trade at this equity, from the same
    derived limits the executor enforces (services/risk/books.py's
    main_engine_spec — the one BookSpec left since the strategy books were
    retired 2026-10-23)."""
    _cfg, eq, lim = _live_limits()
    able = [b for b in lim.books if b.enabled and b.can_trade]
    detail = f"{len(able)} of {len(lim.books)} can trade, {lim.max_open_positions} position(s) at a time"
    blocked = [f"{b.book} (needs ${b.needs_equity_usd:,.0f})" for b in lim.books
               if b.enabled and not b.can_trade and b.needs_equity_usd]
    if blocked:
        r.warn("trade capacity", detail + "; waiting on equity: " + ", ".join(blocked))
    else:
        r.ok("trade capacity", detail)


def check_disk(r: Report) -> None:
    from pathiel import log_setup
    g = log_setup.check_disk_guard()
    gb = g.free_bytes / 1e9
    if g.critical:
        r.block("disk", f"{gb:.1f}GB free is under the critical floor",
                "python scripts/log_rotate.py --once")
    elif g.warn:
        r.warn("disk", f"{gb:.1f}GB free, logs {g.log_dir_bytes / 1e6:.0f}MB")
    else:
        r.ok("disk", f"{gb:.1f}GB free, logs {g.log_dir_bytes / 1e6:.0f}MB")


def check_backup(r: Report) -> None:
    """Is the state backup current and verified?

    2026-10-23 (wave 2): the supervisor/alerting checks that used to live
    here (`check_watchers`, `check_processes`) covered scripts/
    supervise_processes.py and scripts/scheduler.py — both retired with the
    autonomous engine, so there is nothing left to watch. This is the one
    piece of that section with a real, still-running backstop: scripts/
    backup_state.py.
    """
    import time

    from pathiel.agents.atomic_io import read_json
    from pathiel.state import state_file

    receipt = read_json(state_file("backup.json"), default=None) or {}
    bts = float(receipt.get("ts") or 0)
    if bts <= 0:
        r.warn("state backup", "never run — the evidence base has no copy. "
                               "python scripts/backup_state.py")
    elif not receipt.get("verified"):
        r.warn("state backup", f"last archive did not verify: {receipt.get('detail')}")
    else:
        hrs = (time.time() - bts) / 3600
        msg = f"{receipt.get('files')} files, {receipt.get('bytes', 0) / 1e6:.1f}MB, {hrs:.0f}h ago"
        (r.ok if hrs < 36 else r.warn)("state backup", msg)


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    print(f"\n{DIM}pathiel — live readiness{OFF}\n")
    r = Report()
    for check in (check_secrets, check_capital, check_margin_headroom,
                  check_disk, check_backup):
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
