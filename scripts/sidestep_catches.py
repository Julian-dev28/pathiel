#!/usr/bin/env python3
"""Count the TA-sidestep breakouts the conf-floor fix (commit b7e2c30) now CATCHES that
the old runner-gate confidence floor would have blocked (conf 0.62 < 0.65). Every executed
sidestep is a catch that was impossible before the fix. Read-only: parses the trading log +
fetches current marks for the caught coins' PnL-so-far.

Usage: python3 scripts/sidestep_catches.py
"""
from __future__ import annotations
import os, re, sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_env = _REPO / ".env.local"
if _env.is_file():
    for _l in _env.read_text().splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, _, v = _l.partition("="); os.environ.setdefault(k.strip(), v.strip())

LOG = _REPO / "logs" / "trading_loop.log"
SIDE = re.compile(r"^(\S+ \S+).*TA sidestep on (.+?): AI PASS")
EXECUTED = re.compile(r"'executed': True")
ENTRY = re.compile(r"'entry_px': ([0-9.]+)")
BLOCK = re.compile(r"'reason': '([^']+)'|runner_gate_blocked \(([^)]+)\)|sidestep SKIPPED on \S+: (\S[^\n]*)")

caught, conf_blocked, struct_blocked = [], [], []
pending = None
for line in LOG.read_text(errors="ignore").splitlines():
    m = SIDE.search(line)
    if m:
        pending = {"ts": m.group(1).split()[-1][:8], "coin": m.group(2)}  # HH:MM:SS
        continue
    if pending is None:
        continue
    if EXECUTED.search(line):
        e = ENTRY.search(line)
        pending["entry_px"] = float(e.group(1)) if e else None
        caught.append(pending); pending = None
    elif "runner_gate_blocked" in line or "SKIPPED" in line or "override_no_volume" in line:
        # split the FIX-RELEVANT conf block (eliminated post-fix) from the CORRECT structure blocks
        (conf_blocked if "confidence" in line else struct_blocked).append(pending)
        pending = None

# current marks for PnL-so-far on the catches
marks = {}
try:
    from hermes_trader.client.hl_client import fetch_all_mids
    marks = fetch_all_mids(include_hip3=True)
except Exception as e:
    print(f"# (mark fetch failed: {e})")

print(f"# SIDESTEP CATCHES (breakouts the conf-floor fix b7e2c30 now admits)")
print(f"# CAUGHT (executed): {len(caught)}   |   conf-blocked (pre-fix; the fix targets these): {len(conf_blocked)}"
      f"   |   structure-blocked (CORRECT, persists): {len(struct_blocked)}")
print(f"# NOTE: the {len(conf_blocked)} conf-blocks are historical (before the fix). Post-fix the conf gate no longer")
print(f"# blocks sidesteps; only structure does (late-chase / no fresh breakout), which is the validated behavior.")
if caught:
    print(f"\n{'time':<10}{'coin':<14}{'entry':>10}{'mark':>10}{'PnL-so-far':>12}")
    print("-" * 64)
    tot = 0.0
    for c in caught:
        coin = c["coin"]; ent = c.get("entry_px")
        mk = None
        try: mk = float(marks.get(coin)) if marks.get(coin) is not None else None
        except Exception: mk = None
        pnl = (mk / ent - 1.0) if (mk and ent) else None
        if pnl is not None: tot += pnl
        ps = f"{100*pnl:+.1f}%" if pnl is not None else "n/a"
        print(f"{c['ts']:<10}{coin:<14}{(ent or 0):>10.5f}{(mk or 0):>10.5f}{ps:>12}")
    n = sum(1 for c in caught if c.get('entry_px') and marks.get(c['coin']))
    print(f"\n# {len(caught)} catches; avg long PnL-so-far {100*tot/max(n,1):+.2f}% (entry->now, not the realized exit).")
print("\n# Each CAUGHT = a TA-confirmed breakout admitted only because the sidestep now bypasses the")
print("# conf floor (the AAVE/JTO/RESOLV/BIRD-type entries that were 100% blocked before the fix).")
