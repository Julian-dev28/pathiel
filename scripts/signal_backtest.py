#!/usr/bin/env python3
"""Forward signal backtest — reads the outcome store and correlates the signal
snapshot AT ENTRY with realized PnL. Lookahead-free by construction: the signals
were captured at entry time, the PnL is the realized exit.

Becomes meaningful only once enough closes carry `signals_at_entry` (i.e. trades
opened AFTER the entry-context wiring shipped). Until then it says so.

Usage: python3 scripts/signal_backtest.py [path-to-.agent-memory.json]
"""
import json
import sys
from collections import defaultdict


def _stats(rows):
    n = len(rows)
    if not n:
        return (0, 0.0, 0.0)
    wins = sum(1 for r in rows if (r.get("realized_pnl_usd") or 0) > 0)
    net = sum(r.get("realized_pnl_usd") or 0 for r in rows)
    return (n, wins / n * 100, net)


def _line(label, rows):
    n, wr, net = _stats(rows)
    if n:
        print(f"  {label:32} n={n:<3} win={wr:4.0f}%  net=${net:+.2f}  avg=${net/n:+.2f}")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else ".agent-memory.json"
    closes = json.load(open(path)).get("closes", [])
    tagged = [c for c in closes if c.get("signals_at_entry")]
    print(f"\n=== Signal backtest — {len(closes)} closes, {len(tagged)} with entry signals ===")
    if not tagged:
        print("  Not enough data yet: no close carries an entry-signal snapshot.")
        print("  (Populates as positions opened AFTER the entry-context wiring close out.)")
        return

    # 1) crypto whale bias at entry -> outcome
    by_whale = defaultdict(list)
    for c in tagged:
        w = (c.get("signals_at_entry") or {}).get("whale")
        if w:
            by_whale[w.get("bias", "?")].append(c)
    if by_whale:
        print("\n whale bias @ entry:")
        for bias, rows in sorted(by_whale.items()):
            _line(bias, rows)

    # 2) GEX regime at entry (xyz)
    by_gex = defaultdict(list)
    for c in tagged:
        g = (c.get("signals_at_entry") or {}).get("gex")
        if g:
            by_gex[g.get("regime", "?")].append(c)
    if by_gex:
        print("\n GEX regime @ entry (xyz):")
        for reg, rows in sorted(by_gex.items()):
            _line(reg, rows)

    # 3) short-vol regime at entry (xyz)
    by_sv = defaultdict(list)
    for c in tagged:
        s = (c.get("signals_at_entry") or {}).get("short_vol")
        if s:
            by_sv[s.get("regime", "?")].append(c)
    if by_sv:
        print("\n short-vol regime @ entry (xyz):")
        for reg, rows in sorted(by_sv.items()):
            _line(reg, rows)

    # 4) news breaking at entry
    news_break = [c for c in tagged if ((c.get("signals_at_entry") or {}).get("news") or {}).get("breaking")]
    if news_break:
        print("\n news breaking @ entry:")
        _line("breaking", news_break)

    # 5) the decisive one: BOOSTED entries vs not (did boost catch net-positive?)
    boosted = [c for c in tagged if (c.get("enforcement_at_entry") or {}).get("boost")]
    not_boosted = [c for c in tagged if not (c.get("enforcement_at_entry") or {}).get("boost")]
    print("\n enforcement:")
    _line("BOOSTED entries", boosted)
    _line("non-boosted entries", not_boosted)
    print("\n  (VETO effectiveness can't be read here — vetoed trades never open."
          "\n   Grep 'signal VETO' in the log for what was blocked.)")


if __name__ == "__main__":
    main()
