"""Lane UPDOWN — Polymarket BTC 5m up/down, mined as conditional base rates.

The question the market asks is binary and repeats every 5 minutes, which makes
it the one place in this repo where a real sample accumulates fast: 21 days of
1m klines is ~6,000 resolved windows. That is enough to ask "is there a
condition under which UP is not a coin flip" and get an answer with an interval
around it instead of a vibe.

Resolution convention matches POLYMARKET'S OWN RULE, read off the market
description: "resolve to Up if the price at the end of the range is greater
than OR EQUAL TO the price at the beginning". So a flat window is UP here,
unlike `services/polymarket_scout/updown_backtest.window_outcome`, which uses
a strict `>`. Exact ties are rare (0.28% of a 21-day sample) but they move the
base rate by ~0.3pp, which is the same order as the edges being tested.

TWO CAVEATS THAT BOUND EVERYTHING BELOW, both surfaced on the tab:

1. Polymarket resolves these on the CHAINLINK BTC/USD data stream, not on
   Binance. These windows are mined from Binance 1m klines because that is the
   only free minute-resolution history available. The two feeds track each
   other closely but not tick-for-tick, so a small model/market gap is inside
   the feed's own noise and is NOT an edge.
2. The CLOB `/midpoint` endpoint has been observed disagreeing with the top of
   the book (mid 0.325 against a 0.27/0.28 book). Anything comparing the model
   to "the market" therefore reads the BOOK, and prices the side you would
   actually have to lift.

Windows are aligned to epoch multiples of 300s, the same grid the slugs use.

Every conditional carries n, a Wilson 95% interval, and an exact two-sided
binomial p against the sample's own base rate — not against 0.50, because the
base rate itself drifts with the tape. A bucket only becomes a "pattern" when
`significant` is true AND it survives the Bonferroni correction for the number
of buckets tested in its family.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from services.trend_engine import env
from services.trend_engine.metrics import binom_two_sided_p, mean, stdev, wilson

BINANCE = "https://api.binance.com/api/v3/klines"
WINDOW_MIN = 5
WINDOW_MS = WINDOW_MIN * 60_000
ET = ZoneInfo("America/New_York")
CACHE = os.path.join(env.state_dir(), "trend_engine", "btc_1m.json")
DEFAULT_MINUTES = 30_240          # 21 days
MIN_BUCKET_N = 60                 # below this a bucket is reported but never "significant"
# Minimum executable edge before the tab calls a live deviation actionable.
# Covers the Chainlink-vs-Binance feed gap (the market resolves on Chainlink,
# these windows are mined from Binance) plus the model's own ~1-2pp
# calibration error. Anything under this is noise wearing a number.
FEED_BUFFER = 0.05


# ── fetch (own client on purpose — no cross-service imports) ─────────────────


def _curl(url: str, timeout: float = 20.0) -> Any:
    try:
        out = subprocess.run(["curl", "-s", "--max-time", str(int(timeout)), url],
                             capture_output=True, text=True, timeout=timeout + 5)
        return json.loads(out.stdout)
    except Exception:
        return None


def fetch_1m(limit: int = 1000, end_ms: Optional[int] = None,
             runner: Optional[Callable[[str], Any]] = None) -> List[List[Any]]:
    """BTCUSDT 1m klines, oldest first within the batch."""
    url = f"{BINANCE}?symbol=BTCUSDT&interval=1m&limit={int(limit)}"
    if end_ms:
        url += f"&endTime={int(end_ms)}"
    raw = (runner or _curl)(url)
    return raw if isinstance(raw, list) else []


def load_1m(minutes: int = DEFAULT_MINUTES, runner: Optional[Callable] = None,
            cache_path: str = CACHE, use_cache: bool = True) -> List[List[Any]]:
    """Contiguous 1m klines covering ~`minutes`, oldest first, disk-cached.

    The cache is append-merge keyed on open time, so a refresh costs one batch
    instead of thirty. A gap in the cache (session gone for a day) is repaired
    by paging backward from the newest missing minute.
    """
    have: Dict[int, List[Any]] = {}
    if use_cache:
        try:
            with open(cache_path) as fh:
                for row in json.load(fh):
                    have[int(row[0])] = row
        except Exception:
            have = {}

    need_from = int(time.time() * 1000) - minutes * 60_000
    end: Optional[int] = None
    for _ in range(60):                                   # hard page cap
        batch = fetch_1m(1000, end_ms=end, runner=runner)
        if not batch:
            break
        for row in batch:
            have[int(row[0])] = row
        oldest = int(batch[0][0])
        if oldest <= need_from:
            break
        # already cached contiguously below this point? then stop paging
        if (oldest - 60_000) in have and (oldest - 120_000) in have:
            probe = oldest - 60_000
            while probe in have and probe > need_from:
                probe -= 60_000
            if probe <= need_from:
                break
            end = probe
            continue
        end = oldest - 1

    rows = [have[k] for k in sorted(have)]
    if use_cache and rows:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w") as fh:
                json.dump(rows[-max(minutes, DEFAULT_MINUTES):], fh)
        except Exception:
            pass
    return [r for r in rows if int(r[0]) >= need_from]


# ── pure: windows ────────────────────────────────────────────────────────────


def build_windows(bars: Sequence[Sequence[Any]]) -> List[Dict[str, Any]]:
    """Resolved 5m windows on the market's own grid, oldest first.

    Only windows with all 5 bars present are emitted — a partial window would
    silently bias the up-rate toward whichever side the missing bars were on.
    """
    by_t = {int(b[0]): b for b in bars}
    if not by_t:
        return []
    ts = sorted(by_t)
    starts = [t for t in ts if t % WINDOW_MS == 0]
    out: List[Dict[str, Any]] = []
    for s in starts:
        need = [s + i * 60_000 for i in range(WINDOW_MIN)]
        if any(t not in by_t for t in need):
            continue
        first, last = by_t[need[0]], by_t[need[-1]]
        o, c = float(first[1]), float(last[4])
        highs = [float(by_t[t][2]) for t in need]
        lows = [float(by_t[t][3]) for t in need]
        out.append({
            "t": s,
            "open": o,
            "close": c,
            "high": max(highs),
            "low": min(lows),
            "up": c >= o,                     # Polymarket resolves a tie UP
            "tie": c == o,
            "ret_bp": (c / o - 1.0) * 10_000 if o > 0 else 0.0,
            "range_bp": (max(highs) / min(lows) - 1.0) * 10_000 if min(lows) > 0 else 0.0,
            # per-minute closes, so an in-progress window can be replayed
            "c1m": [float(by_t[t][4]) for t in need],
        })
    return out


def _et(t_ms: int) -> datetime:
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).astimezone(ET)


def enrich(windows: List[Dict[str, Any]], vol_lookback: int = 12) -> List[Dict[str, Any]]:
    """Attach the conditioning variables each window is bucketed by.

    Everything here is strictly backward-looking as of the window's OPEN — the
    conditions must be knowable before the window resolves or the base rates
    are lookahead fiction.
    """
    for i, w in enumerate(windows):
        dt = _et(w["t"])
        w["hour_et"] = dt.hour
        w["dow"] = dt.weekday()                      # 0 = Monday
        w["session"] = ("asia" if 19 <= dt.hour or dt.hour < 3 else
                        "europe" if dt.hour < 9 else
                        "us_am" if dt.hour < 13 else "us_pm")
        prev = windows[i - 1] if i > 0 else None
        w["prior_up"] = None if prev is None else prev["up"]
        w["prior_ret_bp"] = None if prev is None else prev["ret_bp"]
        # run length of identical directions ending at the previous window
        streak = 0
        if prev is not None:
            d = prev["up"]
            j = i - 1
            while j >= 0 and windows[j]["up"] == d:
                streak += 1
                j -= 1
            streak = streak if d else -streak
        w["prior_streak"] = streak
        hist = windows[max(0, i - vol_lookback):i]
        w["vol_bp"] = round(stdev([h["ret_bp"] for h in hist]), 2) if len(hist) >= 4 else None
        w["drift_bp"] = round(sum(h["ret_bp"] for h in hist), 2) if hist else None
    return windows


# ── pure: conditional base rates ─────────────────────────────────────────────


def conditional(windows: Sequence[Dict[str, Any]], key: Callable[[Dict[str, Any]], Any],
                family: str, base: Optional[float] = None,
                min_n: int = MIN_BUCKET_N) -> Dict[str, Any]:
    """Up-rate per bucket of one conditioning variable, with intervals.

    `significant` = the exact binomial p against the sample base rate clears
    0.05 AFTER Bonferroni over the buckets in this family. One family = one
    hypothesis test, which is the correction this repo has been burned for
    skipping (best-of-168 numerology).
    """
    buckets: Dict[Any, List[bool]] = {}
    for w in windows:
        k = key(w)
        if k is None:
            continue
        buckets.setdefault(k, []).append(bool(w["up"]))
    if base is None:
        allw = [bool(w["up"]) for w in windows]
        base = (sum(allw) / len(allw)) if allw else 0.5
    m = max(1, len(buckets))
    rows: List[Dict[str, Any]] = []
    for k, vals in buckets.items():
        n = len(vals)
        up = sum(vals)
        p = binom_two_sided_p(up, n, base)
        lo, hi = wilson(up, n)
        rows.append({
            "bucket": k if isinstance(k, (str, int, float)) else str(k),
            "n": n,
            "up": up,
            "rate": round(up / n, 4) if n else 0.0,
            "lift_pp": round((up / n - base) * 100, 2) if n else 0.0,
            "ci_lo": round(lo, 4), "ci_hi": round(hi, 4),
            "p": round(p, 5),
            "p_bonf": round(min(1.0, p * m), 5),
            "significant": bool(n >= min_n and p * m < 0.05),
        })
    rows.sort(key=lambda r: -abs(r["lift_pp"]))
    return {"family": family, "base": round(base, 4), "buckets_tested": m, "rows": rows}


def _vol_bucket(w: Dict[str, Any], cuts: Tuple[float, float, float]) -> Optional[str]:
    v = w.get("vol_bp")
    if v is None:
        return None
    lo, mid, hi = cuts
    return "vol_q1" if v <= lo else ("vol_q2" if v <= mid else ("vol_q3" if v <= hi else "vol_q4"))


def _mag_bucket(w: Dict[str, Any]) -> Optional[str]:
    r = w.get("prior_ret_bp")
    if r is None:
        return None
    a = abs(r)
    tag = "up" if r > 0 else "down"
    size = "tiny" if a < 5 else ("small" if a < 15 else ("mid" if a < 40 else "big"))
    return f"prior_{tag}_{size}"


def _streak_bucket(w: Dict[str, Any]) -> Optional[str]:
    s = w.get("prior_streak")
    if not s:
        return None
    d = "up" if s > 0 else "down"
    n = min(abs(s), 4)
    return f"{d}_x{n}{'+' if abs(s) >= 4 else ''}"


def patterns(windows: Sequence[Dict[str, Any]],
             hl_daily_label: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Every conditioning family, each corrected inside itself.

    `hl_daily_label` maps an ET date string to the HL daily trend label for BTC
    (UP / DOWN / CHOP / ...). That is the cross-lane mix: does the 5-minute
    coin flip lean with the daily tape it lives inside?
    """
    ws = list(windows)
    if not ws:
        return {"status": "no_data"}
    allw = [bool(w["up"]) for w in ws]
    base = sum(allw) / len(allw)
    vols = sorted(w["vol_bp"] for w in ws if w.get("vol_bp") is not None)
    cuts = ((vols[len(vols) // 4], vols[len(vols) // 2], vols[3 * len(vols) // 4])
            if len(vols) >= 8 else (0.0, 0.0, 0.0))

    fams = [
        conditional(ws, lambda w: f"{w['hour_et']:02d}:00 ET", "hour_of_day", base),
        conditional(ws, lambda w: w["session"], "session", base),
        conditional(ws, lambda w: ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[w["dow"]],
                    "day_of_week", base),
        conditional(ws, lambda w: None if w["prior_up"] is None else ("prior_up" if w["prior_up"] else "prior_down"),
                    "prior_direction", base),
        conditional(ws, _streak_bucket, "prior_streak", base),
        conditional(ws, _mag_bucket, "prior_magnitude", base),
        conditional(ws, lambda w: _vol_bucket(w, cuts), "volatility_regime", base),
    ]
    if hl_daily_label:
        fams.append(conditional(
            ws, lambda w: hl_daily_label.get(_et(w["t"]).strftime("%Y-%m-%d")),
            "hl_daily_trend", base))

    hits = [r for f in fams for r in f["rows"] if r["significant"]]
    hits.sort(key=lambda r: -abs(r["lift_pp"]))
    return {
        "status": "ok",
        "n_windows": len(ws),
        "base_rate": round(base, 4),
        "base_ci": [round(x, 4) for x in wilson(sum(allw), len(allw))],
        "base_p_vs_coinflip": round(binom_two_sided_p(sum(allw), len(allw), 0.5), 5),
        "families": fams,
        "significant": hits,
        "verdict": ("no conditional beat the base rate after correction — the 5m window is a coin flip"
                    if not hits else
                    f"{len(hits)} conditional bucket(s) survived correction"),
    }


def rolling_trend(windows: Sequence[Dict[str, Any]], block: int = 288) -> List[Dict[str, Any]]:
    """Up-rate per block of consecutive windows (288 = one day of 5m bars).

    This is the "trend" of the binary itself — whether the tape has been
    printing more up-windows lately, and by how much relative to a coin flip.
    """
    out: List[Dict[str, Any]] = []
    for i in range(0, len(windows) - block + 1, block):
        chunk = windows[i:i + block]
        up = sum(1 for w in chunk if w["up"])
        lo, hi = wilson(up, len(chunk))
        out.append({
            "t_start": chunk[0]["t"],
            "t_end": chunk[-1]["t"],
            "date_et": _et(chunk[0]["t"]).strftime("%Y-%m-%d"),
            "n": len(chunk),
            "up_rate": round(up / len(chunk), 4),
            "ci_lo": round(lo, 4), "ci_hi": round(hi, 4),
            "net_bp": round(sum(w["ret_bp"] for w in chunk), 1),
        })
    return out


def forecast_next(windows: Sequence[Dict[str, Any]], pat: Dict[str, Any],
                  now_ms: Optional[int] = None) -> Dict[str, Any]:
    """p(UP) for the window that is about to open.

    Starts at the sample base rate, then applies ONLY conditionals that
    survived correction and actually match the live state. With nothing
    surviving (the usual outcome) this returns the base rate and says so —
    the honest answer to a coin flip is the coin's own rate, not a fabricated
    lean.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if not windows or pat.get("status") != "ok":
        return {"status": "no_data"}
    nxt_t = (now_ms // WINDOW_MS) * WINDOW_MS + WINDOW_MS
    last = windows[-1]
    dt = _et(nxt_t)
    state = {
        "hour_of_day": f"{dt.hour:02d}:00 ET",
        "session": ("asia" if 19 <= dt.hour or dt.hour < 3 else
                    "europe" if dt.hour < 9 else
                    "us_am" if dt.hour < 13 else "us_pm"),
        "day_of_week": ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[dt.weekday()],
        "prior_direction": "prior_up" if last["up"] else "prior_down",
        "prior_streak": _streak_bucket({"prior_streak": _last_streak(windows)}),
        "prior_magnitude": _mag_bucket({"prior_ret_bp": last["ret_bp"]}),
    }
    p = pat["base_rate"]
    applied: List[Dict[str, Any]] = []
    for fam in pat["families"]:
        want = state.get(fam["family"])
        if want is None:
            continue
        for row in fam["rows"]:
            if row["bucket"] == want and row["significant"]:
                # move in log-odds so stacked conditions cannot leave [0,1]
                p = _odds_shift(p, row["rate"], pat["base_rate"])
                applied.append({"family": fam["family"], "bucket": row["bucket"],
                                "rate": row["rate"], "n": row["n"], "lift_pp": row["lift_pp"]})
    return {
        "status": "ok",
        "window_open_ms": nxt_t,
        "window_open_et": dt.strftime("%Y-%m-%d %H:%M ET"),
        "slug": f"btc-updown-5m-{nxt_t // 1000}",
        "p_up": round(min(0.95, max(0.05, p)), 4),
        "base_rate": pat["base_rate"],
        "state": state,
        "applied": applied,
        "note": ("no surviving conditional matched — this is the sample base rate, "
                 "which is a coin flip inside its interval"
                 if not applied else
                 f"{len(applied)} corrected conditional(s) applied to the base rate"),
    }


def _last_streak(windows: Sequence[Dict[str, Any]]) -> int:
    if not windows:
        return 0
    d = windows[-1]["up"]
    n = 0
    for w in reversed(windows):
        if w["up"] != d:
            break
        n += 1
    return n if d else -n


def _odds_shift(p: float, bucket_rate: float, base: float) -> float:
    """Apply a bucket's log-odds delta to the running probability."""
    def lo(x: float) -> float:
        x = min(0.999, max(0.001, x))
        return math.log(x / (1 - x))
    z = lo(p) + (lo(bucket_rate) - lo(base))
    return 1 / (1 + math.exp(-z))


# ── in-progress window: the only model the live price can be compared to ─────


def randomwalk_prob(move_bp: float, sigma_bp_per_min: float,
                    minutes_left: float) -> float:
    """P(window closes UP | it is already `move_bp` up with `minutes_left` to go).

    Driftless random walk: the window resolves up when the remaining move
    exceeds -move_bp, so p = Phi(move / (sigma * sqrt(t_left))). With no time
    left it collapses to the current sign, which is the correct limit.
    """
    if minutes_left <= 0:
        return 1.0 if move_bp > 0 else 0.0
    s = sigma_bp_per_min * math.sqrt(minutes_left)
    if s <= 0:
        return 1.0 if move_bp > 0 else (0.0 if move_bp < 0 else 0.5)
    return _phi(move_bp / s)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def sigma_bp_per_min(windows: Sequence[Dict[str, Any]], lookback: int = 24) -> float:
    """Realised 1-minute vol in basis points, from the last `lookback` windows."""
    rets: List[float] = []
    for w in windows[-lookback:]:
        cs = w.get("c1m") or []
        for i in range(1, len(cs)):
            if cs[i - 1] > 0:
                rets.append((cs[i] / cs[i - 1] - 1.0) * 10_000)
    return stdev(rets) if len(rets) >= 8 else 0.0


def rw_calibration(windows: Sequence[Dict[str, Any]], minute: int = 3,
                   bins: int = 10) -> Dict[str, Any]:
    """Is `randomwalk_prob` calibrated at `minute` of 5? The lane's real eval.

    For every historical window, replay the state at the end of `minute`
    (a price the market can see) and grade the prediction on the actual close.
    Reports a reliability table, Brier score, and the Brier of the 0.50 null.
    A model that beats 0.25 Brier here is worth pointing at the live price; one
    that does not is decoration.
    """
    rows: List[Tuple[float, bool]] = []
    for i, w in enumerate(windows):
        cs = w.get("c1m") or []
        if len(cs) < WINDOW_MIN or w["open"] <= 0 or minute >= WINDOW_MIN:
            continue
        sig = sigma_bp_per_min(windows[max(0, i - 24):i])
        if sig <= 0:
            continue
        move_bp = (cs[minute - 1] / w["open"] - 1.0) * 10_000
        p = randomwalk_prob(move_bp, sig, WINDOW_MIN - minute)
        rows.append((p, bool(w["up"])))
    if len(rows) < 100:
        return {"status": "too_small", "n": len(rows)}
    brier = mean([(p - (1.0 if up else 0.0)) ** 2 for p, up in rows])
    null = mean([(0.5 - (1.0 if up else 0.0)) ** 2 for _, up in rows])
    table: List[Dict[str, Any]] = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [up for p, up in rows if (lo <= p < hi or (b == bins - 1 and p == 1.0))]
        if not sel:
            continue
        k = sum(sel)
        wl, wh = wilson(k, len(sel))
        table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(sel),
                      "predicted": round((lo + hi) / 2, 3),
                      "realized": round(k / len(sel), 4),
                      "ci_lo": round(wl, 4), "ci_hi": round(wh, 4)})
    # mean |predicted - realized| across populated bins, n-weighted
    tot = sum(t["n"] for t in table)
    cal_err = sum(abs(t["predicted"] - t["realized"]) * t["n"] for t in table) / tot if tot else 0.0
    return {
        "status": "ok",
        "n": len(rows),
        "decision_minute": minute,
        "brier": round(brier, 5),
        "brier_null": round(null, 5),
        "skill_pct": round((1 - brier / null) * 100, 2) if null > 0 else 0.0,
        "calibration_err_pp": round(cal_err * 100, 2),
        "table": table,
        "verdict": ("calibrated and better than a coin flip"
                    if brier < null and cal_err < 0.05 else
                    ("beats the coin flip but is miscalibrated" if brier < null
                     else "no better than a coin flip")),
    }




def live_window(windows: Sequence[Dict[str, Any]],
                book: Optional[Dict[str, Any]] = None,
                spot: Optional[float] = None,
                now_ms: Optional[int] = None,
                feed_buffer: float = FEED_BUFFER) -> Dict[str, Any]:
    """The in-progress window: elapsed move, fair random-walk probability, and
    what the live book actually charges for each side.

    This is the only honest comparison available. The market for the CURRENT
    window already contains the move that has happened, so pricing it against
    a next-window base rate manufactures a 20pp "edge" that does not exist.

    Edges are quoted against EXECUTABLE prices, not a mid: buying UP costs the
    ask, buying DOWN costs 1 - bid. Both edges are usually negative — that is
    what an efficient market with a spread looks like, and the tab says so
    instead of inventing a signal.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    w_start = (now_ms // WINDOW_MS) * WINDOW_MS
    elapsed_min = (now_ms - w_start) / 60_000.0
    left = max(0.0, WINDOW_MIN - elapsed_min)
    open_px = None
    for w in reversed(windows):
        if w["t"] == w_start:
            open_px = w["open"]
            break
    if open_px is None and windows:
        # window still forming: its open is the close of the last complete bar
        open_px = windows[-1]["close"] if windows[-1]["t"] + WINDOW_MS == w_start else None
    if spot is None:
        spot = live_spot()
    sig = sigma_bp_per_min(windows)
    if open_px is None or spot is None or sig <= 0:
        return {"status": "unavailable", "window_start_ms": w_start,
                "minutes_left": round(left, 2)}
    move_bp = (spot / open_px - 1.0) * 10_000
    p = randomwalk_prob(move_bp, sig, left)
    out: Dict[str, Any] = {
        "status": "ok",
        "window_start_ms": w_start,
        "window_start_et": _et(w_start).strftime("%H:%M ET"),
        "slug": f"btc-updown-5m-{w_start // 1000}",
        "open_px": round(open_px, 2),
        "spot": round(spot, 2),
        "move_bp": round(move_bp, 2),
        "minutes_left": round(left, 2),
        "sigma_bp_per_min": round(sig, 2),
        "p_up_randomwalk": round(p, 4),
        "price_feed": "binance BTCUSDT 1m (market resolves on CHAINLINK BTC/USD)",
    }
    if not book or book.get("status") != "ok":
        out["market"] = {"status": (book or {}).get("status", "no_market")}
        return out

    bid, ask = book.get("bid"), book.get("ask")
    edge_up = (p - ask) if ask is not None else None            # buy UP at the ask
    edge_dn = (bid - p) if bid is not None else None            # buy DOWN at 1 - bid
    best = max([e for e in (edge_up, edge_dn) if e is not None], default=None)
    side = None
    if best is not None and best == edge_up:
        side = "UP"
    elif best is not None and best == edge_dn:
        side = "DOWN"
    actionable = bool(best is not None and best >= feed_buffer)
    out["market"] = {
        "status": "ok",
        "bid": bid, "ask": ask, "mid": book.get("mid"), "spread": book.get("spread"),
        "bid_size": book.get("bid_size"), "ask_size": book.get("ask_size"),
    }
    out.update({
        "mkt_up": book.get("mid"),
        "edge_up_pp": round(edge_up * 100, 2) if edge_up is not None else None,
        "edge_down_pp": round(edge_dn * 100, 2) if edge_dn is not None else None,
        "best_side": side,
        "best_edge_pp": round(best * 100, 2) if best is not None else None,
        "edge_pp": round(best * 100, 2) if best is not None else None,
        "actionable": actionable,
        "feed_buffer_pp": round(feed_buffer * 100, 1),
        "note": (
            f"{side} is {best*100:.1f}pp cheap after paying the spread — clears the "
            f"{feed_buffer*100:.0f}pp feed-mismatch buffer"
            if actionable else
            (f"best side ({side}) is only {best*100:+.1f}pp after the spread, under the "
             f"{feed_buffer*100:.0f}pp buffer for the Chainlink/Binance feed gap — no trade"
             if best is not None else "no book to price against")),
    })
    return out


def _edges_block(windows: Sequence[Dict[str, Any]], with_live: bool) -> Dict[str, Any]:
    """Microstructure edges, isolated so a Gamma/CLOB hiccup cannot take the
    whole lane down with it."""
    try:
        from services.trend_engine.updown_edges import read as edges_read
        return edges_read(windows=windows, with_live=with_live)
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200]}


def live_spot() -> Optional[float]:
    d = _curl("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
    try:
        return float(d["price"])
    except Exception:
        return None


# ── lane entry point ─────────────────────────────────────────────────────────


def read(minutes: int = DEFAULT_MINUTES, runner: Optional[Callable] = None,
         hl_daily: Optional[Dict[str, str]] = None, with_market: bool = True,
         now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Full BTC-5m lane: klines -> windows -> patterns -> forecast -> market check."""
    t0 = time.time()
    bars = load_1m(minutes, runner=runner)
    ws = enrich(build_windows(bars))
    if not ws:
        return {"status": "no_data", "generated_at": int(time.time())}
    pat = patterns(ws, hl_daily_label=hl_daily)
    fc = forecast_next(ws, pat, now_ms=now_ms)
    book = live_market_book() if with_market else None
    return {
        "status": "ok",
        "generated_at": int(time.time()),
        "elapsed_s": round(time.time() - t0, 2),
        "window_min": WINDOW_MIN,
        "sample_days": round(len(ws) * WINDOW_MIN / 1440, 1),
        "patterns": pat,
        "rolling": rolling_trend(ws),
        "forecast": fc,
        "live": live_window(ws, book, now_ms=now_ms) if with_market else {"status": "off"},
        "calibration": rw_calibration(ws, minute=3),
        # microstructure block: the three HFT claims, tested on our own data
        "edges": _edges_block(ws, with_live=with_market),
        "recent": [{k: v for k, v in w.items() if k != "c1m"} for w in ws[-24:]],
    }


def live_market_book(now: Optional[float] = None) -> Dict[str, Any]:
    """Top of the current window's UP book: best bid, best ask, sizes, spread.

    Reads the BOOK, not `/midpoint`. Gamma's `outcomePrices` is stale on a
    5-minute market and the midpoint endpoint has been caught quoting 0.325
    against a live 0.27/0.28 book — either one manufactures a double-digit
    "edge" out of nothing. The book is what a fill would actually cost.
    """
    out: Dict[str, Any] = {"status": "no_market"}
    try:
        t = int(time.time() if now is None else now)
        slug = f"btc-updown-5m-{(t // 300) * 300}"
        m = _curl(f"https://gamma-api.polymarket.com/markets?slug={slug}")
        if not (isinstance(m, list) and m and isinstance(m[0], dict)):
            return out
        toks = json.loads(m[0].get("clobTokenIds") or "[]")
        if not toks:
            return out
        book = _curl(f"https://clob.polymarket.com/book?token_id={toks[0]}")
        if not isinstance(book, dict):
            return out
        bids = sorted((b for b in (book.get("bids") or [])),
                      key=lambda b: -float(b["price"]))
        asks = sorted((a for a in (book.get("asks") or [])),
                      key=lambda a: float(a["price"]))
        bid = float(bids[0]["price"]) if bids else None
        ask = float(asks[0]["price"]) if asks else None
        out = {
            "status": "ok" if (bid is not None or ask is not None) else "empty_book",
            "slug": slug,
            "bid": bid, "ask": ask,
            "bid_size": float(bids[0]["size"]) if bids else None,
            "ask_size": float(asks[0]["size"]) if asks else None,
            "mid": round((bid + ask) / 2, 4) if (bid is not None and ask is not None) else None,
            "spread": round(ask - bid, 4) if (bid is not None and ask is not None) else None,
        }
    except Exception:
        return {"status": "error"}
    return out


def live_market_up(now: Optional[float] = None) -> Optional[float]:
    """Book mid for the current window's UP token (None when the book is empty)."""
    return live_market_book(now).get("mid")
