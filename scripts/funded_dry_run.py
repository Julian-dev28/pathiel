#!/usr/bin/env python3
"""What happens the moment this account is funded? Answer it before funding it.

The trading loop is running, the books are live, and the executor refuses every
order because equity is under the structural floor. That means the interesting
question — would these books actually trade, and with what — has never been
answered by running them. This answers it deterministically, from the same
config the executor reads and the same forward ledgers the books wrote, without
invoking the order path at all.

Deliberately NOT a call into `executor.maybe_execute`: mode is LIVE, so driving
the real entry point to "simulate" would place real orders. Nothing here
touches the exchange.

    python scripts/funded_dry_run.py                 # at the derived floor
    python scripts/funded_dry_run.py --equity 250    # at a chosen deposit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _state_env  # noqa: E402

_state_env.load_env_local(ROOT)

GREEN, RED, AMBER, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def book_specs(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every enabled book with a capital path, as the executor sees it.

    `shadow_only` in the key set is the discriminator that keeps a gate
    relaxation (thin_short_relax) from being counted as a book — without it the
    floor came out $111.11 instead of $88.89.
    """
    out = []
    for name, block in sorted(cfg.items()):
        if not isinstance(block, dict):
            continue
        if not {"enabled", "notional_usd", "shadow_only"} <= block.keys():
            continue
        if not block.get("enabled", False):
            continue
        notional = float(block.get("notional_usd") or 0)
        leverage = max(1, int(block.get("leverage", 1) or 1))
        out.append({"book": name, "notional": notional, "leverage": leverage,
                    "margin": notional / leverage,
                    "stop_pct": float(block.get("stop_pct") or 0),
                    "hold_days": float(block.get("hold_days") or 0)})
    return out


def ledger_reach(book: str) -> Tuple[int, Optional[str]]:
    """(signals recorded, most recent coin) from the book's own forward ledger.

    A book with no history is unmeasurable, not zero — those are different
    facts and the report must not conflate them.
    """
    from hermes_trader.agents import shadow_ledger as SL

    for candidate in (book, f"{book}_runin", book.replace("_short", "_short_runin")):
        path = SL._book_path(candidate)
        if not os.path.exists(path):
            continue
        rows = []
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        if rows:
            return len(rows), rows[-1].get("coin")
    return 0, None


def simulate(equity: float, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: what each book can do at `equity`. No network, no order path."""
    from hermes_trader.agents.executor import (MIN_TRADABLE_EQUITY_USD,
                                               min_tradable_equity)

    free_floor = float(cfg.get("min_available_margin_pct", 0.10) or 0.0)
    usable = equity * (1.0 - free_floor)
    books = book_specs(cfg)
    floor = min_tradable_equity(cfg)

    # Greedy in config order: what the account can actually hold at once.
    holdable, spent = [], 0.0
    for b in books:
        if spent + b["margin"] <= usable + 1e-9:
            holdable.append(b["book"])
            spent += b["margin"]

    for b in books:
        b["fits_alone"] = b["margin"] <= usable + 1e-9
        b["clears_exchange_min"] = b["notional"] >= 10.0
    return {"equity": equity, "usable": usable, "free_floor": free_floor,
            "floor": floor, "exchange_min_equity": MIN_TRADABLE_EQUITY_USD,
            "books": books, "holdable": holdable,
            "total_margin": sum(b["margin"] for b in books)}


def main(argv: Optional[List[str]] = None) -> int:
    from hermes_trader.agents.config_store import read_agent_config

    cfg = read_agent_config()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--equity", type=float, default=None,
                    help="simulate at this equity (default: the derived floor)")
    args = ap.parse_args(argv)

    from hermes_trader.agents.executor import min_tradable_equity
    equity = args.equity if args.equity is not None else min_tradable_equity(cfg)
    r = simulate(equity, cfg)

    print(f"\n{DIM}funded dry run — no order path invoked, nothing sent{OFF}\n")
    print(f"  equity            ${r['equity']:.2f}")
    print(f"  free-margin floor {r['free_floor'] * 100:.0f}%  "
          f"{DIM}(usable ${r['usable']:.2f}){OFF}")
    print(f"  all books need    ${r['total_margin']:.2f} margin "
          f"-> ${r['floor']:.2f} equity\n")

    print(f"  {'book':22} {'notional':>9} {'lev':>4} {'margin':>8}  "
          f"{'signals':>8}  status")
    for b in r["books"]:
        n, last = ledger_reach(b["book"])
        hist = f"{n:>8}" if n else f"{'none':>8}"
        if not b["clears_exchange_min"]:
            status = f"{RED}under the $10 exchange minimum{OFF}"
        elif b["book"] in r["holdable"]:
            status = f"{GREEN}can hold a position{OFF}"
        elif b["fits_alone"]:
            status = f"{AMBER}only if it fires first{OFF}"
        else:
            status = f"{RED}cannot open{OFF}"
        print(f"  {b['book']:22} {b['notional']:9.2f} {b['leverage']:4d} "
              f"{b['margin']:8.2f}  {hist}  {status}"
              + (f"  {DIM}last: {last}{OFF}" if last else ""))

    held, total = len(r["holdable"]), len(r["books"])
    print()
    if held == total:
        print(f"{GREEN}all {total} books can hold a position simultaneously{OFF}")
    elif held == 0:
        print(f"{RED}no book can open at ${r['equity']:.2f}{OFF} — "
              f"fund to ${r['floor']:.2f} for all {total}")
    else:
        print(f"{AMBER}{held} of {total} books can be open at once{OFF} — "
              f"the rest wait for one to close. ${r['floor']:.2f} funds all of them.")
    print(f"\n{DIM}Concurrency is first-come: whichever book fires first takes the "
          f"margin.\nThat is not a ranking by edge — at partial funding the account "
          f"trades\nwhatever signals soonest, not whatever signals best.{OFF}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
