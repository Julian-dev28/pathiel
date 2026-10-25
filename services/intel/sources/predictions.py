"""Prediction-market odds: Polymarket Gamma API and Kalshi public market
data. Searched by `spec.prediction_market_queries`.

Verified live 2026-10-17:
  Polymarket  gamma-api.polymarket.com/public-search — public, no key,
              full-text search over events/markets, HTTP 200. (The plain
              `/markets` list endpoint works too but has no text-search
              param; public-search is what actually answers
              "search by spec.prediction_market_queries".)
  Kalshi      api.elections.kalshi.com/trade-api/v2/* — public, no key,
              HTTP 200 for market/series/event reads despite the
              "elections" subdomain (trading-api.kalshi.com, the other
              documented host, returned 401 on the same unauthenticated
              GET). No full-text search endpoint was found on the public
              API, so this fetcher keyword-matches against the `/series`
              catalog in the Commodities + Financials categories (same
              deterministic match_score used for news) rather than
              guessing at an undocumented search parameter. Confirmed real
              commodity price-range ladders exist under series like
              KXWTIW (WTI weekly range), KXWTIMAX (WTI monthly high),
              KXBRENTMON (Brent) — exactly the "implied distribution for
              WTI" the brief calls out as a real edge; exposed as one
              MarketOdds row per strike/bucket so the thesis can read the
              whole ladder.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from contracts.strategies.models import DeskSpec, MarketOdds
from services.intel import http_client
from services.intel.scoring import match_score
from services.intel.textnorm import stable_id

POLYMARKET_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
KALSHI_SERIES_URL = "https://api.elections.kalshi.com/trade-api/v2/series"
KALSHI_EVENTS_URL = "https://api.elections.kalshi.com/trade-api/v2/events"

# Only categories known to carry desk-relevant series (commodities for
# oil/gold, financials for rates/crypto-adjacent). Scanning every category
# would cost latency for no gain — see the 15s cold-build budget.
KALSHI_CATEGORIES = ("Commodities", "Financials")
MIN_VOLUME_USD = 1.0  # "keep active, liquid markets" — drop zero-volume noise


def fetch_polymarket(spec: DeskSpec, *, per_query: int = 15
                      ) -> Tuple[List[MarketOdds], Optional[str]]:
    if not spec.prediction_market_queries:
        return [], None
    out: Dict[str, MarketOdds] = {}
    errors = []
    for q in spec.prediction_market_queries:
        data, err = http_client.fetch_json(POLYMARKET_SEARCH_URL, params={
            "q": q, "limit_per_type": per_query,
        })
        if err:
            errors.append(f"predictions:polymarket:{q[:20]}:{err}")
            continue
        if not isinstance(data, dict):
            errors.append(f"predictions:polymarket:{q[:20]}:bad_shape")
            continue
        for event in data.get("events") or []:
            if not event.get("active") or event.get("closed"):
                continue
            slug = event.get("slug") or event.get("id")
            for m in event.get("markets") or []:
                if not m.get("active") or m.get("closed"):
                    continue
                try:
                    outcomes = json.loads(m.get("outcomes") or "[]")
                    prices = json.loads(m.get("outcomePrices") or "[]")
                except (json.JSONDecodeError, TypeError):
                    continue
                if not outcomes or not prices or len(outcomes) != len(prices):
                    continue
                yes_idx = outcomes.index("Yes") if "Yes" in outcomes else 0
                try:
                    prob = float(prices[yes_idx])
                except (ValueError, IndexError):
                    continue
                vol = float(m.get("volumeNum") or 0)
                if vol < MIN_VOLUME_USD:
                    continue
                mkt_slug = m.get("slug") or m.get("id")
                mid = stable_id("odds", "polymarket", str(m.get("id") or mkt_slug))
                closes = m.get("endDate")
                closes_dt = None
                if closes:
                    try:
                        closes_dt = datetime.fromisoformat(closes.replace("Z", "+00:00"))
                    except ValueError:
                        closes_dt = None
                out[mid] = MarketOdds(
                    id=mid, venue="polymarket",
                    question=(m.get("question") or event.get("title") or "")[:300],
                    probability=round(min(1.0, max(0.0, prob)), 4), volume_usd=vol,
                    url=f"https://polymarket.com/event/{slug}" if slug else "about:blank",
                    closes_at=closes_dt,
                )
    return list(out.values()), ("; ".join(errors) if errors else None)


def _kalshi_series_catalog() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    all_series: List[Dict[str, Any]] = []
    for cat in KALSHI_CATEGORIES:
        data, err = http_client.fetch_json(KALSHI_SERIES_URL, params={"category": cat})
        if err:
            return all_series, f"predictions:kalshi_series:{cat}:{err}"
        if isinstance(data, dict):
            all_series.extend(data.get("series") or [])
    return all_series, None


def _matching_series_tickers(series: List[Dict[str, Any]], queries: List[str],
                              *, threshold: float = 0.5, max_series: int = 4) -> List[str]:
    best_by_ticker: Dict[str, float] = {}
    for s in series:
        title = s.get("title") or ""
        ticker = s.get("ticker") or ""
        if not ticker:
            continue
        best = max((match_score(f"{title} {ticker}", [q]) for q in queries), default=0.0)
        if best >= threshold:
            # Same ticker can appear once per KALSHI_CATEGORIES scan (a
            # series could plausibly be tagged under more than one
            # category) — keep its best score once, not once per sighting,
            # so fetch_kalshi never fetches the same series' events twice.
            best_by_ticker[ticker] = max(best_by_ticker.get(ticker, 0.0), best)
    ranked = sorted(best_by_ticker.items(), key=lambda t: t[1], reverse=True)
    return [ticker for ticker, _score in ranked[:max_series]]


def fetch_kalshi(spec: DeskSpec, *, max_markets_per_series: int = 12
                  ) -> Tuple[List[MarketOdds], Optional[str]]:
    if not spec.prediction_market_queries:
        return [], None
    series, err = _kalshi_series_catalog()
    if err and not series:
        return [], err
    queries = list(spec.prediction_market_queries) + [i.label for i in spec.instruments]
    tickers = _matching_series_tickers(series, queries)
    if not tickers:
        return [], err  # err may be None (no catalog failure, just no match)
    out: List[MarketOdds] = []
    errors = [err] if err else []
    for ticker in tickers:
        data, ev_err = http_client.fetch_json(KALSHI_EVENTS_URL, params={
            "series_ticker": ticker, "with_nested_markets": "true", "status": "open",
        })
        if ev_err:
            errors.append(f"predictions:kalshi_events:{ticker}:{ev_err}")
            continue
        if not isinstance(data, dict):
            errors.append(f"predictions:kalshi_events:{ticker}:bad_shape")
            continue
        for event in (data.get("events") or [])[:3]:
            for m in (event.get("markets") or [])[:max_markets_per_series]:
                if m.get("status") != "active":
                    continue
                bid = float(m.get("yes_bid_dollars") or 0)
                ask = float(m.get("yes_ask_dollars") or 1)
                if ask - bid > 0.9:  # near full [0,1] spread == no real liquidity
                    continue
                prob = (bid + ask) / 2.0
                close_time = m.get("close_time")
                closes_dt = None
                if close_time:
                    try:
                        closes_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                    except ValueError:
                        closes_dt = None
                mid = stable_id("odds", "kalshi", m.get("ticker") or "")
                out.append(MarketOdds(
                    id=mid, venue="kalshi", question=(m.get("title") or "")[:300],
                    probability=round(min(1.0, max(0.0, prob)), 4),
                    volume_usd=float(m.get("volume_fp") or 0) or None,
                    url=f"https://kalshi.com/markets/{ticker.lower()}",
                    closes_at=closes_dt,
                ))
    return out, ("; ".join(errors) if errors else None)


# api.py fans these out to its own thread pool — see that module's 15s
# cold-build budget note.
ATOMIC_FETCHERS = (fetch_polymarket, fetch_kalshi)
