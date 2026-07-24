"""Trending / BREAKING lane — what polymarket.com's front page and /breaking show.

The judgment lane (`scout.py`) hunts quiet mid-tail markets. This lane reads what
the crowd is actually trading RIGHT NOW, because that is what the front end has to
render and what a news-driven forecast has to be pointed at.

Two orderings, both derived from public Gamma data (keyless):

  TRENDING  — events ranked by 24h volume, sports/esports excluded. This is
              polymarket.com's home ordering. Without the sports exclusion the
              list is 90% LoL/NBA game lines: pure latency markets we do not play.
  BREAKING  — the same pool re-ranked by |24h price change| over a volume floor.
              Polymarket's /breaking has no public tag (checked: `breaking` and
              `breaking-news` both return 0 events), so we reconstruct its intent:
              a market that REPRICED hard in a day is a market the news just hit.

Everything here is a pure function over a fetched payload except the two client
calls, so it tests offline. Zero trading, zero capital — this feeds the dashboard
and the forecaster's candidate queue.
"""
from __future__ import annotations

import calendar
import json
import time
from typing import Any, Dict, List, Optional, Sequence

from services.polymarket_scout.scout import _DAY_MS, market_yes_prob

# Gamma tag ids. Sports (1) and Games/esports (100639) are the two families that
# dominate 24h volume and are exactly the latency lane W-Z1 measured as 0/60
# tradeable. Excluded server-side so we do not burn pages fetching them.
SPORTS_TAG_ID = "1"
GAMES_TAG_ID = "100639"
DEFAULT_EXCLUDE_TAGS = (SPORTS_TAG_ID, GAMES_TAG_ID)

DEFAULT_CFG: Dict[str, Any] = {
    "min_volume_24h": 5_000.0,     # a market nobody traded today is not trending
    "min_liquidity": 500.0,
    "min_hours": 6.0,              # sub-6h resolution is a settlement race, not judgment
    "max_days": 120.0,
    "prob_floor": 0.03,
    "prob_ceiling": 0.97,
    "max_spread": 0.10,            # 10c wide = the fill eats any edge we could find
    "breaking_min_move": 0.05,     # 5pp repricing in 24h = the news moved it
    "breaking_min_volume_24h": 20_000.0,
}

# Sports settle in hours, so the judgment lanes' 6h floor would delete the whole
# in-play board. Everything else stays as strict.
SPORTS_CFG: Dict[str, Any] = {**DEFAULT_CFG, "min_hours": 0.0, "max_days": 30.0,
                              "min_volume_24h": 5_000.0}

# "Crazy odds": >= 3x gross payout, i.e. priced at or under 33c.
LONGSHOT_MAX_PROB = 0.33

# Recurring price-ladder / counter / up-down families: mechanically priced,
# sub-daily, and the explicit fee target of the 2026 taker schedule. Not
# judgment markets. Asset-agnostic on purpose — the first live board surfaced
# "S&P 500 (SPX) Opens Up or Down" and "SPY Up or Down", which are the same
# latency game as the crypto ladders with a different ticker.
_LADDER_PATTERNS = (
    "up or down", "higher or lower", "up/down", "opens up or down",
    "what price will", "hit price", "above ___", "dip to", "reach $",
    "hit $", "hit (high)", "hit (low)",       # "Will WTI hit (HIGH) $95 in July?"
    "# tweets", "# of tweets", "number of tweets", "tweets from", "tweets between",
)


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _end_ms(iso: str) -> Optional[int]:
    """ISO-8601 UTC -> epoch ms. Gamma always stamps Z; timegm reads it as UTC."""
    if not iso:
        return None
    try:
        return int(calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) * 1000)
    except Exception:
        return None


def _tokens(m: Dict[str, Any]) -> List[str]:
    try:
        toks = json.loads(m.get("clobTokenIds") or "[]")
        return [str(t) for t in toks] if isinstance(toks, list) else []
    except Exception:
        return []


# ── normalisation ────────────────────────────────────────────────────────────
def flatten_event(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One Gamma event -> one normalised row per binary market inside it.

    The event carries the things the UI needs and the market does not: tags,
    the human title, the icon, and the /event/<slug> permalink. (Verified: the
    `events` stub embedded in a /markets response has NO tags — that is why this
    lane reads /events, not /markets.)
    """
    if not isinstance(ev, dict):
        return []
    tags = [str(t.get("slug") or "") for t in (ev.get("tags") or []) if isinstance(t, dict)]
    slug = str(ev.get("slug") or "")
    sport = ev.get("sport")
    sport_name = str(sport.get("sport") or "") if isinstance(sport, dict) else str(sport or "")
    teams = [str(t.get("name") or "") for t in (ev.get("teams") or []) if isinstance(t, dict)]
    rows: List[Dict[str, Any]] = []
    for m in (ev.get("markets") or []):
        if not isinstance(m, dict):
            continue
        toks = _tokens(m)
        yes = market_yes_prob(m)
        rows.append({
            "market_id": str(m.get("id") or ""),
            "question": str(m.get("question") or ""),
            "group_title": str(m.get("groupItemTitle") or ""),
            "event_id": str(ev.get("id") or ""),
            "event_title": str(ev.get("title") or ""),
            "event_slug": slug,
            "url": f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com/",
            "icon": str(m.get("icon") or ev.get("icon") or ""),
            "tags": tags,
            "yes": yes,
            "bid": _f(m.get("bestBid")),
            "ask": _f(m.get("bestAsk")),
            "spread": _f(m.get("spread")),
            "volume_24h": _f(m.get("volume24hr")) or _f(ev.get("volume24hr")),
            "volume": _f(m.get("volume")),
            "liquidity": _f(m.get("liquidity")),
            "change_24h": _f(m.get("oneDayPriceChange")),
            "change_7d": _f(m.get("oneWeekPriceChange")),
            "end_date": str(m.get("endDate") or ev.get("endDate") or ""),
            "order_book": bool(m.get("enableOrderBook")),
            "closed": bool(m.get("closed")),
            "active": bool(m.get("active", True)),
            "yes_token": toks[0] if len(toks) == 2 else "",
            "no_token": toks[1] if len(toks) == 2 else "",
            # sports extras — present only on tag_id=1 events, harmless elsewhere
            "live": bool(ev.get("live")),
            "score": str(ev.get("score") or ""),
            "start_time": str(ev.get("startTime") or ""),
            "sport": sport_name,
            "teams": teams,
        })
    return rows


def is_ladder_row(row: Dict[str, Any]) -> bool:
    """Recurring strike-ladder / up-down / counter markets — mechanical, not judgment.

    Matched against question AND event title: the strike sits in the question
    ("Bitcoin above $120k") while the family lives in the event title ("What
    price will Bitcoin hit in July?" / "Elon Musk # tweets July 17-24").
    """
    q = f"{row.get('question','')} {row.get('event_title','')}".lower()
    return any(p in q for p in _LADDER_PATTERNS)


def is_tradeable(row: Dict[str, Any], now_ms: int, cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Could we actually take this row at the touch, and is it priced by judgment?

    Order book on, live, two tokens, real 24h volume + resting liquidity, horizon
    inside [min_hours, max_days], priced away from settled, and a crossable spread.
    """
    c = {**DEFAULT_CFG, **(cfg or {})}
    if not row.get("order_book") or row.get("closed") or not row.get("active"):
        return False
    if not row.get("yes_token") or not row.get("no_token"):
        return False
    if is_ladder_row(row):
        return False
    if row.get("volume_24h", 0.0) < _f(c["min_volume_24h"]):
        return False
    if row.get("liquidity", 0.0) < _f(c["min_liquidity"]):
        return False
    end = _end_ms(str(row.get("end_date") or ""))
    if end is None:
        return False
    hours = (end - now_ms) / 3_600_000.0
    if not (_f(c["min_hours"]) <= hours <= _f(c["max_days"]) * 24.0):
        return False
    yes = row.get("yes")
    if yes is None or not (_f(c["prob_floor"]) <= yes <= _f(c["prob_ceiling"])):
        return False
    spread = row.get("spread") or 0.0
    return spread <= _f(c["max_spread"])


def hours_to_end(row: Dict[str, Any], now_ms: int) -> Optional[float]:
    end = _end_ms(str(row.get("end_date") or ""))
    return None if end is None else round((end - now_ms) / 3_600_000.0, 2)


def breaking_score(row: Dict[str, Any]) -> float:
    """How hard the news hit this market: absolute 24h repricing in probability
    points. Sign is kept separately in `change_24h` — the UI needs the direction."""
    return abs(_f(row.get("change_24h")))


def is_breaking(row: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> bool:
    c = {**DEFAULT_CFG, **(cfg or {})}
    return (breaking_score(row) >= _f(c["breaking_min_move"])
            and row.get("volume_24h", 0.0) >= _f(c["breaking_min_volume_24h"]))


# ── the two feeds ────────────────────────────────────────────────────────────
def collect(client, now_ms: Optional[int] = None, cfg: Optional[Dict[str, Any]] = None,
            limit: int = 100, pages: int = 3,
            exclude_tag_ids: Sequence[str] = DEFAULT_EXCLUDE_TAGS) -> List[Dict[str, Any]]:
    """Fetch -> flatten -> filter. One deduped list of tradeable trending rows,
    each annotated with `hours_to_end` and `breaking`."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    c = {**DEFAULT_CFG, **(cfg or {})}
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for ev in client.open_events(limit=limit, pages=pages, exclude_tag_ids=exclude_tag_ids):
        for row in flatten_event(ev):
            if row["market_id"] in seen or not is_tradeable(row, now, c):
                continue
            seen.add(row["market_id"])
            row["hours_to_end"] = hours_to_end(row, now)
            row["breaking"] = is_breaking(row, c)
            out.append(row)
    return out


def collect_sports(client, now_ms: Optional[int] = None, cfg: Optional[Dict[str, Any]] = None,
                   limit: int = 100, pages: int = 2) -> List[Dict[str, Any]]:
    """polymarket.com/sports — the lane the judgment feeds deliberately exclude.

    Its gate is SPORTS_CFG, not DEFAULT_CFG: a game line settles in hours, so the
    6h floor that protects the judgment lanes from settlement races would delete
    the entire in-play board. `live` and `score` come straight off the event.
    """
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    c = {**SPORTS_CFG, **(cfg or {})}
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for ev in client.open_events(limit=limit, pages=pages, tag_ids=(SPORTS_TAG_ID,)):
        for row in flatten_event(ev):
            if row["market_id"] in seen or not is_tradeable(row, now, c):
                continue
            seen.add(row["market_id"])
            row["hours_to_end"] = hours_to_end(row, now)
            row["breaking"] = is_breaking(row, c)
            out.append(row)
    return out


def rank_sports(rows: List[Dict[str, Any]], limit: int = 36) -> List[Dict[str, Any]]:
    """LIVE games first (that is what /sports/live shows), then 24h volume."""
    return sorted(rows, key=lambda r: (bool(r.get("live")), r.get("volume_24h", 0.0)),
                  reverse=True)[:limit]


def payout_x(row: Dict[str, Any]) -> Optional[float]:
    """Gross payout multiple on $1 of YES bought at the touch: $1 / ask. 0.33 ->
    3.0x. Uses the ASK, not the mid — the mid is a price nobody fills at, and on
    a longshot the spread is most of the number."""
    px = _f(row.get("ask")) or _f(row.get("yes"))
    return round(1.0 / px, 2) if px and px > 0 else None


def longshots(rows: List[Dict[str, Any]], max_prob: float = LONGSHOT_MAX_PROB,
              limit: int = 24) -> List[Dict[str, Any]]:
    """The >=3x board: every tradeable market priced at or under `max_prob`.

    Ranked by our AI's DISAGREEMENT first (a 10c market our brain calls 40% is
    the only version of this that has ever been a trade), then by 24h volume for
    the ones nothing has judged yet. Each row carries `payout_x` for the UI.

    Read the number before you size it: buying this bucket is the worst cell in
    the backtest (`fade_longshots_p<=0.20` is +EV, i.e. its mirror — BUYING the
    longshot is the losing side, ~−1%/$ after fees). See the README table.
    """
    out = []
    for r in rows:
        yes = r.get("yes")
        if yes is None or yes > max_prob:
            continue
        row = dict(r)
        row["payout_x"] = payout_x(r)
        out.append(row)
    out.sort(key=lambda r: (abs(r.get("live_edge") or 0.0), r.get("volume_24h", 0.0)),
             reverse=True)
    return out[:limit]


def rank_trending(rows: List[Dict[str, Any]], limit: int = 40) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: r.get("volume_24h", 0.0), reverse=True)[:limit]


def rank_breaking(rows: List[Dict[str, Any]], limit: int = 20,
                  cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    hits = [r for r in rows if is_breaking(r, cfg)]
    return sorted(hits, key=breaking_score, reverse=True)[:limit]


def diversify(rows: List[Dict[str, Any]], limit: int = 10, max_per_event: int = 1,
              max_per_tag: int = 2) -> List[Dict[str, Any]]:
    """Thin a ranked list down to roughly-independent bets, keeping rank order.

    Measured on the first 15 live reads: four were Iran/Israel (two of them the
    SAME event, priced days apart) and two were the same Trump-Netanyahu meeting.
    Fifteen rows, about nine independent outcomes. That matters twice over — the
    go-live gate's n assumes independence, and a book that is five ways long "the
    Middle East does not de-escalate" is one bet wearing five hats.

    An event is Polymarket's own grouping of one underlying question, so
    `max_per_event=1` is the strongest cheap decorrelation available. The tag cap
    then stops a single theme from eating the batch.
    """
    seen_event: Dict[str, int] = {}
    seen_tag: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for r in rows:
        ev = str(r.get("event_id") or r.get("event_title") or r["market_id"])
        if seen_event.get(ev, 0) >= max_per_event:
            continue
        tags = [t for t in (r.get("tags") or []) if t]
        primary = tags[0] if tags else ""
        if primary and seen_tag.get(primary, 0) >= max_per_tag:
            continue
        seen_event[ev] = seen_event.get(ev, 0) + 1
        if primary:
            seen_tag[primary] = seen_tag.get(primary, 0) + 1
        out.append(r)
        if len(out) >= limit:
            break
    return out


def forecast_queue(rows: List[Dict[str, Any]], skip_ids: Optional[set] = None,
                   limit: int = 10, cfg: Optional[Dict[str, Any]] = None,
                   max_per_event: int = 1, max_per_tag: int = 2) -> List[Dict[str, Any]]:
    """Which trending rows to spend LLM tokens on, best first.

    BREAKING first (the news just moved it, so a synthesis edge is most likely to
    exist and be un-priced), then plain trending by 24h volume. Already-forecast
    markets are skipped — re-forecasting the same market daily produces correlated
    duplicates, not independent evidence — and the result is diversified so a
    batch buys independent outcomes rather than one theme five times.
    """
    skip = skip_ids or set()
    brk = [r for r in rank_breaking(rows, limit=limit * 3, cfg=cfg) if r["market_id"] not in skip]
    brk_ids = {r["market_id"] for r in brk}
    rest = [r for r in rank_trending(rows, limit=limit * 8)
            if r["market_id"] not in skip and r["market_id"] not in brk_ids]
    return diversify(brk + rest, limit=limit, max_per_event=max_per_event,
                     max_per_tag=max_per_tag)
