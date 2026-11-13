"""Trade attribution, 2026-11-13: every closed round trip of the pathiel
wallet and the operator's watch-only wallet since 2026-10-15, tagged with
what was true AT ENTRY, then sliced fluke-or-edge with the review's own
statistics.

Operator: "lets use the data to create a strat from these trades ... what
made the wins successful and the losses unsuccessful?"

    python3 -m services.strategies.evals.trade_attribution \\
        --oil-theses-dir $PATHIEL_STATE_DIR/strategies/oil/theses \\
        --work-dir <dir> --out-dir <dir>
    python3 -m services.strategies.evals.trade_attribution --offline <bundle>   # no network (tests)

Inputs, all public and read only: userFillsByTime, historicalOrders,
portfolio, userNonFundingLedgerUpdates and userFunding for each wallet
(cached under --work-dir; --refresh refetches), 1h candles and the hourly
funding history per coin through the backtest cache, the oil journal's
theses (read only), the scheduled-event calendar and the EIA report
clock. No key, no order, no live config, no LLM. Never writes under the
state dir.

Pairing: `loss_audit.reconstruct_trades` (flat -> position -> flat per
coin; a top-up folds into the open trade), `exit_kind`, `initial_levels`
and `enrich` exactly as the review's ledger uses them, so the pathiel
wallet's rows match `docs/strategy/learning/REVIEW_*.md` trade for trade.

Per trade, from CLOSED 1h bars before the entry (never hindsight):

  wallet, desk, coin, side; the first fill's order type (market / limit)
  and where the mid sat in the 24h range (`range_pos`: 0 at the side's
  own edge, under 0 = bought through the 24h low / sold through the
  high); the 24h and 5-day (120h) close-to-close returns; ATR14 on 1h
  and the opening stop's distance in ATR; planned R and achieved R;
  UTC hour and weekday; the funding rate in force (sign, and whether the
  side was paying it); whether the thesis in force was written inside the
  2h before the entry (`thesis_age_min` <= 120); hours to the nearest
  impact-2+ scheduled event or EIA report, and whether the entry sat
  inside a blackout window; hold time; exit type (target / stop /
  weekend / manual).

Slices (`services.review.stats`): n, win rate (Wilson 95%), mean R with a
seeded bootstrap CI, net $, a permutation p against the other trades,
and the label EDGE / LEAK / LEANING+- / FLUKE / NOISE under the same
rules as the daily review (nothing under n = 10 is EDGE or LEAK). The
"known facts" block recomputes the operator's four numbers from the fills
instead of assuming them.

Exit 0 on success, 2 when a wallet has no fills in the window.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.review import inputs as RI
from services.review import ledger as LG
from services.review import stats as S
from services.strategies.backtest import data as D
from services.strategies.evals import loss_audit as LA
from services.strategies.weekend import weekend_closed

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
SINCE_MS = int(datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp() * 1000)
WALLETS: Dict[str, str] = {
    "pathiel": "0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985",     # the desk's master wallet
    "watch": "0x8debbedbdc603769549a6277fed38dae65185019",       # the operator's own, watch only
}


def info_url() -> str:
    """The venue's public /info endpoint for the configured network
    (contracts.runtime; mainnet unless PATHIEL_NETWORK=testnet)."""
    from contracts import runtime
    return runtime.info_url()


BARS_1H = 1800                 # 75 days: 5-day returns need 121 closed bars before the first entry
ATR_N = 14
RET_24H_BARS = 24
RET_5D_BARS = 120
REWRITE_H = 2.0                # "a thesis rewrite within 2h before"
EVENT_MIN_IMPACT = 2
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# The operator's four known facts (the brief of 2026-11-13), recomputed
# from the fills below, never assumed.
KNOWN_FACTS = {
    "october_oil_longs": {"n": 7, "wins": 5, "net_usd": 69.72},
    "november_through_low_longs_11_02_03": {"n": 3, "net_usd": -74.95},
    "silver_shorts": {"n": 5, "wins": 0},
    "last_trade": {"entry_px": 89.97, "exit_px": 92.52, "net_usd": 30.20},
}


def _iso(ms: Optional[int]) -> Optional[str]:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


# ── pure features ──────────────────────────────────────────────────────────

def closed_before(bars1h: Sequence[Candle], t_ms: int) -> List[Candle]:
    """The 1h bars CLOSED by `t_ms` (open + 1h <= t), oldest first."""
    return sorted((b for b in bars1h if b.t + HOUR_MS <= t_ms), key=lambda b: b.t)


def atr14(closed: Sequence[Candle], n: int = ATR_N) -> Optional[float]:
    """Simple-average ATR over the last `n` closed bars (the levels script's
    `levels.atr` and the sim's `_levels` use the same mean of true ranges).
    None under n + 1 bars."""
    if len(closed) < n + 1:
        return None
    w = closed[-(n + 1):]
    trs = [max(w[i].h - w[i].l, abs(w[i].h - w[i - 1].c), abs(w[i].l - w[i - 1].c)) for i in range(1, len(w))]
    return sum(trs) / n


def ret_pct(closed: Sequence[Candle], bars_back: int) -> Optional[float]:
    """The last close against the close `bars_back` bars earlier, in %."""
    if len(closed) <= bars_back:
        return None
    base = closed[-1 - bars_back].c
    return round((closed[-1].c / base - 1.0) * 100.0, 3) if base else None


def order_type(first_oid: Any, orders: Iterable[Dict[str, Any]], coin: str) -> Optional[str]:
    """The venue's orderType and time-in-force of the order that opened the
    trade, as "Limit/Ioc", "Limit/Gtc", "Market/..."."""
    for o in orders:
        oo = o.get("order") or {}
        if oo.get("coin") == coin and oo.get("oid") == first_oid:
            return f"{oo.get('orderType') or '?'}/{oo.get('tif') or '?'}"
    return None


def entry_type(otype: Optional[str]) -> str:
    """"market": a Market order or an immediate-or-cancel limit (what
    place_bracket sends for "take the mid now"); "resting": a Gtc/Alo limit
    that waited at a price; "unknown" when the order is not in the history."""
    if not otype:
        return "unknown"
    kind, _, tif = otype.partition("/")
    if kind.lower().startswith("market") or tif.lower() == "ioc":
        return "market"
    return "resting"


def funding_at(entry_ms: int, rows: Sequence[Dict[str, Any]]) -> Optional[float]:
    """The hourly funding rate in force at `entry_ms`: the latest row at or
    before it. None with no row before the entry."""
    best: Optional[Tuple[int, float]] = None
    for r in rows:
        t = int(r.get("time") or 0)
        if t <= entry_ms and (best is None or t > best[0]):
            try:
                best = (t, float(r.get("fundingRate")))
            except (TypeError, ValueError):
                continue
    return None if best is None else best[1]


def funding_side(rate: Optional[float], side: str) -> Optional[str]:
    """"pays" when the side pays the funding in force (a long with a
    positive rate, a short with a negative one), "receives" the other way,
    "zero" at exactly 0."""
    if rate is None:
        return None
    if rate == 0:
        return "zero"
    long_pays = rate > 0
    return "pays" if (side == "long") == long_pays else "receives"


def event_proximity(coin: str, entry_ms: int, events: Sequence[Dict[str, Any]],
                    eia_times: Sequence[int]) -> Dict[str, Any]:
    """Hours to the nearest impact-2+ scheduled event or EIA report (signed:
    negative = the event was before the entry), its name, and whether the
    entry sat inside an event's blackout window or the EIA 2h window."""
    nearest: Optional[Tuple[float, str]] = None
    inside: Optional[str] = None
    for ev in events:
        if coin not in ev["coins"] or int(ev["impact"]) < EVENT_MIN_IMPACT:
            continue
        dt_h = (ev["at_ms"] - entry_ms) / HOUR_MS
        if nearest is None or abs(dt_h) < abs(nearest[0]):
            nearest = (dt_h, ev["title"])
        w = ev.get("window")
        if w and w["start_ms"] <= entry_ms < w["end_ms"]:
            inside = ev["title"]
    for t in eia_times:
        dt_h = (t - entry_ms) / HOUR_MS
        if nearest is None or abs(dt_h) < abs(nearest[0]):
            nearest = (dt_h, "EIA weekly petroleum report")
        if -2.0 <= -dt_h < 2.0 and inside is None:     # [report - 2h, report + 2h)
            inside = "EIA weekly petroleum report"
    return {"event_h": round(nearest[0], 2) if nearest else None, "event_name": nearest[1] if nearest else None,
            "in_blackout": inside is not None, "blackout_event": inside}


def exit_type(tr: Dict[str, Any], orders: Iterable[Dict[str, Any]]) -> str:
    """target / stop / weekend / manual: the audit's `exit_kind`, with a
    non-stop, non-target close inside the oil weekend window (the flatten
    at Fri 16:00 New York) called "weekend"."""
    kind = LA.exit_kind(tr, orders)
    if kind == "manual" and tr.get("exit_time") is not None and weekend_closed(int(tr["exit_time"])):
        return "weekend"
    return kind


def tag_trade(tr: Dict[str, Any], wallet: str, orders: Sequence[Dict[str, Any]], bars1h: Sequence[Candle],
              funding_rows: Sequence[Dict[str, Any]], events: Sequence[Dict[str, Any]],
              eia_times: Sequence[int]) -> Dict[str, Any]:
    """One attributed record from one enriched, closed loss-audit trade."""
    entry_ms, exit_ms = int(tr["entry_time"]), int(tr["exit_time"])
    closed = closed_before(bars1h, entry_ms)
    a = atr14(closed)
    stop = tr.get("stop_px")
    risk_px = abs(tr["entry_px"] - stop) if stop else None
    risk_usd = risk_px * tr["max_size"] if risk_px else None
    otype = order_type(tr.get("first_oid"), orders, tr["coin"])
    rate = funding_at(entry_ms, funding_rows)
    age = tr.get("thesis_age_min")
    rec: Dict[str, Any] = {
        "wallet": wallet, "desk": tr["desk"], "coin": tr["coin"], "side": tr["side"],
        "entry": _iso(entry_ms), "exit": _iso(exit_ms), "entry_ms": entry_ms, "exit_ms": exit_ms,
        "entry_px": round(tr["entry_px"], 4), "exit_px": round(tr["exit_px"], 4) if tr.get("exit_px") else None,
        "stop_px": stop, "target_px": tr.get("target_px"), "max_size": tr["max_size"], "adds": tr["adds"],
        "closed_pnl": round(tr["closed_pnl"], 4), "fees": round(tr["fees"], 4),
        "funding_usd": tr.get("funding_usd"), "net_usd": round(tr["net_usd"], 4),
        "risk_usd": round(risk_usd, 4) if risk_usd else None,
        "r_mult": round(tr["net_usd"] / risk_usd, 3) if risk_usd else None,
        "planned_r": LG.planned_r(tr["entry_px"], stop, tr.get("target_px")),
        "order_type": otype, "entry_type": entry_type(otype),
        "ret_24h_pct": ret_pct(closed, RET_24H_BARS), "ret_5d_pct": ret_pct(closed, RET_5D_BARS),
        "atr14_1h": round(a, 4) if a else None,
        "stop_atr": round(risk_px / a, 2) if (risk_px and a) else None,
        "hour_utc": int((entry_ms // HOUR_MS) % 24),
        "weekday": WEEKDAYS[datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc).weekday()],
        "funding_rate": rate, "funding_sign": None if rate is None else ("+" if rate > 0 else "-" if rate < 0 else "0"),
        "funding_vs_side": funding_side(rate, tr["side"]),
        "thesis_age_min": age, "rewrite_2h": None if age is None else bool(age <= REWRITE_H * 60),
        "thesis_side": tr.get("thesis_side"),
        "hold_h": round((exit_ms - entry_ms) / HOUR_MS, 2),
        "exit_type": exit_type(tr, orders),
        "mfe_r": tr.get("mfe_r"), "equity_at_size": tr.get("equity_at_size"),
        "risk_pct_equity": tr.get("stop_loss_pct_equity"), "tags": "+".join(tr.get("tags") or []),
    }
    rec.update(LG.range_position(entry_ms, tr["entry_px"], tr["side"], bars1h))
    rec.update(event_proximity(tr["coin"], entry_ms, events, eia_times))
    return rec


# ── slices ─────────────────────────────────────────────────────────────────

def _range(r: Dict[str, Any]) -> Optional[str]:
    p = r.get("range_pos")
    if p is None:
        return None
    if p < 0:
        return "through own edge"
    return "bottom third (at edge)" if p < 1 / 3 else "middle third" if p < 2 / 3 else "top third" if p <= 1 else "past far edge"


def _hour(r: Dict[str, Any]) -> Optional[str]:
    h = r.get("hour_utc")
    if h is None:
        return None
    lo = (int(h) // 6) * 6
    return f"{lo:02d}-{lo + 6:02d}Z"


def _stop_atr(r: Dict[str, Any]) -> Optional[str]:
    x = r.get("stop_atr")
    if x is None:
        return None
    return "<1.5 ATR" if x < 1.5 else "1.5-2.5 ATR" if x < 2.5 else "2.5+ ATR"


def _ret5d(r: Dict[str, Any]) -> Optional[str]:
    x = r.get("ret_5d_pct")
    if x is None:
        return None
    return "5d down >2%" if x < -2 else "5d flat" if x <= 2 else "5d up >2%"


def _ret24(r: Dict[str, Any]) -> Optional[str]:
    x = r.get("ret_24h_pct")
    if x is None:
        return None
    return "24h down >1%" if x < -1 else "24h flat" if x <= 1 else "24h up >1%"


def _planned(r: Dict[str, Any]) -> Optional[str]:
    x = r.get("planned_r")
    if x is None:
        return None
    return "<2R" if x < 2 else "2-3R" if x < 3 else "3-5R" if x < 5 else "5R+"


def _event(r: Dict[str, Any]) -> Optional[str]:
    if r.get("in_blackout"):
        return "inside blackout"
    h = r.get("event_h")
    if h is None:
        return None
    return "event <12h away" if abs(h) < 12 else "event 12-48h away" if abs(h) < 48 else "no event in 48h"


def _hold(r: Dict[str, Any]) -> Optional[str]:
    h = r.get("hold_h")
    if h is None:
        return None
    return "held <2h" if h < 2 else "held 2-12h" if h < 12 else "held 12h+"


def _rewrite(r: Dict[str, Any]) -> Optional[str]:
    v = r.get("rewrite_2h")
    return None if v is None else ("rewrite <2h before" if v else "thesis older than 2h")


def _month(r: Dict[str, Any]) -> Optional[str]:
    return (r.get("entry") or "")[:7] or None


DIMENSIONS: List[Tuple[str, Callable[[Dict[str, Any]], Optional[str]]]] = [
    ("all", lambda r: "all"),
    ("wallet", lambda r: r.get("wallet")),
    ("desk", lambda r: r.get("desk")),
    ("side", lambda r: r.get("side")),
    ("month", _month),
    ("entry", lambda r: r.get("entry_type")),
    ("range", _range),
    ("ret_5d", _ret5d),
    ("ret_24h", _ret24),
    ("stop_atr", _stop_atr),
    ("planned_r", _planned),
    ("hour", _hour),
    ("weekday", lambda r: r.get("weekday")),
    ("funding", lambda r: r.get("funding_vs_side")),
    ("rewrite", _rewrite),
    ("event", _event),
    ("hold", _hold),
    ("exit", lambda r: r.get("exit_type")),
]
DIM_FUNCS = dict(DIMENSIONS)


def bucket(r: Dict[str, Any], dim: str) -> Optional[str]:
    return DIM_FUNCS[dim](r)


def slice_row(records: Sequence[Dict[str, Any]], dim: str, value: str, seed: int = S.SEED) -> Dict[str, Any]:
    """The review's `analysis.slice_row` on these dimensions: same stats,
    same seeding, same labels."""
    key = "all" if dim == "all" else f"{dim}={value}"
    inside = [r for r in records if bucket(r, dim) == value]
    rest = [] if dim == "all" else [r for r in records if bucket(r, dim) != value]
    rs = [float(r["r_mult"]) for r in inside if r.get("r_mult") is not None]
    rest_rs = [float(r["r_mult"]) for r in rest if r.get("r_mult") is not None]
    nets = [float(r["net_usd"]) for r in inside]
    wins = sum(1 for x in nets if x > 0)
    sd = S.sub_seed(seed, key)
    ci = S.bootstrap_ci(rs, S.mean, S.N_BOOT, sd)
    p = S.permutation_p(rs, rest_rs, S.N_PERM, sd + 2) if rs else None
    wlo, whi = S.wilson_ci(wins, len(nets))
    return {"dim": dim, "value": value, "key": key, "n": len(inside), "wins": wins,
            "win_rate": wins / len(nets) if nets else None, "win_lo": wlo, "win_hi": whi,
            "mean_r": S.mean(rs) if rs else None, "r_lo": ci[0], "r_hi": ci[1],
            "net_usd": round(sum(nets), 2), "p": p, "label": S.label_slice(rs, ci, p),
            "one_trade": S.one_trade_flips(rs) if rs else None}


def slice_table(records: Sequence[Dict[str, Any]], seed: int = S.SEED) -> List[Dict[str, Any]]:
    out = []
    for dim, _ in DIMENSIONS:
        counts: Dict[str, int] = {}
        for r in records:
            v = bucket(r, dim)
            if v is not None:
                counts[v] = counts.get(v, 0) + 1
        if dim != "all" and len(counts) == 1 and next(iter(counts.values())) == len(records):
            continue
        for v in sorted(counts, key=lambda x: (-counts[x], x)):
            out.append(slice_row(records, dim, v, seed))
    return out


def winners_vs_losers(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per dimension bucket: how many winners vs losers sit in it, with the
    bucket's net $. The plain answer to "what do the winners share"."""
    out = []
    for dim, _ in DIMENSIONS:
        if dim == "all":
            continue
        counts: Dict[str, Dict[str, float]] = {}
        for r in records:
            v = bucket(r, dim)
            if v is None:
                continue
            row = counts.setdefault(v, {"wins": 0, "losses": 0, "net_usd": 0.0})
            row["wins" if r["net_usd"] > 0 else "losses"] += 1
            row["net_usd"] += r["net_usd"]
        for v, row in sorted(counts.items(), key=lambda kv: -(kv[1]["wins"] + kv[1]["losses"])):
            n = row["wins"] + row["losses"]
            out.append({"dim": dim, "value": v, "n": int(n), "wins": int(row["wins"]), "losses": int(row["losses"]),
                        "win_rate": round(row["wins"] / n, 3), "net_usd": round(row["net_usd"], 2)})
    return out


# ── the operator's facts, recomputed ───────────────────────────────────────

def shorts_check(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Did shorts make money, per wallet and in all? Counts and net $."""
    out: Dict[str, Any] = {}
    for w in sorted({r["wallet"] for r in records} | {"all"}):
        rows = [r for r in records if r["side"] == "short" and (w == "all" or r["wallet"] == w)]
        out[w] = {"n": len(rows), "wins": sum(1 for r in rows if r["net_usd"] > 0),
                  "net_usd": round(sum(r["net_usd"] for r in rows), 2),
                  "coins": sorted({r["coin"] for r in rows}), "made_money": sum(r["net_usd"] for r in rows) > 0}
    return out


def known_facts(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The four facts of the brief, recomputed from the pathiel wallet's
    fills, each next to the stated number."""
    p = [r for r in records if r["wallet"] == "pathiel"]
    oct_longs = [r for r in p if r["coin"] == "xyz:BRENTOIL" and r["side"] == "long" and r["entry"][:7] == "2026-10"]
    through = [r for r in p if r["coin"] == "xyz:BRENTOIL" and r["side"] == "long"
               and r["entry"][:10] in ("2026-11-02", "2026-11-03") and (r.get("range_pos") or 0) < 0]
    silver_shorts = [r for r in p if r["coin"] == "xyz:SILVER" and r["side"] == "short"]
    last = max(p, key=lambda r: r["entry_ms"]) if p else None

    def fact(name: str, got: Dict[str, Any]) -> Dict[str, Any]:
        want = KNOWN_FACTS[name]
        return {"stated": want, "computed": got,
                "matches": all(abs(float(got[k]) - float(v)) <= (0.5 if k.endswith("usd") else 0.011)
                               for k, v in want.items() if k in got)}
    return {
        "october_oil_longs": fact("october_oil_longs", {
            "n": len(oct_longs), "wins": sum(1 for r in oct_longs if r["net_usd"] > 0),
            "net_usd": round(sum(r["net_usd"] for r in oct_longs), 2)}),
        "november_through_low_longs_11_02_03": fact("november_through_low_longs_11_02_03", {
            "n": len(through), "net_usd": round(sum(r["net_usd"] for r in through), 2),
            "entries": [r["entry"] for r in through]}),
        "silver_shorts": fact("silver_shorts", {
            "n": len(silver_shorts), "wins": sum(1 for r in silver_shorts if r["net_usd"] > 0),
            "net_usd": round(sum(r["net_usd"] for r in silver_shorts), 2)}),
        "last_trade": fact("last_trade", {
            "entry": last["entry"] if last else None, "entry_px": last["entry_px"] if last else None,
            "exit_px": last["exit_px"] if last else None, "net_usd": round(last["net_usd"], 2) if last else None,
            "closed_pnl": round(last["closed_pnl"], 2) if last else None}),
    }


# ── inputs (public reads, cached) ──────────────────────────────────────────

def _post_info(payload: Dict[str, Any]) -> Any:
    import requests
    resp = requests.post(info_url(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _cached(path: str, refresh: bool, fetch: Callable[[], Any]) -> Any:
    if os.path.exists(path) and not refresh:
        with open(path) as fh:
            return json.load(fh)
    body = fetch()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(body, fh)
    return body


def fetch_wallet(user: str, raw_dir: str, refresh: bool = False, since_ms: int = SINCE_MS,
                 post: Callable[[Dict[str, Any]], Any] = _post_info) -> Dict[str, Any]:
    """The five public account reads the loss audit uses, for `user`."""
    p = lambda n: os.path.join(raw_dir, n)  # noqa: E731
    return {
        "fills": _cached(p("fills.json"), refresh, lambda: post(
            {"type": "userFillsByTime", "user": user, "startTime": since_ms - 7 * DAY_MS, "aggregateByTime": False})),
        "orders": _cached(p("orders.json"), refresh, lambda: post({"type": "historicalOrders", "user": user})),
        "portfolio": _cached(p("portfolio.json"), refresh, lambda: post({"type": "portfolio", "user": user})),
        "ledger": _cached(p("ledger.json"), refresh, lambda: post(
            {"type": "userNonFundingLedgerUpdates", "user": user, "startTime": since_ms - 30 * DAY_MS})),
        "funding": _cached(p("funding.json"), refresh, lambda: post(
            {"type": "userFunding", "user": user, "startTime": since_ms})),
    }


def fetch_funding_history(coin: str, start_ms: int, end_ms: int, cache_dir: str) -> List[Dict[str, Any]]:
    """Hourly funding rows for [start_ms, end_ms] in 14-day pages (the venue
    caps one fundingHistory reply at 500 rows)."""
    out: Dict[int, Dict[str, Any]] = {}
    page = 14 * DAY_MS
    cur = start_ms
    while cur < end_ms:
        nxt = min(end_ms, cur + page)
        for r in D.fetch_funding_cached(coin, cur, nxt, cache_dir):
            out[int(r["time"])] = r
        cur = nxt
    return [out[t] for t in sorted(out)]


def coins_traded(fills: Iterable[Dict[str, Any]], since_ms: int = SINCE_MS) -> List[str]:
    """Perp coins with a fill at or after `since_ms` (spot pairs, "@123",
    are not trades the desks make)."""
    return sorted({f["coin"] for f in fills if int(f["time"]) >= since_ms and not str(f["coin"]).startswith("@")})


def load_live(work_dir: str, oil_theses_dir: str, refresh: bool = False, since_ms: int = SINCE_MS,
              wallets: Dict[str, str] = WALLETS) -> Dict[str, Any]:
    """Everything one run reads, fetched now (public reads) and cached under
    `work_dir`."""
    cache = os.path.join(work_dir, "cache")
    os.makedirs(cache, exist_ok=True)
    raw = {w: fetch_wallet(addr, os.path.join(work_dir, "raw", w), refresh, since_ms) for w, addr in wallets.items()}
    coins = sorted({c for r in raw.values() for c in coins_traded(r["fills"], since_ms)})
    now_ms = int(time.time() * 1000)
    bars1h = {c: D.fetch_candles_cached(c, "1h", BARS_1H, cache) for c in coins}
    funding = {c: fetch_funding_history(c, since_ms - 2 * DAY_MS, now_ms, cache) for c in coins}
    theses = D.load_journaled_theses(oil_theses_dir) if oil_theses_dir else []
    from services.intel import scheduled_events as SE
    from services.strategies.backtest.oil_calendar import eia_report_times
    events = SE.market_events(since_ms - 3 * DAY_MS, now_ms + 3 * DAY_MS)
    eia = [t for t, _ in eia_report_times(since_ms - 3 * DAY_MS, now_ms + 3 * DAY_MS)]
    return {"raw": raw, "coins": coins, "bars1h": bars1h, "funding": funding, "theses": theses,
            "events": [{**e, "coins": sorted(e["coins"])} for e in events], "eia_times": eia, "as_of_ms": now_ms,
            "since_ms": since_ms}


def save_bundle(bundle: Dict[str, Any], path: str, coins: Optional[Iterable[str]] = None) -> None:
    """A trimmed, replayable copy: `coins` (default: every coin with a fill)
    only, orders from two days before the window, 1h bars from 10 days
    before it."""
    os.makedirs(os.path.join(path, "candles"), exist_ok=True)
    since = bundle["since_ms"]
    coins = set(bundle["coins"] if coins is None else coins)
    for w, raw in bundle["raw"].items():
        d = os.path.join(path, "raw", w)
        os.makedirs(d, exist_ok=True)
        # the orders the pairing reads: the opening and closing orders of
        # each round trip, and every trigger (stop / take-profit) order in
        # the window, which is where the opening stop and target come from
        used = set()
        for tr in LA.reconstruct_trades(raw["fills"], sorted(coins), since):
            used.add(tr.get("first_oid"))
            used.update(tr.get("close_oids") or [])
        trimmed = {
            "fills": [f for f in raw["fills"] if f.get("coin") in coins],
            "orders": [o for o in raw["orders"] if (o.get("order") or {}).get("coin") in coins
                       and int((o.get("order") or {}).get("timestamp") or 0) >= since - 2 * DAY_MS
                       and ((o.get("order") or {}).get("oid") in used or (o.get("order") or {}).get("isTrigger"))],
            "portfolio": raw["portfolio"],
            "ledger": [r for r in raw["ledger"] if int(r.get("time") or 0) >= since - 30 * DAY_MS],
            "funding": [r for r in raw["funding"] if (r.get("delta") or {}).get("coin") in coins],
        }
        for k, body in trimmed.items():
            with open(os.path.join(d, f"{k}.json"), "w") as fh:
                json.dump(body, fh, separators=(",", ":"))
    for c in sorted(coins):
        rows = [[b.t, b.o, b.h, b.l, b.c] for b in bundle["bars1h"][c] if b.t >= since - 10 * DAY_MS]
        with open(os.path.join(path, "candles", RI._coin_file(c, "1h")), "w") as fh:
            json.dump(rows, fh, separators=(",", ":"))
        with open(os.path.join(path, "candles", RI._coin_file(c, "funding")), "w") as fh:
            json.dump([{"time": r["time"], "fundingRate": r["fundingRate"]} for r in bundle["funding"][c]], fh,
                      separators=(",", ":"))
    with open(os.path.join(path, "meta.json"), "w") as fh:
        json.dump({"as_of_ms": bundle["as_of_ms"], "since_ms": since, "coins": sorted(coins),
                   "events": bundle["events"], "eia_times": bundle["eia_times"],
                   "theses": [{"thesis_id": t.thesis_id, "created_at_ms": t.created_at_ms, "side": t.side,
                               "entry_px": t.entry_px, "stop_px": t.stop_px} for t in bundle["theses"]]}, fh)


class _Thesis:
    """The simulator's ThesisDirection fields the audit reads."""
    def __init__(self, d: Dict[str, Any]) -> None:
        self.thesis_id, self.created_at_ms, self.side = d["thesis_id"], int(d["created_at_ms"]), d["side"]
        self.entry_px, self.stop_px = d.get("entry_px"), d.get("stop_px")


def load_bundle(path: str) -> Dict[str, Any]:
    with open(os.path.join(path, "meta.json")) as fh:
        meta = json.load(fh)
    raw = {}
    for w in sorted(os.listdir(os.path.join(path, "raw"))):
        d = os.path.join(path, "raw", w)
        raw[w] = {}
        for k in ("fills", "orders", "portfolio", "ledger", "funding"):
            with open(os.path.join(d, f"{k}.json")) as fh:
                raw[w][k] = json.load(fh)
    bars1h, funding = {}, {}
    for c in meta["coins"]:
        with open(os.path.join(path, "candles", RI._coin_file(c, "1h"))) as fh:
            bars1h[c] = [Candle(t=int(r[0]), o=r[1], h=r[2], l=r[3], c=r[4], v=0.0) for r in json.load(fh)]
        with open(os.path.join(path, "candles", RI._coin_file(c, "funding"))) as fh:
            funding[c] = json.load(fh)
    return {"raw": raw, "coins": meta["coins"], "bars1h": bars1h, "funding": funding,
            "theses": [_Thesis(t) for t in meta["theses"]], "events": meta["events"], "eia_times": meta["eia_times"],
            "as_of_ms": int(meta["as_of_ms"]), "since_ms": int(meta["since_ms"])}


# ── the run (pure on the bundle) ───────────────────────────────────────────

def build_records(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every closed round trip of every wallet since the window start, as an
    attributed record, oldest first."""
    since = bundle["since_ms"]
    out: List[Dict[str, Any]] = []
    for w, raw in bundle["raw"].items():
        coins = [c for c in coins_traded(raw["fills"], since) if c in bundle["bars1h"]]
        trades = LA.reconstruct_trades(raw["fills"], coins, since)
        for tr in trades:
            tr["desk"] = LA.DESK_OF.get(tr["coin"], "other")
        LA.attach_funding(trades, raw.get("funding") or [])
        theses = {"oil": bundle["theses"]} if w == "pathiel" else {}
        LA.enrich(trades, raw["orders"], LA.equity_samples(raw["portfolio"]),
                  LA.external_flows(raw.get("ledger") or [], bundle_user(bundle, w)), bundle["bars1h"], theses)
        for tr in trades:
            if tr["exit_time"] is None:
                continue
            out.append(tag_trade(tr, w, raw["orders"], bundle["bars1h"].get(tr["coin"], []),
                                 bundle["funding"].get(tr["coin"], []), bundle["events"], bundle["eia_times"]))
    return sorted(out, key=lambda r: r["entry_ms"])


def bundle_user(bundle: Dict[str, Any], wallet: str) -> str:
    return (bundle.get("wallets") or WALLETS).get(wallet, WALLETS.get(wallet, ""))


def run(bundle: Dict[str, Any], seed: int = S.SEED) -> Dict[str, Any]:
    recs = build_records(bundle)
    return {"as_of": _iso(bundle["as_of_ms"]), "since": _iso(bundle["since_ms"]), "records": recs,
            "slices": slice_table(recs, seed), "winners_vs_losers": winners_vs_losers(recs),
            "shorts": shorts_check(recs), "facts": known_facts(recs),
            "totals": {w: {"n": len(rs), "wins": sum(1 for r in rs if r["net_usd"] > 0),
                           "net_usd": round(sum(r["net_usd"] for r in rs), 2)}
                       for w, rs in (("all", recs), *((w, [r for r in recs if r["wallet"] == w])
                                                      for w in sorted({r["wallet"] for r in recs})))}}


# ── output ─────────────────────────────────────────────────────────────────

FIELDS = ["wallet", "desk", "coin", "side", "entry", "exit", "entry_px", "exit_px", "stop_px", "target_px", "max_size",
          "adds", "net_usd", "closed_pnl", "fees", "funding_usd", "risk_usd", "r_mult", "planned_r", "order_type",
          "entry_type", "range_lo", "range_hi", "range_pos", "ret_24h_pct", "ret_5d_pct", "atr14_1h", "stop_atr",
          "hour_utc", "weekday", "funding_rate", "funding_sign", "funding_vs_side", "thesis_age_min", "rewrite_2h",
          "thesis_side", "event_h", "event_name", "in_blackout", "blackout_event", "hold_h", "exit_type", "mfe_r",
          "equity_at_size", "risk_pct_equity", "tags"]


def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def _f(x: Optional[float], nd: int = 2, sign: bool = True) -> str:
    if x is None:
        return "-"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def markdown(res: Dict[str, Any], min_n: int = 2) -> str:
    L: List[str] = []
    t = res["totals"]
    L.append(f"Window: entries from {res['since'][:10]} to {res['as_of']}. "
             + "; ".join(f"{w}: {v['n']} trades, {v['wins']} wins, {_f(v['net_usd'])}$" for w, v in t.items()) + ".")
    L.append("")
    L.append("### Shorts")
    L.append("")
    L.append("| wallet | shorts | wins | net $ | coins | made money? |")
    L.append("|---|---|---|---|---|---|")
    for w, s in res["shorts"].items():
        L.append(f"| {w} | {s['n']} | {s['wins']} | {_f(s['net_usd'])} | {', '.join(s['coins']) or '-'} | "
                 f"{'yes' if s['made_money'] else 'no'} |")
    L.append("")
    L.append("### Known facts, recomputed from the fills")
    L.append("")
    L.append("| fact | stated | computed | match |")
    L.append("|---|---|---|---|")
    for k, f in res["facts"].items():
        L.append(f"| {k} | {json.dumps(f['stated'])} | {json.dumps(f['computed'])} | {'yes' if f['matches'] else 'NO'} |")
    L.append("")
    L.append("### Slices (the review's labels; nothing under n = 10 is EDGE or LEAK)")
    L.append("")
    L.append("| slice | n | wins | win % [95%] | mean R [95%] | net $ | p | label |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in res["slices"]:
        if r["n"] < min_n:
            continue
        L.append(f"| {r['key']} | {r['n']} | {r['wins']} | {_pct(r['win_rate'])} [{_pct(r['win_lo'])}, {_pct(r['win_hi'])}] | "
                 f"{_f(r['mean_r'])} [{_f(r['r_lo'])}, {_f(r['r_hi'])}] | {_f(r['net_usd'])} | "
                 f"{_f(r['p'], 3, False)} | {r['label']} |")
    L.append("")
    L.append("### Every trade")
    L.append("")
    L.append("| wallet | coin | side | entry (UTC) | type | range pos | 5d % | 24h % | stop ATR | plan R | hour | day | "
             "funding | rewrite<2h | event | hold h | exit | R | net $ |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in res["records"]:
        ev = "blackout" if r["in_blackout"] else (f"{r['event_h']:+.0f}h" if r.get("event_h") is not None else "-")
        L.append(f"| {r['wallet']} | {r['coin'].replace('xyz:', '')} | {r['side']} | {r['entry'][5:16]} | {r['entry_type']} | "
                 f"{_f(r.get('range_pos'), 2, False)} | {_f(r.get('ret_5d_pct'), 1)} | {_f(r.get('ret_24h_pct'), 1)} | "
                 f"{_f(r.get('stop_atr'), 1, False)} | {_f(r.get('planned_r'), 1, False)} | {r['hour_utc']:02d}Z | {r['weekday']} | "
                 f"{r.get('funding_vs_side') or '-'} | {'-' if r.get('rewrite_2h') is None else ('yes' if r['rewrite_2h'] else 'no')} | "
                 f"{ev} | {r['hold_h']:.1f} | {r['exit_type']} | {_f(r.get('r_mult'))} | {_f(r['net_usd'])} |")
    return "\n".join(L) + "\n"


def write_outputs(res: Dict[str, Any], out_dir: str) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = {"csv": os.path.join(out_dir, "trades.csv"), "json": os.path.join(out_dir, "report.json"),
             "md": os.path.join(out_dir, "attribution.md")}
    with open(paths["csv"], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(res["records"])
    with open(paths["json"], "w") as fh:
        json.dump(res, fh, indent=1, default=str)
    with open(paths["md"], "w") as fh:
        fh.write(markdown(res))
    return paths


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal's theses")
    ap.add_argument("--work-dir", default="/tmp/trade-attribution/work", help="raw reads and candle cache")
    ap.add_argument("--out-dir", default="/tmp/trade-attribution")
    ap.add_argument("--offline", metavar="BUNDLE", help="replay a saved bundle, no network")
    ap.add_argument("--save-bundle", metavar="DIR", help="save this run's inputs as a replayable bundle")
    ap.add_argument("--refresh", action="store_true", help="refetch the account reads")
    ap.add_argument("--seed", type=int, default=S.SEED)
    a = ap.parse_args(argv)
    bundle = load_bundle(a.offline) if a.offline else load_live(a.work_dir, a.oil_theses_dir, a.refresh)
    res = run(bundle, a.seed)
    if a.save_bundle:
        save_bundle(bundle, a.save_bundle, coins={r["coin"] for r in res["records"]})
    paths = write_outputs(res, a.out_dir)
    print(markdown(res))
    print(f"trades: {paths['csv']}\nreport: {paths['json']}\nmarkdown: {paths['md']}")
    empty = [w for w, raw in bundle["raw"].items() if not coins_traded(raw["fills"], bundle["since_ms"])]
    if empty:
        print(f"no fills in the window for: {', '.join(empty)}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
