"""W-WH1 shared logic: rebuild a wallet's round trips from raw Hyperliquid fills.

Pure functions over the JSON the info API returns, so the gate tests in
tests/test_wh1_trips.py can drive them with synthetic fills.

A trip is one position from flat back to flat on one coin. Hyperdash's
TRADES tab uses the same definition, which is what lets the reconstruction
be checked against the operator's screenshots (ETH long +$549,545.01,
BTC long +$196,642.18).
"""
from __future__ import annotations

import math
from bisect import bisect_left

EPS = 1e-9
MS_4H = 4 * 3600 * 1000


def is_perp(coin: str) -> bool:
    """Perp coins: 'BTC', 'xyz:SP500'. Spot pairs are '@107', outcome tokens '#2020'."""
    return not coin.startswith(("@", "#")) and "/" not in coin


def signed_size(fill: dict) -> float:
    return float(fill["sz"]) if fill["side"] == "B" else -float(fill["sz"])


def chain_order(fills: list[dict]) -> list[dict]:
    """Time order, with same-millisecond fills chained by startPosition.

    One aggressive order sweeping the book yields dozens of fills stamped with
    the same millisecond, and their tids are not in execution order. Sorting
    them by tid replays a 60,000 XRP close as random partial closes and
    reopens. Each fill's startPosition is the previous fill's end, so that
    chain is the real order.
    """
    by_coin: dict[str, list[dict]] = {}
    for f in sorted(fills, key=lambda f: f["time"]):
        by_coin.setdefault(f["coin"], []).append(f)
    out: list[dict] = []
    for coin_fills in by_coin.values():
        cur = None
        i = 0
        while i < len(coin_fills):
            j = i
            while j < len(coin_fills) and coin_fills[j]["time"] == coin_fills[i]["time"]:
                j += 1
            remaining = coin_fills[i:j]
            while remaining:
                pick = None
                if cur is not None:
                    pick = next((f for f in remaining
                                 if abs(float(f["startPosition"]) - cur) < 1e-6), None)
                if pick is None:
                    ends = [float(f["startPosition"]) + signed_size(f) for f in remaining]
                    pick = next((f for f in remaining
                                 if not any(abs(float(f["startPosition"]) - e) < 1e-6
                                            for e in ends)), remaining[0])
                remaining.remove(pick)
                out.append(pick)
                cur = float(pick["startPosition"]) + signed_size(pick)
            i = j
    out.sort(key=lambda f: f["time"])   # stable: keeps each coin's chain intact
    return out


def build_trips(fills: list[dict]) -> list[dict]:
    """Group perp fills into flat-to-flat trips per coin.

    Trusts HL's startPosition to open a trip, so a history that begins
    mid-position (HL only serves the 10,000 most recent fills) starts at the
    first fill that opens from flat instead of inventing an entry price.
    A fill that flips the position closes one trip and opens the next.
    """
    ordered = chain_order([f for f in fills if is_perp(f["coin"])])
    open_trip: dict[str, dict] = {}
    trips: list[dict] = []
    for f in ordered:
        coin = f["coin"]
        start = float(f["startPosition"])
        delta = signed_size(f)
        trip = open_trip.get(coin)
        if trip is None:
            if abs(start) > EPS:
                continue  # mid-position with no visible opening fill
            trip = open_trip[coin] = _new_trip(coin, f, delta)
        end = start + delta
        if abs(end) > EPS and (start > EPS) != (end > EPS) and abs(start) > EPS:
            # flip: close the old side at this fill, open the new side with the rest
            close_part = dict(f, sz=str(abs(start)))
            trip["fills"].append(close_part)
            _close(trip, f["time"])
            trips.append(trip)
            rest = dict(f, sz=str(abs(end)), closedPnl="0", fee="0", startPosition="0")
            open_trip[coin] = _new_trip(coin, rest, end)
            open_trip[coin]["fills"].append(rest)
            continue
        trip["fills"].append(f)
        if abs(end) <= EPS:
            _close(trip, f["time"])
            trips.append(trip)
            del open_trip[coin]
    for trip in open_trip.values():
        trip["closed"] = False
        trips.append(trip)
    for trip in trips:
        _summarize(trip)
    trips.sort(key=lambda t: t["t_open"])
    return trips


def _new_trip(coin: str, fill: dict, delta: float) -> dict:
    return {"coin": coin, "side": "long" if delta > 0 else "short",
            "t_open": fill["time"], "t_close": None, "closed": None, "fills": []}


def _close(trip: dict, t: int) -> None:
    trip["t_close"] = t
    trip["closed"] = True


def _summarize(trip: dict) -> None:
    sign = 1.0 if trip["side"] == "long" else -1.0
    pos = 0.0
    max_pos = 0.0
    max_notional = 0.0
    open_cost = open_sz = close_val = close_sz = 0.0
    adds_below_avg = adds_above_avg = 0.0   # notional added under/over the running avg
    open_orders, close_orders = set(), set()
    open_maker = close_maker = 0.0
    realized = fees = 0.0
    for f in trip["fills"]:
        px, sz = float(f["px"]), float(f["sz"])
        d = signed_size(f)
        realized += float(f.get("closedPnl", 0) or 0)
        fees += float(f.get("fee", 0) or 0)
        opening = d * sign > 0
        if opening:
            if open_sz > EPS:
                avg = open_cost / open_sz
                better = px < avg if sign > 0 else px > avg
                if better:
                    adds_below_avg += px * sz
                else:
                    adds_above_avg += px * sz
            open_cost += px * sz
            open_sz += sz
            open_orders.add(f["oid"])
            if not f["crossed"]:
                open_maker += px * sz
        else:
            close_val += px * sz
            close_sz += sz
            close_orders.add(f["oid"])
            if not f["crossed"]:
                close_maker += px * sz
        pos += d
        max_pos = max(max_pos, abs(pos))
        max_notional = max(max_notional, abs(pos) * px)
    trip.update({
        "n_fills": len(trip["fills"]),
        "n_open_orders": len(open_orders),
        "n_close_orders": len(close_orders),
        "open_notional": open_cost,
        "max_pos": max_pos,
        "max_notional": max_notional,
        "first_px": float(trip["fills"][0]["px"]),
        "avg_entry": open_cost / open_sz if open_sz > EPS else math.nan,
        "avg_exit": close_val / close_sz if close_sz > EPS else math.nan,
        "open_pos": pos,
        "realized": realized,
        "fees": fees,
        "maker_share_open": open_maker / open_cost if open_cost > EPS else math.nan,
        "maker_share_close": close_maker / close_val if close_val > EPS else math.nan,
        "adds_below_avg_notional": adds_below_avg,
        "adds_above_avg_notional": adds_above_avg,
    })


def attach_funding(trips: list[dict], funding_rows: list[dict]) -> None:
    """Sum funding (signed, from the wallet's side) paid inside each trip's life.

    Early HL rows are daily aggregates stamped at 00:00 UTC, so a row may land
    up to a day before the opening fill; it goes to the trip that was open
    that day.
    """
    by_coin: dict[str, list[dict]] = {}
    for t in trips:
        t["funding"] = 0.0
        by_coin.setdefault(t["coin"], []).append(t)
    for row in funding_rows:
        d = row["delta"]
        cands = by_coin.get(d["coin"], [])
        ts = row["time"]
        best = None
        for t in cands:
            end = t["t_close"] if t["t_close"] is not None else float("inf")
            if t["t_open"] - 86_400_000 <= ts <= end + 3_600_000:
                if best is None or t["t_open"] > best["t_open"]:
                    best = t
        if best is not None:
            best["funding"] += float(d["usdc"])
    for t in trips:
        t["net"] = t["realized"] - t["fees"] + t["funding"]


def path_stats(trip: dict, candles: list[list[float]], now_ms: int) -> None:
    """Worst mark-to-market point of the trip, on 4h bar extremes.

    Replays fills in time order against each 4h bar: the position and average
    entry held going into a bar are marked at that bar's adverse extreme (low
    for a long, high for a short). Intraday opens inside a single bar are
    marked against that bar too, which overstates adverse excursion slightly
    for very short trips; that bias is conservative for the question asked
    (how much pain did the trip carry).
    """
    trip["mae_usd"] = 0.0
    trip["mae_pct_vs_avg"] = 0.0
    trip["worst_px_vs_first"] = 0.0
    if not candles:
        trip["mae_usd"] = math.nan
        return
    sign = 1.0 if trip["side"] == "long" else -1.0
    end = trip["t_close"] if trip["t_close"] is not None else now_ms
    times = [c[0] for c in candles]
    i = max(bisect_left(times, trip["t_open"]) - 1, 0)
    fills = trip["fills"]
    k = 0
    pos = cost = 0.0
    first = trip["first_px"]
    while i < len(candles) and candles[i][0] <= end:
        t, _o, h, l, _c = candles[i][:5]
        bar_end = t + MS_4H
        while k < len(fills) and fills[k]["time"] < bar_end:
            f = fills[k]
            d = signed_size(f)
            px = float(f["px"])
            if d * sign > 0:
                cost += abs(d) * px
            elif abs(pos) > EPS:
                cost -= cost / abs(pos) * abs(d)
            pos += d
            k += 1
        if abs(pos) > EPS:
            avg = cost / abs(pos)
            adverse = l if sign > 0 else h
            upnl = sign * abs(pos) * (adverse - avg)
            if upnl < trip["mae_usd"]:
                trip["mae_usd"] = upnl
                trip["mae_pct_vs_avg"] = sign * (adverse / avg - 1)
        adverse_first = sign * ((l if sign > 0 else h) / first - 1)
        trip["worst_px_vs_first"] = min(trip["worst_px_vs_first"], adverse_first)
        i += 1


def roundness(px: str) -> str:
    """'exact' if px is a multiple of half its 2-significant-digit unit
    (e.g. 4.0000, 2450), 'sub' if one tick above it is (109.99, 2449.9,
    76999, 69.999: the classic resting order placed just under a round
    number so it fills before the crowd's), else 'other'."""
    p = float(px)
    if p <= 0:
        return "other"
    unit = 10 ** (math.floor(math.log10(p)) - 1)
    half = unit / 2
    if _is_multiple(p, half):
        return "exact"
    for tick in (unit / 10, unit / 100, unit / 1000, unit / 10000):
        if _is_multiple(p + tick, half) and not _is_multiple(p, tick * 10):
            return "sub"
    return "other"


def _is_multiple(x: float, step: float) -> bool:
    q = x / step
    return abs(q - round(q)) < 1e-6
