"""W-L1 — entry-latency sensitivity of the live/shadow books' OWN signals.

Operator question: do LOWER scan intervals catch more alpha? This is NOT a new
signal hunt: each book's historical entries are reconstructed per its LIVE rules
(the *_live.py modules are the spec), then the SAME signal is entered with a
delay of {0, 1, 3, 6, 12} hours after the signal bar close. Paired same-signal
comparison is the control (no matched null needed: every delay trades the exact
same event, only the entry bar moves).

RESOLUTION FLOOR: the data is 1h bars (W-R_cache_hourly.json, 40 coins,
2025-12-13..2026-07-09). "Delay 0h" = entry at the OPEN of the first hourly bar
after the signal closes — the best case measurable here. Anything sub-hour
(30m vs 5m scans) is interpolation of the 0->1h edge plus the W-L2 5m anchor.

Books reconstructed (live geometry, pessimistic intra-bar stops, net 25bps):
  extreme_fade        LONG  daily ret <= -12%              stop 20%, hold 3d
  rally_exhaustion    SHORT +12%/2d, BTC 20d-bar down      stop 25%, hold 5d
  engulf_short        SHORT daily bearish full-body engulf stop 20%, hold 1d
  crash_continue      SHORT -8%/2d, BTC 20d-bar up         stop 20%, hold 10d
  funding_spike_short SHORT 24h funding z>=2 vs own 30d    stop 15%, hold 5d
Skipped (reconstruction would NOT be faithful — see findings):
  majors_swing (intraday trend+pullback state machine), young_listings (xyz
  coins absent from the hourly cache), whale_flow (no historical Binance taker
  prints), news_catalyst (no historical news timestamps).

Funding-spike funding history: W-L_cache_funding.json (W-L0 gap-fill) if
present, else funding.json (90d — narrower sample, flagged in output).

Fees: 25bps round trip, delay-invariant. Funding carry ignored (also
delay-invariant at the hours scale — it cancels in the paired diff).

Usage: .venv/bin/python research/alpha_swarm/hypotheses/W-L1_latency_backtest.py
Writes: W-L1_results.json (same dir).
"""
from __future__ import annotations

import json
import math
import os
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
HOURLY_CACHE = os.path.join(HERE, "W-R_cache_hourly.json")
FUNDING_EXT = os.path.join(HERE, "W-L_cache_funding.json")
FUNDING_BASE = os.path.join(HERE, "..", "funding.json")
RESULTS = os.path.join(HERE, "W-L1_results.json")

T, O, H, L, C, V = 0, 1, 2, 3, 4, 5
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
COST_RT = 0.0025
DELAYS_H = (0, 1, 3, 6, 12)
MIN_COVERAGE = 0.8          # fraction of hourly bars present inside the hold
DVOL_FLOOR = 20_000_000.0   # live min_volume_usd on the short books

BOOKS = {
    # side, stop (spot frac), hold hours
    "extreme_fade":        ("long",  0.20, 72),
    "rally_exhaustion":    ("short", 0.25, 120),
    "engulf_short":        ("short", 0.20, 24),
    "crash_continue":      ("short", 0.20, 240),   # live .agent-config: stop 20, hold 10d
    "funding_spike_short": ("short", 0.15, 120),
}


# ── data ─────────────────────────────────────────────────────────────────────

def load_hourly():
    d = json.load(open(HOURLY_CACHE))
    return {c: {int(b[T]): b for b in bars} for c, bars in d["candles"].items()}


def build_daily(by_ts: dict[int, list]) -> dict[int, dict]:
    """UTC-day bars from hourly. A day is COMPLETE iff all 24 hourly bars exist
    (a partial day must never fire a 'completed daily bar' signal)."""
    days: dict[int, list] = {}
    for t, b in by_ts.items():
        days.setdefault(t // DAY_MS, []).append(b)
    out = {}
    for d, bars in days.items():
        if len(bars) != 24:
            continue
        bars.sort(key=lambda x: x[T])
        out[d] = {"o": bars[0][O], "c": bars[-1][C],
                  "h": max(b[H] for b in bars), "l": min(b[L] for b in bars),
                  "v": sum(b[V] for b in bars)}
    return out


def dvol30(daily: dict[int, dict], d: int) -> float | None:
    """Mean daily dollar volume over the last 30 completed days ending at d
    (mirrors _trailing_dvol: mean of per-bar v*c)."""
    xs = [daily[k]["v"] * daily[k]["c"] for k in range(d - 29, d + 1) if k in daily]
    if len(xs) < 15:
        return None
    return sum(xs) / len(xs)


# ── trade sim: pessimistic intra-bar, hourly ────────────────────────────────

def simulate(by_ts: dict[int, list], t_entry: int, side: str, stop: float,
             hold_h: int) -> float | None:
    """Entry at open of the hourly bar starting at t_entry; stop checked FIRST
    inside every bar (entry bar included); gap-through fills at the bar open
    (worse than the stop). Horizon exit = close of the last bar in the window.
    Returns gross return or None (missing entry bar / coverage < MIN_COVERAGE /
    no bar near the horizon end)."""
    eb = by_ts.get(t_entry)
    if eb is None or eb[O] <= 0:
        return None
    entry = eb[O]
    stop_px = entry * (1 - stop) if side == "long" else entry * (1 + stop)
    seen, last_bar = 0, None
    for k in range(hold_h):
        b = by_ts.get(t_entry + k * HOUR_MS)
        if b is None:
            continue
        seen += 1
        last_bar = b
        if side == "long" and b[L] <= stop_px:
            exit_px = min(b[O], stop_px)
            return exit_px / entry - 1.0
        if side == "short" and b[H] >= stop_px:
            exit_px = max(b[O], stop_px)
            return (entry - exit_px) / entry
    if seen < MIN_COVERAGE * hold_h or last_bar is None:
        return None
    if last_bar[T] < t_entry + (hold_h - 3) * HOUR_MS:
        return None                     # data ends mid-hold: not a real horizon
    exit_px = last_bar[C]
    g = exit_px / entry - 1.0
    return g if side == "long" else -g


# ── signal reconstruction (live rules) ──────────────────────────────────────

def ret_n(daily, d: int, n: int) -> float | None:
    a, b = daily.get(d - n), daily.get(d)
    if not a or not b or a["c"] <= 0:
        return None
    return b["c"] / a["c"] - 1.0


def btc_regime(daily_btc, d: int, window: int = 20) -> float | None:
    """Live _btc_down/_btc_up: c[-1]/c[-window] over the last `window` COMPLETED
    bars => a (window-1)-day return."""
    return ret_n(daily_btc, d, window - 1)


def build_signals(hourly, daily_by, funding):
    """-> {book: [(coin, sig_close_ms, magnitude), ...]}  magnitude for ranking."""
    btc_d = daily_by["BTC"]
    sigs = {b: [] for b in BOOKS}
    for coin, daily in daily_by.items():
        days = sorted(daily)
        for d in days:
            t_close = (d + 1) * DAY_MS
            r1 = ret_n(daily, d, 1)
            # extreme_fade: any perp, no volume floor in the live module
            if r1 is not None and r1 <= -0.12:
                sigs["extreme_fade"].append((coin, t_close, r1))
            dv = dvol30(daily, d)
            if dv is None or dv < DVOL_FLOOR:
                continue
            r2 = ret_n(daily, d, 2)
            btc20 = btc_regime(btc_d, d)
            if r2 is not None and btc20 is not None:
                if btc20 < 0 and r2 >= 0.12:
                    sigs["rally_exhaustion"].append((coin, t_close, r2))
                if btc20 > 0 and r2 <= -0.08:
                    sigs["crash_continue"].append((coin, t_close, r2))
            p, c = daily.get(d - 1), daily.get(d)
            if coin != "BTC" and p and c and p["c"] > p["o"] and c["c"] < c["o"] \
                    and c["o"] >= p["c"] and c["c"] <= p["o"]:
                pb = abs(p["c"] - p["o"])
                if pb > 0 and abs(c["c"] - c["o"]) / pb >= 1.0:
                    sigs["engulf_short"].append((coin, t_close,
                                                 abs(c["c"] - c["o"]) / pb))
    # funding_spike_short: day-granularity z with episode dedup (live spec)
    for coin, rows in (funding or {}).items():
        daily = daily_by.get(coin)
        if not daily:
            continue
        by_day: dict[int, float] = {}
        for t, rate, _prem in rows:
            by_day[t // DAY_MS] = by_day.get(t // DAY_MS, 0.0) + rate
        fdays = sorted(by_day)
        in_episode = False
        for d in fdays:
            hist = [by_day[k] for k in fdays if d - 30 <= k <= d - 1]
            if len(hist) < 15 or (d - 1) not in by_day:
                continue
            last24 = by_day[d]          # settled rows of day d = trailing 24h at close
            mu, sd = statistics.mean(hist), statistics.pstdev(hist)
            z = 0.0 if sd == 0 else (last24 - mu) / sd
            if in_episode:
                if z < 1.0:
                    in_episode = False
                continue
            dv = dvol30(daily, d)
            if dv is None or dv < DVOL_FLOOR:
                continue
            if z >= 2.0:
                in_episode = True
                sigs["funding_spike_short"].append((coin, (d + 1) * DAY_MS, z))
    return sigs


# ── stats ────────────────────────────────────────────────────────────────────

def sign_test_p(diffs: list[float]) -> float:
    """Exact two-sided binomial sign test on the paired diffs (ties dropped)."""
    pos = sum(1 for x in diffs if x > 0)
    neg = sum(1 for x in diffs if x < 0)
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_t(diffs: list[float]) -> float | None:
    if len(diffs) < 3:
        return None
    sd = statistics.stdev(diffs)
    if sd == 0:
        return None
    return statistics.mean(diffs) / (sd / math.sqrt(len(diffs)))


def ev_at_interval(ev_by_delay: dict[int, float], interval_h: float) -> float:
    """Expected EV under a scan every `interval_h` hours: latency ~ U(0, I),
    EV(d) piecewise-linear through the measured grid, averaged over [0, I]."""
    xs = sorted(ev_by_delay)
    def ev(d: float) -> float:
        if d <= xs[0]:
            return ev_by_delay[xs[0]]
        for a, b in zip(xs, xs[1:]):
            if d <= b:
                w = (d - a) / (b - a)
                return ev_by_delay[a] * (1 - w) + ev_by_delay[b] * w
        return ev_by_delay[xs[-1]]
    n_steps = 60
    step = interval_h / n_steps
    area = sum(ev(step * (i + 0.5)) for i in range(n_steps)) / n_steps
    return area


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    hourly = load_hourly()
    daily_by = {c: build_daily(ts) for c, ts in hourly.items()}
    funding_src = FUNDING_EXT if os.path.exists(FUNDING_EXT) else FUNDING_BASE
    funding = json.load(open(funding_src))["funding"]
    print(f"coins={len(hourly)}  funding={os.path.basename(funding_src)}")

    sigs = build_signals(hourly, daily_by, funding)
    res = {"cost_model": "25bps RT, funding carry ignored (delay-invariant)",
           "resolution_note": ("1h bars floor the resolution: delay 0h = open of "
                               "the first hourly bar after signal close; sub-hour "
                               "is interpolation + the W-L2 5m anchor"),
           "delays_h": list(DELAYS_H),
           "bonferroni": {"comparisons": len(BOOKS) * (len(DELAYS_H) - 1),
                          "alpha": round(0.05 / (len(BOOKS) * (len(DELAYS_H) - 1)), 5)},
           "funding_source": os.path.basename(funding_src),
           "books": {}}

    for book, (side, stop, hold_h) in BOOKS.items():
        rows = []
        for coin, t_close, mag in sigs[book]:
            by_ts = hourly[coin]
            outs = {}
            ok = True
            for dh in DELAYS_H:
                g = simulate(by_ts, t_close + dh * HOUR_MS, side, stop, hold_h)
                if g is None:
                    ok = False
                    break
                outs[dh] = g - COST_RT
            if ok:
                rows.append({"coin": coin, "t": t_close, "mag": mag, "ev": outs})
        b = {"side": side, "stop_pct": stop * 100, "hold_h": hold_h,
             "n_signals_raw": len(sigs[book]), "n_paired": len(rows)}
        if rows:
            ev_by = {dh: statistics.mean(r["ev"][dh] for r in rows)
                     for dh in DELAYS_H}
            b["ev25_pct_by_delay"] = {str(dh): round(v * 100, 3)
                                      for dh, v in ev_by.items()}
            b["win_at_0h"] = round(sum(1 for r in rows if r["ev"][0] > 0)
                                   / len(rows), 3)
            b["paired_vs_0h"] = {}
            for dh in DELAYS_H[1:]:
                diffs = [r["ev"][dh] - r["ev"][0] for r in rows]
                b["paired_vs_0h"][str(dh)] = {
                    "mean_pct": round(statistics.mean(diffs) * 100, 3),
                    "t": round(paired_t(diffs), 2) if paired_t(diffs) else None,
                    "sign_p": round(sign_test_p(diffs), 4),
                    "n_worse": sum(1 for x in diffs if x < 0),
                    "n_better": sum(1 for x in diffs if x > 0)}
            # expected EV under scan intervals (avg latency = uniform over interval)
            b["ev25_pct_at_interval"] = {
                lbl: round(ev_at_interval(ev_by, ih) * 100, 3)
                for lbl, ih in (("5m", 1 / 12), ("30m", 0.5), ("1h", 1.0),
                                ("3h", 3.0), ("6h", 6.0), ("12h", 12.0))}
        res["books"][book] = b
        print(f"\n{book} ({side}, stop {stop*100:.0f}%, hold {hold_h}h): "
              f"n_raw={len(sigs[book])} n_paired={len(rows)}")
        if rows:
            print("  EV25% by delay:", b["ev25_pct_by_delay"])
            print("  EV25% at scan interval:", b["ev25_pct_at_interval"])
            print("  paired vs 0h:", b["paired_vs_0h"])

    # top-10 extreme_fade signals by crash magnitude, for the W-L2 5m anchor
    top = sorted(sigs["extreme_fade"], key=lambda s: s[2])[:10]
    res["extreme_fade_top10_for_anchor"] = [
        {"coin": c, "sig_close_ms": t, "daily_ret_pct": round(m * 100, 2)}
        for c, t, m in top]
    json.dump(res, open(RESULTS, "w"), indent=1)
    print(f"\nwrote {RESULTS}")


if __name__ == "__main__":
    main()
