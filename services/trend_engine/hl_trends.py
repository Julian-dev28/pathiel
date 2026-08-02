"""Lane HL — 7-day trend read per coin, market regime, next-week forecast.

Split cleanly in two:
  pure      : `coin_read`, `regime`, `rank` — dicts in, dicts out, no network.
  fetch     : `scan` — pulls daily candles for the liquid universe, then calls
              the pure half. Bounded concurrency because the HL info endpoint
              is rate-limited and this repo has burned that budget before.

Nothing here places, sizes, or persists anything.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.trend_engine import env
from services.trend_engine import flags as flagmod
from services.trend_engine.forecast import consensus, project
from services.trend_engine.metrics import (
    atr_pct, beta, correlation, daily_returns, efficiency_ratio, ema,
    ema_stack, log_slope, max_drawdown_pct, mean, pct_change, range_position,
    sharpe_like, stdev, streak, summarize, trend_label, trend_score, zscore,
)

MAJORS: Tuple[str, ...] = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK")
BENCH = "BTC"
DEFAULT_DAYS = 60          # candles pulled per coin (30d vol + 7d trend + slack)
DEFAULT_TOP_N = 40         # coins scanned, by 24h notional volume
MAX_WORKERS = 4            # HL info endpoint is rate-limited — do not raise blindly
MIN_VOL_USD = 2_000_000.0


def _c(bar: Any, k: str) -> float:
    return float(bar[k] if isinstance(bar, dict) else getattr(bar, k))


def closes_of(bars: Sequence[Any]) -> List[float]:
    return [_c(b, "c") for b in bars if _c(b, "c") > 0]


# ── pure: one coin ───────────────────────────────────────────────────────────


def coin_read(coin: str, bars: Sequence[Any], bench_bars: Optional[Sequence[Any]] = None,
              uni_row: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Full 7d-centred trend read for one coin from daily bars (oldest first).

    Returns None when there is not enough history to fit a 7-day trend AND a
    30-day vol — a partial read would look identical to a real one on the tab,
    which is the failure mode worth refusing.
    """
    cs = closes_of(bars)
    if len(cs) < 10:
        return None
    px = cs[-1]
    w7 = cs[-8:] if len(cs) >= 8 else cs
    slope, r2 = log_slope(w7)
    eff = efficiency_ratio(w7)
    stack = ema_stack(cs)
    rets30 = daily_returns(cs[-31:])
    sigma_day = stdev(rets30)

    ret_7d = pct_change(cs[-8], px) if len(cs) >= 8 else None
    read: Dict[str, Any] = {
        "coin": coin,
        "px": px,
        "ret_1d": pct_change(cs[-2], px) if len(cs) >= 2 else None,
        "ret_3d": pct_change(cs[-4], px) if len(cs) >= 4 else None,
        "ret_7d": ret_7d,
        "ret_14d": pct_change(cs[-15], px) if len(cs) >= 15 else None,
        "ret_30d": pct_change(cs[-31], px) if len(cs) >= 31 else None,
        "slope_pct_day": round(slope, 4),
        "r2": round(r2, 4),
        "efficiency": round(eff, 4),
        "ema_stack": stack,
        "ema7": ema(cs, 7),
        "ema21": ema(cs, 21),
        "streak_days": streak(cs),
        "sigma_day_pct": round(sigma_day * 100.0, 3),
        "atr_pct": round(atr_pct(bars, 7), 3),
        "atr_pct_30d": round(atr_pct(bars, 30), 3),
        "range_pos_7d": range_position(cs, 7),
        "range_pos_30d": range_position(cs, 30),
        "high_7d": max(cs[-7:]),
        "low_7d": min(cs[-7:]),
        "drawdown_30d_pct": round(max_drawdown_pct(cs[-31:]), 2),
        "sharpe_30d": round(sharpe_like(rets30), 2),
        "bars": len(cs),
    }
    read["label"] = trend_label(slope, r2, eff, stack)
    read["score"] = trend_score(slope, r2, eff, ret_7d or 0.0)

    # relative-to-benchmark block (skipped for the benchmark itself)
    if bench_bars is not None and coin != BENCH:
        bcs = closes_of(bench_bars)
        if len(bcs) >= 10:
            br = daily_returns(bcs[-31:])
            cr = rets30
            b = beta(cr, br)
            bench_7d = pct_change(bcs[-8], bcs[-1]) if len(bcs) >= 8 else 0.0
            read["beta_btc"] = round(b, 3)
            read["corr_btc"] = round(correlation(cr, br), 3)
            if ret_7d is not None and bench_7d is not None:
                read["resid_7d"] = round(ret_7d - b * bench_7d, 3)
    else:
        read["beta_btc"] = 1.0
        read["corr_btc"] = 1.0
        read["resid_7d"] = 0.0

    if uni_row:
        vol = float(uni_row.get("dayNtlVlm") or 0.0)
        read["vol_24h_usd"] = vol
        read["open_interest"] = float(uni_row.get("openInterest") or 0.0)
        # HL `funding` is the hourly rate; annualise for a number humans read
        read["funding_apr_pct"] = round(float(uni_row.get("funding") or 0.0) * 24 * 365 * 100, 2)
        vols = [_c(b, "v") * _c(b, "c") for b in bars[-8:-1]]
        avg7 = mean(vols)
        read["volume_ratio"] = round(vol / avg7, 2) if avg7 > 0 else None

    read["forecast"] = project(cs, 7)
    return read


# ── pure: cross-section ──────────────────────────────────────────────────────


def attach_flags(reads: List[Dict[str, Any]], now_ms: Optional[int] = None,
                 unlocks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                 news: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
    """Add `flags` + `flag_bias` in place. Funding z is CROSS-SECTIONAL (a coin
    against today's universe), not a time-series z — one snapshot, no extra
    network, and it answers the question the tab asks: who is crowded *now*."""
    unlocks = flagmod.load_unlocks(now_ms=now_ms) if unlocks is None else unlocks
    news = flagmod.load_news(now_ms=now_ms) if news is None else news
    sample = [float(r["funding_apr_pct"]) for r in reads if r.get("funding_apr_pct") is not None]
    for r in reads:
        fz = None
        if r.get("funding_apr_pct") is not None and len(sample) >= 8:
            fz = round(zscore(float(r["funding_apr_pct"]), sample), 2)
            r["funding_z_xs"] = fz
        r["flags"] = flagmod.flags_for(r, unlocks=unlocks.get(r["coin"]),
                                       news=news.get(r["coin"]), funding_z=fz)
        r["flag_bias"] = round(flagmod.flag_bias(r["flags"]), 3)


def regime(reads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Market-level read: what the whole tape did this week, not one coin.

    Breadth and dispersion carry the message. A +5% BTC week with 30% breadth
    is a BTC week; the same move with 80% breadth is a market week, and those
    two demand opposite books.
    """
    if not reads:
        return {"status": "empty"}
    by_coin = {r["coin"]: r for r in reads}
    btc = by_coin.get(BENCH)
    r7 = [float(r["ret_7d"]) for r in reads if r.get("ret_7d") is not None]
    ups = [x for x in r7 if x > 0]
    above_ema = [r for r in reads if r.get("ema21") and r["px"] > r["ema21"]]
    labels: Dict[str, int] = {}
    for r in reads:
        labels[r["label"]] = labels.get(r["label"], 0) + 1
    trending = labels.get("STRONG_UP", 0) + labels.get("UP", 0) \
        + labels.get("DOWN", 0) + labels.get("STRONG_DOWN", 0)
    corrs = [float(r["corr_btc"]) for r in reads if r.get("corr_btc") is not None and r["coin"] != BENCH]
    fund = [float(r["funding_apr_pct"]) for r in reads if r.get("funding_apr_pct") is not None]
    resid = [float(r["resid_7d"]) for r in reads if r.get("resid_7d") is not None and r["coin"] != BENCH]

    breadth = len(ups) / len(r7) * 100.0 if r7 else 0.0
    btc_7d = float(btc.get("ret_7d") or 0.0) if btc else 0.0
    btc_label = btc.get("label") if btc else "unknown"
    trend_share = trending / len(reads) * 100.0

    if breadth >= 60 and btc_7d > 0:
        tone = "RISK_ON"
    elif breadth <= 40 and btc_7d < 0:
        tone = "RISK_OFF"
    else:
        tone = "MIXED"
    shape = "TRENDING" if trend_share >= 50 else "CHOPPY"
    label = f"{tone}_{shape}"

    ranked = rank(reads)
    return {
        "status": "ok",
        "label": label,
        "tone": tone,
        "shape": shape,
        "btc_ret_7d": round(btc_7d, 2),
        "btc_label": btc_label,
        "btc_px": btc.get("px") if btc else None,
        "eth_ret_7d": round(float(by_coin["ETH"]["ret_7d"]), 2) if by_coin.get("ETH") and by_coin["ETH"].get("ret_7d") is not None else None,
        "sol_ret_7d": round(float(by_coin["SOL"]["ret_7d"]), 2) if by_coin.get("SOL") and by_coin["SOL"].get("ret_7d") is not None else None,
        "breadth_pct": round(breadth, 1),
        "pct_above_ema21": round(len(above_ema) / len(reads) * 100.0, 1),
        "trend_share_pct": round(trend_share, 1),
        "dispersion_pct": round(stdev(r7), 2),
        "median_ret_7d": round(summarize(reads, "ret_7d")["med"], 2),
        "median_corr_btc": round(sorted(corrs)[len(corrs) // 2], 3) if corrs else None,
        "mean_funding_apr_pct": round(mean(fund), 2) if fund else None,
        "alt_strength_pct": round(mean(resid), 2) if resid else None,
        "label_counts": labels,
        "leaders": [{"coin": r["coin"], "ret_7d": round(float(r["ret_7d"] or 0), 2),
                     "score": r["score"], "label": r["label"]} for r in ranked[:5]],
        "laggards": [{"coin": r["coin"], "ret_7d": round(float(r["ret_7d"] or 0), 2),
                      "score": r["score"], "label": r["label"]} for r in ranked[-5:][::-1]],
        "n": len(reads),
        "consensus": consensus(reads),
    }


def rank(reads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reads sorted by trend score, strongest up first."""
    return sorted(reads, key=lambda r: float(r.get("score") or 0.0), reverse=True)


def observations(reads: List[Dict[str, Any]], reg: Dict[str, Any]) -> List[str]:
    """Deterministic plain-English lines about the week.

    These are the "observations" on the tab. They are generated from the same
    numbers shown next to them, so they can never drift from the data — the
    optional AI pass adds interpretation on top, never facts.
    """
    out: List[str] = []
    if reg.get("status") != "ok":
        return out
    ranked = rank(reads)
    out.append(
        f"Tape is {reg['label'].replace('_', ' ').lower()}: BTC {reg['btc_ret_7d']:+.1f}% "
        f"on the week, {reg['breadth_pct']:.0f}% of the scan green, "
        f"{reg['trend_share_pct']:.0f}% of coins in an actual trend (rest is chop).")
    if reg.get("alt_strength_pct") is not None:
        alt = reg["alt_strength_pct"]
        out.append(
            f"Alts are {'outrunning' if alt > 1 else ('lagging' if alt < -1 else 'tracking')} BTC "
            f"after beta ({alt:+.1f}% median residual) — "
            + ("rotation is live, relative-strength books work here."
               if alt > 1 else
               ("BTC is the only bid; alt longs are paying beta for nothing."
                if alt < -1 else "no rotation signal, index-like tape.")))
    # "strongest" must mean strongest TREND, not biggest number — a CHOP coin
    # topping the score table is a ranking artifact, not the week's leader
    ups = [r for r in ranked if r["label"] in ("STRONG_UP", "UP")]
    downs = [r for r in ranked if r["label"] in ("STRONG_DOWN", "DOWN")]
    if ups:
        t = ups[0]
        out.append(f"Strongest uptrend: {t['coin']} {float(t['ret_7d'] or 0):+.1f}% 7d, "
                   f"efficiency {t['efficiency']:.2f}, {t['label']}.")
    else:
        out.append("No coin in the scan holds a clean uptrend — every green name is chop.")
    if downs:
        b = downs[-1]
        out.append(f"Strongest downtrend: {b['coin']} {float(b['ret_7d'] or 0):+.1f}% 7d, "
                   f"efficiency {b['efficiency']:.2f}, {b['label']}.")
    chops = [r for r in reads if r["label"] == "CHOP" and abs(float(r.get("ret_7d") or 0)) >= 8]
    if chops:
        out.append("Round-trip traps (big weekly move, no trend): "
                   + ", ".join(f"{r['coin']} {float(r['ret_7d'] or 0):+.0f}%" for r in chops[:5]) + ".")
    ev = [r for r in reads if any(f["kind"] == "event" for f in r.get("flags") or [])]
    if ev:
        out.append("Dated catalysts inside the forecast window: "
                   + ", ".join(f"{r['coin']} ({(next(f for f in r['flags'] if f['kind'] == 'event'))['note']})"
                               for r in ev[:4]) + ".")
    if reg.get("mean_funding_apr_pct") is not None and abs(reg["mean_funding_apr_pct"]) >= 8:
        out.append(f"Funding across the scan averages {reg['mean_funding_apr_pct']:+.1f}% APR — "
                   + ("longs are paying to hold; crowded." if reg["mean_funding_apr_pct"] > 0
                      else "shorts are paying to hold; squeeze fuel."))
    return out


# ── fetch ────────────────────────────────────────────────────────────────────


def _universe_rows(top_n: int, min_vol: float) -> List[Dict[str, Any]]:
    from hermes_trader.client.universe import get_universe
    uni = [u for u in get_universe()
           if u.get("type") == "perp" and float(u.get("dayNtlVlm") or 0) >= min_vol]
    uni.sort(key=lambda u: float(u.get("dayNtlVlm") or 0), reverse=True)
    picked = uni[:top_n]
    have = {u["coin"] for u in picked}
    for u in uni:                                  # majors always in the scan
        if u["coin"] in MAJORS and u["coin"] not in have:
            picked.append(u)
            have.add(u["coin"])
    return picked


def _fetch_candles(coins: Sequence[str], days: int) -> Dict[str, List[Any]]:
    from hermes_trader.client.hl_client import fetch_hl_candles

    out: Dict[str, List[Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(fetch_hl_candles, c, "1d", days): c for c in coins}
        for fut in concurrent.futures.as_completed(futs):
            coin = futs[fut]
            try:
                bars = fut.result()
            except Exception:
                bars = []
            if bars:
                out[coin] = bars
    return out


def scan(top_n: int = DEFAULT_TOP_N, days: int = DEFAULT_DAYS,
         min_vol: float = MIN_VOL_USD, coins: Optional[Sequence[str]] = None,
         now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Full HL trend lane: universe -> candles -> reads -> regime -> forecasts.

    One network burst (universe + one candle call per coin, 4 at a time), then
    everything else is pure. Safe to call from a request handler behind a TTL.
    """
    t0 = time.time()
    rows = _universe_rows(top_n, min_vol)
    by_coin_row = {r["coin"]: r for r in rows}
    wanted = list(coins) if coins else list(by_coin_row.keys())
    if BENCH not in wanted:
        wanted.append(BENCH)
    candles = _fetch_candles(wanted, days)
    bench = candles.get(BENCH) or []

    reads: List[Dict[str, Any]] = []
    for coin in wanted:
        bars = candles.get(coin)
        if not bars:
            continue
        r = coin_read(coin, bars, bench, by_coin_row.get(coin))
        if r:
            reads.append(r)

    attach_flags(reads, now_ms=now_ms)
    reg = regime(reads)
    ranked = rank(reads)
    # The walk-forward is a separate, much heavier fetch (hundreds of daily
    # bars per coin), so it runs on its own slow cadence and is attached from
    # cache. A forecast column with no eval next to it is exactly the kind of
    # decoration this repo has been burned by.
    ev = load_eval()
    majors = [r for r in reads if r["coin"] in MAJORS]
    majors.sort(key=lambda r: MAJORS.index(r["coin"]))
    return {
        "status": "ok" if reads else "no_data",
        "generated_at": int(time.time()),
        "elapsed_s": round(time.time() - t0, 2),
        "scanned": len(reads),
        "regime": reg,
        "majors": majors,
        "reads": ranked,
        "observations": observations(reads, reg),
        "eval": ev,
        "closes": {r["coin"]: closes_of(candles[r["coin"]])[-30:] for r in reads
                   if candles.get(r["coin"])},
    }


EVAL_PATH = os.path.join(env.state_dir(), "trend_engine", "hl_eval.json")
EVAL_STALE_S = 86_400.0


def load_eval(path: str = EVAL_PATH, max_age_s: float = 7 * 86_400) -> Optional[Dict[str, Any]]:
    """Last saved walk-forward, or None when missing / older than `max_age_s`."""
    try:
        with open(path) as fh:
            ev = json.load(fh)
    except Exception:
        return None
    ts = float(ev.get("saved_at") or 0)
    if not ts or time.time() - ts > max_age_s:
        return None
    ev["age_s"] = round(time.time() - ts, 1)
    return ev


def save_eval(ev: Dict[str, Any], path: str = EVAL_PATH) -> str:
    ev = dict(ev)
    ev["saved_at"] = int(time.time())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(ev, fh)
    return path


def eval_is_stale(path: str = EVAL_PATH, max_age_s: float = EVAL_STALE_S) -> bool:
    return load_eval(path, max_age_s) is None


def backtest(top_n: int = 30, days: int = 180, horizon: int = 7) -> Dict[str, Any]:
    """Walk `project()` forward over the scanned universe — the lane's eval.

    Pulls a long daily history once and grades every anchor. This is the number
    that decides whether the forecast column on the tab is signal or decoration.
    """
    from services.trend_engine.forecast import walk_forward

    rows = _universe_rows(top_n, MIN_VOL_USD)
    coins = [r["coin"] for r in rows]
    candles = _fetch_candles(coins, days)
    series = {c: closes_of(b) for c, b in candles.items() if len(b) >= 40}
    res = walk_forward(series, horizon_days=horizon)
    res["coins"] = len(series)
    res["days"] = days
    return res
