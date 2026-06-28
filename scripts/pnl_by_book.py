#!/usr/bin/env python3
"""
pnl_by_book.py — Realized-PnL attribution by strategy BOOK (read-only).

Splits realized PnL across the books that OPENED each position so the operator
can see whether each book earns or bleeds:

    main-engine (AI research longs/shorts)  <- the default / catch-all
    xs_momentum
    rally_exhaustion
    crash_continue_div_short
    engulf_short
    premium_fade_short
    hail_mary_short
    extreme_fade
    external_alpha

DATA SOURCES
------------
1. Hyperliquid fills:  /info userFillsByTime (paginated; 2000-row cap, advance by
   last fill time). Each fill: {coin, dir, px, sz, closedPnl, fee, time, tid, ...}.
   Realized PnL for a closing fill = closedPnl; net = closedPnl - fee.
2. Session log (~/.hermes-trader-session-log.jsonl): per-book "open footprints".
   The live books all route their opens through the SAME executor (execute_fn) as
   the main engine, so an Open fill alone can NOT tell you which book opened it.
   Attribution therefore JOINS each position's open-time against the book's own
   log events near that time.

ATTRIBUTION RULE (fuzzy coin+time join — stated explicitly)
-----------------------------------------------------------
For each book we collect "intent" footprints (coin, side, ts) from its own events:
  - rally_exhaustion / engulf_short / crash_continue_div_short /
    premium_fade_short / hail_mary_short:  events with opened>=1 -> candidate coins.
  - extreme_fade:   extreme_fade_candidates with shadow=false -> signal coins.
  - xs_momentum:    xs_rebalance with shadow=false -> open_long / open_short coins.
  - external_alpha: external_alpha_exec with executed=true -> coin.
A reconstructed position EPISODE (coin, side, open_ts) is attributed to a book iff
that book has a footprint with the SAME coin, matching side (when the footprint
carries a side), and ts within +/- MATCH_WINDOW_MS of the episode open. The first
book to match (checked in a fixed priority order) wins. Everything unmatched ->
main-engine.

Why this is sound here: in the audited window every short book except
rally_exhaustion ran in SHADOW (opened=0) and xs_momentum/external_alpha never
executed, so the only non-main footprints that can ever match are
rally_exhaustion (1 coin) and extreme_fade (candidate list). The window join is
fuzzy by nature; we report the unmatched % and never silently drop a fill.

USAGE
-----
    python pnl_by_book.py --days 14
    python pnl_by_book.py --days 0      # all available (~60d of fills the API keeps)

Read-only. No order placement, no live-code edits.
"""
from __future__ import annotations
import argparse
import json
import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) \
    if os.path.basename(os.path.dirname(os.path.abspath(__file__))) == "scripts" \
    else "/Users/julian_dev/Documents/code/hermes-trader"
ENV_FILE = os.path.join(REPO, ".env.local")
SESSION_LOG = os.path.expanduser("~/.hermes-trader-session-log.jsonl")

MATCH_WINDOW_MS = 15 * 60 * 1000   # +/-15 min coin+time join tolerance
EPS = 1e-9                          # position-flat epsilon

SHORT_BOOKS = (
    "rally_exhaustion", "engulf_short", "crash_continue_div_short",
    "premium_fade_short", "hail_mary_short",
)
# Priority order when multiple books could match (most specific / live first).
BOOK_PRIORITY = (
    "rally_exhaustion", "engulf_short", "crash_continue_div_short",
    "premium_fade_short", "hail_mary_short", "extreme_fade",
    "xs_momentum", "external_alpha",
)


# ----------------------------------------------------------------------------- env + API
def load_env() -> None:
    if not os.path.exists(ENV_FILE):
        return
    for line in open(ENV_FILE):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def fetch_all_fills(since_ms: int) -> List[Dict[str, Any]]:
    """Paginate userFillsByTime forward (2000-row cap) until caught up to now."""
    from hermes_trader.client.hl_client import resolve_user_address, _http_post
    addr = resolve_user_address()
    out: List[Dict[str, Any]] = []
    seen = set()
    cur = since_ms
    while True:
        batch = _http_post("/info", {"type": "userFillsByTime", "user": addr, "startTime": cur})
        if not batch:
            break
        fresh = [f for f in batch if f["tid"] not in seen]
        for f in fresh:
            seen.add(f["tid"])
        out += fresh
        if len(batch) < 2000:
            break
        nxt = batch[-1]["time"]
        if nxt <= cur:
            break
        cur = nxt
    out.sort(key=lambda f: f["time"])
    return out


# ----------------------------------------------------------------------------- episode rebuild
class Episode:
    __slots__ = ("coin", "side", "open_ts", "close_ts", "closed_pnl", "fee",
                 "n_fills", "open_done")

    def __init__(self, coin: str, side: str, open_ts: int):
        self.coin = coin
        self.side = side          # 'long' / 'short' (from first open fill)
        self.open_ts = open_ts
        self.close_ts = open_ts
        self.closed_pnl = 0.0
        self.fee = 0.0
        self.n_fills = 0
        self.open_done = False    # position returned to flat


def build_episodes(fills: List[Dict[str, Any]]) -> List[Episode]:
    """Walk fills per coin, slicing into flat->flat episodes.

    Signed size: B (buy) adds, A (sell) subtracts. An episode opens when size
    leaves 0 and closes when it returns to ~0. closedPnl/fee accumulate over the
    whole episode. Side = sign at first non-flat size.
    """
    by_coin: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in fills:
        by_coin[f["coin"]].append(f)

    episodes: List[Episode] = []
    for coin, cf in by_coin.items():
        cf.sort(key=lambda f: (f["time"], f["tid"]))
        size = 0.0
        ep: Optional[Episode] = None
        for f in cf:
            sz = float(f["sz"]) * (1 if f["side"] == "B" else -1)
            prev = size
            size += sz
            if ep is None and abs(prev) < EPS and abs(size) > EPS:
                ep = Episode(coin, "long" if size > 0 else "short", f["time"])
            if ep is not None:
                ep.closed_pnl += float(f["closedPnl"])
                ep.fee += float(f["fee"])
                ep.n_fills += 1
                ep.close_ts = f["time"]
                if abs(size) < EPS:       # back to flat -> episode done
                    ep.open_done = True
                    episodes.append(ep)
                    ep = None
        if ep is not None:                # still open at window end
            episodes.append(ep)
    episodes.sort(key=lambda e: e.open_ts)
    return episodes


# ----------------------------------------------------------------------------- book footprints
def extract_footprints(start_ms: int) -> Dict[str, List[Tuple[str, Optional[str], int]]]:
    """Stream the session log -> per-book list of (coin, side, ts) open intents."""
    foot: Dict[str, List[Tuple[str, Optional[str], int]]] = {b: [] for b in BOOK_PRIORITY}
    if not os.path.exists(SESSION_LOG):
        return foot
    for line in open(SESSION_LOG):
        if '"event"' not in line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        ev = e.get("event")
        ts = e.get("ts", 0)
        if not isinstance(ts, (int, float)) or ts < start_ms:
            continue
        ts = int(ts)
        if ev in SHORT_BOOKS:
            if (e.get("opened") or 0) > 0:
                for c in e.get("candidates", []):
                    foot[ev].append((c.get("coin"), c.get("side"), ts))
        elif ev == "extreme_fade_candidates" and not e.get("shadow", True):
            for s in e.get("signals", []):
                foot["extreme_fade"].append((s.get("coin"), s.get("side"), ts))
        elif ev == "xs_rebalance" and not e.get("shadow", True):
            for c in e.get("open_long", []):
                foot["xs_momentum"].append((c, "long", ts))
            for c in e.get("open_short", []):
                foot["xs_momentum"].append((c, "short", ts))
        elif ev == "external_alpha_exec" and e.get("executed"):
            foot["external_alpha"].append((e.get("coin"), None, ts))
    return foot


def attribute(ep: Episode, foot: Dict[str, List[Tuple[str, Optional[str], int]]]) -> str:
    """Return the book that opened this episode, or 'main-engine'."""
    for book in BOOK_PRIORITY:
        for (coin, side, ts) in foot[book]:
            if coin != ep.coin:
                continue
            if side is not None and side != ep.side:
                continue
            if abs(ts - ep.open_ts) <= MATCH_WINDOW_MS:
                return book
    return "main-engine"


# ----------------------------------------------------------------------------- aggregation
def aggregate(episodes: List[Episode], foot) -> Tuple[Dict[str, dict], Dict[str, Dict[str, dict]]]:
    books: Dict[str, dict] = {}
    per_coin: Dict[str, Dict[str, dict]] = {}

    def blank() -> dict:
        return dict(n=0, gross=0.0, fees=0.0, net=0.0, wins=0, losses=0,
                    win_sum=0.0, loss_sum=0.0, longs=0, shorts=0,
                    long_net=0.0, short_net=0.0, open_n=0)

    for ep in episodes:
        book = attribute(ep, foot)
        b = books.setdefault(book, blank())
        net = ep.closed_pnl - ep.fee
        b["n"] += 1
        b["gross"] += ep.closed_pnl
        b["fees"] += ep.fee
        b["net"] += net
        if not ep.open_done:
            b["open_n"] += 1
        if ep.closed_pnl > 0:
            b["wins"] += 1
            b["win_sum"] += ep.closed_pnl
        elif ep.closed_pnl < 0:
            b["losses"] += 1
            b["loss_sum"] += ep.closed_pnl
        if ep.side == "long":
            b["longs"] += 1
            b["long_net"] += net
        else:
            b["shorts"] += 1
            b["short_net"] += net
        pc = per_coin.setdefault(book, {}).setdefault(ep.coin, blank())
        pc["n"] += 1
        pc["gross"] += ep.closed_pnl
        pc["fees"] += ep.fee
        pc["net"] += net
        if ep.side == "long":
            pc["longs"] += 1
        else:
            pc["shorts"] += 1
    return books, per_coin


def fmt_book_table(books: Dict[str, dict]) -> str:
    hdr = ("book", "#", "gross", "fees", "net", "win%", "avgW", "avgL", "L/S net")
    rows = []
    for name in sorted(books, key=lambda k: books[k]["net"]):
        b = books[name]
        decided = b["wins"] + b["losses"]
        winp = 100 * b["wins"] / decided if decided else 0.0
        avgw = b["win_sum"] / b["wins"] if b["wins"] else 0.0
        avgl = b["loss_sum"] / b["losses"] if b["losses"] else 0.0
        ls = f"{b['longs']}L${b['long_net']:+.1f}/{b['shorts']}S${b['short_net']:+.1f}"
        opn = f" ({b['open_n']} open)" if b["open_n"] else ""
        rows.append((name + opn, str(b["n"]), f"{b['gross']:+.2f}", f"{b['fees']:.2f}",
                     f"{b['net']:+.2f}", f"{winp:.0f}", f"{avgw:+.2f}",
                     f"{avgl:+.2f}", ls))
    widths = [max(len(str(r[i])) for r in (rows + [hdr])) for i in range(len(hdr))]
    out = ["  ".join(str(h).ljust(widths[i]) for i, h in enumerate(hdr))]
    out.append("  ".join("-" * widths[i] for i in range(len(hdr))))
    for r in rows:
        out.append("  ".join(str(r[i]).ljust(widths[i]) for i in range(len(hdr))))
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Realized PnL attribution by strategy book")
    ap.add_argument("--days", type=int, default=14,
                    help="lookback window in days (0 = all available fills)")
    args = ap.parse_args()

    load_env()
    now = int(time.time() * 1000)
    if args.days and args.days > 0:
        since = now - args.days * 86400 * 1000
        label = f"last {args.days}d"
    else:
        since = now - 90 * 86400 * 1000   # API only retains ~60d anyway
        label = "all-available"

    fills = fetch_all_fills(since)
    if not fills:
        print("no fills returned")
        return
    span0 = time.strftime("%Y-%m-%d %H:%M", time.localtime(fills[0]["time"] / 1000))
    span1 = time.strftime("%Y-%m-%d %H:%M", time.localtime(fills[-1]["time"] / 1000))

    episodes = build_episodes(fills)
    foot = extract_footprints(fills[0]["time"] - MATCH_WINDOW_MS)
    books, per_coin = aggregate(episodes, foot)

    tot_net = sum(b["net"] for b in books.values())
    tot_gross = sum(b["gross"] for b in books.values())
    tot_fees = sum(b["fees"] for b in books.values())
    n_eps = sum(b["n"] for b in books.values())
    non_main = sum(b["n"] for k, b in books.items() if k != "main-engine")

    print(f"# PnL by book — {label}")
    print(f"fills: {len(fills)}  span: {span0} -> {span1}")
    print(f"episodes: {n_eps}  attributed-to-a-book: {non_main} "
          f"({100*non_main/n_eps:.1f}%)  -> main-engine: {n_eps-non_main} "
          f"({100*(n_eps-non_main)/n_eps:.1f}%)")
    print(f"TOTAL  gross {tot_gross:+.2f}  fees {tot_fees:.2f}  net {tot_net:+.2f}\n")
    print(fmt_book_table(books))

    print("\n## Per-coin within each book")
    for book in sorted(per_coin, key=lambda k: books[k]["net"]):
        print(f"\n### {book}  (net {books[book]['net']:+.2f})")
        coins = per_coin[book]
        for coin in sorted(coins, key=lambda c: coins[c]["net"]):
            c = coins[coin]
            print(f"  {coin:<14} n={c['n']:<3} net {c['net']:+8.2f} "
                  f"(gross {c['gross']:+.2f} fee {c['fees']:.2f}) "
                  f"{c['longs']}L/{c['shorts']}S")


if __name__ == "__main__":
    main()
