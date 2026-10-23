"""Live instrument verification, so a desk's hardcoded instrument list is
never a guess. Pulls the real Hyperliquid universe (main dex + every HIP-3
dex) via pathiel.client.universe.get_universe(include_hip3=True) — the same
data pathiel.agents.hyperfeed.market_list_instruments() serves — and reports
24h volume / open interest / max leverage per coin, so a human can decide
what belongs on a desk and paste the verified numbers into that desk's
docstring.

This is read-only (HL public /info), matching the live-money rule in
docs/overhaul/SWARM_RULES.md section 1. It makes no account calls.

Run it:
    PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m services.strategies.desks._verify oil
    PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m services.strategies.desks._verify top5
    PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m services.strategies.desks._verify --all
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List, Optional

MIN_VOLUME_USD = 1_000_000.0  # the bar every desk in this registry uses

# Coin-symbol keyword groups, one per desk id. Matching is on the symbol
# AFTER the "dex:" prefix, exact-equals (not substring — "CL" as a substring
# would also match "CIRCLE", "ORACLE", etc; HL symbols are short enough that
# exact-match on the known tickers is the right tool here).
_CANDIDATES: Dict[str, List[str]] = {
    "oil": ["CL", "BRENTOIL", "NATGAS", "HO", "RBOB", "GASOLINE", "USOIL", "HEATOIL"],
    "gas": ["NATGAS"],
    "btc": ["BTC"],
    "top5": [],  # top5 ranks ALL native (dex=None) perps by volume instead
    "metals": ["GOLD", "SILVER", "COPPER", "PLATINUM", "PALLADIUM", "XAU", "XAG"],
    "indices": ["XYZ100", "SP500", "JP225", "KR200", "IBOV", "NIFTY", "NDX", "DJI", "VIX"],
    "ai": ["NVDA", "AMD", "MSFT", "GOOGL", "GOOG", "META", "TSM", "PLTR", "MU", "AVGO",
          "ORCL", "ARM", "AAPL", "AMZN", "TSLA", "SMCI", "INTC", "COIN", "CRWD"],
}


def fetch_universe(force_refresh: bool = True) -> List[Dict[str, Any]]:
    from pathiel.client.universe import get_universe
    return get_universe(include_hip3=True, force_refresh=force_refresh)


def _row(m: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "coin": m.get("coin"), "dex": m.get("dex"),
        "volume_24h_usd": float(m.get("dayNtlVlm") or 0),
        "open_interest_usd": float(m.get("openInterest") or 0),
        "max_leverage": m.get("maxLeverage"),
        "mid": m.get("midPx"),
    }


def candidates_for(desk_id: str, universe: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Every perp whose symbol matches this desk's keyword group, sorted by
    volume descending, each flagged `above_bar` against MIN_VOLUME_USD."""
    universe = universe if universe is not None else fetch_universe()
    perps = [m for m in universe if m.get("type") == "perp"]

    if desk_id == "top5":
        rows = [_row(m) for m in perps if not m.get("dex")]
    else:
        symbols = set(_CANDIDATES.get(desk_id, []))
        rows = [_row(m) for m in perps
               if (m.get("coin") or "").split(":")[-1].upper() in symbols]

    for r in rows:
        r["above_bar"] = r["volume_24h_usd"] >= MIN_VOLUME_USD
    return sorted(rows, key=lambda r: r["volume_24h_usd"], reverse=True)


def _print_report(desk_id: str, rows: List[Dict[str, Any]]) -> None:
    print(f"\n=== {desk_id} ===")
    if not rows:
        print("  (no matching symbols found in the live universe)")
        return
    for r in rows:
        bar = "OK " if r["above_bar"] else "THIN"
        print(f"  [{bar}] {r['coin']:<16} vol24h=${r['volume_24h_usd']:>14,.0f}  "
              f"oi=${r['open_interest_usd']:>14,.0f}  maxLev={r['max_leverage']}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("desk", nargs="?", help="desk id (oil, gas, btc, top5, ai, metals, indices)")
    parser.add_argument("--all", action="store_true", help="check every desk")
    args = parser.parse_args(argv)

    if not args.all and not args.desk:
        parser.error("pass a desk id or --all")

    universe = fetch_universe()
    desk_ids = list(_CANDIDATES) if args.all else [args.desk]
    for desk_id in desk_ids:
        if desk_id not in _CANDIDATES:
            print(f"unknown desk '{desk_id}'; known: {', '.join(_CANDIDATES)}", file=sys.stderr)
            return 1
        _print_report(desk_id, candidates_for(desk_id, universe))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
