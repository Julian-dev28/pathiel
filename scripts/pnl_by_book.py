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

ATTRIBUTION RULE (exact-first, fuzzy fallback)
----------------------------------------------
EXACT sources, checked first (audit 2026-07-09: the fuzzy candidates-join
misattributed ALL vol-book/neg_funding_fade PnL to main-engine, and the
"main engine bleeds -$58" number that drove the b7881e9 sizing decision was
actually vol_breakout losses — true main-engine was +$2.51):
  1. session-log `book_open` events {book, coin, side, ts} — written by every
     book at the moment the executor confirms the open (since 2026-07-09).
  2. loop-log "LIVE opened <side> <coin>" lines (logs/trading_loop.log) — the
     module name identifies the book exactly; covers history before book_open.
LEGACY fuzzy fallback (only when no exact source matches): per-book "intent"
footprints from session events with opened>=1 (candidates list — over-attributes
because candidates include coins that never opened), extreme_fade_candidates,
xs_rebalance, external_alpha_exec.
An episode (coin, side, open_ts) matches a footprint iff same coin, matching
side (when the footprint carries one), and ts within +/- MATCH_WINDOW_MS.
Everything unmatched -> main-engine. We report exact/legacy match counts and
never silently drop a fill.

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
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) \
    if os.path.basename(os.path.dirname(os.path.abspath(__file__))) == "scripts" \
    else "/Users/julian_dev/Documents/code/hermes-trader"
ENV_FILE = os.path.join(REPO, ".env.local")
SESSION_LOG = os.path.expanduser("~/.hermes-trader-session-log.jsonl")
LOOP_LOG = os.path.join(REPO, "logs", "trading_loop.log")

MATCH_WINDOW_MS = 15 * 60 * 1000   # +/-15 min coin+time join tolerance
EPS = 1e-9                          # position-flat epsilon

# Books whose session event carries opened>=1 + a candidates list (legacy fuzzy source).
LEGACY_EVENT_BOOKS = (
    "rally_exhaustion", "engulf_short", "crash_continue_div_short",
    "premium_fade_short", "hail_mary_short", "neg_funding_fade",
    "vol_breakout_long", "vol_breakout_wide",
)
# Priority order when multiple books could match (most specific / live first).
# MUST list every book that can open a position: omitting neg_funding_fade and
# the vol books silently rebadged their PnL as main-engine (audit 2026-07-09).
# DELETED books (vol_breakout_*, premium_fade_short, hail_mary_short, ripped out
# 2026-07-09) stay listed: their historical fills exist and must keep attributing
# to them, not to main-engine.
BOOK_PRIORITY = (
    "rally_exhaustion", "engulf_short", "crash_continue_div_short",
    "premium_fade_short", "hail_mary_short", "neg_funding_fade",
    "vol_breakout_long", "vol_breakout_wide", "majors_swing",
    "funding_spike_short", "young_listings", "unlock_short_runin",
    "news_catalyst", "mover_pass",
    "extreme_fade", "xs_momentum", "xs_xyz_equities",
    "external_alpha",
)

# "2026-06-27 08:07:01,658 INFO:hermes_trader.agents.rally_exhaustion_live:[rally-exhaustion] LIVE opened short XPL ..."
OPEN_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) "
    r"INFO:hermes_trader\.agents\.([A-Za-z0-9_]+):\[[^\]]+\] "
    r"LIVE opened (long|short) (\S+)"
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
# Modules whose stripped name is not the book name (xs_xyz_live -> xs_xyz_equities).
_MODULE_BOOK_ALIASES = {"xs_xyz": "xs_xyz_equities"}


def _log_module_to_book(mod: str) -> str:
    """hermes_trader.agents.<mod> logger name -> book name (strip the _live suffix)."""
    base = mod[:-5] if mod.endswith("_live") else mod
    return _MODULE_BOOK_ALIASES.get(base, base)


def extract_exact_footprints(start_ms: int,
                             loop_log: str = LOOP_LOG,
                             session_log: str = SESSION_LOG) -> Dict[str, List[Tuple[str, Optional[str], int]]]:
    """EXACT per-book open records (coin, side, ts) from two sources:
    session-log `book_open` events, and loop-log 'LIVE opened' lines (whose
    timestamps are this machine's LOCAL time -> epoch ms via mktime)."""
    foot: Dict[str, List[Tuple[str, Optional[str], int]]] = {b: [] for b in BOOK_PRIORITY}
    if os.path.exists(session_log):
        for line in open(session_log):
            if '"book_open"' not in line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("event") != "book_open":
                continue
            ts = e.get("ts", 0)
            book = e.get("book")
            if book in foot and isinstance(ts, (int, float)) and ts >= start_ms:
                foot[book].append((e.get("coin"), e.get("side"), int(ts)))
    if os.path.exists(loop_log):
        for line in open(loop_log, errors="replace"):
            m = OPEN_LINE_RE.match(line)
            if not m:
                continue
            stamp, ms, mod, side, coin = m.groups()
            book = _log_module_to_book(mod)
            if book not in foot:
                continue
            try:
                ts = int(time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S")) * 1000) + int(ms)
            except Exception:
                continue
            if ts >= start_ms:
                foot[book].append((coin, side, ts))
    return foot


def extract_footprints(start_ms: int) -> Dict[str, List[Tuple[str, Optional[str], int]]]:
    """LEGACY fuzzy source: per-book (coin, side, ts) open INTENTS from session
    events. The candidates list includes coins that never opened, so this
    over-attributes — used only when no exact source matched."""
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
        if ev in LEGACY_EVENT_BOOKS:
            if (e.get("opened") or 0) > 0:
                for c in e.get("candidates", []):
                    foot[ev].append((c.get("coin"), c.get("side"), ts))
        elif ev == "extreme_fade_candidates" and not e.get("shadow", True):
            for s in e.get("signals", []):
                foot["extreme_fade"].append((s.get("coin"), s.get("side"), ts))
        elif ev in ("xs_rebalance", "xs_xyz_rebalance") and not e.get("shadow", False):
            # live xs*_rebalance events carry NO shadow key — defaulting the
            # missing key to True routed every live xs fill into main-engine
            # attribution (W-X2 audit 2026-07-20); the book looked invisible
            _xs_book = "xs_momentum" if ev == "xs_rebalance" else "xs_xyz_equities"
            for c in e.get("open_long", []):
                foot[_xs_book].append((c, "long", ts))
            for c in e.get("open_short", []):
                foot[_xs_book].append((c, "short", ts))
        elif ev == "external_alpha_exec" and e.get("executed"):
            foot["external_alpha"].append((e.get("coin"), None, ts))
    return foot


def _match(ep: Episode, foot: Dict[str, List[Tuple[str, Optional[str], int]]]) -> Optional[str]:
    for book in BOOK_PRIORITY:
        for (coin, side, ts) in foot[book]:
            if coin != ep.coin:
                continue
            if side is not None and side != ep.side:
                continue
            if abs(ts - ep.open_ts) <= MATCH_WINDOW_MS:
                return book
    return None


def attribute(ep: Episode, exact: Dict[str, List[Tuple[str, Optional[str], int]]],
              legacy: Dict[str, List[Tuple[str, Optional[str], int]]]) -> Tuple[str, str]:
    """(book, source) for this episode; exact sources win over the fuzzy join."""
    book = _match(ep, exact)
    if book is not None:
        return book, "exact"
    book = _match(ep, legacy)
    if book is not None:
        return book, "legacy"
    return "main-engine", "default"


# ----------------------------------------------------------------------------- aggregation
def aggregate(episodes: List[Episode], exact, legacy) -> Tuple[Dict[str, dict], Dict[str, Dict[str, dict]], Dict[str, int]]:
    books: Dict[str, dict] = {}
    per_coin: Dict[str, Dict[str, dict]] = {}
    sources: Dict[str, int] = {"exact": 0, "legacy": 0, "default": 0}

    def blank() -> dict:
        return dict(n=0, gross=0.0, fees=0.0, net=0.0, wins=0, losses=0,
                    win_sum=0.0, loss_sum=0.0, longs=0, shorts=0,
                    long_net=0.0, short_net=0.0, open_n=0)

    for ep in episodes:
        book, source = attribute(ep, exact, legacy)
        sources[source] += 1
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
    return books, per_coin, sources


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
    start = fills[0]["time"] - MATCH_WINDOW_MS
    exact = extract_exact_footprints(start)
    legacy = extract_footprints(start)
    books, per_coin, sources = aggregate(episodes, exact, legacy)

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
    print(f"match sources: exact={sources['exact']} legacy-fuzzy={sources['legacy']} "
          f"default-main={sources['default']}"
          + ("   (WARNING: legacy matches over-attribute — candidates != opens)"
             if sources["legacy"] else ""))
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
