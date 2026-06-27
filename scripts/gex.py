#!/usr/bin/env python3
"""Free GEX / max-pain / gamma-wall report."""

import sys

from hermes_trader.agents.options_gex import gex_signal


def fmt(x):
    return "-" if x is None else (f"{x:g}")


def main():
    for arg in sys.argv[1:] or ["NVDA"]:
        r = gex_signal(arg)
        if not r:
            print(f"{arg}: no free options data")
            continue
        print(f"\n=== {arg}  (underlying {r.ticker}, spot {r.spot:g}) ===")
        print(f"  total GEX: {r.total_gex:+.1f}M  -> {r.regime}")
        print(f"  gamma flip: {fmt(r.gamma_flip)}   ({r.note})")
        print(f"  call wall: {fmt(r.call_wall)}   put wall: {fmt(r.put_wall)}")
        print(f"  max pain: {fmt(r.max_pain)}   | {r.n_contracts} contracts")


if __name__ == "__main__":
    main()
