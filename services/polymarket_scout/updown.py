"""5-minute up/down markets — AI read on the CURRENT rolling window.

These are Polymarket's `{asset}-updown-5m-{epoch}` markets: a new one every 5
minutes, slug carries the window-start epoch, endDate = start + 300s. The URL the
operator pasted was one specific (expired) window; `current_slug` always resolves
the LIVE one from the clock, so the read follows the market as it rolls.

HONESTY FIRST (W-Z1): this is the pure latency lane — the gabagool arb pocket,
bot-saturated, measured 0/60 tradeable, and the explicit target of the 2026 taker
fee. Being *right* does not beat being *fast* here, and a 5-minute horizon is too
short for the LLM's news-synthesis edge (web search is useless — nothing resolves
in 5 min). So this is SHADOW ONLY, zero capital, forever unless the ledger proves
the impossible. What it CAN do is read live BTC momentum and record the AI's
directional call, so the operator can watch — and the ledger can confirm it is a
coin flip minus fees.

Context fed to the model is live price action, not web search: Binance 1m klines
(keyless) → last-5m / last-15m move, range, and where price sits in it.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

from services.polymarket_scout import ledger
from services.polymarket_scout.scout import CLOB, GAMMA, _curl_get, _parse_tokens, market_yes_prob


def clob_midpoint(token_id: str, http_get: Callable[[str], Any] = _curl_get) -> Optional[float]:
    """Live order-book midpoint for one token, or None. This is the price the
    Polymarket app shows — Gamma's cached `outcomePrices` lags it badly (measured
    2026-07-26: Gamma UP 0.575 while the live CLOB mid was 0.885 on the same
    5-min window). For a 5-min market that lag is the whole game."""
    if not token_id:
        return None
    d = http_get(f"{CLOB}/midpoint?token_id={token_id}")
    try:
        return float(d.get("mid")) if isinstance(d, dict) and d.get("mid") is not None else None
    except (TypeError, ValueError):
        return None


def live_up_prob(market: Optional[Dict[str, Any]],
                 http_get: Callable[[str], Any] = _curl_get) -> Optional[float]:
    """UP probability from the LIVE CLOB midpoint, falling back to Gamma's
    outcomePrices only if the book call fails. Always prefer the book."""
    if not market:
        return None
    toks = _parse_tokens(market)
    if len(toks) == 2:
        mid = clob_midpoint(toks[0], http_get)
        if mid is not None:
            return mid
    return market_yes_prob(market)

WINDOW_S = 300
BOOK = "updown_5m"
BINANCE = "https://api.binance.com/api/v3/klines"
# asset symbol on Polymarket slug -> Binance pair
_PAIRS = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT",
          "doge": "DOGEUSDT"}

_SYS = (
    "You are a short-horizon crypto momentum trader. You are given LIVE price "
    "action for one asset and must estimate the probability it closes UP over the "
    "next ~5 minutes (last traded price at the window close above the price now). "
    "There is no news edge at this horizon — reason ONLY from the momentum, range "
    "position and recent candles given. Be honest: 5-minute direction is close to "
    "a coin flip, so stay near 0.50 unless the tape genuinely leans. Reply with "
    'ONLY this JSON on the last line: {"verdict":"UP"|"DOWN","up_prob":<0..1>,'
    '"reasoning":"<1-2 sentences>"}'
)


def window_start(now: Optional[float] = None) -> int:
    now = time.time() if now is None else now
    return int(now // WINDOW_S) * WINDOW_S


def current_slug(asset: str = "btc", now: Optional[float] = None) -> str:
    return f"{asset.lower()}-updown-5m-{window_start(now)}"


def current_market(asset: str = "btc", now: Optional[float] = None,
                   http_get: Callable[[str], Any] = _curl_get) -> Optional[Dict[str, Any]]:
    slug = current_slug(asset, now)
    d = http_get(f"{GAMMA}/markets?slug={slug}")
    if isinstance(d, list) and d and isinstance(d[0], dict):
        return d[0]
    return None


# ── live price context (Binance 1m klines, keyless) ──────────────────────────
def _klines(pair: str, interval: str = "1m", limit: int = 16,
            runner: Optional[Callable] = None) -> List[List[float]]:
    url = f"{BINANCE}?symbol={pair}&interval={interval}&limit={limit}"
    try:
        if runner is not None:
            raw = runner(url)
        else:
            out = subprocess.run(["curl", "-s", "--max-time", "10", url],
                                 capture_output=True, text=True, timeout=15)
            raw = json.loads(out.stdout)
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def _chg(series: List[float], n: int) -> float:
    return (series[-1] - series[-1 - n]) / series[-1 - n] * 100.0 \
        if len(series) > n and series[-1 - n] else 0.0


def _trend3(series: List[float]) -> str:
    """Direction over the last 3 bars: up if each step is >=, down if each <=,
    else mixed. This is the operator's 'consider the last-3-bar trend' — a
    cleaner signal than a single close, per-timeframe."""
    if len(series) < 4:
        return "flat"
    a, b, c, d = series[-4], series[-3], series[-2], series[-1]
    if d > c > b or (d > a and d >= c):
        return "up"
    if d < c < b or (d < a and d <= c):
        return "down"
    return "mixed"


def price_context(asset: str = "btc", runner: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    """Multi-timeframe momentum for a 5-min call: 1-SECOND, 1-MINUTE and 5-MINUTE
    bars, each with its recent momentum AND its last-3-bar trend. A single
    resolution is a thin read; stacking them lets the model separate a fresh 1s
    bounce from a flat 5m/15m backdrop (the operator's own reasoning pattern).

    Layers:
      1s  (last 60s)  -> 10s/30s/60s move + last-3-bar (3s) trend + recent closes
      1m  (last 16m)  -> 1m/5m/15m move + last-3-bar (3m) trend + range position
      5m  (last 12)   -> 5m/15m/30m move + last-3-bar (15m) trend  <- the 15m trend

    `runner` (tests) gets the full URL; it must branch on interval=1s/1m/5m."""
    pair = _PAIRS.get(asset.lower())
    if not pair:
        return None
    m1 = _klines(pair, "1m", 16, runner=runner)
    if len(m1) < 6:
        return None
    s1 = _klines(pair, "1s", 60, runner=runner)
    m5 = _klines(pair, "5m", 12, runner=runner)

    c1 = [float(k[4]) for k in m1]
    hi15, lo15 = max(float(k[2]) for k in m1[-15:]), min(float(k[3]) for k in m1[-15:])
    ctx: Dict[str, Any] = {
        "asset": asset.upper(),
        # kept flat for back-compat with existing callers/UI
        "chg_5m_pct": round(_chg(c1, 5), 3),
        "chg_15m_pct": round(_chg(c1, 15), 3),
        "range_pos_15m": round((c1[-1] - lo15) / (hi15 - lo15), 3) if hi15 > lo15 else 0.5,
        "last6_closes": [round(c, 2) for c in c1[-6:]],
        "m1": {"chg_1m": round(_chg(c1, 1), 3), "chg_5m": round(_chg(c1, 5), 3),
               "chg_15m": round(_chg(c1, 15), 3), "trend3": _trend3(c1),
               "last3": [round(c, 2) for c in c1[-3:]]},
    }
    if len(s1) >= 10:
        sc = [float(k[4]) for k in s1]
        ctx["price"] = sc[-1]
        ctx["chg_10s_pct"] = round(_chg(sc, 10), 4)
        ctx["chg_30s_pct"] = round(_chg(sc, 30), 4)
        ctx["s1"] = {"chg_10s": round(_chg(sc, 10), 4), "chg_30s": round(_chg(sc, 30), 4),
                     "chg_60s": round(_chg(sc, min(60, len(sc) - 1)), 4),
                     "trend3": _trend3(sc), "last5": [round(c, 2) for c in sc[-5:]]}
    else:
        ctx["price"] = c1[-1]
    if len(m5) >= 4:
        c5 = [float(k[4]) for k in m5]
        ctx["m5"] = {"chg_5m": round(_chg(c5, 1), 3), "chg_15m": round(_chg(c5, 3), 3),
                     "chg_30m": round(_chg(c5, 6), 3), "trend3_15m": _trend3(c5),
                     "last3": [round(c, 2) for c in c5[-3:]]}
    ctx["resolution"] = "+".join(k for k, v in (("1s", "s1" in ctx), ("1m", True),
                                                ("5m", "m5" in ctx)) if v)
    return ctx


def live_price(asset: str = "btc", now: Optional[float] = None,
               http_get: Callable[[str], Any] = _curl_get) -> Dict[str, Any]:
    """JUST the live YES/NO from the CLOB book + the countdown — no brain, no
    cache. Cheap enough to poll every few seconds so the panel's market % tracks
    the book in real time, the way the app does."""
    import calendar
    now = time.time() if now is None else now
    m = current_market(asset, now, http_get=http_get)
    up = live_up_prob(m, http_get) if m else None
    end = (m or {}).get("endDate") or ""
    try:
        end_s = calendar.timegm(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S"))
        secs = max(0, int(end_s - now))
    except Exception:
        secs = None
    return {"asset": asset.upper(), "slug": current_slug(asset, now),
            "mkt_up": up, "seconds_left": secs, "end_date": end}


def build_prompt(ctx: Dict[str, Any], mkt_yes: Optional[float]) -> str:
    s1, m1, m5 = ctx.get("s1"), ctx.get("m1") or {}, ctx.get("m5")
    L = [f"ASSET: {ctx['asset']}  price={ctx['price']}",
         "Read the tape across timeframes; weight the fast layers for the next 5 "
         "minutes but keep the slow layers as context (a 1s bounce inside a flat "
         "5m/15m backdrop is a thin edge)."]
    if s1:
        L.append(f"1s  (last minute): 10s {s1['chg_10s']:+.4f}%  30s {s1['chg_30s']:+.4f}%  "
                 f"60s {s1['chg_60s']:+.4f}%  trend(3 bars)={s1['trend3']}  closes={s1['last5']}")
    L.append(f"1m  (last 16m): 1m {m1.get('chg_1m',0):+.3f}%  5m {m1.get('chg_5m',0):+.3f}%  "
             f"15m {m1.get('chg_15m',0):+.3f}%  trend(3 bars)={m1.get('trend3')}  "
             f"closes={m1.get('last3')}  range_pos_15m={ctx.get('range_pos_15m')}")
    if m5:
        L.append(f"5m  (last hour): 5m {m5['chg_5m']:+.3f}%  15m {m5['chg_15m']:+.3f}%  "
                 f"30m {m5['chg_30m']:+.3f}%  trend(last 3 5m bars = 15m)={m5['trend3_15m']}  "
                 f"closes={m5['last3']}")
    L.append(f"market currently prices UP at {mkt_yes if mkt_yes is not None else 'n/a'}.")
    L.append("P(UP over the next 5 minutes)? JSON only.")
    return "\n".join(L)


def _parse(body: str) -> Optional[Dict[str, Any]]:
    import re
    for cand in reversed(re.findall(r'\{[^{}]*"up_prob"[^{}]*\}', body or "")):
        try:
            o = json.loads(cand)
            p = float(o.get("up_prob"))
        except Exception:
            continue
        if 0.0 <= p <= 1.0:
            return {"up_prob": max(0.01, min(0.99, p)),
                    "verdict": "UP" if p >= 0.5 else "DOWN",
                    "reasoning": str(o.get("reasoning") or "")[:300]}
    return None


def analyze(asset: str = "btc", brain: Any = None, now: Optional[float] = None,
            http_get: Callable[[str], Any] = _curl_get,
            kline_runner: Optional[Callable] = None,
            record: bool = True, record_fn: Callable = ledger.record,
            web_search: bool = False) -> Dict[str, Any]:
    """One read on the current window: live momentum -> AI P(up) -> record.

    `web_search=True` gives the model live web lookup so a breaking catalyst (a
    Fed headline, an exchange-halt, a liquidation cascade) that could move BTC
    inside the window is in scope — the operator's ask. It costs 1-4 min though,
    a big chunk of a 5-min window, so it is opt-in.

    Returns a display dict (market + context + verdict), always — a failed brain
    call or missing data yields a row with `up_prob=None` rather than raising."""
    now = time.time() if now is None else now
    mkt = current_market(asset, now, http_get=http_get)
    ctx = price_context(asset, runner=kline_runner)
    out: Dict[str, Any] = {
        "asset": asset.upper(), "slug": current_slug(asset, now),
        "market_id": str(mkt.get("id")) if mkt else None,
        "question": (mkt or {}).get("question"),
        "end_date": (mkt or {}).get("endDate"),
        "mkt_up": live_up_prob(mkt, http_get) if mkt else None,   # LIVE CLOB, not stale Gamma
        "context": ctx, "up_prob": None, "verdict": None, "reasoning": "",
        "edge": None, "ts": int(now * 1000),
    }
    if ctx is None:
        out["reasoning"] = "no live price data"
        return out
    if brain is None:
        from hermes_trader.agents.ai_brain import get_brain
        brain = get_brain()
    out["web_search"] = bool(web_search)
    try:
        body = brain.complete(_SYS, build_prompt(ctx, out["mkt_up"]), web_search=web_search)
    except Exception as exc:
        out["reasoning"] = f"brain error: {exc}"
        return out
    v = _parse(str(body or ""))
    if v is None:
        out["reasoning"] = "unparseable verdict"
        return out
    out.update({"up_prob": v["up_prob"], "verdict": v["verdict"], "reasoning": v["reasoning"]})
    if out["mkt_up"] is not None:
        out["edge"] = round(v["up_prob"] - out["mkt_up"], 4)
    if record and out["market_id"]:
        record_fn(market_id=out["market_id"], question=out["question"] or "",
                  side="YES" if v["verdict"] == "UP" else "NO",
                  token_id="", llm_yes=v["up_prob"], mkt_yes=out["mkt_up"] or 0.5,
                  fill_px=out["mkt_up"] or 0.5, edge=out["edge"] or 0.0,
                  end_date=out["end_date"] or "", category=asset.lower(),
                  reasoning=v["reasoning"], lane="updown_5m",
                  meta={"context": ctx, "shadow": True})
    return out


# ── cache for the dashboard (so a page load never triggers a brain call) ─────
def _cache_path() -> str:
    import os
    return os.path.join(ledger._state_dir(), "updown.json")


def load(now: Optional[float] = None, refresh_price: bool = True,
         http_get: Callable[[str], Any] = _curl_get) -> Dict[str, Any]:
    """Latest reads the refresher wrote, with the LIVE market price refreshed.

    The AI read (`up_prob`) is from the last analysis, but the MARKET price and
    the countdown must be current — otherwise the panel shows a window at its
    50/50 birth while the real book has moved to 8c/92c near close (the mismatch
    the operator caught). So per read we re-fetch the CURRENT window's price
    (one cheap Gamma call) and recompute the edge. `current_window` flags whether
    the AI read is for the window that is live right now; if it rolled, the read
    is stale and the panel should say so."""
    import calendar
    import os
    now = time.time() if now is None else now
    empty = {"status": "empty", "generated_at": None, "reads": []}
    try:
        with open(_cache_path()) as fh:
            payload = json.load(fh)
    except Exception:
        return empty
    if not isinstance(payload, dict):
        return empty
    for r in payload.get("reads") or []:
        asset = str(r.get("asset") or "btc").lower()
        cur_slug = current_slug(asset, now)
        r["current_window"] = (r.get("slug") == cur_slug)
        if refresh_price:
            live = current_market(asset, now, http_get=http_get)
            if live is not None:
                lp = live_up_prob(live, http_get)          # LIVE CLOB midpoint
                if lp is not None:
                    r["mkt_up"] = lp
                    if r.get("up_prob") is not None:
                        r["edge"] = round(float(r["up_prob"]) - lp, 4)
                r["live_slug"] = cur_slug
                r["end_date"] = live.get("endDate") or r.get("end_date")
        end = r.get("end_date") or ""
        try:
            end_s = calendar.timegm(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S"))
            r["stale"] = now > end_s
            r["seconds_left"] = max(0, int(end_s - now))
        except Exception:
            r["stale"] = True
            r["seconds_left"] = 0
    gen = int(payload.get("generated_at") or 0)
    payload["age_s"] = int(now - gen) if gen else None
    payload["status"] = "ok"
    return payload


def refresh(assets: Optional[List[str]] = None, brain: Any = None,
            record: bool = True, now: Optional[float] = None,
            http_get: Callable[[str], Any] = _curl_get,
            kline_runner: Optional[Callable] = None,
            web_search: bool = False) -> Dict[str, Any]:
    """Read every configured asset's current window, write the cache. Used by the
    5-min scheduler job and callable by hand."""
    import os
    assets = assets or ["btc"]
    now = time.time() if now is None else now
    reads = [analyze(a, brain=brain, now=now, record=record, web_search=web_search,
                     http_get=http_get, kline_runner=kline_runner) for a in assets]
    payload = {"generated_at": int(now), "reads": reads}
    try:
        os.makedirs(os.path.dirname(_cache_path()), exist_ok=True)
        tmp = _cache_path() + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, _cache_path())
    except Exception:
        pass
    return payload


# ── LIVE Hyperliquid execution (operator-armed 2026-07-26) ───────────────────
# Polymarket order placement is geoblocked/keyless, so "not shadow" means trading
# the AI's up/down CALL on a Hyperliquid BTC perp instead: long UP, short DOWN,
# flattened at the window close. On record this is a latency-disadvantaged coin
# flip and 5-min churn is fee-dominated (-EV). Tiny, bounded, killable.
LIVE_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "place": False,
    "coin": "BTC",
    "min_lean": 0.06,          # only trade when |up_prob - 0.5| >= this (a real lean)
    # Hyperliquid rejects orders below ~$10.92 notional, so an equity-fraction
    # size ($0.65 on a small account) never places. A fixed notional clears the
    # floor; keep it small. 0 = fall back to equity_frac × equity × leverage.
    "notional_usd": 15.0,
    "leverage": 3,
    "stop_pct": 0.01,          # BTC 1% stop on a 5-min scalp
    "tp_pct": 0.015,
}


def live_cfg(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = (config or {}).get("updown_live") or {}
    return {**LIVE_DEFAULTS, **raw} if isinstance(raw, dict) else dict(LIVE_DEFAULTS)


def lean(read: Dict[str, Any]) -> float:
    """Signed distance from a coin flip: + = leans UP, - = leans DOWN."""
    p = read.get("up_prob")
    return 0.0 if p is None else round(float(p) - 0.5, 4)


def live_should_trade(read: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    return read.get("up_prob") is not None and abs(lean(read)) >= float(cfg.get("min_lean", 0.06))


def to_hl_analysis(read: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Build the Hyperliquid perp analysis dict from an up/down read. LONG on UP,
    SHORT on DOWN; tiny bounded size via the executor's override keys; a short
    stop/tp for the 5-min horizon. `strategy_book='updown_5m'` clears the
    books-only gate; every risk gate still runs. `window_end` rides in meta so
    the loop can flatten at the window close."""
    import uuid
    up = read.get("verdict") == "UP"
    px = float((read.get("context") or {}).get("price") or 0)
    stop_pct = float(cfg.get("stop_pct") or 0.01)
    tp_pct = float(cfg.get("tp_pct") or 0.015)
    stop = px * (1 - stop_pct) if up else px * (1 + stop_pct)
    tp = px * (1 + tp_pct) if up else px * (1 - tp_pct)
    import calendar
    try:
        end_s = calendar.timegm(time.strptime((read.get("end_date") or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        end_s = int(time.time()) + WINDOW_S
    return {
        "id": str(uuid.uuid4()),
        "coin": str(cfg.get("coin") or "BTC"),
        "verdict": "LONG" if up else "SHORT",
        "side": "long" if up else "short",
        "confidence": abs(lean(read)) * 2,          # 0..1 from the lean
        "entryPx": px, "stopPx": round(stop, 6), "tpPx": round(tp, 6),
        "reasoning": read.get("reasoning", ""),
        "composite_score": 0.0,
        "strategy_book": BOOK,
        "leverage_override": int(cfg.get("leverage") or 3),
        # fixed notional to clear the HL minimum; the executor reads
        # strategy_book_notional as an absolute $ size when > 0.
        "strategy_book_notional": float(cfg.get("notional_usd") or 0),
        "strategy_book_equity_frac_override": float(cfg.get("equity_frac") or 0),
        "source": "updown_live", "ai_brain_provider": "updown_live",
        "updown_window_end": int(end_s),
        "updown_slug": read.get("slug"),
    }


import os as _os


def _live_state_path() -> str:
    return _os.path.join(ledger._state_dir(), ".updown_live.json")


def _read_live_state() -> Dict[str, Any]:
    try:
        with open(_live_state_path()) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _write_live_state(st: Dict[str, Any]) -> None:
    try:
        _os.makedirs(_os.path.dirname(_live_state_path()), exist_ok=True)
        with open(_live_state_path(), "w") as fh:
            json.dump(st, fh)
    except Exception:
        pass


def _position_open(positions: Any, coin: str) -> bool:
    """True if `coin` appears with non-zero size in the loop's positions list."""
    for p in (positions or []):
        try:
            pc = p.get("coin") or (p.get("position") or {}).get("coin")
            sz = float((p.get("position") or {}).get("szi") or p.get("szi") or 0)
        except Exception:
            pc, sz = None, 0.0
        if pc == coin and abs(sz) > 0:
            return True
    return False


def live_maybe_run(config: Dict[str, Any], universe: Any = None, positions: Any = None,
                   execute_fn: Optional[Callable] = None, close_fn: Optional[Callable] = None,
                   brain: Any = None, now: Optional[float] = None,
                   asset: str = "btc") -> Optional[Dict[str, Any]]:
    """LIVE Hyperliquid execution of the up/down call. No-op unless
    updown_live.enabled. FLATTENS a prior window's position at its close, then —
    once per window — takes a fresh read and (if place and the lean clears
    min_lean) opens a small BTC perp via execute_fn. Never raises into the loop.

    Order matters: flatten FIRST (close the expiring window), then maybe open the
    new one, so the book is never long two windows at once."""
    cfg = live_cfg(config)
    if not cfg.get("enabled"):
        return None
    now = time.time() if now is None else now
    coin = str(cfg.get("coin") or "BTC")
    st = _read_live_state()
    out: Dict[str, Any] = {"flattened": False, "opened": False}

    # 1) flatten the previous window at/after its close
    win_end = float(st.get("window_end") or 0)
    if win_end and now >= win_end and _position_open(positions, coin) and close_fn is not None:
        try:
            close_fn(coin)
            out["flattened"] = True
        except Exception as exc:
            logger.warning(f"[updown-live] flatten failed: {exc}")
        st.pop("window_end", None)
        _write_live_state(st)

    # 2) once per window: fresh read + maybe open
    cur = window_start(now)
    if st.get("last_window") == cur:
        return out                                   # already acted this window
    st["last_window"] = cur
    _write_live_state(st)
    read = analyze(asset, brain=brain, now=now, record=True, web_search=False)
    out["read"] = {k: read.get(k) for k in ("up_prob", "verdict", "mkt_up", "edge")}
    if not (cfg.get("place") and live_should_trade(read, cfg) and execute_fn is not None):
        return out
    if _position_open(positions, coin):              # don't stack windows
        out["skipped"] = "position already open"
        return out
    analysis = to_hl_analysis(read, cfg)
    res = execute_fn(analysis)
    out["opened"] = bool(isinstance(res, dict) and res.get("executed"))
    out["result"] = res
    if out["opened"]:
        st["window_end"] = analysis.get("updown_window_end")
        _write_live_state(st)
    logger.info(f"[updown-live] window={cur} verdict={read.get('verdict')} "
                f"lean={lean(read):+.3f} opened={out['opened']} flattened={out['flattened']}")
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="AI read on the current 5-min up/down window(s).")
    ap.add_argument("--assets", default="btc", help="comma list, e.g. btc,eth,sol")
    ap.add_argument("--no-record", action="store_true")
    args = ap.parse_args()
    p = refresh([a.strip() for a in args.assets.split(",") if a.strip()],
                record=not args.no_record)
    for r in p["reads"]:
        up = f"{r['up_prob']:.2f}" if r["up_prob"] is not None else "—"
        mk = f"{r['mkt_up']:.2f}" if r["mkt_up"] is not None else "—"
        print(f"[{r['asset']}] {r['slug']} AI_up={up} mkt_up={mk} "
              f"edge={r['edge']} :: {r['reasoning'][:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
