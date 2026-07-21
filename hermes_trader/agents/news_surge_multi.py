"""Multi-source news-surge recorder (worldmonitor thesis, 2026-07-21).

The `news_surge_short` book fires off a PER-COIN Google-News query. This
recorder tests whether a BROADER attention measure — a fixed pool of curated
finance/tech firehoses, entity-matched to our scan candidates — is a better
surge signal, using the exact dose-response the thesis test validated: on our
own 3,858-row news ledger, breaking-attention shorts graded +9.71%/sig (80%
win) vs +1.94% for non-breaking, a ~5x dose-response (lookahead-safe grader,
6% live stop). More/earlier attention should mean a bigger next-day move.

WHY POOL-THEN-MATCH instead of per-coin queries: it is O(feeds) not
O(coins) — fetch each firehose ONCE per pass (cached), then count relevant
headlines for every candidate against the shared pool. ~15 fetches covers the
whole universe, versus one query per coin.

ZERO CAPITAL. This only records to a new ledger (`news_surge_multi`); the
autonomous cycle grades it forward and, per the standing test->build->ship
flow, it earns a bounded $20/10x live arm ONLY if it grades EV+ AND beats the
live single-source `news_surge_short`. Records the validated SHORT side at the
live geometry (1d horizon, 6% stop) so the two ledgers are directly
comparable. Feed list: research/worldmonitor/rss-feeds-report.csv (the working
DIRECT firehoses; Google-proxied entries are dropped — we already read those).
"""
from __future__ import annotations

import json
import logging
import time
from statistics import median
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.news_catalyst import (
    _title_relevant, _within_hours, rss_headlines,
)
from hermes_trader.agents.rebalancer_owned import state_file

logger = logging.getLogger(__name__)

_BOOK_NAME = "news_surge_multi"
_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_TS_FILE = state_file(".news_surge_multi_ts.json")
_BASELINE_FILE = state_file(".news_surge_multi_baseline.json")
_MAX_COINS_PER_PASS = 12

# The working DIRECT finance/tech firehoses from worldmonitor's curated list
# (Google-proxied entries dropped — news_surge_short already reads those).
FIREHOSES: List[str] = [
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",   # CNBC markets
    "https://www.cnbc.com/id/19854910/device/rss/rss.html",    # CNBC tech
    "https://finance.yahoo.com/news/rssindex",
    "https://finance.yahoo.com/rss/topstories",
    "https://www.ft.com/rss/home",
    "https://seekingalpha.com/market_currents.xml",
    "https://techcrunch.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://www.theverge.com/rss/index.xml",
    "https://www.technologyreview.com/feed/",
    "https://www.zdnet.com/news/rss.xml",
    "https://www.techmeme.com/feed.xml",
    "https://www.engadget.com/rss.xml",
    "https://feeds.feedburner.com/fastcompany/headlines",
    "https://hnrss.org/frontpage",
]

# Breaking thresholds mirror news_catalyst so the two books grade like-for-like.
_BREAKING_MIN_RECENT = 3
_BREAKING_MIN_SURGE = 3.0
_HORIZON_DAYS = 1.0
_STOP_PCT = 6.0
_BASELINE_KEEP = 20        # rolling window of prior per-coin counts


def _last_pass_ms() -> int:
    try:
        raw = json.load(open(_TS_FILE))
        return int(raw.get("ts", 0)) if isinstance(raw, dict) else 0
    except Exception:
        return 0


def _mark_pass(now_ms: int) -> None:
    try:
        with open(_TS_FILE, "w") as fh:
            json.dump({"ts": now_ms}, fh)
    except Exception:
        pass


def _load_baseline() -> Dict[str, List[float]]:
    try:
        raw = json.load(open(_BASELINE_FILE))
        return {str(k): [float(x) for x in v] for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_baseline(b: Dict[str, List[float]]) -> None:
    try:
        with open(_BASELINE_FILE, "w") as fh:
            json.dump(b, fh)
    except Exception:
        pass


def _surge(count: int, prior: List[float]) -> float:
    """count / median(prior), guarded. No prior history -> surge 1.0 (neutral),
    so a coin's FIRST read never counts as breaking (matches news_catalyst,
    which needs a baseline before it can surge)."""
    base = median(prior) if prior else 0.0
    if base <= 0:
        return 1.0 if count <= 0 else float(count)  # first non-zero read is not yet a "surge"
    return round(count / base, 2)


def count_relevant(coin: str, headlines) -> int:
    """Recent (≤24h) pooled headlines that are ABOUT this coin — the same
    entity matcher (cashtag / ALL-CAPS ticker / name+context, with the xyz
    ticker→company aliases) news_surge_short uses, so the surge is measured on
    the identical relevance definition, just over a wider source pool."""
    sym = coin.split(":")[-1]
    equity = ":" in coin
    n = 0
    for a in headlines or []:
        if not _within_hours(a):
            continue
        if _title_relevant(sym, a.title, equity=equity):
            n += 1
    return n


def maybe_run(config: Dict[str, Any],
              perceptions: Optional[List[Dict[str, Any]]],
              positions: Optional[List[Dict[str, Any]]] = None,
              execute_fn: Optional[Callable] = None) -> int:
    """Call once per scan. Throttled to scan_interval_min. Pools the firehoses
    once, counts relevant headlines per candidate, records a SHORT signal with
    the multi-source surge. Zero capital — execute_fn is accepted for symmetry
    with the other recorders but is only used if a future operator flip sets
    shadow_only=false (kept record-only here). Returns rows recorded."""
    cfg = (config.get("news_surge_multi") or {})
    if not bool(cfg.get("enabled", True)):
        return 0
    now_ms = int(time.time() * 1000)
    interval_min = float(cfg.get("scan_interval_min", 30.0))
    if now_ms - _last_pass_ms() < interval_min * 60_000:
        return 0
    _mark_pass(now_ms)  # mark first: a failing fetch must not retry-storm

    try:
        pool = rss_headlines(feeds=FIREHOSES, limit=400)
    except Exception as exc:
        logger.debug(f"[news-surge-multi] firehose fetch failed ({exc})")
        return 0

    baseline = _load_baseline()
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for p in perceptions or []:
        coin = str(p.get("coin") or "")
        if not coin or coin in seen:
            continue
        seen.add(coin)
        if len(seen) > _MAX_COINS_PER_PASS:
            break
        try:
            mid = float(p.get("mid") or 0.0)
        except (TypeError, ValueError):
            mid = 0.0
        if mid <= 0:
            continue
        count = count_relevant(coin, pool)
        prior = baseline.get(coin, [])
        sx = _surge(count, prior)
        breaking = count >= _BREAKING_MIN_RECENT and sx >= _BREAKING_MIN_SURGE
        # update rolling baseline AFTER computing surge (no lookahead on itself)
        baseline[coin] = (prior + [float(count)])[-_BASELINE_KEEP:]
        rows.append({
            "coin": coin, "side": "short",
            "signal_bar_t": (now_ms // _HOUR_MS) * _HOUR_MS,
            "entry_ref_px": mid, "horizon_days": _HORIZON_DAYS, "stop_pct": _STOP_PCT,
            "meta": {"n_recent": count, "surge_x": sx, "breaking": bool(breaking),
                     "equity": ":" in coin, "n_feeds": len(FIREHOSES),
                     "shadow": True},
        })
    _save_baseline(baseline)
    n = shadow_ledger.record_many(_BOOK_NAME, rows)
    if n:
        nb = sum(1 for r in rows if r["meta"]["breaking"])
        logger.info(f"[news-surge-multi] recorded {n} read(s), {nb} breaking "
                    f"(pooled {len(pool)} headlines from {len(FIREHOSES)} firehoses)")
    return n
