"""FREE news-catalyst feed — our own build of the Unusual-Whales / Twitter
"breaking headline" workflow, with NO paid feed and NO X API.

The pain this solves: a market-moving headline breaks (e.g. a US-Iran peace
deal) and we want to fire longs the SECOND it hits — instead of finding out
late by scrolling Twitter.

Two free sources, combined:
  1. GDELT 2.0 DOC API  (https://api.gdeltproject.org/api/v2/doc/doc) — indexes
     global news every ~15 min, full-text searchable, free, no key. Gives us:
       - latest matching articles (headline + domain + timestamp), and
       - a coverage-VOLUME timeline, so a SURGE in coverage = a developing
         catalyst (the "breaking" detector).
  2. RSS wires (Yahoo Finance / CNBC / CoinDesk / CoinTelegraph) — lowest-latency
     major headlines, keyword-filtered.

PURE parsers (testable) + thin cached fetch. Nothing here trades; it's the signal
product. Wiring into perception/override is a separate, gated step.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:                     # pragma: no cover
    _SSL = ssl._create_unverified_context()

_GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"

# Free, no-auth RSS wires. Mix of macro + crypto so a catalyst on either side
# surfaces. Add/remove freely.
_RSS_FEEDS = [
    "https://finance.yahoo.com/news/rssindex",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    domain: str
    seen: Optional[datetime]   # UTC
    source: str = ""           # "gdelt" | rss feed host


@dataclass(frozen=True)
class CatalystReport:
    query: str
    n_recent: int              # articles in the window
    breaking: bool             # coverage surging vs its own baseline
    surge_x: float             # latest coverage bin / baseline median
    headlines: List[Article]   # newest first
    note: str = ""


# ── GDELT parsing (pure) ─────────────────────────────────────────────────────

def _parse_gdelt_date(s: str) -> Optional[datetime]:
    # GDELT seendate format: "20260615T143000Z"
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def parse_gdelt_artlist(payload: dict) -> List[Article]:
    out: List[Article] = []
    for a in (payload or {}).get("articles", []) or []:
        out.append(Article(
            title=(a.get("title") or "").strip(),
            url=a.get("url") or "",
            domain=a.get("domain") or "",
            seen=_parse_gdelt_date(a.get("seendate") or ""),
            source="gdelt",
        ))
    out.sort(key=lambda x: x.seen or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out


def detect_surge(volume_points: List[float], min_baseline: float = 1e-9) -> tuple:
    """Given a coverage-volume timeline (oldest->newest), is the latest bin a
    SURGE vs the baseline (median of the earlier bins)? Returns (breaking, x)."""
    if len(volume_points) < 3:
        return (False, 1.0)
    latest = volume_points[-1]
    base = median(volume_points[:-1]) or min_baseline
    x = latest / base if base > 0 else 0.0
    # "breaking" = latest coverage at least 2.5x its recent baseline AND nonzero
    return (x >= 2.5 and latest > 0, round(x, 2))


def parse_gdelt_timeline(payload: dict) -> List[float]:
    """Extract the coverage-volume series from a GDELT TimelineVol payload."""
    tl = (payload or {}).get("timeline") or []
    if not tl:
        return []
    pts = tl[0].get("data") or []     # first (only) series
    return [float(p.get("value") or 0) for p in pts]


# ── RSS parsing (pure) ───────────────────────────────────────────────────────

def _parse_rss_date(s: str) -> Optional[datetime]:
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(s.strip(), fmt)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def parse_rss(xml_text: str, source: str = "") -> List[Article]:
    """Parse an RSS/Atom feed into Articles. Tolerant of malformed feeds."""
    out: List[Article] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    # RSS <item> and Atom <entry>
    items = root.iter("item")
    for it in items:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date") or ""
        dom = urllib.parse.urlparse(link).netloc
        if title:
            out.append(Article(title=title, url=link, domain=dom,
                               seen=_parse_rss_date(pub), source=source or dom))
    return out


def filter_keywords(articles: List[Article], keywords: List[str]) -> List[Article]:
    """Keep articles whose title contains ANY keyword (case-insensitive)."""
    if not keywords:
        return articles
    kw = [k.lower() for k in keywords if k]
    return [a for a in articles if any(k in a.title.lower() for k in kw)]


# ── thin cached fetch ────────────────────────────────────────────────────────
_CACHE_TTL_S = 300.0           # news moves fast; 5-min cache
_cache: Dict[str, tuple] = {}
_lock = threading.Lock()


_GDELT_MIN_INTERVAL_S = 5.5      # GDELT hard-limits to 1 req / 5s (observed 2026-07-11)
_gdelt_last_req = [0.0]


def _get_json(url: str, timeout: float = 25.0) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    # pace GDELT requests globally — a rate-limited reply is plain text, not JSON
    if "gdeltproject.org" in url:
        with _lock:
            wait = _GDELT_MIN_INTERVAL_S - (time.time() - _gdelt_last_req[0])
            if wait > 0:
                time.sleep(wait)
            _gdelt_last_req[0] = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            body = r.read().decode("utf-8", "replace")
        if body.lstrip().startswith("Please limit requests"):
            logger.warning("[news] GDELT rate-limited — backing off")
            return None
        return json.loads(body) if body.strip() else None
    except Exception:
        return None


def _get_text(url: str, timeout: float = 12.0) -> Optional[str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def catalyst_scan(query: str, timespan: str = "1h", max_records: int = 30,
                  ttl: float = _CACHE_TTL_S,
                  allow_fetch: bool = True) -> Optional[CatalystReport]:
    """Free catalyst scan for a topic/ticker via GDELT: latest headlines + a
    coverage-surge ('breaking') read. Cached per (query, timespan).

    allow_fetch=False = CACHE-ONLY (return last cached value or None, no network)."""
    key = f"gdelt::{query}::{timespan}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
    if not allow_fetch:
        return hit[1] if hit else None

    if "sourcelang:" not in query:
        query = f"{query} sourcelang:eng"
    q = urllib.parse.quote(query)
    art = _get_json(f"{_GDELT}?query={q}&mode=ArtList&maxrecords={max_records}"
                    f"&format=json&sortby=datedesc&timespan={timespan}")
    vol = _get_json(f"{_GDELT}?query={q}&mode=TimelineVol&format=json&timespan={timespan}")
    if art is None and vol is None:
        with _lock:
            _cache[key] = (now, None)
        return None

    headlines = parse_gdelt_artlist(art or {})
    breaking, surge_x = detect_surge(parse_gdelt_timeline(vol or {}))
    rep = CatalystReport(
        query=query, n_recent=len(headlines), breaking=breaking, surge_x=surge_x,
        headlines=headlines[:max_records],
        note=("⚡ BREAKING — coverage surging" if breaking
              else "elevated coverage" if surge_x >= 1.5 else ""),
    )
    with _lock:
        _cache[key] = (now, rep)
    return rep


def rss_headlines(keywords: Optional[List[str]] = None, feeds: Optional[List[str]] = None,
                  limit: int = 25, ttl: float = _CACHE_TTL_S) -> List[Article]:
    """Lowest-latency major-wire headlines, optionally keyword-filtered. Cached."""
    feeds = feeds or _RSS_FEEDS
    key = "rss::" + ",".join(sorted(feeds)) + "::" + ",".join(sorted(keywords or []))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
    arts: List[Article] = []
    for f in feeds:
        txt = _get_text(f)
        if txt:
            arts.extend(parse_rss(txt, source=urllib.parse.urlparse(f).netloc))
    if keywords:
        arts = filter_keywords(arts, keywords)
    arts.sort(key=lambda x: x.seen or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    arts = arts[:limit]
    with _lock:
        _cache[key] = (now, arts)
    return arts


# ── Google News RSS (2026-07-11): the live query engine ─────────────────────
# Measured head-to-head: GDELT 1/5 success at 10-20s even politely paced vs
# Google News RSS 0.4-0.7s with per-coin queries (100 items for "bitcoin").
# GDELT keeps ONE role — historical date-range search for offline research —
# and must never sit on a live path.
_GOOGLE_NEWS = "https://news.google.com/rss/search"


_QUERY_NOISE = '-casino -gambling -"price prediction" -giveaway'


def _coin_query(coin: str) -> str:
    """Query hygiene: quote the symbol (LIT/VIRTUAL are English words) +
    asset-class context + negations for observed SEO-spam classes. xyz:
    symbols are TOKENIZED EQUITIES — querying them with 'crypto' returned
    generic Bitcoin/macro coverage (xyz:BE incident 2026-07-12)."""
    sym = coin.split(":")[-1]
    if ":" in coin:
        return f'"{sym}" stock {_QUERY_NOISE}'
    return f'"{sym}" crypto {_QUERY_NOISE}'


# Title-level relevance guard: Google News matches the query terms loosely, so
# a word-symbol coin (GRASS, PUMP, TRUMP, LIT) also returns its English
# homonym — observed live 2026-07-12: a beagles-touch-grass story counted
# toward GRASS coverage, polluting the surge baseline that now gates real
# money. A title is crypto-relevant if it carries the cashtag, the ALL-CAPS
# ticker, or an unambiguous crypto-context term.
_CRYPTO_CONTEXT_RE = re.compile(
    r"crypto|token|\bcoin\b|blockchain|binance|coinbase|kraken|exchange|"
    r"listing|airdrop|defi|perpetual|solana|ethereum|bitcoin|on-?chain|web3|"
    r"staking|market cap|bull|bear|rally|surge[sd]?\b|all-time high|ath\b|"
    r"price target|trading|\betf\b|whale|hack(?:ed|er)?\b|mainnet|testnet",
    re.IGNORECASE,
)


_EQUITY_CONTEXT_RE = re.compile(
    r"stock|shares?\b|earnings|nasdaq|nyse|ipo\b|dividend|guidance|"
    r"quarterly|revenue|market cap|sec filing|8-k|10-q",
    re.IGNORECASE,
)


# Ticker -> common name: headlines say "Bitcoin", not "BTC". Majors only —
# for everything else the ticker itself is how news refers to the token.
_SYM_ALIASES = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "DOGE": "dogecoin", "ADA": "cardano", "AVAX": "avalanche", "DOT": "polkadot",
    "LINK": "chainlink", "LTC": "litecoin", "BNB": "binance coin",
    "ARB": "arbitrum", "OP": "optimism", "TIA": "celestia", "NEAR": "near protocol",
}


def _title_relevant(sym: str, title: str, *, equity: bool = False) -> bool:
    """The article must be ABOUT the coin, not merely about crypto.

    v1 accepted any crypto-context title even when the symbol never appeared —
    so xyz:BE counted generic Bitcoin/Iran coverage as its own, surged 5.4x,
    and fired a live BREAKING entry (blocked only by the unfunded dex,
    2026-07-12). Now the symbol itself must appear: cashtag, ALL-CAPS ticker,
    or the symbol-as-word PLUS asset-class context to disambiguate homonyms."""
    t = title or ""
    if f"${sym.upper()}" in t.upper():
        return True                       # cashtag: $GRASS / $BE
    if re.search(rf"(?<![A-Za-z]){re.escape(sym.upper())}(?![A-Za-z])", t):
        return True                       # ALL-CAPS ticker used as a ticker
    ctx = _EQUITY_CONTEXT_RE if equity else _CRYPTO_CONTEXT_RE
    # Soft rule (case-insensitive word + asset context) only for names long
    # enough not to collide with English function words: "BE"/"IT"/"OP" as
    # words appear in half of all headlines ("Might Not Be A Great Idea...").
    names = [n for n in
             ([sym] + ([_SYM_ALIASES[sym.upper()]] if sym.upper() in _SYM_ALIASES else []))
             if len(n) >= 4]
    for name in names:
        if re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", t, re.IGNORECASE) \
                and ctx.search(t):
            return True                   # "Grass token rallies" / "Bitcoin ETF..."
    return False


_MAX_ARTICLE_AGE_DAYS = 7.0


_BREAKING_MAX_AGE_H = 24.0


def _within_hours(a: Article, max_age_h: float = _BREAKING_MAX_AGE_H) -> bool:
    """Strict recency check for BREAKING specifically (operator order
    2026-07-15): unlike _fresh() below, an article with NO parseable publish
    date does NOT count here — 'coverage is surging right now' is a strong
    claim that gates a live $20 entry, so it needs a verified bar, not a
    lenient one. Google's when:1h search reflects INDEXING, not publish
    time (observed live: multi-day-old articles surface in the '1h'
    bucket), so recency is independently verified from each article's own
    pubDate rather than trusted from the query window."""
    if a.seen is None:
        return False
    age_h = (datetime.now(timezone.utc) - a.seen).total_seconds() / 3600.0
    return age_h <= max_age_h


def _fresh(a: Article, max_age_days: float = _MAX_ARTICLE_AGE_DAYS) -> bool:
    """Publish-date guard: Google's `when:` window still returns evergreen/
    re-syndicated items with old pubDates (a Jul-2025 PUMP unlock article
    rendered on 2026-07-13). Unknown dates pass (parse failures must not
    blank the feed); known-old dates are dropped from counting AND display."""
    if a.seen is None:
        return True
    age_s = (datetime.now(timezone.utc) - a.seen).total_seconds()
    return age_s <= max_age_days * 86_400


def relevant_articles(sym: str, articles: List[Article],
                      *, equity: bool = False) -> List[Article]:
    """Drop everything not about THIS coin, and anything stale, before
    counting/display."""
    return [a for a in articles or []
            if _fresh(a) and _title_relevant(sym, a.title, equity=equity)]


def google_news_search(query: str, when: str = "1d", limit: int = 25,
                       ttl: float = _CACHE_TTL_S) -> List[Article]:
    """Per-query news via Google News RSS (free, keyless, fast). `when` is a
    Google window like '1h' / '1d' / '7d'. Cached per (query, when)."""
    key = f"gnews::{query}::{when}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1][:limit]
    url = (f"{_GOOGLE_NEWS}?q={urllib.parse.quote(query)}+when:{when}"
           f"&hl=en-US&gl=US&ceid=US:en")
    txt = _get_text(url)
    arts = parse_rss(txt, source="news.google.com") if txt else []
    arts.sort(key=lambda a: a.seen or datetime.min.replace(tzinfo=timezone.utc),
              reverse=True)
    with _lock:
        _cache[key] = (now, arts)
    return arts[:limit]


# Market-moving WORLD context, not per-coin: macro policy, geopolitics,
# political sentiment, whale-adjacent public figures, congressional trading.
# One cached pass feeds every research prompt (operator order 2026-07-12).
_MACRO_QUERIES = (
    ("macro", 'crypto market bitcoin -"price prediction"'),
    ("fed", '"Federal Reserve" OR "interest rates" OR inflation markets'),
    ("politics", 'Trump crypto OR tariffs OR sanctions markets'),
    ("geopolitics", 'war OR strike OR conflict oil markets'),
    ("figures", '"Elon Musk" OR "Pelosi trades" OR congress stock trading'),
)


def macro_headlines(per_query: int = 2, ttl: float = 1800.0) -> List[str]:
    """Top world/market headlines across the macro query set — deduped,
    newest-first within each bucket, bounded. Google News RSS, keyless."""
    out: List[str] = []
    seen: set = set()
    for tag, q in _MACRO_QUERIES:
        try:
            arts = google_news_search(q, when="1d", limit=10, ttl=ttl)
        except Exception:
            continue
        n = 0
        for a in arts:
            t = (a.title or "").strip()
            key = t.lower()[:60]
            if not t or key in seen:
                continue
            seen.add(key)
            out.append(f"[{tag}] {t}")
            n += 1
            if n >= per_query:
                break
    return out


def coin_catalyst(coin: str, ttl: float = _CACHE_TTL_S) -> CatalystReport:
    """Live catalyst read for one coin: fresh headlines + a coverage-surge
    signal computed from headline COUNTS (last 1h vs the trailing-24h hourly
    baseline). Two cached Google News queries; no GDELT on this path.

    BREAKING counts only articles independently verified within 24h of their
    own publish date (_within_hours) — Google's when:1h window is not
    trusted on its own (see _within_hours docstring). n_recent/headlines
    report that same verified-fresh set, so the number on the page matches
    what actually gated the signal."""
    q = _coin_query(coin)
    sym = coin.split(":")[-1]
    is_equity = ":" in coin
    recent_raw = relevant_articles(sym, google_news_search(q, when="1h", limit=50, ttl=ttl),
                                   equity=is_equity)
    recent = [a for a in recent_raw if _within_hours(a)]
    daily = relevant_articles(sym, google_news_search(q, when="1d", limit=100, ttl=ttl),
                              equity=is_equity)
    baseline_per_h = max(len(daily) / 24.0, 0.25)
    surge_x = round(len(recent) / baseline_per_h, 2)
    breaking = len(recent) >= 3 and surge_x >= 3.0
    return CatalystReport(
        query=coin, n_recent=len(recent), breaking=breaking, surge_x=surge_x,
        headlines=recent[:10],
        note=("⚡ BREAKING — coverage surging" if breaking
              else "elevated coverage" if surge_x >= 1.5 else ""),
    )
