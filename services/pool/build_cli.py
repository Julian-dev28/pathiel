"""python -m services.pool build [--as-of ISO] [--offline BUNDLE] [--save-bundle DIR]
                               [--out DIR] [--no-ledger] [--dry-run]

Grades every active member's public fills and writes
docs/scoreboard/scoreboard.json. Fails closed: a pool file that does not
verify, a testnet process or a venue read that fails stops the build before
anything is written. The build writes only under `paths.state_root()` and
the out directory.

Exit codes: 0 done, 2 refused (an invalid pool file, a live run off
mainnet), 3 the venue could not be read.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import contracts.pool as pool
from contracts import runtime
from services.pool import fetch as F
from services.pool import grade as G
from services.pool import paths
from services.pool import scoreboard as SB
from services.pool import venue


def _stamp(as_of_ms: int) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(as_of_ms / 1000))


def _read_previous(out_dir: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(out_dir, pool.SCOREBOARD_JSON)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if not pool.validate_scoreboard(doc) else None


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    assert args and args[0] == "build"
    ap = argparse.ArgumentParser(prog="python -m services.pool build",
                                 description="Grade every active member's public fills and write "
                                             "docs/scoreboard/scoreboard.json.")
    ap.add_argument("--as-of", help="ISO build time (default: now; offline defaults to the bundle's as_of_ms)")
    ap.add_argument("--offline", metavar="BUNDLE", help="read inputs and candles from a saved bundle, no network")
    ap.add_argument("--save-bundle", metavar="DIR", help="save the raw inputs and candles this build read")
    ap.add_argument("--out", metavar="DIR", help="where to read and write scoreboard.json (default: docs/scoreboard)")
    ap.add_argument("--no-ledger", action="store_true", help="grade this run's records only, never touch the ledger")
    ap.add_argument("--dry-run", action="store_true", help="compute the scoreboard but write nothing")
    a = ap.parse_args(args[1:])

    bundle = F.load_bundle(a.offline) if a.offline else None
    if a.as_of:
        as_of_ms = pool.ms_from_iso(a.as_of)
    elif bundle is not None:
        as_of_ms = bundle["as_of_ms"]
    else:
        as_of_ms = int(time.time() * 1000)

    found = pool.scan(paths.repo_pool_root(), as_of_ms)
    if found["problems"]:
        for p in found["problems"]:
            print(f"pathiel pool build: {p['path']}: {p['code']}: {p['message']}", file=sys.stderr)
        return 2
    members: List[Dict[str, Any]] = ([found["house"]] if found["house"] else []) + found["members"]
    out_dir = a.out or paths.scoreboard_dir()

    if bundle is None:
        if runtime.network() != "mainnet":
            print("pathiel pool build: a live build is mainnet only (PATHIEL_NETWORK is not mainnet)",
                  file=sys.stderr)
            return 2
        inputs: Dict[str, Any] = {}
        try:
            for i, member in enumerate(members, start=1):
                print(f"POOL-BUILD {i}/{len(members)} {pool.short_address(member['address'])}", file=sys.stderr)
                inputs[member["address"]] = F.member_inputs(member, as_of_ms)
            bars5, bars1h = F.load_candles(os.path.join(paths.work_dir(_stamp(as_of_ms)), "cache"))
        except venue.VenueError as e:
            print(f"pathiel pool build: {e}", file=sys.stderr)
            return 3
    else:
        inputs = bundle.get("inputs") or {}
        bars5, bars1h = bundle.get("bars5") or {}, bundle.get("bars1h") or {}

    if a.save_bundle:
        F.save_bundle(a.save_bundle, {"as_of_ms": as_of_ms, "inputs": inputs, "bars5": bars5, "bars1h": bars1h})

    records_by_address: Dict[str, List[Dict[str, Any]]] = {}
    for member in members:
        address = member["address"]
        one_inputs = inputs.get(address) or {"fills": [], "orders": []}
        records = G.member_records(one_inputs, bars5, bars1h, member["count_from_ms"], as_of_ms)
        if a.no_ledger or a.dry_run:
            records_by_address[address] = records
        else:
            G.update_ledger(address, records, as_of_ms)
            records_by_address[address] = G.ledger_records(address, member["count_from_ms"])

    if not a.dry_run and not a.no_ledger:
        G.purge_ledgers([m["address"] for m in members])

    previous = _read_previous(out_dir)
    doc = SB.build(members, records_by_address, as_of_ms, left=len(found["left"]))
    changed: Any = "dry" if a.dry_run else SB.write(doc, out_dir)

    for line in SB.new_rows(previous, doc):
        print(line, file=sys.stderr)
    if previous is not None:
        prev_graded, prev_left = previous["counts"]["graded_trades"], previous["counts"]["left"]
        if doc["counts"]["graded_trades"] < prev_graded and doc["counts"]["left"] <= prev_left:
            print(f"POOL-WARN graded fell from {prev_graded} to {doc['counts']['graded_trades']} with no new "
                  "leave: older trades lost their stop or the ledger is gone", file=sys.stderr)

    print(SB.summary_line(doc, changed, out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
