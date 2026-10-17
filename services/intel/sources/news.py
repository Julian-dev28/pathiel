"""News headlines: GDELT DOC 2.0, Google News RSS, oilprice.com RSS (oil
desks only), and a crypto RSS (crypto desks only). Every parser here was
built against a REAL captured response — see tests/fixtures/ — not against
assumed schema.

Verified live 2026-10-17:
  GDELT      api.gdeltproject.org/api/v2/doc/doc  — works, but this
             sandbox's shared egress IP hit "429 ... limit to one request
             every 5 seconds" on most attempts even single-threaded with a
             10-20s gap between calls; one request in several succeeded.
             Treated as a best-effort source: one retry, short internal
             pacing, and a clean sources_failed entry on 429 rather than
             ever blocking the rest of the packet on it.
  Google News news.google.com/rss/search            — reliable, HTTP 200.
  oilprice.com oilprice.com/rss/main                — reliable, HTTP 200.
               (/rss and /rss/latest-energy-news both 404 — /rss/main is
               the real path.)
  Cointelegraph cointelegraph.com/rss               — reliable, HTTP 200.
               (coindesk.com/arc/outboundfeeds/rss/ 308-redirects and was
               dropped in favor of Cointelegraph, which the brief allows:
               "a crypto RSS (CoinDesk or similar)".)
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


def query_phrases(spec: DeskSpec) -> List[str]:
    return list(spec.news_queries) + desk_rules.instrument_tokens(spec)


# ── RSS (shared parse: Google News / oilprice.com / Cointelegraph) ──────

def _parse_rss_pubdate(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
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
    data, err = http_client.fetch_json(GDELT_URL, params={
        "query": query, "mode": "artlist", "format": "json",
        "maxrecords": max_records, "sort": "datedesc",
    }, timeout_s=8.0)
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
# api.py fans every one of these out to its own thread so a slow/retrying
# source (GDELT in particular) cannot serialize behind the others — see
# api.py's module docstring for the 15s cold-build budget this exists for.
ATOMIC_FETCHERS = (fetch_google_news, fetch_gdelt, fetch_oilprice, fetch_crypto_rss)


def score_and_dedup(raw_items: List[Dict[str, Any]], spec: DeskSpec,
                     now: datetime) -> List[Headline]:
    """Pure (no I/O): merge raw RSS/GDELT dicts from every source into
    scored, deduped, sorted `Headline`s. Split out from `fetch_headlines`
    so api.py's parallel fetch path can call the network fetchers
    concurrently and run this once, after every future resolves."""
    phrases = query_phrases(spec)
    sorted_raw = sorted(raw_items, key=lambda r: r.get("published_at") or datetime.min.replace(
        tzinfo=timezone.utc), reverse=True)
    deduped = dedup_by_title(sorted_raw, lambda r: r["title"])

    headlines: List[Headline] = []
    for r in deduped:
        title, url, source, pub = r["title"], r["url"], r["source"], r["published_at"]
        rel = scoring.relevance(title, queries=phrases, source=source, published_at=pub, now=now)
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
