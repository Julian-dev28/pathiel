"""Lane POLITICS — Polymarket political markets, trended in probability space.

A political market's "price" is a probability, so the trend questions change
shape: drift is measured in percentage POINTS per day, and the honest null is
a martingale (today's price is the best forecast of next week's price). This
module measures whether that null holds on the live sample instead of assuming
it, then forecasts accordingly.

`momentum_test()` is the pattern hunt: across every sampled market, does the
change over the PREVIOUS week predict the change over the LAST week? If the
correlation is flat, weekly drift is noise and any "trend" on this board is
storytelling. If it is negative, weekly moves overshoot and fade. Either way
the number is on the tab.
"""
from __future__ import annotations

import concurrent.futures
import json
import math
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from services.trend_engine.metrics import (
    correlation, efficiency_ratio, linear_slope, mean, stdev, wilson,
)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
POLITICS_TAG = "2"
HOUR = 3600
DAY = 86400
MAX_WORKERS = 6
DEFAULT_MARKETS = 30
MIN_VOLUME = 50_000.0


def _curl(url: str, timeout: float = 20.0) -> Any:
    try:
        out = subprocess.run(["curl", "-s", "--max-time", str(int(timeout)), url],
                             capture_output=True, text=True, timeout=timeout + 5)
        return json.loads(out.stdout)
    except Exception:
        return None


# ── fetch ────────────────────────────────────────────────────────────────────


def fetch_markets(limit: int = DEFAULT_MARKETS, min_volume: float = MIN_VOLUME,
                  getter: Optional[Callable[[str], Any]] = None) -> List[Dict[str, Any]]:
    """Liquid, open, binary political markets ordered by 24h volume.

    Events (not markets) are the fetch unit because only the event payload
    carries `tags` — the same constraint the scout hit. Multi-outcome events
    contribute each leg separately; a leg without a YES/NO pair is dropped
    because "probability of YES" is the only quantity this lane trends.
    """
    get = getter or _curl
    rows: List[Dict[str, Any]] = []
    for off in (0, 100):
        evs = get(f"{GAMMA}/events?closed=false&limit=100&offset={off}"
                  f"&order=volume24hr&ascending=false&tag_id={POLITICS_TAG}")
        if not isinstance(evs, list):
            break
        for ev in evs:
            if not isinstance(ev, dict):
                continue
            for m in ev.get("markets") or []:
                if not isinstance(m, dict) or m.get("closed") or m.get("archived"):
                    continue
                try:
                    toks = json.loads(m.get("clobTokenIds") or "[]")
                    prices = json.loads(m.get("outcomePrices") or "[]")
                except Exception:
                    continue
                vol = float(m.get("volumeNum") or 0.0)
                if len(toks) != 2 or vol < min_volume:
                    continue
                rows.append({
                    "market_id": str(m.get("id") or ""),
                    "event": str(ev.get("title") or ""),
                    "question": str(m.get("question") or ""),
                    "slug": str(m.get("slug") or ""),
                    "yes_token": toks[0],
                    "px_yes": float(prices[0]) if prices else None,
                    "volume": vol,
                    "volume_24h": float(m.get("volume24hr") or ev.get("volume24hr") or 0.0),
                    "liquidity": float(m.get("liquidityNum") or 0.0),
                    "end_date": str(m.get("endDate") or ""),
                    "gamma_1d": m.get("oneDayPriceChange"),
                    "gamma_1w": m.get("oneWeekPriceChange"),
                    "tags": [t.get("slug") for t in (ev.get("tags") or []) if isinstance(t, dict)],
                })
        if len(rows) >= limit * 3:
            break
    rows.sort(key=lambda r: r["volume_24h"], reverse=True)
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for r in rows:                                   # one leg per event, best volume first
        if r["event"] in seen:
            continue
        seen.add(r["event"])
        out.append(r)
        if len(out) >= limit:
            break
    return out


def fetch_history(token_id: str, days: int = 30,
                  getter: Optional[Callable[[str], Any]] = None) -> List[Dict[str, float]]:
    """Hourly YES-price series for one token, oldest first."""
    get = getter or _curl
    interval = "1m" if days <= 31 else "max"
    raw = get(f"{CLOB}/prices-history?market={token_id}&interval={interval}&fidelity=60")
    hist = raw.get("history") if isinstance(raw, dict) else raw
    if not isinstance(hist, list):
        return []
    cutoff = time.time() - days * DAY
    out = [{"t": int(h["t"]), "p": float(h["p"])} for h in hist
           if isinstance(h, dict) and h.get("t") and h.get("p") is not None
           and int(h["t"]) >= cutoff]
    out.sort(key=lambda h: h["t"])
    return out


# ── pure: one market ─────────────────────────────────────────────────────────


def _at(hist: Sequence[Dict[str, float]], ts: float) -> Optional[float]:
    """Last observed price at or before `ts` (None if the series starts later)."""
    prev = None
    for h in hist:
        if h["t"] <= ts:
            prev = h["p"]
        else:
            break
    return prev


def market_read(row: Dict[str, Any], hist: Sequence[Dict[str, float]],
                now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """7-day probability trend for one market.

    Everything is in percentage POINTS. A market moving 0.30 -> 0.42 moved
    +12pp, which is the unit political traders actually think in — a "+40%
    move" on the same market is a true statement that helps nobody.
    """
    now = time.time() if now is None else now
    if len(hist) < 8:
        return None
    ps = [h["p"] for h in hist]
    p_now = ps[-1]
    p_1d = _at(hist, now - DAY)
    p_7d = _at(hist, now - 7 * DAY)
    p_14d = _at(hist, now - 14 * DAY)
    # a THIRD window, one week older, so the null test below can correlate two
    # changes that share no endpoint price (see momentum_test)
    p_21d = _at(hist, now - 21 * DAY)
    if p_7d is None:
        return None

    week = [h["p"] for h in hist if h["t"] >= now - 7 * DAY]
    hourly_changes = [week[i] - week[i - 1] for i in range(1, len(week))]
    # slope in pp/day: linear OLS on hourly points (probability space is
    # additive — a log fit would distort moves near 0 and 1)
    slope_h, r2 = linear_slope(week) if len(week) >= 4 else (0.0, 0.0)
    slope_pp_day = slope_h * 24 * 100
    eff = efficiency_ratio(week) if len(week) >= 3 else 0.0

    end_ms = _parse_end(row.get("end_date"))
    days_left = round((end_ms / 1000 - now) / DAY, 1) if end_ms else None

    delta_7d = (p_now - p_7d) * 100
    read = {
        **{k: row[k] for k in ("market_id", "event", "question", "slug", "volume",
                               "volume_24h", "liquidity", "end_date", "tags")},
        "p_now": round(p_now, 4),
        "p_1d": round(p_1d, 4) if p_1d is not None else None,
        "p_7d": round(p_7d, 4),
        "p_14d": round(p_14d, 4) if p_14d is not None else None,
        "delta_1d_pp": round((p_now - p_1d) * 100, 2) if p_1d is not None else None,
        "delta_7d_pp": round(delta_7d, 2),
        "delta_prev_week_pp": round((p_7d - p_14d) * 100, 2) if p_14d is not None else None,
        "delta_gap_week_pp": (round((p_14d - p_21d) * 100, 2)
                              if (p_14d is not None and p_21d is not None) else None),
        "slope_pp_day": round(slope_pp_day, 3),
        "r2": round(r2, 3),
        "efficiency": round(eff, 3),
        "vol_pp_hour": round(stdev(hourly_changes) * 100, 3) if len(hourly_changes) > 2 else 0.0,
        "high_7d": round(max(week), 4) if week else None,
        "low_7d": round(min(week), 4) if week else None,
        "days_left": days_left,
        "points": len(hist),
        # ~28 evenly-spaced points of the week for the tab's sparkline
        "spark": [round(week[i], 4) for i in
                  range(0, len(week), max(1, len(week) // 28))][:28] if week else [],
    }
    read["label"] = trend_label_pp(delta_7d, eff, read["vol_pp_hour"])
    read["forecast"] = project_prob(read)
    return read


def _parse_end(iso: str) -> Optional[int]:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None


def trend_label_pp(delta_7d_pp: float, eff: float, vol_pp_hour: float) -> str:
    """Label a probability path. CHURN is the important one: a market that
    moved 15pp and came back is not trending, it is being fought over."""
    if abs(delta_7d_pp) < 2.0:
        return "STABLE"
    if eff < 0.2:
        return "CHURN"
    if delta_7d_pp > 0:
        return "DRIFTING_YES" if delta_7d_pp < 10 else "REPRICING_YES"
    return "DRIFTING_NO" if delta_7d_pp > -10 else "REPRICING_NO"


def project_prob(read: Dict[str, Any], carry: float = 0.0) -> Dict[str, Any]:
    """Next-week probability projection.

    Default `carry=0` is the martingale: today's price IS the forecast. The
    band comes from the market's own realised hourly vol scaled to a week,
    clipped to [0.01, 0.99]. `carry` is only ever set from a measured
    momentum coefficient (see `momentum_test`) — never from a prior.
    """
    p = float(read["p_now"])
    vol_h = float(read.get("vol_pp_hour") or 0.0) / 100.0
    sigma_w = vol_h * math.sqrt(7 * 24)
    drift = carry * (float(read.get("delta_7d_pp") or 0.0) / 100.0)
    p50 = min(0.99, max(0.01, p + drift))
    return {
        "p50": round(p50, 4),
        "p10": round(min(0.99, max(0.01, p50 - 1.2816 * sigma_w)), 4),
        "p90": round(min(0.99, max(0.01, p50 + 1.2816 * sigma_w)), 4),
        "sigma_week_pp": round(sigma_w * 100, 2),
        "carry": carry,
        "model": "martingale" if carry == 0 else f"momentum carry {carry:+.2f}",
    }


# ── pure: cross-section ──────────────────────────────────────────────────────


def _corr_block(pairs: Sequence[Tuple[float, float]]) -> Dict[str, Any]:
    xs = [a for a, _ in pairs]
    ys = [b for _, b in pairs]
    r = correlation(xs, ys)
    mx, my = mean(xs), mean(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    carry = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx) if vx > 0 else 0.0
    n = len(pairs)
    if n <= 3:
        z = 0.0
    elif abs(r) >= 0.999:
        # Fisher's transform diverges at |r|=1. A degenerate perfect fit is
        # maximally significant, not insignificant — clamping it to 0 would
        # have made the guard below silently pass a fabricated relationship.
        z = math.copysign(99.0, r)
    else:
        z = 0.5 * math.log((1 + r) / (1 - r)) * math.sqrt(n - 3)
    return {"n": n, "corr": round(r, 4), "carry_coef": round(carry, 4),
            "fisher_z": round(z, 2)}


def momentum_test(reads: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Does last week's move predict this week's move? The lane's null test.

    Correlation of (t-14d -> t-7d) change against (t-7d -> now) change across
    every market carrying both windows, plus the OLS carry coefficient.

    THE MEASUREMENT IS GAPPED. The obvious design — correlate (t-14d -> t-7d)
    against (t-7d -> now) — shares the t-7d price between the two windows, so
    noise in that single observation enters one change positively and the other
    negatively and manufactures a negative correlation out of nothing. This
    uses (t-21d -> t-14d) against (t-7d -> now) instead: adjacent weeks, no
    shared endpoint, no built-in sign.

    Two further guards before the coefficient touches a forecast:

    1. `robust` requires the sign to survive a split by liquidity. An effect
       that lives only in the thin half is a spread artifact.
    2. n >= 25, because one weekly snapshot of a few dozen noisy prices is a
       small sample no matter how clean the correlation looks.

    `usable` is the conjunction. Only a usable carry bends `project_prob`.
    The ungapped version is still reported as `shared_endpoint_corr` so the
    difference between the two is visible rather than asserted.
    """
    def paired(xkey: str) -> List[Tuple[float, float]]:
        return [(float(r[xkey]), float(r["delta_7d_pp"])) for r in reads
                if r.get(xkey) is not None and r.get("delta_7d_pp") is not None]

    pairs = paired("delta_gap_week_pp")
    gapped = True
    if len(pairs) < 8:                       # not enough 21-day history yet
        pairs = paired("delta_prev_week_pp")
        gapped = False
    if len(pairs) < 8:
        return {"status": "too_small", "n": len(pairs)}
    full = _corr_block(pairs)
    xkey = "delta_gap_week_pp" if gapped else "delta_prev_week_pp"

    by_liq = sorted((r for r in reads
                     if r.get(xkey) is not None and r.get("delta_7d_pp") is not None),
                    key=lambda r: float(r.get("liquidity") or 0.0))
    half = len(by_liq) // 2
    halves = {}
    for name, rows in (("thin", by_liq[:half]), ("deep", by_liq[half:])):
        if len(rows) >= 6:
            halves[name] = _corr_block([(float(r[xkey]), float(r["delta_7d_pp"]))
                                        for r in rows])
    robust = (len(halves) == 2
              and halves["thin"]["corr"] * halves["deep"]["corr"] > 0
              and halves["deep"]["corr"] * full["corr"] > 0)
    significant = bool(abs(full["fisher_z"]) >= 1.96)
    # An ungapped measurement is confounded by construction: never usable.
    usable = bool(significant and robust and gapped and full["n"] >= 25)
    shared = _corr_block(paired("delta_prev_week_pp")) if len(paired("delta_prev_week_pp")) >= 8 else None
    return {
        "status": "ok",
        **full,
        "gapped": gapped,
        "windows": ("t-21d→t-14d vs t-7d→now" if gapped else "t-14d→t-7d vs t-7d→now"),
        "shared_endpoint_corr": (shared or {}).get("corr"),
        "halves": halves,
        "significant": significant,
        "robust": robust,
        "usable": usable,
        "verdict": (
            "weekly moves are a martingale — last week's drift says nothing about next week's"
            if not significant else
            (("weekly moves CONTINUE — drift carries" if full["corr"] > 0 else
              "weekly moves OVERSHOOT and fade — drift reverses") if usable else
             ("correlation measured on OVERLAPPING windows (not enough 21-day history) — "
              "the shared t-7d price manufactures this sign; treat as martingale"
              if not gapped else
              "correlation present but not robust across the liquidity split — treat as martingale"))),
    }


def longshot_test(reads: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Do longshots decay and favourites converge over a week?

    The one structural effect prediction markets are supposed to show. Measured
    on this sample, not assumed: mean 7d change of markets that started the
    week under 15% vs those that started over 85%.
    """
    low = [r for r in reads if r.get("p_7d") is not None and float(r["p_7d"]) <= 0.15]
    high = [r for r in reads if r.get("p_7d") is not None and float(r["p_7d"]) >= 0.85]
    mid = [r for r in reads if r.get("p_7d") is not None and 0.15 < float(r["p_7d"]) < 0.85]

    def blk(rows: Sequence[Dict[str, Any]], name: str) -> Dict[str, Any]:
        d = [float(r["delta_7d_pp"]) for r in rows if r.get("delta_7d_pp") is not None]
        k = sum(1 for x in d if x > 0)
        lo, hi = wilson(k, len(d)) if d else (0.0, 1.0)
        return {"bucket": name, "n": len(d),
                "mean_delta_pp": round(mean(d), 2) if d else 0.0,
                "pct_up": round(k / len(d) * 100, 1) if d else 0.0,
                "ci_lo_pct_up": round(lo * 100, 1), "ci_hi_pct_up": round(hi * 100, 1)}

    rows = [blk(low, "longshot (<=15%)"), blk(mid, "contested (15-85%)"),
            blk(high, "favourite (>=85%)")]
    decay = rows[0]["mean_delta_pp"] < 0 and rows[2]["mean_delta_pp"] > 0
    return {"status": "ok", "rows": rows,
            "verdict": ("longshots decayed and favourites firmed this week — "
                        "the textbook shape" if decay else
                        "no clean longshot decay in this sample")}


def board(reads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate the political board: movers, churn, and where risk repriced."""
    if not reads:
        return {"status": "empty"}
    moved = sorted(reads, key=lambda r: -abs(float(r.get("delta_7d_pp") or 0)))
    labels: Dict[str, int] = {}
    for r in reads:
        labels[r["label"]] = labels.get(r["label"], 0) + 1
    deltas = [abs(float(r["delta_7d_pp"])) for r in reads if r.get("delta_7d_pp") is not None]
    return {
        "status": "ok",
        "n": len(reads),
        "median_abs_move_pp": round(sorted(deltas)[len(deltas) // 2], 2) if deltas else 0.0,
        "mean_abs_move_pp": round(mean(deltas), 2) if deltas else 0.0,
        "label_counts": labels,
        "top_movers": [{"question": r["question"][:90], "p_now": r["p_now"],
                        "delta_7d_pp": r["delta_7d_pp"], "label": r["label"],
                        "days_left": r["days_left"]} for r in moved[:8]],
        "resolving_soon": [{"question": r["question"][:90], "p_now": r["p_now"],
                            "days_left": r["days_left"], "delta_7d_pp": r["delta_7d_pp"]}
                           for r in sorted((x for x in reads if x.get("days_left") is not None
                                            and 0 <= x["days_left"] <= 14),
                                           key=lambda r: r["days_left"])[:8]],
    }


def observations(reads: Sequence[Dict[str, Any]], brd: Dict[str, Any],
                 mom: Dict[str, Any], ls: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    if brd.get("status") != "ok":
        return out
    out.append(f"{brd['n']} liquid political markets scanned; the median one moved "
               f"{brd['median_abs_move_pp']:.1f}pp this week. "
               f"{brd['label_counts'].get('STABLE', 0)} went nowhere, "
               f"{brd['label_counts'].get('CHURN', 0)} churned without trending.")
    if mom.get("status") == "ok":
        out.append(f"Weekly-drift test: corr {mom['corr']:+.2f} (z {mom['fisher_z']:+.2f}, "
                   f"n={mom['n']}) — {mom['verdict']}.")
    if ls.get("status") == "ok":
        r = {x["bucket"]: x for x in ls["rows"]}
        out.append("Longshot check: <=15% bucket "
                   f"{r['longshot (<=15%)']['mean_delta_pp']:+.1f}pp avg (n={r['longshot (<=15%)']['n']}), "
                   f">=85% bucket {r['favourite (>=85%)']['mean_delta_pp']:+.1f}pp "
                   f"(n={r['favourite (>=85%)']['n']}) — {ls['verdict']}.")
    if brd.get("top_movers"):
        m = brd["top_movers"][0]
        out.append(f"Biggest repricing: \"{m['question']}\" {m['delta_7d_pp']:+.1f}pp to "
                   f"{m['p_now']*100:.0f}% ({m['label'].lower().replace('_', ' ')}).")
    soon = brd.get("resolving_soon") or []
    if soon:
        out.append("Resolving inside two weeks: "
                   + "; ".join(f"{s['question'][:48]} at {s['p_now']*100:.0f}% "
                               f"({s['days_left']:.0f}d)" for s in soon[:3]) + ".")
    return out


# ── lane entry point ─────────────────────────────────────────────────────────


def read(limit: int = DEFAULT_MARKETS, min_volume: float = MIN_VOLUME,
         days: int = 30, getter: Optional[Callable[[str], Any]] = None,
         now: Optional[float] = None) -> Dict[str, Any]:
    """Full politics lane: markets -> histories -> reads -> null tests -> board."""
    t0 = time.time()
    rows = fetch_markets(limit, min_volume, getter=getter)
    if not rows:
        return {"status": "no_data", "generated_at": int(time.time())}

    hists: Dict[str, List[Dict[str, float]]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(fetch_history, r["yes_token"], days, getter): r["market_id"]
                for r in rows}
        for fut in concurrent.futures.as_completed(futs):
            try:
                hists[futs[fut]] = fut.result()
            except Exception:
                hists[futs[fut]] = []

    reads = [x for x in (market_read(r, hists.get(r["market_id"]) or [], now=now) for r in rows)
             if x]
    # Gamma lists some markets as open past their own end date. They cannot
    # move any more, so they would pollute every drift statistic here.
    expired = [r for r in reads if r.get("days_left") is not None and r["days_left"] < 0]
    reads = [r for r in reads if r.get("days_left") is None or r["days_left"] >= 0]
    mom = momentum_test(reads)
    ls = longshot_test(reads)
    # only a MEASURED, significant, ROBUST carry is allowed to bend the forecast
    carry = max(-0.5, min(0.5, mom["carry_coef"])) if mom.get("usable") else 0.0
    for r in reads:
        r["forecast"] = project_prob(r, carry=carry)
    brd = board(reads)
    return {
        "status": "ok" if reads else "no_data",
        "generated_at": int(time.time()),
        "elapsed_s": round(time.time() - t0, 2),
        "scanned": len(reads),
        "expired_dropped": len(expired),
        "board": brd,
        "momentum_test": mom,
        "longshot_test": ls,
        "reads": sorted(reads, key=lambda r: -abs(float(r.get("delta_7d_pp") or 0))),
        "observations": observations(reads, brd, mom, ls),
    }
