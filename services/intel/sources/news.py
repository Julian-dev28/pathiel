"""News headlines: Google News RSS (one broad OR query plus one request per
desk query phrase), Yahoo Finance quote headlines, EIA's own two feeds,
Investing.com commodities, oilprice.com RSS (oil desks only), and a crypto
RSS (crypto desks only). Every parser here was built against a REAL
captured response — see tests/fixtures/ — not against assumed schema.

Verified live 2026-10-17:
  Google News news.google.com/rss/search            — reliable, HTTP 200.
  oilprice.com oilprice.com/rss/main                — reliable, HTTP 200.
               (/rss and /rss/latest-energy-news both 404 — /rss/main is
               the real path.)
  Cointelegraph cointelegraph.com/rss               — reliable, HTTP 200.
               (coindesk.com/arc/outboundfeeds/rss/ 308-redirects and was
               dropped in favor of Cointelegraph, which the brief allows:
               "a crypto RSS (CoinDesk or similar)".)

Verified live 2026-10-24 (all HTTP 200, each under 1.5s):
  Yahoo Finance feeds.finance.yahoo.com/rss/2.0/headline?s=BZ=F,CL=F,NG=F
               — 19 items, newest 1h old; one request covers every symbol.
  EIA          eia.gov/rss/todayinenergy.xml (20 items) and
               eia.gov/rss/press_rss.xml (12 items). pubDate carries a
               double space and "EST"; email.utils parses both.
  Investing.com investing.com/rss/news_11.rss (commodities, 10 items).
               pubDate is "YYYY-MM-DD HH:MM:SS" (not RFC 822), handled by
               _parse_rss_pubdate's fallback, taken as UTC.
  Google News per-phrase: a quoted phrase plus `when:2d` returns a fresh,
               phrase-specific list (14 items for "OPEC+") where the single
               8-phrase OR query returned a mixed bag capped at 30.
  Refused automated readers (HTTP 403), so NOT used: opec.org press RSS,
               iea.org news RSS, ukmto.org advisories, state.gov press.
               Treasury's press feeds hung past 12s; centcom.mil's RSS
               endpoint returned an empty body.

GDELT (api.gdeltproject.org/api/v2/doc/doc) is no longer in
ATOMIC_FETCHERS. Measured 2026-10-24: a successful call took 21s and every
second call was a 429 ("limit requests to one every 5 seconds"), against a
12s whole-packet deadline — it could never answer in time, and it showed up
in every brief as `news:gdelt:timeout`. `fetch_gdelt` stays importable for
the online smoke test and anyone who wants to run it by hand.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from contracts.strategies.models import DeskSpec, Headline
from services.intel import desk_rules, http_client, scoring
from services.intel.textnorm import dedup_by_title, stable_id, title_tokens

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS_URL = "https://news.google.com/rss/search"
OILPRICE_URL = "https://oilprice.com/rss/main"
CRYPTO_RSS_URL = "https://cointelegraph.com/rss"
YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
EIA_TODAY_IN_ENERGY_URL = "https://www.eia.gov/rss/todayinenergy.xml"
EIA_PRESS_URL = "https://www.eia.gov/rss/press_rss.xml"
INVESTING_COMMODITIES_URL = "https://www.investing.com/rss/news_11.rss"

# Google News per-phrase fan-out: one request per desk query phrase, each
# restricted to the last two days so a quiet phrase does not pad the list
# with week-old reprints. Capped so a desk with a long query list cannot
# fan out into dozens of requests per packet.
GNEWS_RECENCY = "when:2d"
GNEWS_MAX_QUERIES = 8

# Yahoo Finance quote symbols for the perps this desk trades. Commodity and
# index coins need an explicit map; a crypto coin is "<COIN>-USD" and a
# plain equity ticker is itself. Anything else is skipped, never guessed.
_YAHOO_SYMBOLS = {
    "BRENTOIL": "BZ=F", "CL": "CL=F", "NATGAS": "NG=F",
    "GOLD": "GC=F", "SILVER": "SI=F",
    "SP500": "^GSPC", "XYZ100": "^NDX", "JP225": "^N225", "KR200": "^KS200",
}


def query_phrases(spec: DeskSpec) -> List[str]:
    return list(spec.news_queries) + desk_rules.instrument_tokens(spec)


# ── RSS (shared parse: Google News / oilprice.com / Cointelegraph) ──────

def _parse_rss_pubdate(raw: Optional[str]) -> Optional[datetime]:
    """RFC 822 first (Google News, Yahoo, oilprice, EIA), then the plain
    "YYYY-MM-DD HH:MM:SS" Investing.com emits, read as UTC. Anything else
    is None: a headline with no date is scored as undated, never guessed."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


_GNEWS_SOURCE_SUFFIX = re.compile(r"\s+-\s+([^-]+)$")


def _split_gnews_title(raw_title: str) -> Tuple[str, Optional[str]]:
    """Google News RSS appends ' - <Source>' to every title. Strip it off
    and return the source separately so scoring can tier it correctly."""
    m = _GNEWS_SOURCE_SUFFIX.search(raw_title)
    if m:
        return raw_title[: m.start()].strip(), m.group(1).strip()
    return raw_title.strip(), None


def parse_rss_items(xml_text: str, *, default_source: Optional[str] = None,
                     split_source_from_title: bool = False) -> List[Dict[str, Any]]:
    """RSS 2.0 <item> -> [{title, url, source, published_at}]. Tolerant of
    missing fields; a malformed feed yields fewer items, never an exception
    (parse errors are caught by the caller's http_client-level isolation —
    this function itself raises on genuinely broken XML, which callers
    wrap in try/except so one bad feed cannot sink the whole packet)."""
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        source_el = item.find("source")
        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            continue
        source = default_source
        if source_el is not None and (source_el.text or "").strip():
            source = source_el.text.strip()
        if split_source_from_title:
            title, gnews_source = _split_gnews_title(title)
            source = gnews_source or source
        out.append({
            "title": title,
            "url": (link_el.text or "").strip() if link_el is not None else "",
            "source": source or "unknown",
            "published_at": _parse_rss_pubdate(pub_el.text if pub_el is not None else None),
        })
    return out


def fetch_google_news(spec: DeskSpec, *, max_items: int = 30) -> Tuple[List[Dict], Optional[str]]:
    phrases = query_phrases(spec)
    if not phrases:
        return [], None
    q = " OR ".join(f'"{p}"' for p in phrases[:8])
    url = f"{GOOGLE_NEWS_URL}?q={quote(q)}&hl=en-US&gl=US&ceid=US:en"
    text, err = http_client.fetch_text(url)
    if err:
        return [], f"news:google_news:{err}"
    try:
        items = parse_rss_items(text, split_source_from_title=True)
    except ET.ParseError:
        return [], "news:google_news:bad_xml"
    return items[:max_items], None


def _make_gnews_query_fetcher(index: int):
    """One Google News request for the desk's `index`-th news query phrase,
    quoted and restricted to the last two days. Built as a named function
    (not a functools.partial) so api.py's `_fn_key` gives each its own
    cache file and its own `sources_failed` entry, and so each runs in its
    own thread like every other ATOMIC_FETCHERS entry."""
    def fetch(spec: DeskSpec, *, max_items: int = 20) -> Tuple[List[Dict], Optional[str]]:
        queries = list(spec.news_queries)[:GNEWS_MAX_QUERIES]
        if index >= len(queries):
            return [], None
        q = f'"{queries[index]}" {GNEWS_RECENCY}'
        url = f"{GOOGLE_NEWS_URL}?q={quote(q)}&hl=en-US&gl=US&ceid=US:en"
        text, err = http_client.fetch_text(url, timeout_s=5.0, retries=0)
        if err:
            return [], f"news:google_news_q{index}:{err}"
        try:
            items = parse_rss_items(text, split_source_from_title=True)
        except ET.ParseError:
            return [], f"news:google_news_q{index}:bad_xml"
        return items[:max_items], None

    fetch.__name__ = fetch.__qualname__ = f"fetch_google_news_q{index}"
    fetch.__doc__ = f"Google News RSS for news_queries[{index}], last two days."
    return fetch


GOOGLE_NEWS_QUERY_FETCHERS = tuple(_make_gnews_query_fetcher(i) for i in range(GNEWS_MAX_QUERIES))


def yahoo_symbols(spec: DeskSpec) -> List[str]:
    """Yahoo Finance symbols for this desk's instruments, in instrument
    order, deduped. See _YAHOO_SYMBOLS for the rule."""
    crypto = desk_rules.crypto_tickers()
    out: List[str] = []
    for inst in spec.instruments:
        coin = inst.coin.split(":", 1)[-1].upper()
        if coin in _YAHOO_SYMBOLS:
            sym = _YAHOO_SYMBOLS[coin]
        elif coin in crypto:
            sym = f"{coin}-USD"
        elif coin.isalpha() and 1 <= len(coin) <= 5:
            sym = coin
        else:
            continue
        if sym not in out:
            out.append(sym)
    return out


def fetch_yahoo_quotes(spec: DeskSpec, *, max_items: int = 25) -> Tuple[List[Dict], Optional[str]]:
    """Yahoo Finance's per-quote headline feed, one request for every symbol
    this desk trades. Items carry no <source>; Yahoo is the aggregator, so
    the source is "Yahoo Finance" (tier 2 in scoring.py)."""
    symbols = yahoo_symbols(spec)
    if not symbols:
        return [], None
    url = f"{YAHOO_RSS_URL}?s={quote(','.join(symbols), safe=',=^-')}&region=US&lang=en-US"
    text, err = http_client.fetch_text(url, timeout_s=6.0)
    if err:
        return [], f"news:yahoo_quotes:{err}"
    try:
        items = parse_rss_items(text, default_source="Yahoo Finance")
    except ET.ParseError:
        return [], "news:yahoo_quotes:bad_xml"
    return items[:max_items], None


def _fetch_eia_feed(spec: DeskSpec, url: str, key: str, max_items: int) -> Tuple[List[Dict], Optional[str]]:
    if not desk_rules.is_natgas_desk(spec):   # oil desks and the gas desk, see desk_rules
        return [], None
    text, err = http_client.fetch_text(url, timeout_s=6.0)
    if err:
        return [], f"news:{key}:{err}"
    try:
        items = parse_rss_items(text, default_source="EIA")
    except ET.ParseError:
        return [], f"news:{key}:bad_xml"
    return items[:max_items], None


def fetch_eia_today_in_energy(spec: DeskSpec, *, max_items: int = 20) -> Tuple[List[Dict], Optional[str]]:
    """EIA's daily Today in Energy articles: the primary source for US
    refinery runs, inventories and production context. Tier 1."""
    return _fetch_eia_feed(spec, EIA_TODAY_IN_ENERGY_URL, "eia_today_in_energy", max_items)


def fetch_eia_press(spec: DeskSpec, *, max_items: int = 12) -> Tuple[List[Dict], Optional[str]]:
    """EIA press releases: STEO publication notices and outlook headlines."""
    return _fetch_eia_feed(spec, EIA_PRESS_URL, "eia_press", max_items)


def fetch_investing_commodities(spec: DeskSpec, *, max_items: int = 20) -> Tuple[List[Dict], Optional[str]]:
    """Investing.com's commodities news feed (oil, gas, metals desks)."""
    if not (desk_rules.is_oil_desk(spec) or desk_rules.is_natgas_desk(spec)
            or desk_rules.is_metals_desk(spec)):
        return [], None
    text, err = http_client.fetch_text(INVESTING_COMMODITIES_URL, timeout_s=6.0)
    if err:
        return [], f"news:investing_commodities:{err}"
    try:
        items = parse_rss_items(text, default_source="Investing.com")
    except ET.ParseError:
        return [], "news:investing_commodities:bad_xml"
    return items[:max_items], None


def fetch_oilprice(spec: DeskSpec, *, max_items: int = 20) -> Tuple[List[Dict], Optional[str]]:
    if not desk_rules.is_oil_desk(spec):
        return [], None
    text, err = http_client.fetch_text(OILPRICE_URL)
    if err:
        return [], f"news:oilprice:{err}"
    try:
        items = parse_rss_items(text, default_source="OilPrice.com")
    except ET.ParseError:
        return [], "news:oilprice:bad_xml"
    return items[:max_items], None


def fetch_crypto_rss(spec: DeskSpec, *, max_items: int = 20) -> Tuple[List[Dict], Optional[str]]:
    if not desk_rules.is_crypto_desk(spec):
        return [], None
    text, err = http_client.fetch_text(CRYPTO_RSS_URL)
    if err:
        return [], f"news:crypto_rss:{err}"
    try:
        items = parse_rss_items(text, default_source="Cointelegraph")
    except ET.ParseError:
        return [], "news:crypto_rss:bad_xml"
    return items[:max_items], None


# ── GDELT ─────────────────────────────────────────────────────────────

def _gdelt_query(spec: DeskSpec) -> str:
    phrases = spec.news_queries or desk_rules.instrument_tokens(spec)
    inner = " OR ".join(f'"{p}"' for p in phrases[:6])
    return f"({inner}) sourcelang:english" if inner else ""


# A handful of common English function words. Vietnamese and Finnish are
# both Latin-script languages written mostly in plain ASCII letters plus a
# few diacritics, so an ASCII-ratio check alone does not catch them (it
# only catches scripts like Chinese) — confirmed against the real capture
# in tests/fixtures/gdelt_oil_sample.json, which has a Vietnamese headline
# ("Giá xăng dầu hôm nay...") that is >85% ASCII characters by count. None
# of these function words exist in Vietnamese or Finnish, so requiring at
# least one is a cheap, deterministic, zero-dependency second check rather
# than pulling in a language-detection library for one filter.
_ENGLISH_STOPWORDS = frozenset({
    "the", "a", "an", "as", "to", "of", "in", "on", "for", "is", "are",
    "says", "after", "amid", "with", "and", "from", "by", "at", "as",
})


def _is_mostly_ascii(text: str, *, min_ratio: float = 0.85) -> bool:
    if not text:
        return False
    ascii_count = sum(1 for c in text if ord(c) < 128)
    return (ascii_count / len(text)) >= min_ratio


def _looks_english(text: str) -> bool:
    """mostly-ASCII AND contains at least one common English function
    word. See _ENGLISH_STOPWORDS for why both checks are needed."""
    if not _is_mostly_ascii(text):
        return False
    tokens = title_tokens(text)
    return bool(tokens & _ENGLISH_STOPWORDS)


def fetch_gdelt(spec: DeskSpec, *, max_records: int = 30) -> Tuple[List[Dict], Optional[str]]:
    query = _gdelt_query(spec)
    if not query:
        return [], None
    # retries=0: this sandbox's shared egress IP hits GDELT's "one request
    # every 5 seconds" 429 limit on most calls (see module docstring), and
    # http_client's retry backoff is a fixed 1.0s — nowhere near 5s, so a
    # retry here almost never succeeds and only burns budget. Two 8s
    # attempts plus a 1s backoff (the old default) could cost ~17s on its
    # own, well over build_packet's 12s FETCH_DEADLINE_S, which is exactly
    # why desk_refresh was showing `news.fetch_gdelt:timeout_budget_exceeded`
    # (the whole-job timeout, not GDELT's own error) even when GDELT itself
    # answered the first attempt's 429 promptly. One bounded attempt keeps
    # this source's own worst case comfortably inside the packet budget.
    data, err = http_client.fetch_json(GDELT_URL, params={
        "query": query, "mode": "artlist", "format": "json",
        "maxrecords": max_records, "sort": "datedesc",
    }, timeout_s=5.0, retries=0)
    if err:
        return [], f"news:gdelt:{err}"
    if not isinstance(data, dict):
        return [], "news:gdelt:bad_shape"
    out = []
    for a in data.get("articles") or []:
        title = (a.get("title") or "").strip()
        if not title or not _looks_english(title):
            continue
        seen = a.get("seendate")  # "20261017T023000Z"
        pub = None
        if seen:
            try:
                pub = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                pub = None
        out.append({"title": title, "url": a.get("url") or "",
                    "source": a.get("domain") or "unknown", "published_at": pub})
    return out, None


# ── orchestrator ─────────────────────────────────────────────────────
# One callable per network source, each `(spec) -> (List[Dict], Optional[str])`.
# api.py fans every one of these out to its own thread so a slow source
# cannot serialize behind the others — see api.py's module docstring for
# the cold-build budget this exists for. GDELT is deliberately absent (see
# the module docstring: 21s per answer against a 12s packet deadline).
ATOMIC_FETCHERS = (
    fetch_google_news,
    *GOOGLE_NEWS_QUERY_FETCHERS,
    fetch_yahoo_quotes,
    fetch_eia_today_in_energy,
    fetch_eia_press,
    fetch_investing_commodities,
    fetch_oilprice,
    fetch_crypto_rss,
)


def score_and_dedup(raw_items: List[Dict[str, Any]], spec: DeskSpec,
                     now: datetime) -> List[Headline]:
    """Pure (no I/O): merge raw RSS/GDELT dicts from every source into
    scored, deduped, sorted `Headline`s. Split out from `fetch_headlines`
    so api.py's parallel fetch path can call the network fetchers
    concurrently and run this once, after every future resolves."""
    phrases = query_phrases(spec)
    instruments = desk_rules.instrument_tokens(spec)
    context = (list(spec.news_queries) + list(getattr(spec, "policy_topics", None) or [])
               + list(getattr(spec, "prediction_market_queries", None) or []))
    sorted_raw = sorted(raw_items, key=lambda r: r.get("published_at") or datetime.min.replace(
        tzinfo=timezone.utc), reverse=True)
    deduped = dedup_by_title(sorted_raw, lambda r: r["title"])

    headlines: List[Headline] = []
    for r in deduped:
        title, url, source, pub = r["title"], r["url"], r["source"], r["published_at"]
        rel = scoring.relevance(title, queries=phrases, source=source, published_at=pub, now=now)
        # a bare instrument token with no desk context ("David Brent in The
        # Office") keeps a quarter of its score and sinks below the fold
        rel = round(rel * scoring.instrument_only_factor(
            title, query_phrases=spec.news_queries, instrument_phrases=instruments,
            context_phrases=context), 4)
        headlines.append(Headline(
            id=stable_id("headline", url or title, title),
            title=title[:500], url=url or "about:blank", source=source or "unknown",
            published_at=pub, stance=scoring.stance(title, spec.id),
            relevance=rel, summary=None,
        ))
    headlines.sort(key=lambda h: h.relevance, reverse=True)
    return headlines


def fetch_headlines(spec: DeskSpec, now: datetime) -> Tuple[List[Headline], List[str]]:
    """Convenience sequential path (used by tests and anywhere parallel
    fetch isn't worth the setup) — calls every ATOMIC_FETCHERS entry in
    turn, then `score_and_dedup`. api.py's build_packet uses the fetchers
    directly through a thread pool instead of this function."""
    raw: List[Dict[str, Any]] = []
    failed: List[str] = []
    for fn in ATOMIC_FETCHERS:
        items, err = fn(spec)
        if err:
            failed.append(err)
        raw.extend(items)
    return score_and_dedup(raw, spec, now), failed
