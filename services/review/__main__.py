"""python3 -m services.review run [--since YYYY-MM-DD] [--offline BUNDLE] ...

Fetch the desks' fills (public, read only), update the ledger, compute the
fluke-or-edge table, write docs/strategy/learning/REVIEW_<UTC date>.md and
print one summary line. `--offline BUNDLE` reads a saved bundle instead of
the network (tests, replays); `--save-bundle DIR` saves this run's inputs.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

from services.review import env
from services.review import inputs as I
from services.review import review
from services.review import stats as S
from services.strategies.evals import loss_audit as LA


def _since(s: Optional[str]) -> int:
    if not s:
        return LA.SINCE_MS
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m services.review", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="update the ledger and write the review")
    r.add_argument("--since", help="only trades entered on or after this UTC date (default 2026-10-20)")
    r.add_argument("--offline", metavar="BUNDLE", help="read a saved bundle dir, no network")
    r.add_argument("--save-bundle", metavar="DIR", help="save this run's inputs as a bundle")
    r.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""))
    r.add_argument("--metals-theses-dir", default=os.environ.get("PATHIEL_METALS_THESES_DIR", ""))
    r.add_argument("--work-dir", default="", help="raw fetch cache for this run (default /tmp/review/<stamp>)")
    r.add_argument("--ledger", default="", help="ledger path (default $PATHIEL_STATE_DIR/review/ledger.jsonl)")
    r.add_argument("--report-dir", default="", help="default docs/strategy/learning")
    r.add_argument("--seed", type=int, default=S.SEED)
    a = ap.parse_args(argv)

    if a.offline:
        bundle = I.load_bundle(a.offline)
    else:
        dirs = {"oil": a.oil_theses_dir or env.theses_dir("oil"),
                "metals": a.metals_theses_dir or env.theses_dir("metals")}
        work = a.work_dir or os.path.join("/tmp/review", time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
        bundle = I.load_live(work, dirs)
    if a.save_bundle:
        I.save_bundle(bundle, a.save_bundle)
    ledger_path = a.ledger or env.ledger_path()
    res = review.run(bundle, since_ms=_since(a.since), ledger_path=ledger_path,
                     last_run_path=os.path.join(os.path.dirname(ledger_path), "last_run.json"),
                     report_dir=a.report_dir or None, seed=a.seed)
    print(res["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
