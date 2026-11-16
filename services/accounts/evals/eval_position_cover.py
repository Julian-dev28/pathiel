#!/usr/bin/env python3
"""Does every open position on the live account read as covered by its stop?

2026-11-16 06:10Z: a hand-placed 26.84 Brent long carried the Hyperliquid
app's whole-position TP/SL, which rests with isPositionTpsl=true and sz 0.0.
The sweep and the positions view summed order sizes, read stop_covers 0.0
and called the position NOT FULLY COVERED, which the desk tick answers with a
close. The fix counts such an order as covering the whole position.

This eval reads the live account (public Hyperliquid info reads, no key, no
orders) through services.accounts.positions and prints one line per open
position. It fails when a position has a stop order resting on the venue but
still reads as naked: that is the bug class this guards. A position with no
stop at all is reported, not failed (that is a trading problem, not this bug).

    PATHIEL_STATE_DIR=$HOME/Documents/code/pathia/.state \
        .venv/bin/python -m services.accounts.evals.eval_position_cover [--address 0x...]

Exit 0 when no covered-but-naked position is found, 1 otherwise.
"""
from __future__ import annotations

import argparse
import sys

DEFAULT_ADDRESS = "0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985"   # the pathiel master (public address)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--address", default=DEFAULT_ADDRESS)
    a = ap.parse_args(argv)
    from services.accounts import positions
    snap = positions.fetch_positions_for_account(a.address)
    rows = [p for acct in (snap.get("accounts") or []) for p in (acct.get("positions") or [])]
    bad = 0
    for p in rows:
        has_stop = bool(p.get("stop_px"))
        mismatch = has_stop and p.get("naked")
        bad += int(bool(mismatch))
        print(f"POSITION-COVER {'FAIL' if mismatch else 'OK'} {p.get('coin')} {p.get('side')} "
              f"size={p.get('size')} stop_px={p.get('stop_px')} stop_covers={p.get('stop_covers')} "
              f"naked={p.get('naked')}")
    if not rows:
        print("POSITION-COVER OK no open positions")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
