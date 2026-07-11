"""W-U1: token-unlock event study on HL perps (pre-registered 2026-07-11).

Mechanistic hypothesis: scheduled supply unlocks >=1% of circulating supply
are known-in-advance sell pressure; price drifts down into and/or through the
event.

PRIMARY CELL (registered before any data was scored):
    SHORT at close(T-1d), exit close(T+2d), stop 15% (pessimistic: stop
    checked on daily HIGH before the exit day), events with unlock >=1% of
    circSupply, net 25bps round trip. Verdict vs matched same-coin
    random-day nulls (>=2000 draws) + OOS time halves.

EXPLORATORY (reported, not promotable without a re-run): 3% tier, run-in
window T-3d->T, post drift T->T+3d, LONG post-unlock fade.

Data: DefiLlama open datasets bucket (emissionsIndex) x HL daily candles.
Free, point-in-time safe: unlock schedules are published far in advance, so
using today's schedule file for past events is NOT lookahead for cliff dates
(the date was known before the event); it IS potentially survivorship-biased
toward tokens still listed — caveat carried in the findings.

Usage: .venv/bin/python research/alpha_swarm/hypotheses/W-U1_unlock_backtest.py
Writes: W-U1_results.json (same dir), candle cache W-U_cache_daily.json.
"""
from __future__ import annotations

import json
import os
import random
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from hermes_trader.client.hl_client import fetch_hl_candles  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
IDX_CACHE = os.path.join(HERE, "W-U_cache_emissions_index.json")
MAP_FILE = os.path.join(HERE, "W-U_map.json")
CANDLE_CACHE = os.path.join(HERE, "W-U_cache_daily.json")
RESULTS = os.path.join(HERE, "W-U1_results.json")

DAY_MS = 86_400_000
COST_RT = 0.0025          # 25bps round trip
STOP = 0.15
N_NULL = 2000
random.seed(20260711)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def load_index() -> dict:
    if os.path.exists(IDX_CACHE):
        return json.load(open(IDX_CACHE))
    url = "https://defillama-datasets.llama.fi/emissionsIndex"
    print(f"fetching {url} ...")
    with urllib.request.urlopen(url, timeout=120) as r:
        d = json.load(r)
    json.dump(d, open(IDX_CACHE, "w"))
    return d


def extract_events(idx: dict, name_to_sym: dict, min_pct: float) -> list[dict]:
    out = []
    for r in idx["data"]:
        sym = name_to_sym.get(r.get("name") or "")
        circ = _num(r.get("circSupply"))
        if not sym or circ <= 0:
            continue
        for e in r.get("events") or []:
            ts = _num(e.get("timestamp"))
            toks = e.get("noOfTokens")
            amt = sum(_num(x) for x in toks) if isinstance(toks, list) else 0.0
            if ts <= 0 or amt <= 0:
                continue
            pct = amt / circ * 100.0
            if pct >= min_pct:
                out.append({"coin": sym, "t_ms": int(ts * 1000), "pct": pct,
                            "type": e.get("unlockType") or ""})
    # one event per (coin, day): cliffs are often split into rows
    seen, dedup = set(), []
    for e in sorted(out, key=lambda x: x["t_ms"]):
        k = (e["coin"], e["t_ms"] // DAY_MS)
        if k in seen:
            for d in dedup:
                if (d["coin"], d["t_ms"] // DAY_MS) == k:
                    d["pct"] += e["pct"]
                    break
            continue
        seen.add(k)
        dedup.append(e)
    return dedup


def _plain(candle) -> dict:
    """fetch_hl_candles returns Candle objects; reduce to a JSON-safe dict."""
    if isinstance(candle, dict):
        return {k: candle.get(k) for k in ("t", "o", "h", "l", "c")}
    return {k: getattr(candle, k, None) for k in ("t", "o", "h", "l", "c")}


def load_candles(coins: list[str]) -> dict[str, list]:
    cache = json.load(open(CANDLE_CACHE)) if os.path.exists(CANDLE_CACHE) else {}
    missing = [c for c in coins if c not in cache]
    for i, c in enumerate(missing):
        try:
            cache[c] = [_plain(x) for x in (fetch_hl_candles(c, "1d", 2000) or [])]
        except Exception as exc:
            print(f"  {c}: fetch failed ({exc})")
            cache[c] = []
        time.sleep(2.0)  # live loop shares this IP — stay far under budget
        if (i + 1) % 20 == 0:
            print(f"  candles {i+1}/{len(missing)}")
            json.dump(cache, open(CANDLE_CACHE, "w"))
    json.dump(cache, open(CANDLE_CACHE, "w"))
    return cache


def day_index(candles: list) -> dict[int, dict]:
    return {int(_num(c.get("t")) // DAY_MS): c for c in candles}


def short_trade(days: dict[int, dict], d0: int) -> float | None:
    """SHORT close(T-1d) -> close(T+2d), 15% stop on daily highs (pessimistic:
    stop checked before profit on every day). Returns net fractional ret."""
    e = days.get(d0 - 1)
    x = days.get(d0 + 2)
    if not e or not x:
        return None
    entry = _num(e.get("c"))
    if entry <= 0:
        return None
    for k in (d0, d0 + 1, d0 + 2):
        bar = days.get(k)
        if bar and _num(bar.get("h")) >= entry * (1 + STOP):
            return -STOP - COST_RT
    exit_px = _num(x.get("c"))
    if exit_px <= 0:
        return None
    return (entry - exit_px) / entry - COST_RT


def window_ret(days: dict[int, dict], a: int, b: int) -> float | None:
    ca, cb = days.get(a), days.get(b)
    if not ca or not cb:
        return None
    pa, pb = _num(ca.get("c")), _num(cb.get("c"))
    return (pb / pa - 1.0) if pa > 0 and pb > 0 else None


def stats(rets: list[float]) -> dict:
    if not rets:
        return {"n": 0}
    m = statistics.mean(rets)
    return {"n": len(rets), "mean_pct": round(m * 100, 3),
            "median_pct": round(statistics.median(rets) * 100, 3),
            "win": round(sum(1 for r in rets if r > 0) / len(rets), 3)}


def mc_pvalue(days_by_coin: dict, ev_rets: list[float], ev_coins: list[str],
              fn) -> float:
    """Null: same coins, random eligible days. p = P(null mean <= event mean)
    for shorts (more negative underlying = better short)."""
    obs = statistics.mean(ev_rets)
    hits = 0
    draws = 0
    coin_days = {c: sorted(days_by_coin[c].keys()) for c in set(ev_coins)}
    while draws < N_NULL:
        sample = []
        for c in ev_coins:
            ds = coin_days[c]
            if len(ds) < 10:
                continue
            d0 = random.choice(ds[3:-3])
            r = fn(days_by_coin[c], d0)
            if r is not None:
                sample.append(r)
        if len(sample) < max(5, len(ev_rets) // 2):
            draws += 1
            continue
        if statistics.mean(sample) >= obs:
            hits += 1
        draws += 1
    return hits / max(draws, 1)


def main() -> None:
    name_to_sym = json.load(open(MAP_FILE))
    idx = load_index()
    events = extract_events(idx, name_to_sym, min_pct=1.0)
    print(f"events >=1% circ on HL coins: {len(events)}")
    coins = sorted({e["coin"] for e in events})
    candles = load_candles(coins)
    days_by_coin = {c: day_index(candles[c]) for c in coins}

    res: dict = {"registered": "short close(T-1d)->close(T+2d), stop 15%, >=1% circ, net 25bps",
                 "n_events_total": len(events)}

    def run_cell(evs, label):
        rets, used_coins, ts = [], [], []
        for e in evs:
            d0 = e["t_ms"] // DAY_MS
            r = short_trade(days_by_coin[e["coin"]], d0)
            if r is not None:
                rets.append(r)
                used_coins.append(e["coin"])
                ts.append(e["t_ms"])
        cell = stats(rets)
        if rets:
            cell["p_mc"] = mc_pvalue(days_by_coin, rets, used_coins, short_trade)
            mid = statistics.median(ts)
            h1 = [r for r, t in zip(rets, ts) if t <= mid]
            h2 = [r for r, t in zip(rets, ts) if t > mid]
            cell["half1"] = stats(h1)
            cell["half2"] = stats(h2)
        res[label] = cell
        print(f"{label}: {cell}")

    run_cell(events, "PRIMARY_short_1pct")
    run_cell([e for e in events if e["pct"] >= 3.0], "EXPL_short_3pct")

    # exploratory drift windows (no trade construction, raw close-to-close)
    for label, a_off, b_off in (("EXPL_runin_Tm3_T", -3, 0), ("EXPL_post_T_Tp3", 0, 3)):
        rets = []
        for e in events:
            d0 = e["t_ms"] // DAY_MS
            r = window_ret(days_by_coin[e["coin"]], d0 + a_off, d0 + b_off)
            if r is not None:
                rets.append(r)
        res[label] = stats(rets)
        print(f"{label}: {res[label]}")

    json.dump(res, open(RESULTS, "w"), indent=1)
    print(f"wrote {RESULTS}")


if __name__ == "__main__":
    main()
