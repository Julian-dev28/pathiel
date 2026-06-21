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
import ssl
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
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


def _get_json(url: str, timeout: float = 12.0) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
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

    allow_fetch=False = CACHE-ONLY (return a fresh cached value or None, no network)."""
    key = f"gdelt::{query}::{timespan}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
    if not allow_fetch:
        return None

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
