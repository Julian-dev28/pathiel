#!/usr/bin/env python3
"""Realized-PnL attribution from agent memory and the current OI snapshot."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from _memory_io import load_memory


Row = dict[str, Any]


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _b(v: Any) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    return "unknown"


def _pnl_usd(row: Row) -> float:
    """Net USD PnL, preferring net realized pct over older gross USD fields."""
    pct = row.get("realized_pnl_pct")
    notional = _f(row.get("notional_usd"))
    leverage = _f(row.get("leverage"), 1.0)
    if pct is not None and notional > 0 and leverage > 0:
        return (notional / leverage) * _f(pct) / 100.0
    return _f(row.get("realized_pnl_usd"))


def _stats(rows: Iterable[Row]) -> dict[str, float]:
    rs = list(rows)
    pnl = sum(_pnl_usd(r) for r in rs)
    wins = [r for r in rs if _pnl_usd(r) > 0]
    gw = sum(_pnl_usd(r) for r in wins)
    gl = -sum(_pnl_usd(r) for r in rs if _pnl_usd(r) < 0)
    return {
        "n": float(len(rs)),
        "pnl": pnl,
        "win_rate": (len(wins) / len(rs) * 100.0) if rs else 0.0,
        "pf": (gw / gl) if gl > 0 else (999.0 if gw > 0 else 0.0),
        "avg": (pnl / len(rs)) if rs else 0.0,
    }


def _line(label: str, rows: Iterable[Row]) -> str:
    s = _stats(rows)
    return (
        f"{label:<28} n={int(s['n']):>3} "
        f"pnl={s['pnl']:>+8.2f} wr={s['win_rate']:>5.1f}% "
        f"pf={s['pf']:>5.2f} avg={s['avg']:>+6.2f}"
    )


def _group(rows: list[Row], title: str, key_fn: Callable[[Row], Any], *, limit: int = 20) -> None:
    groups: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        groups[str(key_fn(r))].append(r)
    print(f"\n{title}")
    for key, rs in sorted(groups.items(), key=lambda kv: _stats(kv[1])["pnl"]):
        print(_line(key[:28], rs))
        limit -= 1
        if limit <= 0:
            break


def _load_oi(path: Path) -> dict[str, float]:
    try:
        with path.open() as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, float] = {}
    for coin, val in (raw.get("oi") or {}).items():
        if isinstance(val, dict):
            oi = _f(val.get("oi"))
        else:
            oi = _f(val)
        if oi > 0:
            out[str(coin)] = oi
    return out


def _base_coin(coin: str) -> str:
    return coin.split(":", 1)[-1]


def _gex(close: Row) -> dict[str, Any]:
    sig = close.get("signals_at_entry") or {}
    g = sig.get("gex") or sig.get("options_gex") or {}
    return g if isinstance(g, dict) else {}


def _call_wall_distance(close: Row) -> float | None:
    g = _gex(close)
    call_wall = _f(g.get("call_wall"))
    spot = _f(g.get("spot"))
    if call_wall <= 0 or spot <= 0:
        return None
    return (call_wall - spot) / spot * 100.0


def _link_analysis(memory: dict[str, Any], closes: list[Row]) -> None:
    analyses = {
        str(a.get("id")): a
        for a in memory.get("analyses", [])
        if isinstance(a, dict) and a.get("id")
    }
    trades_by_key: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for t in memory.get("trades", []):
        if not isinstance(t, dict):
            continue
        trades_by_key[(str(t.get("coin")), str(t.get("side", "")).lower())].append(t)
    for trades in trades_by_key.values():
        trades.sort(key=lambda t: _f(t.get("executed_at")))

    for c in closes:
        entry_ts = _f(c.get("entry_time"))
        if entry_ts <= 0:
            continue
        key = (str(c.get("coin")), str(c.get("side", "")).lower())
        candidates = trades_by_key.get(key, [])
        best: Row | None = None
        best_gap = 15 * 60 * 1000.0
        for t in candidates:
            gap = abs(_f(t.get("executed_at")) - entry_ts)
            if gap <= best_gap:
                best = t
                best_gap = gap
        if best and best.get("analysis_id") in analyses:
            c["_analysis"] = analyses[best["analysis_id"]]


def _gex_thresholds(rows: list[Row]) -> None:
    gex_rows = [
        r for r in rows
        if ":" in str(r.get("coin")) and str(r.get("side", "")).lower() == "long"
        and _gex(r).get("regime") == "pin_long_gamma"
        and _call_wall_distance(r) is not None
    ]
    print("\nHIP3 GEX call-wall filter test")
    if not gex_rows:
        print("no HIP3 closes with entry GEX wall data")
        return
    print(_line("all gex rows", gex_rows))
    for threshold in (1, 3, 5, 7, 10, 15, 20):
        blocked = [r for r in gex_rows if 0 <= (_call_wall_distance(r) or -999.0) <= threshold]
        kept = [r for r in gex_rows if r not in blocked]
        print(
            f"call wall 0..{threshold:>2}%  "
            f"blocked {int(_stats(blocked)['n']):>2} pnl={_stats(blocked)['pnl']:>+7.2f}  "
            f"kept {int(_stats(kept)['n']):>2} pnl={_stats(kept)['pnl']:>+7.2f}"
        )
    print("worst/nearest gex rows")
    for r in sorted(gex_rows, key=lambda x: (_call_wall_distance(x) or 999.0))[:12]:
        print(
            f"  {str(r.get('coin')):<10} pnl={_pnl_usd(r):>+6.2f} "
            f"dist={(_call_wall_distance(r) or 0.0):>5.1f}% "
            f"forced={_b(r.get('forced_override')):<7} "
            f"regime={str(r.get('regime_at_entry', 'unknown'))}"
        )


def _oi_buckets(rows: list[Row], oi: dict[str, float]) -> None:
    print("\nCurrent OI snapshot filter test")
    if not oi:
        print("no OI values found")
        return
    covered = [r for r in rows if _base_coin(str(r.get("coin"))) in oi]
    print(f"oi coverage: {len(covered)}/{len(rows)} closes")
    if not covered:
        return
    for threshold in (1_000_000, 5_000_000, 10_000_000, 20_000_000, 50_000_000, 100_000_000):
        kept = [r for r in covered if oi.get(_base_coin(str(r.get("coin"))), 0.0) >= threshold]
        print(_line(f"oi >= ${threshold/1_000_000:g}M", kept))


def _linked_trigger_stats(rows: list[Row]) -> None:
    linked = [r for r in rows if isinstance(r.get("_analysis"), dict)]
    print("\nLinked analysis trigger test")
    if not linked:
        print("no closes linkable to stored trades/analyses")
        return
    print(_line("linked closes", linked))
    trigger_names = (
        "daily_mover_fired",
        "breakout_fired",
        "volume_spike_fired",
        "momentum_burst_fired",
        "uptrend_momentum_fired",
        "slow_burn_fired",
        "whale_signal",
    )
    for name in trigger_names:
        fired = [r for r in linked if bool((r.get("_analysis") or {}).get(name))]
        if fired:
            print(_line(name, fired))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("memory", nargs="?", default=".agent-memory.json")
    ap.add_argument("oi_history", nargs="?", default=".oi-history.json")
    args = ap.parse_args()

    memory_path = Path(args.memory)
    oi_path = Path(args.oi_history)
    memory = load_memory(memory_path)
    closes = [c for c in memory.get("closes", []) if isinstance(c, dict)]
    _link_analysis(memory, closes)
    oi = _load_oi(oi_path)

    print(f"memory={memory_path} closes={len(closes)} trades={len(memory.get('trades', []))} "
          f"analyses={len(memory.get('analyses', []))}")
    print(f"oi_snapshot={oi_path} symbols={len(oi)}")
    print("\nOverall")
    print(_line("all closes", closes))

    _group(closes, "By side", lambda r: r.get("side", "unknown"))
    _group(closes, "By HIP3", lambda r: _b(r.get("is_hip3", ":" in str(r.get("coin")))))
    _group(closes, "By forced override", lambda r: _b(r.get("forced_override")))
    _group(closes, "By entry regime", lambda r: r.get("regime_at_entry", "unknown"))
    _group(closes, "Worst coins", lambda r: r.get("coin", "unknown"), limit=12)
    _gex_thresholds(closes)
    _oi_buckets(closes, oi)
    _linked_trigger_stats(closes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
