#!/usr/bin/env python3
"""The evidence loop, without a human in it.

Standing operator order (2026-07-20): "all tasks where I have to talk to you
should be automated... you have full reign to evolve, research, theorize and
grow, prune and add." This script is that order made deterministic. It runs on
cron, grades every book against its own forward ledger, and ACTS — promoting
what the evidence earns, demoting what it refutes — then reports what it did.

Decision table (per book, evaluated every run):

  n < MIN_N                                   -> PENDING, no action
  EV <= 0 at the real fee tier                -> DEMOTE  (live -> shadow_only)
  EV > 0 AND both OOS halves > 0
       AND survives 25bps AND mc_p < 0.05     -> PROMOTE (shadow -> live, bounded)
  otherwise (MARGINAL)                        -> hold, no action

Asymmetry is deliberate and is the core safety property: DEMOTION needs only a
non-positive EV at the grader's min-n, because stopping a bleed is cheap and
being wrong costs a forgone edge. PROMOTION additionally demands both OOS
halves positive, survival at a 4x-conservative cost tier, and significance
against a matched same-coin random-time null — because being wrong there costs
real money. A book can be demoted by evidence that would never have promoted it.

Every promotion is BOUNDED: $20 notional, 10x leverage, a reachable stop (the
executor clamps the backup SL to 60/leverage percent, so a wider stop is
decoration), and it inherits the same n=8 review bar it was just promoted by —
so a promoted book that turns is demoted by the next run, automatically.

Config is hot-read, so promotions/demotions need NO restart. Code is untouched.

Usage:
    python3 scripts/autonomous_cycle.py            # grade + act + report
    python3 scripts/autonomous_cycle.py --dry-run  # report only, change nothing
    python3 scripts/autonomous_cycle.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_ENV = _REPO / ".env.local"
if _ENV.is_file():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from hermes_trader.agents import shadow_ledger as SL  # noqa: E402

MIN_N = 8
NULL_DRAWS = 2000
PROMOTE_MAX_P = 0.05
REAL_FEE_TIER = "slip6"      # measured 6.1bps round-trip (730 fills, 30d)
STRICT_FEE_TIER = "slip25"   # promotions must survive 4x the real cost

# book -> where its live switch lives in .agent-config.json.
# ("top", key)            -> cfg[key]["shadow_only"]
# ("nested", key, sub)    -> cfg[key][sub]["shadow_only"]
_SWITCHES: Dict[str, tuple] = {
    "engulf_short": ("top", "engulf_short"),
    "crash_continue_div_short": ("top", "crash_continue_div_short"),
    "rally_exhaustion": ("top", "rally_exhaustion"),
    "funding_spike_short": ("top", "funding_spike_short"),
    "unlock_short_runin": ("top", "unlock_short"),
    "news_surge_short": ("top", "news_surge_short"),
    "xs_xyz_equities": ("top", "xs_xyz_equities"),
    "extreme_fade": ("top", "extreme_fade"),
    "mover_pass": ("nested", "mover_recorders", "pass_live"),
    "mover_pass_short": ("nested", "mover_recorders", "pass_short_live"),
    "young_mover_short": ("nested", "mover_recorders", "young_short_live"),
}

# Books whose live arm is a counterfactual/recorder with no capital path, or
# whose direction is owned by another book. Never auto-promoted.
_NEVER_PROMOTE = frozenset({
    "news_catalyst", "young_listings", "majors_swing", "mover_b15_up",
    "whale_flow", "news_ta_quadrant", "wallet_follow", "v2_xs_momentum",
    "unlock_short", "premium_fade_short", "neg_funding_fade",
})

# Promotion sizing — bounded, and the stop must be REACHABLE: the executor
# clamps the backup SL to entry*(backup_sl_max_frac_of_liq/leverage).
PROMOTE_NOTIONAL_USD = 20.0
PROMOTE_LEVERAGE = 10
PROMOTE_STOP_PCT = 6.0       # == 60/10, exactly at the clamp boundary


def _cfg_path() -> Path:
    return _REPO / ".agent-config.json"


def _read_cfg() -> Dict[str, Any]:
    return json.loads(_cfg_path().read_text())


def _write_cfg(cfg: Dict[str, Any]) -> None:
    _cfg_path().write_text(json.dumps(cfg, indent=2) + "\n")


def _block(cfg: Dict[str, Any], book: str) -> Optional[Dict[str, Any]]:
    sw = _SWITCHES.get(book)
    if not sw:
        return None
    if sw[0] == "top":
        return cfg.get(sw[1])
    return (cfg.get(sw[1]) or {}).get(sw[2])


def _is_live(cfg: Dict[str, Any], book: str) -> Optional[bool]:
    b = _block(cfg, book)
    if not isinstance(b, dict):
        return None
    if not b.get("enabled", False):
        return False
    return not bool(b.get("shadow_only", False))


def grade_book(book: str, now_ms: int) -> Dict[str, Any]:
    """Forward-grade one book, with a matched same-coin random-time null."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "sis", str(_REPO / "scripts" / "shadow_inverse_status.py"))
    sis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sis)

    recs = SL.load(book)
    if not recs:
        return {"book": book, "n": 0}
    kept, _ = SL.dedup_episodes(recs)
    fetch_fwd, fetch_funding, cache = sis._make_fetchers(kept, now_ms)
    g = SL.grade_records(kept, fetch_fwd, now_ms=now_ms,
                         fetch_funding=fetch_funding, dedup=False)
    n = int(g.get("n", 0))
    out = {"book": book, "n": n,
           "ev_real": (g.get(REAL_FEE_TIER) or {}).get("mean_pct"),
           "ev_strict": (g.get(STRICT_FEE_TIER) or {}).get("mean_pct"),
           "halves": g.get("oos_12bps") or {}}
    if n >= MIN_N:
        null = sis.matched_null(g.get("detail") or [], cache,
                                NULL_DRAWS, random.Random(7))
        out["mc_p"] = (null or {}).get("mc_p")
        out["excess"] = (null or {}).get("excess_pct")
    return out


def decide(grade: Dict[str, Any], live: Optional[bool]) -> Dict[str, str]:
    """The whole decision table. Pure — every branch is unit-tested."""
    book, n = grade["book"], grade.get("n", 0)
    if n < MIN_N:
        return {"verdict": "PENDING", "action": "none",
                "why": f"n={n} < {MIN_N}"}
    ev = grade.get("ev_real")
    if ev is None:
        return {"verdict": "PENDING", "action": "none", "why": "no EV"}
    if ev <= 0:
        return ({"verdict": "REFUTED", "action": "demote",
                 "why": f"EV{REAL_FEE_TIER}={ev:+.2f}% <= 0 at n={n}"}
                if live else
                {"verdict": "REFUTED", "action": "none",
                 "why": f"EV{REAL_FEE_TIER}={ev:+.2f}% <= 0, already not live"})
    h1, h2 = grade.get("halves", {}).get("first"), grade.get("halves", {}).get("second")
    strict, p = grade.get("ev_strict"), grade.get("mc_p")
    passes = (h1 is not None and h2 is not None and h1 > 0 and h2 > 0
              and strict is not None and strict > 0
              and p is not None and p < PROMOTE_MAX_P)
    if not passes:
        return {"verdict": "MARGINAL", "action": "none",
                "why": (f"EV+{ev:+.2f}% but halves={h1}/{h2} "
                        f"strict={strict} mc_p={p}")}
    if book in _NEVER_PROMOTE:
        return {"verdict": "VALIDATED", "action": "none",
                "why": "validated but has no bounded capital path (recorder//counterfactual)"}
    if live:
        return {"verdict": "VALIDATED", "action": "none", "why": "already live"}
    return {"verdict": "VALIDATED", "action": "promote",
            "why": (f"EV{REAL_FEE_TIER}={ev:+.2f}%, halves {h1:+.2f}/{h2:+.2f}, "
                    f"survives 25bps ({strict:+.2f}%), mc_p={p}")}


def apply_action(cfg: Dict[str, Any], book: str, action: str) -> bool:
    b = _block(cfg, book)
    if not isinstance(b, dict):
        return False
    if action == "demote":
        if b.get("shadow_only") is True:
            return False
        b["shadow_only"] = True
        return True
    if action == "promote":
        b["enabled"] = True
        b["shadow_only"] = False
        b["notional_usd"] = PROMOTE_NOTIONAL_USD
        b["leverage"] = PROMOTE_LEVERAGE
        b["stop_pct"] = PROMOTE_STOP_PCT     # reachable by construction
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    args = ap.parse_args()

    now_ms = int(time.time() * 1000)
    cfg = _read_cfg()
    rows: List[Dict[str, Any]] = []
    for book in SL.list_books():
        g = grade_book(book, now_ms)
        live = _is_live(cfg, book)
        d = decide(g, live)
        rows.append({**g, "live": live, **d})

    acted: List[str] = []
    if not args.dry_run:
        for r in rows:
            if r["action"] in ("promote", "demote") and apply_action(cfg, r["book"], r["action"]):
                acted.append(f"{r['action'].upper()} {r['book']}: {r['why']}")
        if acted:
            _write_cfg(cfg)

    if args.json:
        print(json.dumps({"rows": rows, "acted": acted}, indent=2, default=str))
        return 0

    stamp = time.strftime("%Y-%m-%d %H:%M")
    print(f"# autonomous cycle {stamp} — graded {len(rows)} book(s) at "
          f"{REAL_FEE_TIER} (real 6.1bps), promotion needs 25bps + mc_p<{PROMOTE_MAX_P}")
    print(f"{'book':<26} {'live':>5} {'n':>4} {'EV6':>8} {'EV25':>8} {'mc_p':>7}  verdict")
    print("-" * 92)
    for r in sorted(rows, key=lambda x: (x["verdict"] != "VALIDATED", -x.get("n", 0))):
        ev = f"{r['ev_real']:+.2f}%" if r.get("ev_real") is not None else "—"
        ev25 = f"{r['ev_strict']:+.2f}%" if r.get("ev_strict") is not None else "—"
        p = f"{r['mc_p']:.4f}" if r.get("mc_p") is not None else "—"
        lv = "yes" if r["live"] else ("no" if r["live"] is not None else "—")
        print(f"{r['book']:<26} {lv:>5} {r.get('n',0):>4} {ev:>8} {ev25:>8} {p:>7}  "
              f"{r['verdict']}: {r['why']}")
    print()
    if acted:
        print("ACTED (config is hot-read — no restart needed):")
        for a in acted:
            print(f"  {a}")
    else:
        print("No action: no book crossed a promotion or demotion bar this run.")

    if acted and not args.dry_run and not args.no_commit:
        try:
            subprocess.run(["git", "add", ".agent-config.json"], cwd=_REPO, check=True)
            msg = "auto(cycle): " + "; ".join(acted)[:1500]
            subprocess.run(["git", "commit", "-q", "-m", msg], cwd=_REPO, check=True)
            subprocess.run(["git", "push", "-q", "origin", "able"], cwd=_REPO, check=True)
            print("\ncommitted + pushed")
        except Exception as exc:
            print(f"\ncommit/push failed (config change still applied): {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
