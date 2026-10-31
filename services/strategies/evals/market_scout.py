#!/usr/bin/env python3
"""Eval: which markets besides Brent could run the same desk-tick rules?

Operator, 2026-10-31: Brent (`xyz:BRENTOIL`) works -- which OTHER markets
could replicate it, long or short? Named candidates: `xyz:SILVER` (shorting
it), `xyz:DIESEL` (thought to correlate with oil), `io:ANT`, `io:OPENAI`
("lots of interlinked news"). Reference markets: `xyz:BRENTOIL`, `xyz:CL`
(WTI), `xyz:GOLD`, `xyz:NATGAS`.

Two of the four named candidates don't exist on the venue under that name
(checked against the venue's own `perpDexs`/`metaAndAssetCtxs` for dex=xyz
and dex=io, 2026-10-31):

  - `xyz:DIESEL` does not exist. The closest market is `xyz:HO` (NY Harbor
    ULSD / heating oil -- the standard diesel-proxy futures ticker; CL/HO
    crack-spread is literally what refiners trade).
  - `io:ANT` does not exist on the `io` dex. The `io` dex has `io:ANTH`
    (Anthropic's private-company perp); `xyz:ANT` exists but is a different
    company (Ant Group / Alibaba-affiliate fintech), on a different dex.
  - `io:OPENAI` does not exist; the `io` dex has `io:OAI` (OpenAI's perp).

`MARKETS` below uses the corrected names. docs/strategy/MARKET_SCOUT_2026-10-31.md
has the full writeup, including a note on `xyz:ANT` itself (real, but 5 days
old -- not enough history for any of this).

Three report sections per market, mirroring the operator's three questions:

  1. Tradeability -- can a $2.4k-notional trade even clear this market at a
     sane cost: venue max leverage, 24h volume, open interest, funding (live
     + annualized), mid, how much 15m/1d history the venue has, L2 depth
     (slippage in bps to fill $2.5k and $25k market orders, each side), and
     whether a 1.3-ATR stop (the live non-Brent rule,
     `services.strategies.next_trade.stop_buffer_for`) would pass
     `pathiel.agents.bracket.check_leverage` at this market's own trading
     leverage (min(20x, venue max)) -- fraction of days it does.
  2. Behaviour -- 1h ATR14 %, median daily range %, daily-return correlation
     with Brent (and silver with gold), trend persistence (does a 5-day move
     keep going), and how much of the day's move happens in the
     London-to-NY session (07:00-21:00 UTC).
  3. Backtest -- the live rules (`desk_tick_sim.live_params()`) with the
     stop buffer rescaled to this coin's own ATR and leverage capped at the
     venue max, over three direction controls (always long, always short,
     the 20-day trend sign) since there is no thesis for any of these
     markets yet -- these are mechanics-only controls, same spirit as
     `cross_market_backtest.py`'s Brent/gold/BTC/S&P run, but with the
     CURRENT (2026-10-30) live rules instead of the old ones.

Runnable as a script; hits the public, read-only `/info` endpoint (no key)
on an empty cache:

    python3 -m services.strategies.evals.market_scout --cache-dir /tmp/x --out-dir /tmp/y

Every pure function here (slippage from a book, the ATR/correlation/
persistence/session math, the leverage-gate fraction, the scaled Params
builder) is gate-tested offline on synthetic data in
services/strategies/tests/test_market_scout.py. Only `fetch_market_meta`,
`fetch_l2_book`, `load_coin_candles` and `main` touch the network, and all
three are READ ONLY against Hyperliquid's public info API -- no key, no
order, no pathiel MCP tool, no write under ~/.pathiel or pathia's .state.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import statistics
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.agents.bracket import check_leverage
from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, live_params, simulate
from services.strategies.evals.cross_market_backtest import mean_atr
from services.strategies.evals.desk_tick_backtest import (
    DAY_MS, HOUR_MS, always_direction, build_daily_sign_direction)
from services.strategies.evals.strategy_sweep import per_day
from services.strategies.next_trade import ATR_BUFFER_MULT

FIFTEEN_MIN_MS = 900_000
START_EQUITY = 64.0                 # same starting stake every other eval in this repo uses
BASE_LEVERAGE = 20.0                # the live desk's leverage, capped per-market at the venue max
LONDON_NY_SESSION_UTC: Tuple[int, int] = (7, 21)   # the session option strategy_sweep already sweeps
BACKTEST_WINDOWS_DAYS = (30, 50)    # the oil eval's window and the most 15m history the venue returns
BASELINE = "xyz:BRENTOIL"
EXTRA_CORRELATION_PAIRS: Tuple[Tuple[str, str], ...] = (("xyz:SILVER", "xyz:GOLD"),)   # + every market vs Brent

# Operator-named candidates, corrected against the venue (see module
# docstring), plus the four reference markets. (coin, label, note).
MARKETS: Tuple[Tuple[str, str, str], ...] = (
    ("xyz:SILVER", "Silver", "named by operator (considering a short)"),
    ("xyz:HO", "Diesel / ULSD", "operator named xyz:DIESEL; does not exist on the venue -- "
                                "xyz:HO (NY Harbor ULSD / heating oil) is the diesel-proxy market"),
    ("io:ANTH", "Anthropic (io)", "operator named io:ANT; does not exist on the io dex -- "
                                  "io:ANTH (Anthropic) is the closest; xyz:ANT exists but is a "
                                  "different company (Ant Group) on a different dex"),
    ("io:OAI", "OpenAI (io)", "operator named io:OPENAI; does not exist -- io:OAI is OpenAI's perp"),
    ("xyz:BRENTOIL", "Brent", "reference: the live desk"),
    ("xyz:CL", "WTI", "reference"),
    ("xyz:GOLD", "Gold", "reference"),
    ("xyz:NATGAS", "Nat gas", "reference"),
)


# ── tradeability: pure math over an already-fetched book/meta ──────────────

def walk_book(levels: Sequence[Tuple[float, float]], notional_usd: float) -> Tuple[Optional[float], bool]:
    """Volume-weighted average fill price for a market order of `notional_usd`
    against `levels` (best price first, as Hyperliquid's l2Book returns each
    side). Returns (vwap_px, filled) -- filled is False when the book run out
    before the order did, in which case vwap_px is the average over whatever
    WAS there (still useful: it says the order can't actually clear)."""
    cum_notional = 0.0
    cum_size = 0.0
    for px, sz in levels:
        level_notional = px * sz
        if cum_notional + level_notional >= notional_usd:
            need = notional_usd - cum_notional
            cum_size += need / px if px > 0 else 0.0
            return (notional_usd / cum_size if cum_size > 0 else None), True
        cum_notional += level_notional
        cum_size += sz
    return (cum_notional / cum_size if cum_size > 0 else None), False


def slippage_bps(levels: Sequence[Tuple[float, float]], notional_usd: float, mid: float,
                 side: str) -> Dict[str, Any]:
    """Slippage in bps of a `side` ("buy"|"sell") market order for
    `notional_usd`, against `mid`. `levels` must already be the right side
    (asks for buy, bids for sell), best price first."""
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be buy or sell, got {side!r}")
    vwap, filled = walk_book(levels, notional_usd)
    if vwap is None or mid <= 0:
        return {"bps": None, "vwap_px": None, "filled": False}
    bps = (vwap / mid - 1.0) * 1e4 if side == "buy" else (1.0 - vwap / mid) * 1e4
    return {"bps": round(bps, 2), "vwap_px": vwap, "filled": filled}


# ── behaviour: pure math over 1h/15m candles ────────────────────────────────

def atr14_series(candles_1h: Sequence[Candle], period: int = 14) -> List[Tuple[int, float, float]]:
    """(close_time, atr14, close_px) for every bar with `period` true ranges
    behind it -- the same window `cross_market_backtest.mean_atr` and
    `desk_tick_sim._levels` use, just kept per-bar instead of averaged."""
    bars = sorted(candles_1h, key=lambda c: c.t)
    if len(bars) < period + 1:
        return []
    trs = [max(b.h - b.l, abs(b.h - a.c), abs(b.l - a.c)) for a, b in zip(bars, bars[1:])]
    out: List[Tuple[int, float, float]] = []
    for i in range(period, len(trs) + 1):
        atr = sum(trs[i - period:i]) / period
        bar = bars[i]
        out.append((bar.t + HOUR_MS, atr, bar.c))   # close_time = this bar's own close
    return out


def leverage_gate_pass_frac(candles_1h: Sequence[Candle], trade_leverage: float,
                            venue_max_leverage: float, atr_mult: float = ATR_BUFFER_MULT) -> Dict[str, Any]:
    """Per UTC day, at that day's last 1h close: would a stop `atr_mult` x
    ATR14 away (the live non-Brent rule) still pass `check_leverage` at
    `trade_leverage` (both a long's and a short's stop)? Fraction of days it
    does, out of every day with enough history for ATR14."""
    by_day: Dict[int, Tuple[float, float]] = {}
    for t, atr, px in atr14_series(candles_1h):
        by_day[t // DAY_MS] = (atr, px)      # last bar of the day wins
    if not by_day:
        return {"n_days": 0, "pass_frac": None}
    n_pass = 0
    for atr, px in by_day.values():
        buf = atr_mult * atr
        if buf <= 0 or px <= 0:
            continue
        long_ok = check_leverage(px, px - buf, trade_leverage, venue_max_leverage)["ok"]
        short_ok = check_leverage(px, px + buf, trade_leverage, venue_max_leverage)["ok"]
        if long_ok and short_ok:
            n_pass += 1
    return {"n_days": len(by_day), "pass_frac": n_pass / len(by_day)}


def daily_ohlc(candles_1h: Sequence[Candle]) -> List[Tuple[int, float, float, float]]:
    """(utc_day_index, day_high, day_low, last_close) per UTC day that has
    at least one 1h bar, oldest first."""
    by_day: Dict[int, List[Candle]] = {}
    for c in sorted(candles_1h, key=lambda c: c.t):
        by_day.setdefault(c.t // DAY_MS, []).append(c)
    return [(day, max(b.h for b in bars), min(b.l for b in bars), bars[-1].c)
           for day, bars in sorted(by_day.items())]


def daily_range_pct_median(candles_1h: Sequence[Candle]) -> Optional[float]:
    ranges = [(h - l) / c for _, h, l, c in daily_ohlc(candles_1h) if c > 0]
    return statistics.median(ranges) if ranges else None


def daily_returns(candles_1h: Sequence[Candle]) -> Dict[int, float]:
    """{utc_day_index -> that day's close-to-close return}, only for
    consecutive day pairs (a gap in the data breaks the pair, not fabricates
    a multi-day return)."""
    rows = daily_ohlc(candles_1h)
    out: Dict[int, float] = {}
    for (d0, _, _, c0), (d1, _, _, c1) in zip(rows, rows[1:]):
        if d1 == d0 + 1 and c0 > 0:
            out[d1] = c1 / c0 - 1.0
    return out


def pearson_corr(a: Dict[int, float], b: Dict[int, float]) -> Optional[float]:
    """Pearson correlation of two {day -> return} maps, aligned by day.
    None under 3 overlapping days or a constant series (undefined)."""
    days = sorted(set(a) & set(b))
    if len(days) < 3:
        return None
    xs, ys = [a[d] for d in days], [b[d] for d in days]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / (vx * vy) ** 0.5 if vx > 0 and vy > 0 else None


def trend_persistence_frac(candles_1h: Sequence[Candle], h: int = 5) -> Optional[float]:
    """Fraction of days where the prior `h`-day return's sign matches the
    NEXT `h`-day return's sign (does a move tend to keep going, the same
    question `desk_tick_backtest.build_daily_sign_direction` bets on).
    Flat (zero) returns are excluded, not counted either way."""
    rows = daily_ohlc(candles_1h)
    closes = {d: c for d, _, _, c in rows}
    n_match = n_total = 0
    for d in closes:
        if d - h not in closes or d + h not in closes:
            continue
        prev_ret = closes[d] / closes[d - h] - 1.0
        next_ret = closes[d + h] / closes[d] - 1.0
        if prev_ret == 0 or next_ret == 0:
            continue
        n_total += 1
        if (prev_ret > 0) == (next_ret > 0):
            n_match += 1
    return (n_match / n_total) if n_total else None


def _in_session_hour(t: int, session: Tuple[int, int]) -> bool:
    hr = (t // HOUR_MS) % 24
    start, end = session
    return start <= hr < end if start <= end else (hr >= start or hr < end)


def session_share(candles_15m: Sequence[Candle], session: Tuple[int, int] = LONDON_NY_SESSION_UTC) -> Optional[float]:
    """Share of the total |close-open| move (summed over every 15m bar) that
    happens inside `session` (default: London open to NY close, UTC)."""
    total = sum(abs(c.c - c.o) for c in candles_15m)
    if total <= 0:
        return None
    in_session = sum(abs(c.c - c.o) for c in candles_15m if _in_session_hour(c.t, session))
    return in_session / total


# ── backtest: the live rules, stop buffer rescaled per coin ────────────────

def scouting_params(venue_max_leverage: float, atr_price_mean: float, base: Optional[Params] = None) -> Params:
    """`desk_tick_sim.live_params()` with the stop buffer rescaled to this
    coin's own mean 1h ATR14 (the live non-Brent rule, `ATR_BUFFER_MULT` x
    ATR -- `services.strategies.next_trade.stop_buffer_for`) and leverage
    capped at the venue max. Everything else (market entry, 24h range, 2R
    gate, 0.5% target offset, limit target, fixed stop, re-entry) is
    untouched, so these runs are judged on the SAME mechanics as Brent."""
    base = base or live_params()
    leverage = min(base.leverage, venue_max_leverage)
    return dataclasses.replace(base, stop_buffer=ATR_BUFFER_MULT * atr_price_mean,
                               leverage=leverage, max_leverage=venue_max_leverage)


DirectionName = str   # "always_long" | "always_short" | "daily_sign"
SERIES_NAMES: Tuple[DirectionName, ...] = ("always_long", "always_short", "daily_sign")


def direction_series(name: DirectionName, c1h_long: Sequence[Candle]) -> Callable[[int], Any]:
    if name == "always_long":
        return always_direction("long")
    if name == "always_short":
        return always_direction("short")
    if name == "daily_sign":
        direction_at, _, _ = build_daily_sign_direction(c1h_long)
        return direction_at
    raise ValueError(f"unknown direction series {name!r}")


def backtest_window(c15_long: Sequence[Candle], c1h_long: Sequence[Candle], params: Params,
                    start_ms: int, end_ms: int, series: DirectionName) -> Dict[str, Any]:
    """One `per_day` row: `series` over [start_ms, end_ms), with 1h history
    before `start_ms` kept in so the levels are warm on the window's first bar."""
    c15 = [c for c in c15_long if start_ms <= c.t < end_ms]
    c1h = [c for c in c1h_long if c.t < end_ms]
    run = simulate(c15, c1h, direction_series(series, c1h_long), params)
    return per_day(run, start_ms, end_ms, start_equity=START_EQUITY)


def backtest_coin(c15_long: Sequence[Candle], c1h_long: Sequence[Candle], venue_max_leverage: float,
                  windows_days: Sequence[int] = BACKTEST_WINDOWS_DAYS) -> Dict[str, Any]:
    """Every (window, series) row for one coin, plus a first-half/second-half
    split of each window. `windows_days` is capped to what `c15_long` actually
    covers; a window under 1 day of candles is skipped (ATR14 alone needs 15
    1h bars, so anything shorter can't even build a setup)."""
    if not c15_long:
        return {"windows": {}, "note": "no candle history"}
    end_ms = max(c.t for c in c15_long) + FIFTEEN_MIN_MS
    start_of_history = min(c.t for c in c15_long)
    available_days = (end_ms - start_of_history) / DAY_MS
    days_to_run = sorted({min(d, int(available_days)) for d in windows_days if min(d, available_days) >= 1})

    atr_price, _ = mean_atr(c1h_long)
    out: Dict[str, Any] = {"available_days": round(available_days, 2), "windows": {}}
    if atr_price is None:
        out["note"] = "under 15 1h bars: no ATR14, cannot size a stop"
        return out
    params = scouting_params(venue_max_leverage, atr_price)
    out["stop_buffer"] = params.stop_buffer
    out["leverage"] = params.leverage

    for days in days_to_run:
        start_ms = end_ms - days * DAY_MS
        mid_ms = (start_ms + end_ms) // 2
        window_row: Dict[str, Any] = {}
        for series in SERIES_NAMES:
            row = backtest_window(c15_long, c1h_long, params, start_ms, end_ms, series)
            row["halves"] = {
                "first": backtest_window(c15_long, c1h_long, params, start_ms, mid_ms, series),
                "second": backtest_window(c15_long, c1h_long, params, mid_ms, end_ms, series),
            }
            window_row[series] = row
        out["windows"][days] = window_row
    return out


# ── live fetch (the only parts that touch the network) ─────────────────────

def fetch_market_meta(coins: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Per-coin {maxLeverage, dayNtlVlm, openInterest, funding_hourly, markPx,
    midPx, szDecimals} from the venue's own `metaAndAssetCtxs`, one call per
    dex named among `coins` (main dex: dex=""). Public, read-only, no cache
    (this eval runs rarely, not on every tick, so there is nothing to save
    a second fetch for)."""
    from pathiel.client.exchange import _http_post
    out: Dict[str, Dict[str, Any]] = {}
    dexes = sorted({c.split(":", 1)[0] if ":" in c else "" for c in coins})
    for dex in dexes:
        data = _http_post("/info", {"type": "metaAndAssetCtxs", **({"dex": dex} if dex else {})})
        if not (isinstance(data, list) and len(data) >= 2):
            continue
        meta, ctx = data[0], data[1]
        for i, u in enumerate(meta.get("universe", [])):
            name = u.get("name")
            if name not in coins:
                continue
            c = ctx[i] if i < len(ctx) else {}
            out[name] = {
                "maxLeverage": float(u.get("maxLeverage") or 0),
                "szDecimals": u.get("szDecimals"),
                "dayNtlVlm": float(c.get("dayNtlVlm") or 0),
                "openInterest": float(c.get("openInterest") or 0),
                "funding_hourly": float(c.get("funding") or 0),
                "markPx": float(c.get("markPx") or 0),
                "midPx": float(c.get("midPx") or c.get("markPx") or 0),
            }
    return out


def fetch_l2_book(coin: str) -> Dict[str, List[Tuple[float, float]]]:
    """{"bids": [(px, sz), ...], "asks": [...]}, best price first, from the
    venue's public `l2Book`."""
    from pathiel.client.exchange import _http_post
    raw = _http_post("/info", {"type": "l2Book", "coin": coin}) or {}
    levels = raw.get("levels") or [[], []]
    bids = [(float(r["px"]), float(r["sz"])) for r in (levels[0] if len(levels) > 0 else [])]
    asks = [(float(r["px"]), float(r["sz"])) for r in (levels[1] if len(levels) > 1 else [])]
    return {"bids": bids, "asks": asks}


def tradeability_row(coin: str, meta: Dict[str, Any], book: Dict[str, List[Tuple[float, float]]],
                     c1h_long: Sequence[Candle], c15_long: Sequence[Candle], c1d: Sequence[Candle],
                     c15_first: Optional[Candle]) -> Dict[str, Any]:
    mid = meta.get("midPx") or meta.get("markPx") or 0.0
    venue_max = meta.get("maxLeverage") or 0.0
    trade_leverage = min(BASE_LEVERAGE, venue_max) if venue_max else None
    slip = {}
    for side, levels in (("buy", book["asks"]), ("sell", book["bids"])):
        for notional in (2_500.0, 25_000.0):
            slip[f"{side}_{int(notional)}"] = slippage_bps(levels, notional, mid, side) if mid else \
                {"bps": None, "vwap_px": None, "filled": False}
    lev_gate = leverage_gate_pass_frac(c1h_long, trade_leverage, venue_max) if trade_leverage else \
        {"n_days": 0, "pass_frac": None}
    return {
        "coin": coin, "mid": mid, "venue_max_leverage": venue_max, "trade_leverage": trade_leverage,
        "day_ntl_vlm_usd": meta.get("dayNtlVlm"), "open_interest_coin": meta.get("openInterest"),
        "open_interest_usd": (meta.get("openInterest") or 0) * mid if mid else None,
        "funding_hourly": meta.get("funding_hourly"),
        "funding_annualized_pct": (meta.get("funding_hourly") or 0) * 24 * 365 * 100,
        "first_candle_1d": _iso_date(c1d[0].t) if c1d else None,
        "first_candle_15m": _iso_date(c15_first.t) if c15_first else None,
        "slippage_bps": slip, "leverage_gate_pass_frac": lev_gate,
    }


def behaviour_row(c1h_long: Sequence[Candle], c15_long: Sequence[Candle]) -> Dict[str, Any]:
    atr_price, atr_pct = mean_atr(c1h_long)
    return {
        "atr14_pct_mean": atr_pct, "atr14_price_mean": atr_price,
        "daily_range_pct_median": daily_range_pct_median(c1h_long),
        "trend_persistence_5d_frac": trend_persistence_frac(c1h_long, 5),
        "session_share_london_ny": session_share(c15_long),
    }


def _iso_date(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def load_coin_candles(coin: str, cache_dir: str) -> Dict[str, List[Candle]]:
    """15m x 50 days, 1h x 75 days (room for the daily-sign series' 25-day
    extra lookback on a 50-day window), 1d x 400 (for "first candle on 1d"),
    all through the cached fetcher (`services.strategies.backtest.data`)."""
    return {
        "c15": D.fetch_candles_cached(coin, "15m", 50 * 24 * 4 + 20, cache_dir),
        "c1h": D.fetch_candles_cached(coin, "1h", 75 * 24, cache_dir),
        "c1d": D.fetch_candles_cached(coin, "1d", 400, cache_dir),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default="/tmp/pathiel-backtest/market-scout")
    parser.add_argument("--out-dir", default="/tmp/market-scout")
    args = parser.parse_args(argv)
    os.makedirs(args.cache_dir, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)

    coins = [c for c, _, _ in MARKETS]
    meta = fetch_market_meta(coins)
    missing = [c for c in coins if c not in meta]
    if missing:
        print(f"FATAL: no venue meta for {missing} -- check the coin name against perpDexs/metaAndAssetCtxs")
        return 2

    data = {c: load_coin_candles(c, args.cache_dir) for c in coins}
    books = {c: fetch_l2_book(c) for c in coins}

    rows: Dict[str, Dict[str, Any]] = {}
    for coin, label, note in MARKETS:
        d = data[coin]
        c15_first = min(d["c15"], key=lambda c: c.t) if d["c15"] else None
        row = {
            "label": label, "note": note,
            "tradeability": tradeability_row(coin, meta[coin], books[coin], d["c1h"], d["c15"], d["c1d"], c15_first),
            "behaviour": behaviour_row(d["c1h"], d["c15"]),
            "backtest": backtest_coin(d["c15"], d["c1h"], meta[coin]["maxLeverage"]),
        }
        rows[coin] = row

    correlations = {}
    pairs = [(c, BASELINE) for c, _, _ in MARKETS if c != BASELINE] + list(EXTRA_CORRELATION_PAIRS)
    for a, b in pairs:
        if a in data and b in data:
            correlations[f"{a}_vs_{b}"] = pearson_corr(daily_returns(data[a]["c1h"]), daily_returns(data[b]["c1h"]))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "markets": rows,
        "correlations": correlations,
    }
    path = os.path.join(args.out_dir, "report.json")
    with open(path, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    _print(rows, correlations)
    print(f"\nreport: {path}")
    return 0


def _print(rows: Dict[str, Dict[str, Any]], correlations: Dict[str, Optional[float]]) -> None:
    print(f"{'coin':<14} {'venue_max':>9} {'dayVol$M':>9} {'oi$M':>7} {'fund_ann%':>9} "
          f"{'atr%':>6} {'buy2.5k_bps':>11} {'buy25k_bps':>10} {'lev_pass%':>9}")
    for coin, r in rows.items():
        t, b = r["tradeability"], r["behaviour"]
        vol_m = (t["day_ntl_vlm_usd"] or 0) / 1e6
        oi_m = (t["open_interest_usd"] or 0) / 1e6
        fund = t["funding_annualized_pct"]
        atr = b["atr14_pct_mean"]
        buy25 = t["slippage_bps"]["buy_2500"]["bps"]
        buy25k = t["slippage_bps"]["buy_25000"]["bps"]
        lev = t["leverage_gate_pass_frac"]["pass_frac"]
        print(f"{coin:<14} {t['venue_max_leverage']:>9g} {vol_m:>9.1f} {oi_m:>7.1f} {fund:>9.1f} "
              f"{(atr or 0) * 100:>6.2f} {('n/a' if buy25 is None else f'{buy25:.1f}'):>11} "
              f"{('n/a' if buy25k is None else f'{buy25k:.1f}'):>10} "
              f"{('n/a' if lev is None else f'{lev:.0%}'):>9}")
    print("\ncorrelations:", correlations)


if __name__ == "__main__":
    raise SystemExit(main())
