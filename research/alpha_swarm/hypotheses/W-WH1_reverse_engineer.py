#!/usr/bin/env python
"""W-WH1: reverse-engineer wallet 0xe282...df29 from its full public record.

Reads the caches W-WH1_fetch.py wrote; no network. Writes W-WH1_results.json
and prints the tables quoted in findings/W-WH1_wallet_0xe282.md.

Questions, each answered by a number, not a read of a screenshot:
  1. Does the reconstruction match Hyperdash? (ETH +549,545.01, BTC +196,642.18)
  2. Win rate: all-time vs the trailing-30d window Hyperdash displays.
  3. What did the winners carry on the way? (worst mark-to-market per trip)
  4. Entries: how deep and how often does it average down?
  5. Exits: maker or taker, and are they parked under round numbers?
  6. Account: flow-neutral PnL path, capital injected, liquidations.
  7. Outcome markets and spot, which Hyperdash's perp stats leave out.
"""
import importlib.util
import json
import math
import os
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("wh1lib", os.path.join(HERE, "W-WH1_lib.py"))
lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lib)

DAY = 86_400_000


def load(name):
    with open(os.path.join(HERE, f"W-WH1_cache_{name}.json")) as f:
        return json.load(f)


def ts(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def fmt(x):
    return f"{x:+,.0f}"


def main():
    fills = load("fills")
    funding = load("funding")
    ledger = load("ledger")
    state = load("state")
    candles = load("4h")
    now = state["fetched_ms"]

    trips = lib.build_trips(fills)
    lib.attach_funding(trips, funding)
    for t in trips:
        lib.path_stats(t, candles.get(t["coin"], []), now)

    closed = [t for t in trips if t["closed"]]
    open_ = [t for t in trips if not t["closed"]]
    res = {"n_fills": len(fills), "first_fill": ts(fills[0]["time"]),
           "fetched": ts(now)}

    # 1. reconstruction check against the operator's screenshot
    def find(coin, side, open_day, close_day):
        for t in closed:
            if (t["coin"] == coin and t["side"] == side and ts(t["t_open"]) == open_day
                    and ts(t["t_close"]) == close_day):
                return t
        return None

    checks = []
    for coin, side, o, c, want_net, want_fund in [
        ("ETH", "long", "2025-10-10", "2026-08-21", 549545.01, -305381.36),
        ("BTC", "long", "2025-10-10", "2026-08-21", 196642.18, -163893.80),
        ("BTC", "long", "2026-09-04", "2026-09-11", 11830.17, -1490.27),
        ("WLD", "long", "2026-06-26", "2026-07-01", -20481.28, -105.70),
        ("XRP", "short", "2025-07-25", "2025-07-26", -10359.28, 0.0),
    ]:
        t = find(coin, side, o, c)
        row = {"trip": f"{coin} {side} {o}->{c}", "hyperdash_net": want_net,
               "hyperdash_funding": want_fund,
               "ours_net": None if t is None else round(t["net"], 2),
               "ours_funding": None if t is None else round(t["funding"], 2)}
        checks.append(row)
    res["reconstruction_checks"] = checks
    print("\n== 1. reconstruction vs Hyperdash TRADES tab")
    for r in checks:
        print(f"  {r['trip']:<32} hyperdash {r['hyperdash_net']:>12,.2f}  ours {r['ours_net']}"
              f"   funding hd {r['hyperdash_funding']:,.2f} ours {r['ours_funding']}")

    # 2. win rate, all-time vs trailing 30d
    def wl(ts_list):
        wins = [t for t in ts_list if t["net"] > 0]
        losses = [t for t in ts_list if t["net"] <= 0]
        return {"n": len(ts_list), "wins": len(wins), "losses": len(losses),
                "win_rate": len(wins) / len(ts_list) if ts_list else math.nan,
                "sum_net": sum(t["net"] for t in ts_list),
                "sum_loss": sum(t["net"] for t in losses),
                "median_win": st.median([t["net"] for t in wins]) if wins else 0,
                "median_loss": st.median([t["net"] for t in losses]) if losses else 0}

    last30 = [t for t in closed if t["t_close"] >= now - 30 * DAY]
    res["win_rate_all_time"] = wl(closed)
    res["win_rate_last_30d"] = wl(last30)
    print("\n== 2. win rate (closed perp trips, net of fees + funding)")
    for label, w in [("all time", res["win_rate_all_time"]), ("last 30d", res["win_rate_last_30d"])]:
        print(f"  {label:<9} n={w['n']:>3}  wins {w['wins']:>3}  losses {w['losses']:>3}  "
              f"win {w['win_rate']:.1%}  net {fmt(w['sum_net'])}  losses sum {fmt(w['sum_loss'])}")
    by_year = defaultdict(list)
    for t in closed:
        by_year[ts(t["t_close"])[:7]].append(t)
    res["by_month"] = {m: wl(v) for m, v in sorted(by_year.items())}
    print("  by close month: " + "  ".join(
        f"{m} {v['wins']}W/{v['losses']}L" for m, v in res["by_month"].items()))
    biggest_losses = sorted(closed, key=lambda t: t["net"])[:12]
    res["biggest_losing_trips"] = [_brief(t) for t in biggest_losses if t["net"] < 0]
    print("  biggest losing trips:")
    for t in res["biggest_losing_trips"]:
        print(f"    {t['coin']:<10} {t['side']:<5} {t['open']}->{t['close']}  net {fmt(t['net'])}"
              f"  dur {t['days']:.1f}d  notional {t['open_notional']:,.0f}")

    # 3. what the winners carried
    carried = sorted(closed, key=lambda t: t["mae_usd"])[:12]
    res["worst_mae_closed"] = [_brief(t) for t in carried]
    print("\n== 3. worst mark-to-market inside CLOSED trips (4h bar extremes)")
    for t in res["worst_mae_closed"]:
        print(f"    {t['coin']:<10} {t['side']:<5} {t['open']}->{t['close']}  "
              f"worst {fmt(t['mae_usd'])} ({t['mae_pct_vs_avg']:+.1%} vs avg entry, "
              f"price {t['worst_px_vs_first']:+.1%} vs first fill)  final net {fmt(t['net'])}  "
              f"{t['n_open_orders']} buy orders")
    wins = [t for t in closed if t["net"] > 0]
    underwater_wins = [t for t in wins if t["mae_pct_vs_avg"] <= -0.05]
    res["wins_that_were_5pct_underwater"] = {
        "count": len(underwater_wins), "of_wins": len(wins),
        "net_from_them": sum(t["net"] for t in underwater_wins),
        "net_from_all_wins": sum(t["net"] for t in wins)}
    print(f"  wins that sat >=5% under their avg entry first: {len(underwater_wins)}/{len(wins)}, "
          f"contributing {fmt(res['wins_that_were_5pct_underwater']['net_from_them'])} "
          f"of {fmt(res['wins_that_were_5pct_underwater']['net_from_all_wins'])}")

    # 4. entries
    big = [t for t in closed if t["open_notional"] >= 1_000_000]
    below = sum(t["adds_below_avg_notional"] for t in trips)
    above = sum(t["adds_above_avg_notional"] for t in trips)
    long_notional = sum(t["open_notional"] for t in trips if t["side"] == "long")
    short_notional = sum(t["open_notional"] for t in trips if t["side"] == "short")
    res["entries"] = {
        "add_notional_below_running_avg": below,
        "add_notional_above_running_avg": above,
        "averaging_down_share": below / (below + above) if below + above else math.nan,
        "long_share_of_open_notional": long_notional / (long_notional + short_notional),
        "median_buy_orders_per_trip_ge_1m": st.median([t["n_open_orders"] for t in big]) if big else 0,
        "maker_share_open": _wavg(trips, "maker_share_open", "open_notional"),
    }
    print("\n== 4. entries")
    e = res["entries"]
    print(f"  adds below running avg entry (averaging down): {e['averaging_down_share']:.1%} of add notional")
    print(f"  long share of opened notional: {e['long_share_of_open_notional']:.1%}")
    print(f"  median separate buy orders per trip >= $1M: {e['median_buy_orders_per_trip_ge_1m']}")
    print(f"  maker share of opening notional: {e['maker_share_open']:.1%}")

    # 5. exits
    perp_fills = [f for f in fills if lib.is_perp(f["coin"])]
    close_f = [f for f in perp_fills if f["dir"].startswith("Close")]
    open_f = [f for f in perp_fills if f["dir"].startswith("Open")]

    def round_mix(fs, maker_only):
        sel = [f for f in fs if (not f["crossed"]) or not maker_only]
        notional = sum(float(f["px"]) * float(f["sz"]) for f in sel)
        c = Counter()
        for f in sel:
            c[lib.roundness(f["px"])] += float(f["px"]) * float(f["sz"])
        return {k: c[k] / notional for k in ("exact", "sub", "other")} if notional else {}

    res["exits"] = {
        "maker_share_close": _wavg(trips, "maker_share_close", "open_notional"),
        "roundness_close_maker": round_mix(close_f, True),
        "roundness_open_maker": round_mix(open_f, True),
        "roundness_close_all": round_mix(close_f, False),
        "roundness_open_all": round_mix(open_f, False),
        "median_exit_vs_avg_entry_wins": st.median(
            [t["side_sign"] * (t["avg_exit"] / t["avg_entry"] - 1)
             for t in [dict(x, side_sign=1 if x["side"] == "long" else -1) for x in wins]]),
        "open_orders_now": state["open_orders"],
    }
    x = res["exits"]
    print("\n== 5. exits")
    print(f"  maker share of closing notional: {x['maker_share_close']:.1%}")
    print(f"  closing maker notional at/just-under round numbers: "
          f"exact {x['roundness_close_maker'].get('exact', 0):.1%}  sub {x['roundness_close_maker'].get('sub', 0):.1%}")
    print(f"  opening maker notional (control):                  "
          f"exact {x['roundness_open_maker'].get('exact', 0):.1%}  sub {x['roundness_open_maker'].get('sub', 0):.1%}")
    print(f"  median winning exit vs avg entry: {x['median_exit_vs_avg_entry_wins']:+.2%}")
    print(f"  resting orders now: {[(o['coin'], o['side'], o['limitPx'], o['sz'], o['reduceOnly']) for o in x['open_orders_now']]}")

    # 6. account
    portfolio = dict(state["portfolio"])
    curve = portfolio["allTime"]["pnlHistory"]
    perp_curve = portfolio["perpAllTime"]["pnlHistory"]
    res["account"] = {"all": _dd(curve), "perp": _dd(perp_curve)}
    flows = Counter()
    for row in ledger:
        d = row["delta"]
        if d["type"] in ("deposit", "withdraw"):
            flows[d["type"]] += float(d["usdc"])
        elif d["type"] == "send":
            key = "send_in" if d["destination"].lower() == lib_user() else "send_out"
            flows[key] += float(d["usdcValue"])
        elif d["type"] == "vaultLeaderCommission":
            flows["vault_commission"] += float(d["usdc"])
    liqs = [{"date": ts(r["time"]), **r["delta"]} for r in ledger if r["delta"]["type"] == "liquidation"]
    res["account"]["flows"] = dict(flows)
    res["account"]["liquidations"] = liqs
    a = res["account"]
    print("\n== 6. account (HL's own flow-neutral PnL curve, weekly samples)")
    for k in ("all", "perp"):
        d = a[k]
        print(f"  {k:<5} peak {fmt(d['peak'])} on {d['peak_date']}  trough after it {fmt(d['trough'])} on "
              f"{d['trough_date']}  drawdown {fmt(d['max_dd'])}  now {fmt(d['now'])}")
    print("  flows: " + "  ".join(f"{k} {v:,.0f}" for k, v in sorted(flows.items())))
    for l in liqs:
        print(f"  LIQUIDATED {l['date']}: {l['liquidatedPositions']}  notional {float(l['liquidatedNtlPos']):,.0f}  "
              f"account value at liquidation {float(l['accountValue']):,.0f}")

    # 7. outcome markets + spot
    outcome = defaultdict(lambda: {"pnl": 0.0, "fees": 0.0, "fills": 0})
    for f in fills:
        if not lib.is_perp(f["coin"]):
            o = outcome[f["coin"]]
            o["pnl"] += float(f["closedPnl"])
            o["fees"] += float(f["fee"])
            o["fills"] += 1
    res["non_perp"] = {k: dict(v, net=v["pnl"] - v["fees"]) for k, v in sorted(outcome.items())}
    print("\n== 7. outcome markets (#) and spot (@), realized")
    for k, v in res["non_perp"].items():
        print(f"  {k:<7} fills {v['fills']:>4}  net {fmt(v['net'])}")

    # 8. calibration: the ladder's parameters, read off the wallet (W-WH2 freezes these)
    res["calibration"] = calibrate(trips, candles)
    c = res["calibration"]
    print("\n== 8. calibration for W-WH2 (long trips >= $250K opened, n=%d)" % c["n_trips"])
    print(f"  entry: first fill vs trailing-30d high, median {c['trigger_dd_median']:+.1%} "
          f"(quartiles {c['trigger_dd_q1']:+.1%} / {c['trigger_dd_q3']:+.1%})")
    print(f"  rung gap (each new-low buy order vs the previous lowest), median {c['rung_gap_median']:+.1%}")
    print(f"  rungs per trip (new-low orders + first), median {c['rungs_median']}")
    print(f"  deepest price vs first fill, median {c['depth_median']:+.1%}  worst {c['depth_worst']:+.1%}")
    print(f"  lowest buy vs first buy, median {c['buy_span_median']:+.1%}  worst {c['buy_span_worst']:+.1%}"
          f"  -> {c['rungs_median']:.0f} rungs every {c['ladder_gap_from_span']:+.1%}")
    print(f"  take profit, winning closed trips: exit vs avg entry median {c['tp_median_wins']:+.2%}")

    # open book right now
    res["open_trips"] = [_brief(t) for t in open_]
    print("\n== open positions (trip-level, marked on last 4h bar)")
    for t in res["open_trips"]:
        print(f"    {t['coin']:<10} {t['side']:<5} since {t['open']}  {t['n_open_orders']} buy orders  "
              f"worst {fmt(t['mae_usd'])} ({t['mae_pct_vs_avg']:+.1%})  funding {fmt(t['funding'])}")

    res["trips"] = [_brief(t) for t in trips]
    with open(os.path.join(HERE, "W-WH1_results.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"\nwrote W-WH1_results.json  ({len(trips)} trips, {len(closed)} closed)")


def calibrate(trips, candles, min_notional=250_000):
    """Ladder parameters as the wallet actually traded them, long side only."""
    sel = [t for t in trips if t["side"] == "long" and t["open_notional"] >= min_notional]
    trigger, gaps, rungs, depth, tps, spans = [], [], [], [], [], []
    for t in sel:
        buys = [float(f["px"]) for f in t["fills"] if lib.signed_size(f) > 0]
        spans.append(min(buys) / buys[0] - 1)
        bars = candles.get(t["coin"], [])
        prior = [b for b in bars if t["t_open"] - 30 * DAY <= b[0] < t["t_open"]]
        if prior:
            trigger.append(t["first_px"] / max(b[2] for b in prior) - 1)
        orders = {}
        for f in t["fills"]:
            if lib.signed_size(f) > 0:
                o = orders.setdefault(f["oid"], [f["time"], 0.0, 0.0])
                o[1] += float(f["px"]) * float(f["sz"])
                o[2] += float(f["sz"])
        low, n = None, 0
        for _t, cost, sz in sorted(orders.values()):
            px = cost / sz
            if low is None:
                low, n = px, 1
            elif px < low:
                gaps.append(px / low - 1)
                low, n = px, n + 1
        rungs.append(n)
        depth.append(t["worst_px_vs_first"])
        if t["closed"] and t["net"] > 0:
            tps.append(t["avg_exit"] / t["avg_entry"] - 1)
    q = st.quantiles(trigger, n=4) if len(trigger) >= 4 else [math.nan] * 3
    k = st.median(rungs)
    span = st.median(spans)
    return {"n_trips": len(sel), "trigger_dd_median": st.median(trigger),
            "trigger_dd_q1": q[0], "trigger_dd_q3": q[2],
            "rung_gap_median": st.median(gaps), "n_gaps": len(gaps),
            "rungs_median": k, "depth_median": st.median(depth),
            "depth_worst": min(depth), "tp_median_wins": st.median(tps), "n_tp": len(tps),
            "buy_span_median": span, "buy_span_worst": min(spans),
            # K rungs evenly spaced (geometrically) across the median span he bought through
            "ladder_gap_from_span": (1 + span) ** (1 / (k - 1)) - 1}


def lib_user():
    return "0xe2823659be02e0f48a4660e4da008b5e1abfdf29"


def _wavg(trips, key, weight):
    num = den = 0.0
    for t in trips:
        v = t[key]
        if not math.isnan(v):
            num += v * t[weight]
            den += t[weight]
    return num / den if den else math.nan


def _dd(curve):
    peak, peak_t, best = -math.inf, None, (0.0, None, None, None)
    for t, v in curve:
        v = float(v)
        if v > peak:
            peak, peak_t = v, t
        if v - peak < best[0]:
            best = (v - peak, peak, peak_t, t)
    dd, pk, pk_t, tr_t = best
    return {"max_dd": dd, "peak": pk, "peak_date": ts(pk_t) if pk_t else None,
            "trough": pk + dd, "trough_date": ts(tr_t) if tr_t else None,
            "now": float(curve[-1][1])}


def _brief(t):
    end = t["t_close"] or t["t_open"]
    return {"coin": t["coin"], "side": t["side"], "open": ts(t["t_open"]),
            "close": ts(t["t_close"]) if t["t_close"] else None,
            "days": (end - t["t_open"]) / DAY, "closed": t["closed"],
            "net": t["net"], "realized": t["realized"], "fees": t["fees"],
            "funding": t["funding"], "open_notional": t["open_notional"],
            "max_notional": t["max_notional"], "n_open_orders": t["n_open_orders"],
            "avg_entry": t["avg_entry"], "avg_exit": t["avg_exit"], "first_px": t["first_px"],
            "mae_usd": t["mae_usd"], "mae_pct_vs_avg": t["mae_pct_vs_avg"],
            "worst_px_vs_first": t["worst_px_vs_first"],
            "maker_share_close": t["maker_share_close"]}


if __name__ == "__main__":
    main()
