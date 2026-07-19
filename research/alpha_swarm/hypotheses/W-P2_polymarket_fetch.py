"""W-P2: Polymarket pre-event odds for the classified event table.

Two subcommands:
  search "<query>"      -> list candidate markets (slug, question, volume, dates)
  prices                -> for every event in W-P2_events.json with a pm_slug,
                           fetch CLOB price history and record the last trade
                           price at T-24h and T-1h before the resolution ts.

The market->event mapping (pm_slug + pm_outcome_side) is committed in
W-P2_events.json BEFORE any price history is pulled; bucket rules were locked
in the findings file before that. p_realized = price of the REALIZED outcome
token at the snapshot instant (last point at or before it).

Gamma: gamma-api.polymarket.com (free). CLOB: clob.polymarket.com (free).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS = os.path.join(HERE, "W-P2_events.json")
CACHE = os.path.join(HERE, "W-P2_cache_polymarket.json")
SLEEP_S = 0.6

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def _get(url: str, params: dict | None = None):
    for i in range(4):
        try:
            r = requests.get(url, params=params, timeout=25)
            if r.ok:
                return r.json()
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def search(q: str) -> None:
    res = _get(f"{GAMMA}/public-search", {"q": q, "limit_per_type": 12})
    if not res:
        print("search failed")
        return
    for ev in res.get("events", []) or []:
        print(f"EVENT  {ev.get('slug')}  vol={ev.get('volume')}")
        for m in ev.get("markets", []) or []:
            print(f"   MKT {m.get('slug')} | {m.get('question')} | "
                  f"end={m.get('endDate')} | outcomes={m.get('outcomes')}")
    time.sleep(SLEEP_S)


def market_by_slug(slug: str) -> dict | None:
    res = _get(f"{GAMMA}/markets", {"slug": slug, "closed": "true"})
    time.sleep(SLEEP_S)
    if isinstance(res, list) and res:
        return res[0]
    return None


def price_at(token_id: str, ts_unix: int, lookback_h: int = 240) -> float | None:
    """Last traded price at or before ts_unix (searches back lookback_h)."""
    res = _get(f"{CLOB}/prices-history", {
        "market": token_id,
        "startTs": ts_unix - lookback_h * 3600,
        "endTs": ts_unix,
        "fidelity": 60,
    })
    time.sleep(SLEEP_S)
    hist = (res or {}).get("history") or []
    pts = [p for p in hist if p.get("t", 0) <= ts_unix]
    if not pts:
        return None
    return float(pts[-1]["p"])


def jump_pin(token_id: str, day_start_unix: int) -> int | None:
    """First sustained >=0.95 print (two consecutive 1m points) on event day.
    Returns unix ts or None. Public-knowledge pin per spec amendment 3."""
    res = _get(f"{CLOB}/prices-history", {
        "market": token_id,
        "startTs": day_start_unix,
        "endTs": day_start_unix + 86400,
        "fidelity": 1,
    })
    time.sleep(SLEEP_S)
    hist = (res or {}).get("history") or []
    for a, b in zip(hist, hist[1:]):
        if float(a.get("p", 0)) >= 0.95 and float(b.get("p", 0)) >= 0.95:
            return int(a["t"])
    if hist and float(hist[-1].get("p", 0)) >= 0.95:
        return int(hist[-1]["t"])
    return None


def prices() -> None:
    events = json.load(open(EVENTS))["events"]
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    for e in events:
        slug = e.get("pm_slug")
        eid = e["id"]
        if not slug or eid in cache:
            continue
        m = market_by_slug(slug)
        if not m:
            cache[eid] = {"error": "market not found"}
            print(f"{eid}: MARKET NOT FOUND {slug}")
            continue
        outcomes = json.loads(m.get("outcomes") or "[]")
        token_ids = json.loads(m.get("clobTokenIds") or "[]")
        side = e.get("pm_outcome_side", "Yes")
        try:
            idx = [o.lower() for o in outcomes].index(side.lower())
        except ValueError:
            cache[eid] = {"error": f"outcome {side} not in {outcomes}"}
            continue
        tid = token_ids[idx] if idx < len(token_ids) else None
        ts = int(datetime.fromisoformat(e["ts_utc"]).timestamp())
        pin_ts = None
        if e.get("pin_pm") and tid:
            day0 = ts - (ts % 86400)
            pin_ts = jump_pin(tid, day0)
            if pin_ts is not None:
                ts = pin_ts          # snapshots measured back from the pin
        rec = {"slug": slug, "side": side, "volume": m.get("volumeNum") or m.get("volume"),
               "pin_ts": pin_ts,
               "p_t24h": price_at(tid, ts - 24 * 3600) if tid else None,
               "p_t1h": price_at(tid, ts - 3600) if tid else None}
        cache[eid] = rec
        print(f"{eid}: p24={rec['p_t24h']} p1={rec['p_t1h']} vol={rec['volume']} ({slug} / {side})")
        json.dump(cache, open(CACHE, "w"), indent=1)
    json.dump(cache, open(CACHE, "w"), indent=1)


if __name__ == "__main__":
    if sys.argv[1:2] == ["search"]:
        search(" ".join(sys.argv[2:]))
    elif sys.argv[1:2] == ["prices"]:
        prices()
    else:
        print(__doc__)
