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
import os
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


def check_brain(r: Report) -> None:
    from pathiel.agents.ai_brain import provider_readiness
    x = provider_readiness()
    if x.get("ready"):
        note = "" if x.get("deployable") else "local-only provider"
        r.ok("ai brain", f"{x['provider']} {note}")
    else:
        r.block("ai brain", x.get("reason", "not usable"),
                "set AI_BRAIN_PROVIDER=openrouter with OPENROUTER_API_KEY, "
                "or install the CLI binary")


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


def check_feed(r: Report) -> None:
    from pathiel.agents import perception
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


def check_watchers(r: Report) -> None:
    """Are supervision and alerting themselves still running?

    Both are scheduler jobs. PathielSupervisionStale and PathielAlertingStale
    cover them under Prometheus, but the local evaluator cannot page for its
    own death — so it is reported here, where a human is already looking.
    """
    import time

    from pathiel.agents.atomic_io import read_json
    from pathiel.agents.rebalancer_owned import state_file

    for label, name, why in (
        ("supervision", "supervisor.json", "dead processes stay dead"),
        ("alerting", "alerts.json", "no alert is being delivered"),
    ):
        payload = read_json(state_file(name), default=None)
        ts = float((payload or {}).get("ts") or 0)
        if ts <= 0:
            r.warn(label, f"has never run — {why}. Start with scripts/restart.sh sched")
            continue
        age = time.time() - ts
        if age > 900:
            r.block(label, f"last ran {age / 60:.0f} min ago — {why}",
                    "scripts/restart.sh sched")
        else:
            r.ok(label, f"ran {age / 60:.0f} min ago")
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

    firing = (read_json(state_file("alerts.json"), default=None) or {}).get("firing") or []
    if firing:
        r.warn("alerts firing", ", ".join(firing))


def check_processes(r: Report) -> None:
    """Is each managed process actually up?

    Uses the supervisor's detector, which identifies the process by its exact
    shape (python running the script, or python -m the module) rather than by a
    substring of `ps` output. The substring version matched any command line
    that merely MENTIONED the name — including the shell running this check —
    so it could report a dead process "running". The same code lived here.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import supervise_processes as sup

    commands = sup._command_lines()
    for comp, why in (("loop", "scripts/restart.sh"),
                      ("scheduler", "scripts/restart.sh sched"),
                      ("rotator", "scripts/restart.sh rotate")):
        spec = sup.COMPONENTS[comp]
        if sup.alive(spec, commands):
            r.ok(spec["label"], "running")
        else:
            r.warn(spec["label"], f"not running — start with {why}")


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    print(f"\n{DIM}pathiel — live readiness{OFF}\n")
    r = Report()
    for check in (check_secrets, check_brain, check_capital, check_margin_headroom,
                  check_feed, check_disk, check_processes, check_watchers):
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
