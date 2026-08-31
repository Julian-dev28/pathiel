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

# .env.local carries PATHIA_STATE_DIR. Load it BEFORE importing anything that
# resolves a state path, or every book reads as empty from the wrong directory.
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
    from pathia.agents.ai_brain import provider_readiness
    x = provider_readiness()
    if x.get("ready"):
        note = "" if x.get("deployable") else "local-only provider"
        r.ok("ai brain", f"{x['provider']} {note}")
    else:
        r.block("ai brain", x.get("reason", "not usable"),
                "set AI_BRAIN_PROVIDER=openrouter with OPENROUTER_API_KEY, "
                "or install the CLI binary")


def check_capital(r: Report) -> None:
    from pathia.agents.config_store import read_agent_config
    from pathia.agents.executor import min_tradable_equity
    from pathia.dashboard import _risk_payload
    x = _risk_payload()
    # The LIVE config's floor, not the bare backstop — it is derived from the
    # enabled book set, so quoting min_tradable_equity({}) here would print $25
    # while the executor actually refuses below $88.89.
    floor = min_tradable_equity(read_agent_config())
    eq = float(x.get("equity") or 0)
    if eq >= floor:
        r.ok("equity", f"${eq:.2f} clears the ${floor:.0f} floor")
    else:
        r.block("equity", f"${eq:.2f} is under the ${floor:.2f} floor "
                          f"(what all enabled books cost to hold at once)",
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
    import pathia.dashboard as db
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
            importlib.import_module(f"pathia.agents.{mod}")
        except Exception as exc:
            broken.append(f"{mod}: {str(exc)[:60]}")
    if broken:
        r.block("book imports", "; ".join(broken),
                "the trading loop imports these at module scope and cannot start")
    else:
        r.ok("book imports", "all live books import")


def check_book_reachability(r: Report) -> None:
    """Can the live books actually fire under the current allowlist?

    W-FUND1: the majors allowlist blocked 92-100% of every book's historical
    signals, and 100% of unlock_short_runin's. A book that is live, validated
    and structurally unable to fire is the shadow state wearing a live badge —
    and it is invisible until someone funds the account and waits.
    """
    import json

    import pathia.dashboard as db
    from pathia.agents import shadow_ledger as SL
    from pathia.agents.config_store import read_agent_config
    from pathia.agents.universe import in_allowlist

    allow = read_agent_config().get("coin_allowlist") or []
    if not allow:
        r.ok("book reachability", "no allowlist — books trade their own universe")
        return
    worst = []
    for book in sorted(db._KNOWN_BOOK_NAMES):
        path = SL._book_path(book)
        if not os.path.exists(path):
            continue
        coins = []
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        coins.append(json.loads(line)["coin"])
                    except Exception:
                        continue
        if not coins:
            continue
        ok = sum(1 for c in coins if in_allowlist(c, allow))
        pct = 100 * ok / len(coins)
        if pct < 25:
            worst.append(f"{book} {pct:.0f}%")
    if worst:
        r.block("book reachability",
                f"allowlist blocks most signals: {', '.join(worst)}",
                "clear coin_allowlist (volume floors still gate liquidity), or "
                "delete the books that cannot fire — see W-FUND1")
    else:
        r.ok("book reachability", "books can fire under the allowlist")


def check_margin_headroom(r: Report) -> None:
    """Enough equity for the books to hold positions simultaneously.

    Funding to exactly the dust floor buys a system where the first book to fire
    consumes the whole budget and the rest are margin-blocked behind it.
    """
    import pathia.dashboard as db
    from pathia.agents.config_store import read_agent_config
    from pathia.dashboard import _risk_payload

    cfg = read_agent_config()
    min_avail = float(cfg.get("min_available_margin_pct", 0.10))
    total = 0.0
    for book in db._KNOWN_BOOK_NAMES:
        c = cfg.get(book) or cfg.get("unlock_short") or {}
        n = float(c.get("notional_usd", 0) or 0)
        lev = max(1, int(c.get("leverage", 1) or 1))
        total += n / lev
    needed = total / (1 - min_avail) if min_avail < 1 else total
    eq = float(_risk_payload().get("equity") or 0)
    if eq >= needed:
        r.ok("margin headroom", f"${eq:.2f} covers all {len(db._KNOWN_BOOK_NAMES)} books "
                                f"(${needed:.2f})")
    else:
        holdable = int(eq * (1 - min_avail) // (total / max(len(db._KNOWN_BOOK_NAMES), 1))) \
            if total else 0
        r.warn("margin headroom",
               f"${eq:.2f} holds {holdable} of {len(db._KNOWN_BOOK_NAMES)} books at once "
               f"— ${needed:.2f} needed for all")


def check_feed(r: Report) -> None:
    from pathia.agents import perception
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
    from pathia import log_setup
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

    Both are scheduler jobs. PathiaSupervisionStale and PathiaAlertingStale
    cover them under Prometheus, but the local evaluator cannot page for its
    own death — so it is reported here, where a human is already looking.
    """
    import time

    from pathia.agents.atomic_io import read_json
    from pathia.agents.rebalancer_owned import state_file

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
    print(f"\n{DIM}pathia — live readiness{OFF}\n")
    r = Report()
    for check in (check_secrets, check_brain, check_capital, check_books,
                  check_book_reachability, check_margin_headroom,
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
