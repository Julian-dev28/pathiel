"""Loss audit, 2026-11-02: what actually made the desks unprofitable.

Operator, 2026-11-02: "can u backtest the solutions to what made us
unprofitable, fix the issues and show me the results". This module is the
first half: every closed xyz:BRENTOIL and xyz:SILVER trade on the pathiel
wallet since 2026-10-20, rebuilt from the venue's own fills, each with a
cause tag computed by rule (no judgement, no LLM):

  stop_too_tight   exited on its stop order and price traded back to the
                   entry within `BACK_HOURS` (12h) after the stop
  wrong_direction  a loss, and the desk's thesis flipped to the other side
                   within `FLIP_HOURS` (12h) after the exit
  round_trip       the best price reached at least 1R in favour, then the
                   trade closed under 0.5R
  size             a loss of more than `SIZE_FRAC` (10%) of account equity

A trade can carry several tags. `cause_table` sums net P&L per tag
(overlapping) and per primary cause (the first tag in `PRIORITY`, so the
rows add up to the total), and ranks by dollars.

The second half (`loss_fix_test.py`) backtests a fix per cause.

The CLI entry point lives in `loss_audit_cli.py`, not here (2026-11-13):
that split is the only reason `services/pool/grade.py` can import this
module's pure functions without dragging the backtest/eval chain into the
plugin bundle. `scripts/build_plugin_bundle.py` walks every import reachable
from the MCP server, lazy ones inside functions included; the CLI's own
`from services.strategies.evals.stop_cap_test import same_market` pulled in
`stop_cap_test`, `desk_tick_backtest`, `live_params`, `profit_lock_test`,
`market_scout`, `strategy_sweep`, `scenario_sweep` and `cross_market_backtest`
the moment grade.py reached this file -- none of those are needed to grade a
trade, all of them are "paid or slow eval lanes" the bundle explicitly keeps
out (DATA_EXCLUDE's `*/evals/*`).

    python -m services.strategies.evals.loss_audit_cli \\
        [--oil-theses-dir <pathia>/.state/strategies/oil/theses] \\
        [--metals-theses-dir <pathia>/.state/strategies/metals/theses]

Network: the public Hyperliquid /info endpoint only (userFillsByTime,
historicalOrders, portfolio, userNonFundingLedgerUpdates, userFunding,
candleSnapshot), read only, cached under --raw-dir. The theses dirs are read
only. Writes /tmp/loss-audit/trades.csv, causes.csv and audit.json.
"""
from __future__ import annotations

import json
import os
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

USER = "0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985"
INFO_URL = "https://api.hyperliquid.xyz/info"
# xyz:GOLD since 2026-11-05: the metals desk's primary (GOLD_DESK.md); its
# silver fills stay in the record.
COINS = ("xyz:BRENTOIL", "xyz:SILVER", "xyz:GOLD")
DESK_OF = {"xyz:BRENTOIL": "oil", "xyz:SILVER": "metals", "xyz:GOLD": "metals"}
SINCE_MS = int(datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp() * 1000)
HOUR_MS = 3_600_000
FIVE_MS = 300_000

BACK_HOURS = 12.0       # stop_too_tight: price back to the entry within this long after the stop
FLIP_HOURS = 12.0       # wrong_direction: the thesis flipped within this long after the exit
ROUND_TRIP_MFE_R = 1.0  # round_trip: best price at least this many R in favour ...
ROUND_TRIP_EXIT_R = 0.5  # ... then closed under this many R
SIZE_FRAC = 0.10        # size: a loss of more than this share of account equity
TAGS = ("wrong_direction", "stop_too_tight", "round_trip", "size")
PRIORITY = TAGS         # the primary cause is the first tag in this order


def _iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


# ── trades from fills (pure) ───────────────────────────────────────────────

def _signed(f: Dict[str, Any]) -> float:
    return float(f["sz"]) if f["side"] == "B" else -float(f["sz"])


def chain_fills(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One coin's fills in position order. Fills of one order share a
    timestamp and their trade ids are not in fill order (the 2026-10-31
    Brent long's three fills came back 36.68, 31.1, 0.0 by tid), so within
    a timestamp each next fill is the one whose startPosition is where the
    last one left the position. Pure."""
    ordered = sorted(rows, key=lambda f: int(f["time"]))
    out: List[Dict[str, Any]] = []
    pos: Optional[float] = None
    i = 0
    while i < len(ordered):
        j = i
        while j < len(ordered) and ordered[j]["time"] == ordered[i]["time"]:
            j += 1
        group = list(ordered[i:j])
        if pos is None:
            ends = {round(float(g["startPosition"]) + _signed(g), 9) for g in group}
            heads = [g for g in group if round(float(g["startPosition"]), 9) not in ends]
            pos = float((heads or group)[0]["startPosition"])
        while group:
            k = min(range(len(group)), key=lambda n: abs(float(group[n]["startPosition"]) - pos))
            f = group.pop(k)
            out.append(f)
            pos = float(f["startPosition"]) + _signed(f)
        i = j
    return out


def reconstruct_trades(fills: Iterable[Dict[str, Any]], coins: Sequence[str] = COINS,
                       since_ms: int = SINCE_MS) -> List[Dict[str, Any]]:
    """Round trips flat -> position -> flat per coin, from raw userFills rows,
    oldest first. Top-ups fold into the open trade. A trade still open at the
    last fill is returned with `exit_time` None. Only trades whose first fill
    is at or after `since_ms` are kept. Net = closedPnl - fees (the venue's
    own realized P&L; funding is kept apart, `attach_funding`). Pure."""
    rows = []
    for coin in coins:
        rows.extend(chain_fills([f for f in fills if f.get("coin") == coin]))
    rows.sort(key=lambda f: int(f["time"]))       # stable: each coin's chain order holds
    open_by_coin: Dict[str, Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for f in rows:
        coin, sz, px = f["coin"], float(f["sz"]), float(f["px"])
        signed = sz if f["side"] == "B" else -sz
        start = float(f["startPosition"])
        after = start + signed
        tr = open_by_coin.get(coin)
        if tr is None:
            if abs(start) > 1e-9:
                continue            # a position opened before the fills we hold: skip its tail
            tr = {"coin": coin, "desk": DESK_OF.get(coin, coin), "side": "long" if signed > 0 else "short",
                  "entry_time": int(f["time"]), "first_oid": f.get("oid"), "open_sz": 0.0, "open_ntl": 0.0,
                  "first_sz": 0.0, "first_ntl": 0.0, "close_sz": 0.0, "close_ntl": 0.0, "closed_pnl": 0.0,
                  "fees": 0.0, "max_size": 0.0, "adds": 0, "close_oids": [], "exit_time": None,
                  "last_open_time": int(f["time"])}
            open_by_coin[coin] = tr
        opening = abs(after) > abs(start) + 1e-12
        if opening:
            tr["last_open_time"] = int(f["time"])
            tr["open_sz"] += sz
            tr["open_ntl"] += sz * px
            if f.get("oid") == tr["first_oid"]:
                tr["first_sz"] += sz
                tr["first_ntl"] += sz * px
            elif int(f["time"]) > tr["entry_time"]:
                tr["adds"] += 1
        else:
            tr["close_sz"] += sz
            tr["close_ntl"] += sz * px
            if f.get("oid") not in tr["close_oids"]:
                tr["close_oids"].append(f.get("oid"))
        tr["closed_pnl"] += float(f.get("closedPnl") or 0.0)
        tr["fees"] += float(f.get("fee") or 0.0)
        tr["max_size"] = max(tr["max_size"], abs(after))
        if abs(after) < 1e-9:
            tr["exit_time"] = int(f["time"])
            out.append(open_by_coin.pop(coin))
    out.extend(open_by_coin.values())
    done = []
    for tr in sorted(out, key=lambda x: x["entry_time"]):
        if tr["entry_time"] < since_ms:
            continue
        tr["entry_px"] = tr["first_ntl"] / tr["first_sz"] if tr["first_sz"] else tr["open_ntl"] / tr["open_sz"]
        tr["avg_entry_px"] = tr["open_ntl"] / tr["open_sz"] if tr["open_sz"] else None
        tr["exit_px"] = tr["close_ntl"] / tr["close_sz"] if tr["close_sz"] else None
        tr["net_usd"] = tr["closed_pnl"] - tr["fees"]
        tr["notional_usd"] = tr["open_ntl"]
        done.append(tr)
    return done


def attach_funding(trades: List[Dict[str, Any]], funding_rows: Iterable[Dict[str, Any]]) -> None:
    """Sum the userFunding payments on each trade's coin while it was open
    (`funding_usd`, + = received). Mutates `trades`."""
    rows = [(int(r["time"]), r["delta"]["coin"], float(r["delta"]["usdc"])) for r in funding_rows
            if (r.get("delta") or {}).get("type") == "funding"]
    for tr in trades:
        end = tr["exit_time"] if tr["exit_time"] is not None else 1 << 62
        tr["funding_usd"] = round(sum(u for t, c, u in rows if c == tr["coin"] and tr["entry_time"] < t <= end), 6)


# ── orders: exit kind, initial stop and target (pure) ─────────────────────

def _orders(orders: Iterable[Dict[str, Any]], coin: str) -> List[Dict[str, Any]]:
    out = []
    for o in orders:
        oo = o.get("order") or {}
        if oo.get("coin") != coin:
            continue
        out.append({"oid": oo.get("oid"), "t": int(oo.get("timestamp") or 0), "side": oo.get("side"),
                    "type": oo.get("orderType") or "", "trigger": float(oo.get("triggerPx") or 0.0),
                    "limit": float(oo.get("limitPx") or 0.0), "status": o.get("status"),
                    "status_t": int(o.get("statusTimestamp") or 0)})
    return out


def exit_kind(trade: Dict[str, Any], orders: Iterable[Dict[str, Any]]) -> str:
    """How the trade closed, from its closing order's type: "stop" (Stop
    Market), "target" (Take Profit), "manual" (any other order, e.g. a
    reduce-only limit the operator sent), "open" (still open)."""
    if trade["exit_time"] is None:
        return "open"
    types = {o["oid"]: o["type"] for o in _orders(orders, trade["coin"]) if o["type"]}
    kinds = []
    for oid in trade["close_oids"]:
        t = types.get(oid, "")
        kinds.append("stop" if t.startswith("Stop") else "target" if t.startswith("Take Profit") else "manual")
    for k in ("stop", "target"):
        if k in kinds:
            return k
    return "manual"


def initial_levels(trade: Dict[str, Any], orders: Iterable[Dict[str, Any]],
                   lookback_ms: int = 48 * HOUR_MS, grace_ms: int = 60_000) -> Dict[str, Optional[float]]:
    """The stop and target the trade opened with: the trigger price of the
    latest reduce-side Stop Market (Take Profit) order placed in the
    `lookback_ms` before the first fill, up to `grace_ms` after it (a market
    entry's bracket lands a second later; a resting entry's was placed with
    it, hours before)."""
    close_side = "A" if trade["side"] == "long" else "B"
    t0 = trade["entry_time"]
    best: Dict[str, Tuple[int, Optional[float]]] = {"stop": (-1, None), "target": (-1, None)}
    for o in _orders(orders, trade["coin"]):
        if o["side"] != close_side or o["trigger"] <= 0 or not (t0 - lookback_ms <= o["t"] <= t0 + grace_ms):
            continue
        k = "stop" if o["type"].startswith("Stop") else "target" if o["type"].startswith("Take Profit") else None
        if k and o["t"] >= best[k][0]:
            best[k] = (o["t"], o["trigger"])
    return {"stop_px": best["stop"][1], "target_px": best["target"][1]}


# ── account equity (pure) ──────────────────────────────────────────────────

def equity_samples(portfolio: Sequence[Any]) -> List[Tuple[int, float]]:
    """Every account-value sample in the portfolio reply (day, week, month,
    allTime; the perp* series are the same account less spot), deduped and
    sorted."""
    pts: Dict[int, float] = {}
    for name, body in portfolio:
        if name.startswith("perp"):
            continue
        for t, v in (body or {}).get("accountValueHistory") or []:
            pts[int(t)] = float(v)
    return sorted(pts.items())


def external_flows(ledger: Iterable[Dict[str, Any]], user: str = USER) -> List[Tuple[int, float]]:
    """(time, usd) for money in (+) or out (-) of the account from another
    address. Moves between this account's own dexes (spot, main, xyz) are
    not flows."""
    me = user.lower()
    out = []
    for row in ledger:
        d = row.get("delta") or {}
        src, dst = str(d.get("user") or "").lower(), str(d.get("destination") or "").lower()
        if d.get("type") not in ("send", "spotTransfer", "deposit", "withdraw", "accountClassTransfer"):
            continue
        usd = float(d.get("usdcValue") or d.get("usdc") or d.get("amount") or 0.0)
        if d.get("type") == "deposit" or (dst == me and src and src != me):
            out.append((int(row["time"]), usd))
        elif d.get("type") == "withdraw" or (src == me and dst and dst != me):
            out.append((int(row["time"]), -usd - float(d.get("fee") or 0.0)))
    return sorted(out)


def equity_at(t: int, samples: Sequence[Tuple[int, float]], flows: Sequence[Tuple[int, float]]) -> Optional[float]:
    """Account value just before `t`: the last sample at or before it plus
    any outside money that arrived between that sample and `t`."""
    base = None
    for ts, v in samples:
        if ts > t:
            break
        base = (ts, v)
    if base is None:
        return None
    return base[1] + sum(usd for ts, usd in flows if base[0] < ts <= t)


# ── price path (pure) ──────────────────────────────────────────────────────

def excursion(trade: Dict[str, Any], bars: Sequence[Candle], bar_ms: int = FIVE_MS) -> Dict[str, Any]:
    """Best price in favour while open, on whole bars strictly after the
    entry bar and ending at or before the exit (the fill bar's own range
    may come before the fill; a part bar after the exit is not the trade's)."""
    is_long = trade["side"] == "long"
    end = trade["exit_time"] if trade["exit_time"] is not None else 1 << 62
    start = trade["entry_time"] - trade["entry_time"] % bar_ms + bar_ms
    inside = [b for b in bars if b.t >= start and b.t + bar_ms <= end]
    if not inside:
        return {"mfe_px": None, "mae_px": None}
    return {"mfe_px": max(b.h for b in inside) if is_long else min(b.l for b in inside),
            "mae_px": min(b.l for b in inside) if is_long else max(b.h for b in inside)}


def after_exit(trade: Dict[str, Any], bars: Sequence[Candle], hours: float = 24.0,
               bar_ms: int = FIVE_MS) -> Dict[str, Any]:
    """After the exit: hours until price traded back to the entry and to the
    initial target (None if not within `hours`), and how many hours of bars
    there were to look at (the data may end sooner)."""
    if trade["exit_time"] is None:
        return {"back_to_entry_h": None, "back_to_tp_h": None, "hours_seen": 0.0}
    is_long = trade["side"] == "long"
    x = trade["exit_time"]
    start = x - x % bar_ms + bar_ms     # the exit bar's range holds prices from before the exit
    lim = x + hours * HOUR_MS
    seen = [b for b in bars if start <= b.t < lim]
    entry, tp = trade["avg_entry_px"] or trade["entry_px"], trade.get("target_px")

    def first(level: Optional[float]) -> Optional[float]:
        if level is None:
            return None
        for b in seen:
            if (b.h >= level) if is_long else (b.l <= level):
                return round((b.t + bar_ms - x) / HOUR_MS, 2)     # by the end of that bar
        return None
    last_end = (seen[-1].t + bar_ms) if seen else x
    return {"back_to_entry_h": first(entry), "back_to_tp_h": first(tp),
            "hours_seen": round(max(0.0, min(last_end, lim) - x) / HOUR_MS, 2)}


def thesis_flip_after(trade: Dict[str, Any], theses: Sequence[Any], hours: float = FLIP_HOURS) -> Optional[float]:
    """Hours from the exit to the first thesis of the other side written
    within `hours` after it (`theses` carry created_at_ms and side), else None."""
    if trade["exit_time"] is None:
        return None
    other = "short" if trade["side"] == "long" else "long"
    for th in sorted(theses, key=lambda x: x.created_at_ms):
        dt = th.created_at_ms - trade["exit_time"]
        if 0 <= dt <= hours * HOUR_MS and th.side == other:
            return round(dt / HOUR_MS, 2)
    return None


def thesis_at(t: int, theses: Sequence[Any]) -> Optional[Any]:
    """The thesis in force at `t` (the latest written at or before it)."""
    cur = None
    for th in sorted(theses, key=lambda x: x.created_at_ms):
        if th.created_at_ms <= t:
            cur = th
        else:
            break
    return cur


# ── tags and the cause table (pure) ────────────────────────────────────────

def tag_trade(tr: Dict[str, Any]) -> List[str]:
    """The cause tags for one enriched closed trade (see the module doc)."""
    tags = []
    loss = tr["net_usd"] < 0
    if loss and tr.get("flip_after_h") is not None:
        tags.append("wrong_direction")
    if tr.get("exit_kind") == "stop" and tr.get("back_to_entry_h") is not None and tr["back_to_entry_h"] <= BACK_HOURS:
        tags.append("stop_too_tight")
    if tr.get("mfe_r") is not None and tr.get("exit_r") is not None and tr["mfe_r"] >= ROUND_TRIP_MFE_R \
            and tr["exit_r"] < ROUND_TRIP_EXIT_R:
        tags.append("round_trip")
    if loss and tr.get("equity_at_size") and -tr["net_usd"] > SIZE_FRAC * tr["equity_at_size"]:
        tags.append("size")
    return tags


def primary_cause(tags: Sequence[str]) -> str:
    for t in PRIORITY:
        if t in tags:
            return t
    return "none"


def cause_table(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Per tag (overlapping: a trade counts under each of its tags) and per
    primary cause (exclusive: the rows add up to the total): trades, net $,
    $ lost on the losing ones, and the $ the tag's fix could reach
    (`at_stake_usd`: a round trip's gap from its exit up to +0.5R, what a
    stop moved there once up 1R banks; a size loss's part beyond SIZE_FRAC
    of equity; else the loss).
    Ranked by $ lost, then $ at stake."""
    over: Dict[str, Dict[str, float]] = defaultdict(lambda: {"trades": 0, "net_usd": 0.0, "lost_usd": 0.0,
                                                             "at_stake_usd": 0.0})
    prim: Dict[str, Dict[str, float]] = defaultdict(lambda: {"trades": 0, "net_usd": 0.0, "lost_usd": 0.0})
    for tr in trades:
        for tag in tr["tags"]:
            row = over[tag]
            row["trades"] += 1
            row["net_usd"] += tr["net_usd"]
            row["lost_usd"] += min(0.0, tr["net_usd"])
            if tag == "round_trip":
                row["at_stake_usd"] += tr.get("lock_gap_usd") or 0.0
            elif tag == "size":
                row["at_stake_usd"] += -tr["net_usd"] - SIZE_FRAC * tr["equity_at_size"]
            else:
                row["at_stake_usd"] += -min(0.0, tr["net_usd"])
        p = prim[primary_cause(tr["tags"])]
        p["trades"] += 1
        p["net_usd"] += tr["net_usd"]
        p["lost_usd"] += min(0.0, tr["net_usd"])
    rnd = lambda d: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}  # noqa: E731
    order = lambda d: sorted(d.items(), key=lambda kv: (kv[1]["lost_usd"], -kv[1].get("at_stake_usd", 0.0)))  # noqa: E731
    return {"by_tag": [{"cause": k, **rnd(v)} for k, v in order(over)],
            "by_primary": [{"cause": k, **rnd(v)} for k, v in order(prim)],
            "total": {"trades": len(trades), "net_usd": round(sum(t["net_usd"] for t in trades), 2),
                      "lost_usd": round(sum(min(0.0, t["net_usd"]) for t in trades), 2),
                      "won_usd": round(sum(max(0.0, t["net_usd"]) for t in trades), 2)}}


def enrich(trades: List[Dict[str, Any]], orders: Sequence[Dict[str, Any]], samples, flows,
           bars_by_coin: Dict[str, Sequence[Candle]], theses_by_desk: Dict[str, Sequence[Any]]) -> List[Dict[str, Any]]:
    """Add the exit kind, the initial stop and target, R, equity, excursion,
    the after-exit path, the thesis flip and the tags to every trade. Pure
    on its inputs; mutates and returns `trades`."""
    for tr in trades:
        tr["exit_kind"] = exit_kind(tr, orders)
        tr.update(initial_levels(tr, orders))
        sign = 1.0 if tr["side"] == "long" else -1.0
        r_unit = abs(tr["entry_px"] - tr["stop_px"]) if tr.get("stop_px") else None
        tr["r_unit"] = r_unit
        eq0 = equity_at(tr["entry_time"], samples, flows)
        tr["equity_before"] = round(eq0, 2) if eq0 is not None else None
        # the equity when the position reached its full size (its last opening
        # fill): a top-up after a deposit is sized off the bigger account
        eq = equity_at(tr["last_open_time"], samples, flows)
        tr["equity_at_size"] = round(eq, 2) if eq is not None else None
        tr["pct_equity"] = round(100.0 * tr["net_usd"] / eq, 1) if eq else None
        tr["eff_leverage"] = round(tr["notional_usd"] / eq, 1) if eq else None
        tr["stop_loss_pct_equity"] = (round(100.0 * r_unit * tr["max_size"] / eq, 1) if eq and r_unit else None)
        bars = bars_by_coin.get(tr["coin"], [])
        tr.update(excursion(tr, bars))
        tr.update(after_exit(tr, bars))
        avg = tr["avg_entry_px"] or tr["entry_px"]
        tr["mfe_r"] = round(sign * (tr["mfe_px"] - tr["entry_px"]) / r_unit, 2) if r_unit and tr["mfe_px"] else None
        tr["exit_r"] = (round(sign * (tr["exit_px"] - tr["entry_px"]) / r_unit, 2)
                        if r_unit and tr["exit_px"] is not None else None)
        tr["give_back_usd"] = (round(sign * (tr["mfe_px"] - tr["exit_px"]) * tr["max_size"], 2)
                               if tr["mfe_px"] and tr["exit_px"] is not None else None)
        # what a stop moved to +0.5R once up 1R would have banked over the exit
        tr["lock_gap_usd"] = (round(max(0.0, ROUND_TRIP_EXIT_R * r_unit - sign * (tr["exit_px"] - tr["entry_px"]))
                                    * tr["max_size"], 2) if r_unit and tr["exit_px"] is not None else None)
        theses = theses_by_desk.get(tr["desk"], [])
        tr["flip_after_h"] = thesis_flip_after(tr, theses)
        th = thesis_at(tr["entry_time"], theses)
        tr["thesis_age_min"] = round((tr["entry_time"] - th.created_at_ms) / 60_000, 1) if th else None
        tr["thesis_side"] = th.side if th else None
        tr["thesis_entry_px"] = th.entry_px if th else None
        tr["avg_entry_px"] = avg
        tr["tags"] = tag_trade(tr) if tr["exit_time"] is not None else []
        tr["primary"] = primary_cause(tr["tags"]) if tr["exit_time"] is not None else "open"
    return trades


TRADE_FIELDS = ["coin", "side", "entry", "exit", "entry_px", "avg_entry_px", "exit_px", "max_size", "adds",
                "notional_usd", "stop_px", "target_px", "exit_kind", "closed_pnl", "fees", "funding_usd", "net_usd",
                "equity_before", "equity_at_size", "pct_equity", "eff_leverage", "stop_loss_pct_equity", "mfe_px", "mfe_r", "exit_r",
                "give_back_usd", "lock_gap_usd", "back_to_entry_h", "back_to_tp_h", "hours_seen", "flip_after_h", "thesis_side",
                "thesis_entry_px", "thesis_age_min", "tags", "primary"]


def trade_row(tr: Dict[str, Any]) -> Dict[str, Any]:
    row = {k: tr.get(k) for k in TRADE_FIELDS}
    row["entry"], row["exit"] = _iso(tr["entry_time"]), _iso(tr["exit_time"])
    row["tags"] = "+".join(tr.get("tags") or [])
    for k in ("entry_px", "avg_entry_px", "exit_px", "closed_pnl", "fees", "net_usd", "notional_usd"):
        if isinstance(row[k], float):
            row[k] = round(row[k], 4)
    return row


# ── network (read only) ────────────────────────────────────────────────────

def info(payload: Dict[str, Any], cache_path: Optional[str] = None, refresh: bool = False) -> Any:
    """POST the public /info endpoint (read only), cached to `cache_path`."""
    if cache_path and os.path.exists(cache_path) and not refresh:
        with open(cache_path) as fh:
            return json.load(fh)
    req = urllib.request.Request(INFO_URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    if cache_path:
        with open(cache_path, "w") as fh:
            json.dump(body, fh)
    return body


def load_inputs(raw_dir: str, refresh: bool = False) -> Dict[str, Any]:
    os.makedirs(raw_dir, exist_ok=True)
    p = lambda n: os.path.join(raw_dir, n)  # noqa: E731
    return {
        "fills": info({"type": "userFillsByTime", "user": USER, "startTime": SINCE_MS - 7 * 24 * HOUR_MS,
                       "aggregateByTime": False}, p("fills.json"), refresh),
        "orders": info({"type": "historicalOrders", "user": USER}, p("orders.json"), refresh),
        "portfolio": info({"type": "portfolio", "user": USER}, p("portfolio.json"), refresh),
        "ledger": info({"type": "userNonFundingLedgerUpdates", "user": USER, "startTime": SINCE_MS - 30 * 24 * HOUR_MS},
                       p("ledger.json"), refresh),
        "funding": info({"type": "userFunding", "user": USER, "startTime": SINCE_MS}, p("funding.json"), refresh),
    }


def run_audit(inputs: Dict[str, Any], bars_by_coin: Dict[str, Sequence[Candle]],
              theses_by_desk: Dict[str, Sequence[Any]], since_ms: int = SINCE_MS) -> Dict[str, Any]:
    """The whole audit on already-loaded inputs. Pure."""
    trades = reconstruct_trades(inputs["fills"], COINS, since_ms)
    attach_funding(trades, inputs.get("funding") or [])
    enrich(trades, inputs["orders"], equity_samples(inputs["portfolio"]), external_flows(inputs.get("ledger") or []),
           bars_by_coin, theses_by_desk)
    closed = [t for t in trades if t["exit_time"] is not None]
    windows = {"since_10_20": closed,
               "since_10_31": [t for t in closed if t["entry_time"] >= int(
                   datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp() * 1000)]}
    return {"trades": trades, "open": [t for t in trades if t["exit_time"] is None],
            "causes": {k: cause_table(v) for k, v in windows.items()}}


# The CLI entry point (`main`, `_print`, the `python -m ...` runner) lives in
# loss_audit_cli.py, on purpose: see the module docstring above. Nothing
# below this line imports services.strategies.backtest or
# services.strategies.evals.stop_cap_test, and nothing here should start.
