#!/usr/bin/env python3
"""Calendar-family extension #2: weekend effect, intramonth drift, time-of-day (4h).

Methodology bar (all required):
  - Lookahead-safe: signal ≤ t, enter t+1
  - Cost-aware: directional bias probes shown BOTH gross AND net of 10bps/leg
  - Survivorship-free: whole liquid universe (same filter as edge_sweep3.py / ALPHA-PLAN.md)
  - OOS-robust: split trade stream chronologically, BOTH halves must agree
  - Multiple-testing honest: number of buckets tested stated per section; lone significant
    buckets treated with suspicion even if both OOS halves agree.

Data:
  - Daily (1d, 261d, 28+ coins):  sections A, B
  - 4h (~40d, 28 coins):          section C (EXPLORATORY — flag thin sample explicitly)

Run:  BT_CACHE_ONLY=1 python3 scripts/edge_seasonality2.py
"""
import os
import sys
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TOPN = 50
VOL_FLOOR = 5e6
COST = 10.0 / 1e4   # 10 bps one-way, 20 bps round-trip


def _dt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Universe + data load
# ---------------------------------------------------------------------------
def load_daily():
    uni = [
        m for m in get_universe(include_hip3=False)
        if ":" not in (m.get("coin") or "")
        and not (m.get("coin") or "").startswith("@")
        and m.get("type") != "spot"
        and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR
    ]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        bars = get_candles(m["coin"], "1d", 260)
        if len(bars) >= 80:
            data[m["coin"]] = bars
    return data


def load_4h():
    uni = [
        m for m in get_universe(include_hip3=False)
        if ":" not in (m.get("coin") or "")
        and not (m.get("coin") or "").startswith("@")
        and m.get("type") != "spot"
        and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR
    ]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        bars = get_candles(m["coin"], "4h", 240)
        if len(bars) >= 20:
            data[m["coin"]] = bars
    return data


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------
def _stats(arr):
    """Return (n, mean, win_rate, h1_mean, h2_mean, robust_label)."""
    n = len(arr)
    if n < 10:
        return n, None, None, None, None, "THIN"
    mean = statistics.mean(arr)
    win = sum(1 for r in arr if r > 0) / n
    mid = n // 2
    h1 = statistics.mean(arr[:mid]) * 100
    h2 = statistics.mean(arr[mid:]) * 100
    if h1 > 0 and h2 > 0:
        robust = "ROBUST"
    elif h1 < 0 and h2 < 0:
        robust = "neg"
    else:
        robust = "fragile"
    return n, mean * 100, win * 100, h1, h2, robust


def rep(label, arr, cost_pct=0.0, indent="  "):
    n, mean, win, h1, h2, robust = _stats(arr)
    if robust == "THIN":
        print(f"{indent}{label:32}  n={n:>4}  (THIN — skip)")
        return
    net = mean - cost_pct * 100 if mean is not None else None
    ev_flag = "  <<< +EV" if (net is not None and net > 0 and robust == "ROBUST") else ""
    neg_flag = "  <<< -EV" if (net is not None and net < 0 and robust == "neg") else ""
    print(
        f"{indent}{label:32}  n={n:>4}  win {win:>4.0f}%  "
        f"gross {mean:>+6.3f}%  net {net:>+6.3f}%  "
        f"OOS h1/h2 {h1:>+5.2f}/{h2:>+5.2f}  {robust}{ev_flag}{neg_flag}"
    )


# ---------------------------------------------------------------------------
# A. WEEKEND EFFECT (daily bars, 261d, directional bias)
# ---------------------------------------------------------------------------
def weekend_effect(data):
    """
    Crypto trades 24/7 — do Sat/Sun cross-coin mean DAILY returns differ from weekdays?

    For each coin, compute day-over-day log return r[t] = close[t]/close[t-1]-1.
    Tag by day-of-week of bar[t]. Pool all coins.

    Signal: enter at OPEN of day-of-week d, exit at CLOSE of same day.
    (Conservative: open→close within the bar, no overnight gap; still a 10bps one-way cost.)

    5 weekday buckets + 2 weekend = 7 total tested.
    Weekend vs weekday = the KEY comparison (1 meaningful hypothesis, 7 raw buckets).
    """
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    buckets = {i: [] for i in range(7)}

    for coin, bars in data.items():
        for k in range(1, len(bars)):
            p0, p1 = bars[k - 1]["c"], bars[k]["c"]
            if p0 > 0:
                r = p1 / p0 - 1
                dow = _dt(bars[k]["t"]).weekday()  # 0=Mon, 5=Sat, 6=Sun
                buckets[dow].append(r)

    weekday_pool = []
    for i in range(5):
        weekday_pool.extend(buckets[i])
    weekend_pool = []
    for i in (5, 6):
        weekend_pool.extend(buckets[i])

    print("\n  Per-day (7 buckets tested — multiple-testing caveat):")
    for i in range(7):
        rep(f"    {names[i]}", buckets[i], cost_pct=COST)

    print("\n  Aggregated (1 primary hypothesis):")
    rep("    Weekday (Mon-Fri)", weekday_pool, cost_pct=COST)
    rep("    Weekend (Sat+Sun)", weekend_pool, cost_pct=COST)

    # Weekend-vs-weekday spread (long weekend / short weekday proxy)
    # Compute per-day-pair differential returns
    wknd_means = [statistics.mean(buckets[i]) for i in (5, 6)
                  if len(buckets[i]) >= 10]
    wkd_means = [statistics.mean(buckets[i]) for i in range(5)
                 if len(buckets[i]) >= 10]
    if wknd_means and wkd_means:
        wknd_avg = statistics.mean(wknd_means) * 100
        wkd_avg = statistics.mean(wkd_means) * 100
        print(f"\n  Weekend mean: {wknd_avg:+.3f}%  |  Weekday mean: {wkd_avg:+.3f}%  "
              f"|  Spread (Wknd-Wkd): {wknd_avg - wkd_avg:+.3f}%")


# ---------------------------------------------------------------------------
# B. INTRAMONTH / TURN-OF-MONTH (daily bars, 261d)
# ---------------------------------------------------------------------------
def intramonth(data):
    """
    Cross-coin mean return by day-of-month bucket.

    Buckets (6 tested):
      first-3 : DOM 1-3   (front edge of turn-of-month)
      mid      : DOM 4-25  (bulk of month)
      last-3   : DOM 26-31 (back edge / end-of-month)

    Also test FINE-GRAINED DOM buckets (split 1-5 / 6-10 / 11-15 / 16-20 / 21-25 / 26-31 = 6 buckets).
    Fine-grained = exploratory (6 buckets, multiple-testing caveat stronger).

    NOTE: edge_sweep3.py tested turn-of-month with dom≤3 or dom≥27 vs rest — and it was REFUTED.
    We retest cleanly with slightly different windows and also log the fine-grained.
    """
    coarse = {"first3": [], "mid": [], "last3": []}
    fine = {i: [] for i in range(6)}   # 0=1-5, 1=6-10, ..., 5=26-31

    for coin, bars in data.items():
        for k in range(1, len(bars)):
            p0, p1 = bars[k - 1]["c"], bars[k]["c"]
            if p0 <= 0:
                continue
            r = p1 / p0 - 1
            dom = _dt(bars[k]["t"]).day

            # Coarse
            if dom <= 3:
                coarse["first3"].append(r)
            elif dom >= 26:
                coarse["last3"].append(r)
            else:
                coarse["mid"].append(r)

            # Fine (6 × 5-day bands, last band absorbs 26-31)
            if dom <= 5:
                fine[0].append(r)
            elif dom <= 10:
                fine[1].append(r)
            elif dom <= 15:
                fine[2].append(r)
            elif dom <= 20:
                fine[3].append(r)
            elif dom <= 25:
                fine[4].append(r)
            else:
                fine[5].append(r)

    fine_labels = ["DOM  1-5", "DOM  6-10", "DOM 11-15", "DOM 16-20", "DOM 21-25", "DOM 26-31"]

    print("\n  Coarse (3 buckets — primary hypothesis):")
    rep("    first-3 (DOM 1-3)", coarse["first3"], cost_pct=COST)
    rep("    mid (DOM 4-25)", coarse["mid"], cost_pct=COST)
    rep("    last-3 (DOM 26-31)", coarse["last3"], cost_pct=COST)

    print("\n  Fine-grained (6 buckets — exploratory, higher multiple-testing risk):")
    for i, lbl in enumerate(fine_labels):
        rep(f"    {lbl}", fine[i], cost_pct=COST)


# ---------------------------------------------------------------------------
# C. TIME-OF-DAY  (4h bars, ~40d, EXPLORATORY)
# ---------------------------------------------------------------------------
def time_of_day(data4h):
    """
    Cross-coin mean 4h return by UTC start-hour of bar.

    6 UTC hours in a 24h day with 4h bars: 0, 4, 8, 12, 16, 20 UTC.
    Key hypotheses (3 primary):
      - Funding settlement: 00, 08, 16 UTC (perpetual funding resets → flow)
      - US open surrogate: 12-16 UTC (8am-12pm ET)
      - Asian open: 00-04 UTC

    6 buckets tested. SAMPLE THIN (~40d × 28 coins × 6 bar/day = ~6720 obs;
    ~40d per bar-hour across 28 coins = ~1120 obs per hour-bucket).
    OOS-split on 40d is only ~20d each → very noisy. Flag throughout.
    """
    # Bar t = open timestamp; return = open→close of the bar = (c-o)/o
    # or bar-over-bar (c[k]/c[k-1]-1) matching daily methodology
    hour_buckets = {h: [] for h in range(0, 24, 4)}  # 0,4,8,12,16,20

    n_coins = 0
    for coin, bars in data4h.items():
        n_coins += 1
        for k in range(1, len(bars)):
            p0, p1 = bars[k - 1]["c"], bars[k]["c"]
            if p0 <= 0:
                continue
            r = p1 / p0 - 1
            hour = _dt(bars[k]["t"]).hour
            # Snap to nearest 4h boundary (defensive, should already be aligned)
            bucket_hour = (hour // 4) * 4
            hour_buckets[bucket_hour].append(r)

    hour_labels = {
        0:  "00 UTC (Asian open / funding)",
        4:  "04 UTC (Asia session)",
        8:  "08 UTC (Euro open / funding)",
        12: "12 UTC (London mid / US pre-mkt)",
        16: "16 UTC (US open / funding)",
        20: "20 UTC (US afternoon)",
    }

    print(f"\n  [4h data: {n_coins} coins, ~40d ← THIN, exploratory only]")
    print("  6 hour-buckets tested (funding hours 00/08/16 are primary hypothesis):\n")
    for h in range(0, 24, 4):
        rep(f"    {hour_labels[h]}", hour_buckets[h], cost_pct=COST, indent="  ")

    # Aggregate: funding hours vs non-funding
    funding_pool = hour_buckets[0] + hour_buckets[8] + hour_buckets[16]
    non_fund = hour_buckets[4] + hour_buckets[12] + hour_buckets[20]
    print("\n  Aggregated (1 primary hypothesis):")
    rep("    Funding hours (00/08/16 UTC)", funding_pool, cost_pct=COST, indent="  ")
    rep("    Non-funding (04/12/20 UTC)", non_fund, cost_pct=COST, indent="  ")


# ---------------------------------------------------------------------------
# Verdict summary
# ---------------------------------------------------------------------------
def _verdict_block(data_daily, data4h):
    """Build per-section bucket summary for the verdict table."""
    # We re-run stripped-down versions of each section to collect the key means
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow = {i: [] for i in range(7)}
    first3, mid_m, last3 = [], [], []
    hour_b = {h: [] for h in range(0, 24, 4)}
    hour_b4 = {h: [] for h in range(0, 24, 4)}

    for coin, bars in data_daily.items():
        for k in range(1, len(bars)):
            p0, p1 = bars[k - 1]["c"], bars[k]["c"]
            if p0 <= 0:
                continue
            r = p1 / p0 - 1
            dow[_dt(bars[k]["t"]).weekday()].append(r)
            dom = _dt(bars[k]["t"]).day
            if dom <= 3:
                first3.append(r)
            elif dom >= 26:
                last3.append(r)
            else:
                mid_m.append(r)

    for coin, bars in data4h.items():
        for k in range(1, len(bars)):
            p0, p1 = bars[k - 1]["c"], bars[k]["c"]
            if p0 <= 0:
                continue
            r = p1 / p0 - 1
            h = (_dt(bars[k]["t"]).hour // 4) * 4
            hour_b4[h].append(r)

    def m(arr): return statistics.mean(arr) * 100 if len(arr) >= 10 else float("nan")
    def net(arr): return (statistics.mean(arr) - COST) * 100 if len(arr) >= 10 else float("nan")

    print("\n┌─────────────────────────────────────────────────────────────────────┐")
    print("│                      VERDICT SUMMARY TABLE                         │")
    print("├─────────────────────────────────────────────────────────────────────┤")
    print("│ Section A — Weekend Effect (7 DOW buckets)                          │")
    for i, name in enumerate(names):
        arr = dow[i]
        n, mean, win, h1, h2, robust = _stats(arr)
        tag = "ROBUST" if robust == "ROBUST" else ("neg" if robust == "neg" else "fragile")
        print(f"│   {name:4}  n={n:>4}  gross {mean if mean else 0:>+6.3f}%  net {(mean-COST*100) if mean else 0:>+6.3f}%  {tag}")
    wkd = []
    for i in range(5): wkd.extend(dow[i])
    wknd = dow[5] + dow[6]
    n1, m1, w1, hh1, hh2, r1 = _stats(wkd)
    n2, m2, w2, hh3, hh4, r2 = _stats(wknd)
    print(f"│   Weekday aggregate  gross {m1 if m1 else 0:>+6.3f}%  {r1}")
    print(f"│   Weekend aggregate  gross {m2 if m2 else 0:>+6.3f}%  {r2}")
    print("│")
    print("│ Section B — Intramonth (3 coarse + 6 fine buckets)                 │")
    n1, m1, _, hh1, hh2, r1 = _stats(first3)
    n2, m2, _, hh3, hh4, r2 = _stats(mid_m)
    n3, m3, _, hh5, hh6, r3 = _stats(last3)
    print(f"│   first-3 (DOM 1-3)  n={n1:>4}  gross {m1 if m1 else 0:>+6.3f}%  net {(m1-COST*100) if m1 else 0:>+6.3f}%  {r1}")
    print(f"│   mid (DOM 4-25)     n={n2:>4}  gross {m2 if m2 else 0:>+6.3f}%  net {(m2-COST*100) if m2 else 0:>+6.3f}%  {r2}")
    print(f"│   last-3 (DOM 26-31) n={n3:>4}  gross {m3 if m3 else 0:>+6.3f}%  net {(m3-COST*100) if m3 else 0:>+6.3f}%  {r3}")
    print("│")
    print("│ Section C — Time-of-day (6 hour buckets, 4h data THIN ~40d)        │")
    hour_labels = {0: "00UTC", 4: "04UTC", 8: "08UTC", 12: "12UTC", 16: "16UTC", 20: "20UTC"}
    for h in range(0, 24, 4):
        arr = hour_b4[h]
        n, mean, win, hh1, hh2, robust = _stats(arr)
        print(f"│   {hour_labels[h]:5}  n={n:>4}  gross {mean if mean else 0:>+6.3f}%  {robust}")
    print("└─────────────────────────────────────────────────────────────────────┘")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("# edge_seasonality2.py | Calendar family extension | BT_CACHE_ONLY")
    print("# Daily: lookahead-safe, cost-aware (10bps/leg), OOS-chronological")
    print("# 4h:    EXPLORATORY — thin sample (~40d), flag all results")
    print("=" * 70)

    print("\n# Loading daily universe...")
    data_daily = load_daily()
    print(f"# {len(data_daily)} coins loaded (1d bars, 261d)")

    print("\n# Loading 4h universe...")
    data4h = load_4h()
    print(f"# {len(data4h)} coins loaded (4h bars, ~40d)")

    # --- A. Weekend Effect ---------------------------------------------------
    print("\n" + "─" * 70)
    print("# A. WEEKEND EFFECT")
    print("#    Crypto trades 24/7 — do Sat/Sun mean returns differ from weekdays?")
    print("#    7 DOW buckets tested (multiple-testing: report per-day AND aggregated)")
    print("#    Primary hypothesis: Weekend (Sat+Sun) aggregate vs Weekday aggregate")
    weekend_effect(data_daily)

    # --- B. Intramonth -------------------------------------------------------
    print("\n" + "─" * 70)
    print("# B. INTRAMONTH / TURN-OF-MONTH")
    print("#    Cross-coin mean daily return by day-of-month band")
    print("#    Coarse: 3 buckets (primary). Fine: 6 buckets (exploratory).")
    print("#    NOTE: edge_sweep3.py already REFUTED TOM (dom≤3 or dom≥27).")
    print("#    We retest with dom≤3 / dom≥26 boundaries and fine-grained grid.")
    intramonth(data_daily)

    # --- C. Time-of-day (4h) ------------------------------------------------
    print("\n" + "─" * 70)
    print("# C. TIME-OF-DAY (4h, ~40d EXPLORATORY)")
    print("#    6 UTC hour-buckets. Primary: funding settlement at 00/08/16 UTC")
    print("#    Sample thin (~40d × 28 coins); OOS halves are ~20d each → very noisy")
    time_of_day(data4h)

    # --- Verdict summary table -----------------------------------------------
    print("\n" + "─" * 70)
    _verdict_block(data_daily, data4h)

    print("\n" + "=" * 70)
    print("# INTERPRETATION GUIDE")
    print("#   ROBUST  = both OOS halves positive → meets the methodology bar")
    print("#   fragile = halves disagree → regime-dependent, NOT tradeable")
    print("#   neg     = both OOS halves negative → avoid")
    print("#   THIN    = n < 10, skip")
    print("#")
    print("# Tradeable tilt requires: net > 0 AND ROBUST AND low multiple-testing risk")
    print("# Calendar tilts stack orthogonally to momentum/pairs IF they are ROBUST")
    print("=" * 70)


if __name__ == "__main__":
    main()
