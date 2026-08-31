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
import os
import logging
import time
import uuid
from statistics import median
from typing import Any, Callable, Dict, List, Optional, Set

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.book_helpers import bounded_exit_override
from hermes_trader.agents.book_helpers import execute_block_detail as _execute_block_detail
from hermes_trader.agents.book_helpers import execute_opened as _execute_opened
from hermes_trader.agents.book_helpers import last_pass_ms, load_seen, mark_pass, save_seen
from hermes_trader.agents.news_catalyst import (
    _title_relevant, _within_hours, rss_headlines,
)
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file
from hermes_trader.agents.rebalancer_owned import held_coins_with_dsl as _held_coins
from hermes_trader.models.types import BookAnalysis
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

_BOOK_NAME = "news_surge_multi"
_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_TS_FILE = state_file(".news_surge_multi_ts.json")
_BASELINE_FILE = state_file(".news_surge_multi_baseline.json")
_SEEN_FILE = state_file(".news_surge_multi_seen.json")
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
    return last_pass_ms(_TS_FILE)


def _mark_pass(now_ms: int) -> None:
    mark_pass(_TS_FILE, now_ms)


def _load_baseline() -> Dict[str, List[float]]:
    """Rolling per-coin article counts. A missing file is a cold start; an
    unreadable one is a fault, and the two must not look the same.

    Returning {} on corruption is silently expensive: every `prior` comes back
    empty, so `_surge` returns 1.0 for every coin, nothing is ever `breaking`,
    and the book cannot fire — while looking exactly like a quiet news day.
    _save_baseline then overwrites the damaged file with fresh single-entry
    lists, so the history is gone and the book stays unable to fire until
    _BASELINE_KEEP cycles have re-accrued. All of that with no log line.
    """
    if not os.path.exists(_BASELINE_FILE):
        return {}                                   # cold start, not a fault
    try:
        raw = json.load(open(_BASELINE_FILE))
    except Exception as exc:
        logger.warning(
            f"[news_surge_multi] baseline at {_BASELINE_FILE} is unreadable "
            f"({type(exc).__name__}: {exc}) — every surge reads neutral, so this "
            f"book cannot fire until the baseline re-accrues")
        return {}
    if not isinstance(raw, dict):
        logger.warning(
            f"[news_surge_multi] baseline at {_BASELINE_FILE} is a "
            f"{type(raw).__name__}, expected an object — discarding it")
        return {}
    return {str(k): [float(x) for x in v] for k, v in raw.items()}


def _save_baseline(b: Dict[str, List[float]]) -> None:
    try:
        with open(_BASELINE_FILE, "w") as fh:
            json.dump(b, fh)
    except Exception:
        pass


def _surge(count: int, prior: List[float]) -> float:
    """count / median(prior), guarded. No usable baseline -> surge 1.0
    (neutral), so a coin can NEVER be 'breaking' on its first read or before a
    baseline exists — this is the guard that stopped news_catalyst from firing a
    live entry off a single unbaselined spike (xyz:BE, 2026-07-12). A coin needs
    accrued history before a surge is meaningful; the raw count is still recorded
    so the baseline builds."""
    base = median(prior) if prior else 0.0
    if base <= 0:
        return 1.0
    return round(count / base, 2)


def _load_seen() -> Dict[str, int]:
    return load_seen(_SEEN_FILE)


def _save_seen(seen: Dict[str, int]) -> None:
    save_seen(_SEEN_FILE, seen)


def _live_analysis(coin: str, surge_x: float, n_recent: int, cfg: Dict[str, Any]) -> BookAnalysis:
    """Bounded live order (operator flip 2026-07-21): $20/10x, 6% stop, 1d — the
    exact validated geometry of news_surge_short. Rides the VALIDATED
    attention-fade DIRECTION (short a coverage spike); the multi-source firehose
    TRIGGER is itself unvalidated, so this carries the standard n=8 kill."""
    stop_pct = float(cfg.get("stop_pct", 6.0))
    leverage = max(1, int(cfg.get("leverage", 10)))
    hold_days = float(cfg.get("hold_days", 1.0))
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "SHORT", "side": "short",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (f"[{_BOOK_NAME}] multi-firehose coverage surge {surge_x:.1f}x "
                      f"(n={n_recent}) — fading attention spike"),
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": _BOOK_NAME,
        "strategy_book_notional": float(cfg.get("notional_usd", 20.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "min_short_volume_usd_override": float(cfg.get("min_volume_usd", 250_000.0)),
        "dsl_exit_override": bounded_exit_override(stop_pct, leverage, hold_days * 1440.0),
    }


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
        # This is a LIVE book — a persistent fetch failure means this pass
        # records nothing and opens nothing, indistinguishable from "no
        # surge today." Needs to be visible, not debug-only.
        logger.warning(f"[news-surge-multi] firehose fetch failed ({exc})")
        return 0

    shadow_only = bool(cfg.get("shadow_only", True))
    baseline = _load_baseline()
    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()
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
                     "shadow": shadow_only},
        })
    _save_baseline(baseline)
    n = shadow_ledger.record_many(_BOOK_NAME, rows)
    if n:
        nb = sum(1 for r in rows if r["meta"]["breaking"])
        logger.info(f"[news-surge-multi] recorded {n} read(s), {nb} breaking "
                    f"(pooled {len(pool)} headlines from {len(FIREHOSES)} firehoses)")

    # LIVE arm (operator flip 2026-07-21): breaking multi-source surge opens a
    # bounded $20/10x short. Every perception here is already a SCAN MOVER, so
    # a breaking read is an attention spike on a mover — as close to the
    # validated news_surge_short pattern as this signal gets. The TRIGGER is
    # unvalidated (zero forward grades, thin firehose coverage) so it carries
    # the n=8 kill: the autonomous cycle demotes it if EV<=0 at that bar.
    if bool(cfg.get("shadow_only", True)) or execute_fn is None:
        return n
    breaking_rows = [r for r in rows if r["meta"]["breaking"]]
    if not breaking_rows:
        return n
    opened_seen = _load_seen()
    held = _held_coins(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK_NAME)
    blocked_by_claim = claims.claimed_by_others(_BOOK_NAME)
    max_new = int(cfg.get("max_new_per_cycle", 1))
    opened = 0
    for r in sorted(breaking_rows, key=lambda x: -x["meta"]["surge_x"]):
        if opened >= max_new:
            break
        coin = r["coin"]
        day_key = f"{coin}:{now_ms // _DAY_MS}"
        if day_key in opened_seen or coin in held or coin in blocked_by_claim:
            continue
        if not claims.claim(coin, _BOOK_NAME):
            continue
        try:
            result = execute_fn(_live_analysis(coin, r["meta"]["surge_x"],
                                               r["meta"]["n_recent"], cfg))
            if _execute_opened(result):
                opened += 1
                opened_seen[day_key] = now_ms
                log_event({"event": "book_open", "book": _BOOK_NAME, "coin": coin,
                           "side": "short", "sig_t": r.get("signal_bar_t")})
                logger.info(f"[news-surge-multi] LIVE opened short {coin} "
                            f"(surge {r['meta']['surge_x']:.1f}x, n={r['meta']['n_recent']})")
            else:
                claims.release(coin, _BOOK_NAME)
                logger.warning(f"[news-surge-multi] {coin} not opened: "
                               f"{_execute_block_detail(result)}")
        except Exception as exc:
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[news-surge-multi] open {coin} failed: {exc}")
    if opened:
        cutoff = (now_ms - 60 * _DAY_MS) // _DAY_MS
        _save_seen({k: v for k, v in opened_seen.items()
                    if int(k.rsplit(":", 1)[1]) >= cutoff})
        claims.save()
    return n
