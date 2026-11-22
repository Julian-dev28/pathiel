"""The six research MCP tools' implementations, as plain functions that
take/return JSON-serializable Python values. server.py wraps these as MCP
tools/call handlers; tests call them directly.

Every tool composes PUBLIC functions from services/intel, services/thesis
and services/strategies -- never a private (`_`-prefixed) internal, and
never services.strategies.next_trade / pathiel.agents.bracket / pathiel.
client.exchange (order-placement and signing code; see
tests/test_no_trading_imports.py) or anything that reads PATHIEL_STATE_DIR's
.env.local / the repo's own .env.local / ~/.pathiel (see coins.py's and
scheduled_events.load()'s docstrings for exactly which call would do that
and how each tool here avoids it with use_overlay=False).

Every tool catches its own data sources' failures and reports them in
`sources_failed` instead of raising -- same "never silently empty" contract
services/intel/api.py's build_packet uses, so one dead feed never takes
down the whole response. A tool only raises for a genuinely bad call (an
unsupported coin, neither coin nor query given); server.py turns that into
a clean tool error, never a crash.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from contracts.strategies.models import Catalyst, DeskSpec, Headline, Instrument, MarketOdds
from services.intel import scheduled_events
from services.intel.sources import catalysts as catalysts_src
from services.intel.sources import fred as fred_src
from services.intel.sources import news as news_src
from services.intel.sources import predictions as predictions_src
from services.research_mcp import coins as coins_mod
from services.research_mcp.concurrent import fetch_many
from services.strategies import journal
from services.strategies.levels import DEFAULT_HOURS_1H, atr, closed_only, fetch_candles, fetch_mid, price_range

# ── coins the data behind every COIN_GROUPS-aware tool actually covers ──────
_CURATED_CALENDAR_COINS = frozenset().union(*scheduled_events.COIN_GROUPS.values())

FRED_KEY_HELP = ("fred_series needs the user's own FRED_API_KEY in the environment. Get a free "
                "key at https://fred.stlouisfed.org/docs/api/api_key.html (no credit card), "
                "then export FRED_API_KEY=... before starting this server.")


# ── levels ────────────────────────────────────────────────────────────────

def summarize_levels(coin: str, now_ms: Optional[int] = None) -> Dict[str, Any]:
    """mid, the last closed 1h candle, 8h/24h/48h range, ATR14 (1h) and the
    last four closed 15m candles' range, for `coin` -- public Hyperliquid
    candle math, no key needed.

    This is services.strategies.levels.summarize()/summarize_coin()'s own
    math, reimplemented here from levels.py's PURE exported building blocks
    (fetch_candles, fetch_mid, closed_only, atr, price_range) instead of
    calling summarize_coin() directly, because summarize_coin() -> summarize()
    -> _breakout_levels()/hours_1h_for() import services.strategies.
    next_trade, which imports pathiel.agents.bracket -- the module that ALSO
    defines place_bracket/close_position/set_stop/set_target/sweep (its own
    docstring: "the operator's direct-trade path"). This server must never
    import order-placement code, so it skips next_trade's breakout_24h/
    closes_1h fields (never part of this tool's advertised output) and
    builds the rest from the same public pieces levels.py exports for this.

    Raises RuntimeError if the venue has no live mid for `coin` (no reads
    failed; there is simply no live level to show), same contract
    summarize_coin() uses."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    mid = fetch_mid(coin)
    if mid is None:
        raise RuntimeError(f"could not read a live mid for {coin}")
    c1h = fetch_candles(coin, "1h", DEFAULT_HOURS_1H, now_ms)
    c15 = fetch_candles(coin, "15m", 3, now_ms)
    closed = closed_only(c1h, now_ms)
    last = closed[-1] if closed else None
    r24 = price_range(closed[-24:])
    out: Dict[str, Any] = {
        "coin": coin,
        "mid": mid,
        "last_closed_1h": ({"open": float(last["o"]), "high": float(last["h"]),
                            "low": float(last["l"]), "close": float(last["c"]),
                            "closed_at_utc": time.strftime(
                                "%Y-%m-%dT%H:%M", time.gmtime((int(last["T"]) + 1) / 1000))}
                           if last else None),
        "range_8h": price_range(closed[-8:]),
        "range_24h": r24,
        "range_48h": price_range(closed[-48:]),
        "atr14_1h": atr(closed),
        "range_15m_last4": price_range(closed_only(c15, now_ms)[-4:]),
        "n_closed_1h": len(closed),
    }
    if r24 and r24["high"] > r24["low"]:
        out["mid_pos_in_24h"] = round((mid - r24["low"]) / (r24["high"] - r24["low"]), 3)
    if out["atr14_1h"]:
        out["atr14_pct"] = round(out["atr14_1h"] / mid * 100, 3)
    return out


def levels_tool(coin: str) -> Dict[str, Any]:
    canon = coins_mod.canonical_coin(coin)
    return summarize_levels(canon)


# ── catalysts / scheduled events ─────────────────────────────────────────

def upcoming_catalysts(spec: DeskSpec, now: datetime, *, within_days: Optional[float] = None,
                        limit: int = 8) -> List[Catalyst]:
    """calendar_catalysts(..., use_overlay=False): see that function's
    docstring for why this server always passes use_overlay=False."""
    rows = catalysts_src.calendar_catalysts(spec, now, use_overlay=False)
    rows = [c for c in rows if c.at >= now]
    if within_days is not None:
        cutoff = now + timedelta(days=within_days)
        rows = [c for c in rows if c.at <= cutoff]
    rows.sort(key=lambda c: c.at)
    return rows[:limit]


def _event_row_json(r: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(r)
    coins_field = out.get("coins")
    if isinstance(coins_field, (set, frozenset)):
        out["coins"] = sorted(coins_field)
    return out


def scheduled_calendar(canon_coin: str, now: datetime, *, days: float = 7.0) -> Dict[str, Any]:
    """The curated scheduled-event calendar plus the active blackout flag
    for `canon_coin`, or an explicit note when this coin has no curated
    calendar group yet (BTC/ETH today -- only oil/metals/sp500/hype coins
    do; calendar_catalysts still carries FOMC/CPI for every coin).
    use_overlay=False throughout: see scheduled_events.load()'s docstring."""
    if canon_coin not in _CURATED_CALENDAR_COINS:
        return {"events": [], "active_blackout": None,
                "note": (f"{canon_coin} has no curated scheduled-event calendar group yet "
                        "(only oil/metals/sp500/hype coins do today); calendar_catalysts "
                        "still carries FOMC/CPI for every coin.")}
    now_ms = int(now.timestamp() * 1000)
    end_ms = now_ms + int(days * 24 * 3_600_000)
    rows = scheduled_events.market_events(now_ms, end_ms, canon_coin, use_overlay=False)
    active_blackout = scheduled_events.blackout(canon_coin, now_ms, use_overlay=False)
    return {"events": [_event_row_json(r) for r in rows], "active_blackout": active_blackout}


def events_tool(coins: Optional[Sequence[str]] = None, days: float = 7.0) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    days = max(1.0, min(float(days if days is not None else 7.0), 60.0))
    targets = list(coins) if coins else list(coins_mod.SUPPORTED_COINS)
    out: Dict[str, Any] = {}
    errors: List[str] = []
    for raw in targets:
        try:
            canon = coins_mod.canonical_coin(raw)
        except coins_mod.UnsupportedCoinError as exc:
            errors.append(str(exc))
            continue
        out[canon] = scheduled_calendar(canon, now, days=days)
    return {"as_of": now.isoformat(), "window_days": days, "coins": out, "sources_failed": errors}


# ── headlines ─────────────────────────────────────────────────────────────

def fetch_headlines_fast(spec: DeskSpec, now: datetime, *, deadline_s: float = 10.0
                          ) -> Tuple[List[Headline], List[str]]:
    """news.ATOMIC_FETCHERS fanned across threads (services/intel/api.py's
    own pattern -- see concurrent.py's docstring), then news.score_and_dedup
    once over everything that came back. Never raises."""
    raw, errors = fetch_many([(lambda fn=fn: fn(spec)) for fn in news_src.ATOMIC_FETCHERS],
                             deadline_s=deadline_s)
    return news_src.score_and_dedup(raw, spec, now), errors


def _adhoc_spec(id_: str, *, news_queries: Optional[List[str]] = None,
                prediction_market_queries: Optional[List[str]] = None, label: str = "") -> DeskSpec:
    label = (label or "").strip()[:60] or "query"
    return DeskSpec(id=id_, name="Ad hoc", tagline="services/research_mcp free-text query",
                    instruments=[Instrument(coin="ADHOC", label=label, role="primary")],
                    news_queries=news_queries or [], prediction_market_queries=prediction_market_queries or [])


def headlines_tool(coin: Optional[str] = None, topic: Optional[str] = None,
                    hours: float = 24.0) -> Dict[str, Any]:
    if not coin and not topic:
        raise ValueError("headlines needs either coin or topic")
    now = datetime.now(timezone.utc)
    hours = max(1.0, min(float(hours if hours is not None else 24.0), 168.0))
    if coin:
        canon = coins_mod.canonical_coin(coin)
        spec = coins_mod.desk_spec_for(canon)
        query_label = canon
    else:
        spec = _adhoc_spec("adhoc_news", news_queries=[topic], label=topic or "")
        query_label = topic
    rows, errors = fetch_headlines_fast(spec, now)
    cutoff = now - timedelta(hours=hours)
    rows = [h for h in rows if h.published_at is None or h.published_at >= cutoff]
    return {"query": query_label, "hours": hours,
            "headlines": [h.model_dump(mode="json") for h in rows[:25]],
            "sources_failed": errors}


# ── prediction odds ───────────────────────────────────────────────────────

def prediction_odds_for_spec(spec: DeskSpec, *, deadline_s: float = 10.0
                              ) -> Tuple[List[MarketOdds], List[str]]:
    rows, errors = fetch_many(
        [lambda: predictions_src.fetch_polymarket(spec), lambda: predictions_src.fetch_kalshi(spec)],
        deadline_s=deadline_s)
    by_id = {o.id: o for o in rows}
    ranked = sorted(by_id.values(), key=lambda o: o.volume_usd or 0.0, reverse=True)
    return ranked, errors


def prediction_odds_tool(query: Optional[str] = None, coin: Optional[str] = None) -> Dict[str, Any]:
    if not query and not coin:
        raise ValueError("prediction_odds needs either coin or query")
    if coin:
        canon = coins_mod.canonical_coin(coin)
        spec = coins_mod.desk_spec_for(canon)
        query_label = canon
    else:
        spec = _adhoc_spec("adhoc_odds", prediction_market_queries=[query], label=query or "")
        query_label = query
    odds, errors = prediction_odds_for_spec(spec)
    return {"query": query_label, "odds": [o.model_dump(mode="json") for o in odds[:25]],
            "sources_failed": errors}


# ── FRED series ───────────────────────────────────────────────────────────

def fred_series_tool(series_id: str, start: Optional[str] = None,
                      end: Optional[str] = None) -> Dict[str, Any]:
    data, err = fred_src.fetch_observations(series_id, start=start, end=end)
    if err == "FRED_API_KEY_missing":
        return {"error": "FRED_API_KEY_missing", "series_id": series_id, "help": FRED_KEY_HELP}
    if err:
        return {"error": err, "series_id": series_id}
    return {"series_id": series_id, "start": start, "end": end, "observations": data}


# ── the latest stored thesis ──────────────────────────────────────────────

def latest_thesis_json(desk_id: str) -> Optional[Dict[str, Any]]:
    """The desk's newest saved Thesis (services.strategies.journal.
    latest_thesis), or None if it has never been refreshed. Serves what is
    already on disk only -- writing a NEW thesis needs an LLM call, which
    this read-only server never makes (CLAUDE.md: no hosted LLM API, and
    the local-Claude-Code route belongs to the trading server, not this
    one)."""
    thesis = journal.latest_thesis(desk_id)
    return thesis.model_dump(mode="json") if thesis else None


# ── research_brief: the one-call summary ─────────────────────────────────

def research_brief(coin: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    canon = coins_mod.canonical_coin(coin)
    desk_id = coins_mod.desk_id_for(canon)
    spec = coins_mod.desk_spec_for(canon)
    now = now or datetime.now(timezone.utc)
    errors: List[str] = []

    try:
        levels = summarize_levels(canon)
    except Exception as exc:  # noqa: BLE001 - one dead source must not sink the brief
        levels = None
        errors.append(f"levels:{exc}")

    try:
        catalysts_out = [c.model_dump(mode="json")
                         for c in upcoming_catalysts(spec, now, within_days=30, limit=8)]
    except Exception as exc:  # noqa: BLE001
        catalysts_out = []
        errors.append(f"catalysts:{exc}")

    try:
        headline_rows, herrs = fetch_headlines_fast(spec, now)
        errors.extend(herrs)
        headlines_out = [h.model_dump(mode="json") for h in headline_rows[:10]]
    except Exception as exc:  # noqa: BLE001
        headlines_out = []
        errors.append(f"headlines:{exc}")

    try:
        odds_rows, oerrs = prediction_odds_for_spec(spec)
        errors.extend(oerrs)
        odds_out = [o.model_dump(mode="json") for o in odds_rows[:10]]
    except Exception as exc:  # noqa: BLE001
        odds_out = []
        errors.append(f"odds:{exc}")

    try:
        thesis_out = latest_thesis_json(desk_id)
    except Exception as exc:  # noqa: BLE001
        thesis_out = None
        errors.append(f"thesis:{exc}")

    note = None
    if desk_id == "top5":
        note = (f"{canon} has no standalone desk in pathiel; catalysts, headlines, odds and "
                f"the stored thesis above reflect the top5 basket desk's coverage (BTC, ETH, "
                f"SOL, HYPE, ZEC), not {canon} specifically.")

    return {
        "coin": canon, "desk_id": desk_id, "as_of": now.isoformat(),
        "levels": levels, "catalysts": catalysts_out, "headlines": headlines_out,
        "odds": odds_out, "thesis": thesis_out, "note": note, "sources_failed": errors,
    }


__all__ = [
    "levels_tool", "summarize_levels", "events_tool", "scheduled_calendar", "upcoming_catalysts",
    "headlines_tool", "fetch_headlines_fast", "prediction_odds_tool", "prediction_odds_for_spec",
    "fred_series_tool", "latest_thesis_json", "research_brief", "FRED_KEY_HELP",
]
