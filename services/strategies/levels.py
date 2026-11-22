"""Deterministic candle-derived levels for a desk's primary instrument.

Moved out of `scripts/oil_levels.py` (2026-10-23) so the Oil Desk dashboard
page (`GET /api/desks/oil/levels`) and the oil-tick skill's CLI share one
implementation instead of two copies drifting apart. The script stays as a
thin CLI that imports these functions; nothing here does its own network
I/O — `fetch_candles`/`fetch_mid` (the HTTP leg) stay in the script, which
is the only caller that needs its own CA bundle handling (the system
python3 has none; see the script's own docstring). The HTTP route instead reads
candles through `pathiel.client.hl_client`, which already has a working
HTTPS client in-process.

No em dashes in any user-facing string here, by repo convention.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

HOUR_MS = 3_600_000


def closed_only(candles: List[Dict[str, Any]], now_ms: int) -> List[Dict[str, Any]]:
    """Candles whose close time has passed. The open candle's high/low are
    still moving and must not anchor a stop."""
    return [c for c in candles if int(c["T"]) <= now_ms]


def true_range(prev_close: float, c: Dict[str, Any]) -> float:
    h, lo = float(c["h"]), float(c["l"])
    return max(h - lo, abs(h - prev_close), abs(lo - prev_close))


def atr(candles: List[Dict[str, Any]], n: int = 14) -> Optional[float]:
    """Simple-average ATR over the last n closed candles (needs n+1)."""
    if len(candles) < n + 1:
        return None
    window = candles[-(n + 1):]
    trs = [true_range(float(window[i - 1]["c"]), window[i]) for i in range(1, len(window))]
    return sum(trs) / n


def price_range(candles: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    if not candles:
        return None
    return {"high": max(float(c["h"]) for c in candles), "low": min(float(c["l"]) for c in candles)}


def summarize(c1h: List[Dict[str, Any]], c15: List[Dict[str, Any]], mid: float, now_ms: int) -> Dict[str, Any]:
    """Mid, the last CLOSED 1h candle, 8h/24h/48h range, ATR14 on 1h, where
    the mid sits in the 24h range, and the last four closed 15m candles'
    range. Pure: every input is already-fetched candle data."""
    closed = closed_only(c1h, now_ms)
    last = closed[-1] if closed else None
    r24 = price_range(closed[-24:])
    out: Dict[str, Any] = {
        "mid": mid,
        "last_closed_1h": ({"open": float(last["o"]), "high": float(last["h"]), "low": float(last["l"]),
                            "close": float(last["c"]), "closed_at_utc": time.strftime(
                                # HL's T is the last ms of the candle (xx:59:59.999); +1 shows the hour it closed on.
                                "%Y-%m-%dT%H:%M", time.gmtime((int(last["T"]) + 1) / 1000))} if last else None),
        "range_8h": price_range(closed[-8:]),
        "range_24h": r24,
        "range_48h": price_range(closed[-48:]),
        "atr14_1h": atr(closed),
        "range_15m_last4": price_range(closed_only(c15, now_ms)[-4:]),
        "n_closed_1h": len(closed),
        # the silver breakout entry's inputs (next_trade.breakout_levels); None under 25 closed bars
        "breakout_24h": _breakout_levels(closed),
        # CLOSED 1h closes, oldest first, for the HYPE trend-gate veto
        # (next_trade.next_trade's closes_1h/trend_gate_span). Bounded to the
        # caller's own fetch window; every other coin's `main()` fetches 50
        # hours as before, so this list is just that window's closes for them
        # too -- cheap, and `trend_gate` on a coin with no span never reads it.
        "closes_1h": [float(c["c"]) for c in closed],
    }
    if r24 and r24["high"] > r24["low"]:
        out["mid_pos_in_24h"] = round((mid - r24["low"]) / (r24["high"] - r24["low"]), 3)
    if out["atr14_1h"]:
        out["atr14_pct"] = round(out["atr14_1h"] / mid * 100, 3)
    return out


def _breakout_levels(closed: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    from services.strategies.next_trade import breakout_levels
    return breakout_levels(closed)


def fetch_candles(coin: str, interval: str, hours: int, now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    """Raw candleSnapshot rows (with HL's own `t`/`T` open/close fields),
    via the in-process HTTP client. `pathiel.client.hl_client.fetch_hl_candles`
    is not reusable here: it drops the `T` close-timestamp field that
    closed_only()/summarize() need to exclude the still-open candle."""
    from pathiel.client.hl_client import _http_post
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    rows = _http_post("/info", {"type": "candleSnapshot", "req": {
        "coin": coin, "interval": interval, "startTime": now_ms - hours * HOUR_MS, "endTime": now_ms}})
    return rows if isinstance(rows, list) else []


def fetch_mid(coin: str) -> Optional[float]:
    """The coin's current mid, including HIP-3 dexes (xyz:BRENTOIL lives on
    its own dex, not main)."""
    from pathiel.client.hl_client import fetch_all_mids
    mids = fetch_all_mids(include_hip3=True)
    try:
        return float(mids[coin])
    except (KeyError, TypeError, ValueError):
        return None


# Hours of 1h candles summarize_coin fetches. The usual 50, or (operator bug,
# 2026-11-20: the HYPE trend gate never applied -- a fire at a -5.8% 120h
# return still said "buys now") a coin's trend-gate span plus margin when
# that needs more than 50: trend_gate (next_trade.py) needs span_h + 1
# CLOSED bars to compute the span_h-hour return at all, else it reports
# "veto not applied" instead of judging it, same as too little history ever
# did. scripts/oil_levels.py's own `--next` fetch reads this function (not
# its own copy) so the CLI and summarize_coin's callers (the watcher, the
# dashboard's /next and /levels routes) can never drift onto different
# windows again, the exact failure mode this fixes.
DEFAULT_HOURS_1H = 50
TREND_GATE_FETCH_MARGIN_H = 10


def hours_1h_for(coin: str) -> int:
    """1h candle-hours to fetch for `coin`: DEFAULT_HOURS_1H (every coin but
    HYPE today), or trend_gate_span_for(coin) + TREND_GATE_FETCH_MARGIN_H
    when that is larger -- enough closed bars for next_trade's trend gate to
    actually compute the span-hour return instead of silently skipping it
    for want of data. Pure."""
    from services.strategies.next_trade import trend_gate_span_for
    span = trend_gate_span_for(coin) or 0
    return max(DEFAULT_HOURS_1H, span + TREND_GATE_FETCH_MARGIN_H)


_LEVELS_CACHE: Dict[str, Any] = {}
# oil_desk.js polls GET /api/desks/{id}/levels every REFRESH_MS (60s,
# pathiel/static/oil_desk.js) — see tests/test_page_polling's sibling check
# in services/strategies/tests/test_levels.py,
# test_levels_ttl_covers_one_full_desk_poll_interval, which fails the build
# if the two drift apart again. At the old 20s TTL every single 60s poll
# was a cache MISS (20s < 60s), so the Levels card paid the full two
# candleSnapshot reads (weight 20 each) on every refresh forever — the
# budget fix (af82adf) stopped those reads from being REFUSED, it never
# made them free. 1h/15m candles do not change meaningfully inside one
# minute, so caching across the page's own poll cycle costs no real
# freshness; 75s gives one poll's worth of margin for request jitter.
LEVELS_TTL_S = 75.0
_mono = time.monotonic


def summarize_coin(coin: str, now_ms: Optional[int] = None) -> Dict[str, Any]:
    """summarize() for `coin`, fetching its own candles and mid. Raises
    RuntimeError if the mid cannot be read at all (no reads failed, there is
    simply no live level to show) - callers (the HTTP route) turn that into
    a clear error, never a 200 with made-up numbers. Cached LEVELS_TTL_S per
    coin: two candle reads at weight 20 each per call, polled by every open
    Desk page (budget starvation, 2026-10-23)."""
    hit = _LEVELS_CACHE.get(coin)
    if now_ms is None and hit is not None and _mono() - hit[0] < LEVELS_TTL_S:
        return dict(hit[1])
    out = _summarize_coin_uncached(coin, now_ms)
    if now_ms is None:
        _LEVELS_CACHE[coin] = (_mono(), dict(out))
    return out


def _summarize_coin_uncached(coin: str, now_ms: Optional[int] = None) -> Dict[str, Any]:
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    mid = fetch_mid(coin)
    if mid is None:
        raise RuntimeError(f"could not read a live mid for {coin}")
    c1h = fetch_candles(coin, "1h", hours_1h_for(coin), now_ms)
    c15 = fetch_candles(coin, "15m", 3, now_ms)
    out = summarize(c1h, c15, mid, now_ms)
    out["coin"] = coin
    return out


def check(entry: float, stop: float, tp: float, leverage: Optional[float] = None,
         max_leverage: float = 20.0) -> Dict[str, Any]:
    """The R gate, plus the leverage gate when `leverage` is given:
    liquidation price, how much of the liquidation distance the stop uses,
    and the highest leverage this stop allows (the "20x line")."""
    from pathiel.agents.bracket import check_levels, check_leverage
    out = {"entry": entry, "stop": stop, "tp": tp, **check_levels(entry, stop, tp)}
    if leverage:
        lev = check_leverage(entry, stop, leverage, max_leverage)
        out["leverage"] = lev
        out["ok"] = bool(out.get("ok")) and bool(lev.get("ok"))
        out["verdict"] = (f"{leverage:g}x OK" if lev.get("ok")
                          else f"no {leverage:g}x: max {lev.get('max_leverage_ok')}x for this stop")
    return out


__all__ = ["HOUR_MS", "closed_only", "true_range", "atr", "price_range", "summarize", "check",
          "fetch_candles", "fetch_mid", "summarize_coin", "hours_1h_for", "DEFAULT_HOURS_1H",
          "TREND_GATE_FETCH_MARGIN_H"]
