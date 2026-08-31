#!/usr/bin/env python3
"""Record deposits and withdrawals so the drawdown number means something.

Without this, an equity curve cannot tell a trading loss from the operator
moving USDC out, and the risk panel has to label its own drawdown "an equity
decline, not necessarily a loss". Run it on a schedule; it is idempotent, so
overlapping windows never double-count.

    python scripts/record_capital_flows.py                 # last 30 days
    python scripts/record_capital_flows.py --since-days 400  # backfill
    python scripts/record_capital_flows.py --status          # what is recorded
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pathia.agents import capital_flows as cf   # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-days", type=float, default=30.0,
                    help="how far back to pull the ledger (default 30)")
    ap.add_argument("--status", action="store_true",
                    help="print what is already recorded and exit")
    a = ap.parse_args(argv)

    rows = cf.load_flows()
    real = [r for r in rows if not str(r.get("kind", "")).startswith("_")]

    if a.status:
        started = cf._recording_started_at()
        print(f"flows file: {cf._flows_path()}")
        print(f"recording started: "
              f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(started / 1000)) if started else 'never'}")
        print(f"recorded flows: {len(real)}")
        for r in real[-12:]:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(r['ts']) / 1000))
            print(f"  {when}  {r['usd']:>+12.2f}  {r['kind']}")
        if real:
            print(f"net capital in: {sum(float(r['usd']) for r in real):+.2f}")
        return 0

    # env.load equivalent — the CLI must see the same credentials the loop does
    env_file = Path(__file__).resolve().parent.parent / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    user = os.environ.get("HYPERLIQUID_MASTER_ADDRESS") or \
        os.environ.get("HYPERLIQUID_WALLET_ADDRESS", "")
    if not user:
        print("no HYPERLIQUID_WALLET_ADDRESS / _MASTER_ADDRESS in the environment",
              file=sys.stderr)
        return 2

    since = int((time.time() - a.since_days * 86400) * 1000)
    res = cf.record_flows(user, since)
    # The marker is what lets "no deposits ever" be distinguished from "nothing
    # was ever recorded". Written only on a successful fetch.
    if res.get("status") == "ok":
        cf.mark_recording_started(since)
    print(f"capital flows: {res}")
    return 0 if res.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
