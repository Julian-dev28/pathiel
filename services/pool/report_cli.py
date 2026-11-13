"""python -m services.pool report [--address 0x...] [--offline BUNDLE] [--json]

This account's own graded report from its public fills since it joined the
research pool. Read only; publishes nothing.

Exit codes: 0 done, 2 a rule failed (not a member, no account), 3 the venue
could not be read.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

import contracts.pool as pool
from services.pool import account as ACC
from services.pool import fetch as F
from services.pool import report as RPT
from services.pool.venue import VenueError


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    assert args and args[0] == "report"
    ap = argparse.ArgumentParser(prog="python -m services.pool report",
                                 description="This account's own graded research pool report.")
    ap.add_argument("--address", help="default: services.pool.account.configured_account()")
    ap.add_argument("--offline", metavar="BUNDLE", help="read inputs and candles from a saved bundle, no network")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(args[1:])

    address = a.address or ACC.configured_account()
    if not address:
        print("pathiel pool report: no account configured and no --address given", file=sys.stderr)
        return 2
    bundle = F.load_bundle(a.offline) if a.offline else None
    try:
        result = RPT.personal(address, bundle=bundle)
    except pool.PoolError as e:
        print(f"pathiel pool report: {e.code}: {e}", file=sys.stderr)
        return 2
    except VenueError as e:
        print(f"pathiel pool report: {e}", file=sys.stderr)
        return 3
    print(json.dumps(result) if a.json else result["markdown"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
