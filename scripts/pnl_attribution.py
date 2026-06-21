#!/usr/bin/env python3
"""Realized-PnL attribution from .agent-memory.json.

Offline, no exchange/API calls. Use this after strategy changes to separate
legacy damage from current live buckets instead of treating one all-time number
as the whole truth.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable


def _f(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rows(closes: list[dict[str, Any]], key_fn: Callable[[dict[str, Any]], str]) -> list[tuple[float, str, int, int, float]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for close in closes:
        groups.setdefault(key_fn(close), []).append(close)
    out = []
    for key, rows in groups.items():
        pnl = sum(_f(r.get("realized_pnl_usd")) for r in rows)
        wins = sum(1 for r in rows if _f(r.get("realized_pnl_usd")) > 0)
        avg_roe = sum(_f(r.get("realized_pnl_pct")) for r in rows) / len(rows)
        out.append((pnl, key, len(rows), wins, avg_roe))
    return sorted(out)


def _print_group(title: str, closes: list[dict[str, Any]], key_fn: Callable[[dict[str, Any]], str], limit: int) -> None:
    print(f"\n{title}")
    for pnl, key, n, wins, avg_roe in _rows(closes, key_fn)[:limit]:
        print(f"  {key:24s} n={n:3d} win={wins:3d}/{n:<3d} pnl=${pnl:+8.2f} avgROE={avg_roe:+6.1f}%")
    best = list(reversed(_rows(closes, key_fn)))[:limit]
    if best:
        print("  best")
        for pnl, key, n, wins, avg_roe in best:
            print(f"  {key:24s} n={n:3d} win={wins:3d}/{n:<3d} pnl=${pnl:+8.2f} avgROE={avg_roe:+6.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory", default=".agent-memory.json")
    ap.add_argument("--hours", type=float, default=0.0, help="0 = all recorded closes")
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()

    mem = json.loads(Path(args.memory).read_text())
    closes = [c for c in mem.get("closes", []) if isinstance(c, dict)]
    if args.hours > 0:
        now_ms = max([int(c.get("closed_at", 0) or 0) for c in closes] or [int(time.time() * 1000)])
        cutoff = now_ms - int(args.hours * 3600_000)
        closes = [c for c in closes if int(c.get("closed_at", 0) or 0) >= cutoff]

    total = sum(_f(c.get("realized_pnl_usd")) for c in closes)
    wins = sum(1 for c in closes if _f(c.get("realized_pnl_usd")) > 0)
    print(f"closes={len(closes)} win={wins}/{len(closes)} realized_pnl=${total:+.2f}")
    _print_group("by asset class", closes, lambda c: "hip3" if ":" in str(c.get("coin", "")) else "crypto", args.limit)
    _print_group("by path/class", closes, lambda c: f"{'forced' if c.get('forced_override') else 'ai'}:{'hip3' if ':' in str(c.get('coin', '')) else 'crypto'}", args.limit)
    _print_group("by coin", closes, lambda c: str(c.get("coin", "?")), args.limit)
    _print_group("by regime", closes, lambda c: str(c.get("regime_at_entry", "unknown")), args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
