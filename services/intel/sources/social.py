"""Trump's posts and the official feeds, for the surprise-headline watcher.

Operator, 2026-11-05: "A Truth Social / X feed for the headline watcher.
BUILD IT THEN, CHECK IF IT CAN BE DONE FIRST". Checked from this machine
the same day (services/intel/README.md, "Trump posts and official feeds"):

  truthsocial.com        no RSS (the .rss path serves the web app); the
                         Mastodon-style API answers 403 from Cloudflare
                         without a logged-in session; the terms forbid any
                         scraper or automated access. Not used.
  X / Twitter            no free read tier in 2026: pay-per-use at $0.005 per
                         post read (docs.x.com/x-api/getting-started/pricing),
                         a card on file. Third-party mirrors (Nitter) are dead
                         or against the terms. Not used; the cost is reported.
  www.trumpstruth.org    a public archive of every @realDonaldTrump post with
                         a documented RSS feed (?limit=1..500, date filters),
                         robots.txt allows everything, no auth. pubDate is the
                         ORIGINAL post time; the archive catches a post 15
                         minutes to hours later, so this is a fallback that
                         sometimes beats the wires, never a guarantee.
  whitehouse.gov/news    official releases, statements, actions (RSS, WordPress),
                         200 to this module's own User-Agent. Used.
  state.gov press        an RSS exists but answers 403 to any non-browser
                         User-Agent (200 only to a browser string; the eval
                         of 2026-11-05 15:20Z shows the 403). We do not spoof
                         a browser to get past a bot filter. Not used.
  Treasury / OFAC        no press RSS exists (home.treasury.gov/rss.xml is a
                         site-wide feed of program pages, ofac.treasury.gov/
                         rss.xml lists sanctions programs, newest 2025). OFAC's
                         recent-actions HTML is already read by policy.py for
                         the thesis; too slow (10s) for a one-minute watcher.

Pure parsing here plus a thin fetch through http_client. The watcher
(scripts/trigger_watch.py) polls, services/intel/headline_watch.py decides.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from services.intel import http_client
from services.intel.sources.news import _parse_rss_pubdate, parse_rss_items

TRUMPSTRUTH_FEED_URL = "https://www.trumpstruth.org/feed"
TRUMPSTRUTH_LIMIT = 40                      # one page; a minute of posts is never 40
TRUMP_SOURCE = "truthsocial:realDonaldTrump"
WHITEHOUSE_NEWS_URL = "https://www.whitehouse.gov/news/feed/"
STATE_PRESS_URL = "https://www.state.gov/rss-feed/press-releases/feed/"   # 403 to our UA; parse-tested only
OFFICIAL_FEEDS: Tuple[Tuple[str, str], ...] = (("whitehouse.gov", WHITEHOUSE_NEWS_URL),)
FEED_TIMEOUT_S = 8.0

_NS = {"truth": "https://truthsocial.com/ns", "archive": "https://www.trumpstruth.org/rss-extensions"}
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(fragment: Optional[str]) -> str:
    """Tags out, entities decoded, whitespace collapsed. A tag boundary
    becomes a space so "<p>a</p><p>b</p>" reads "a b"."""
    if not fragment:
        return ""
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()


def parse_trumpstruth(xml_text: str) -> List[Dict[str, Any]]:
    """www.trumpstruth.org/feed -> [{id, url, archive_url, text, title, source,
    post_type, published_at}], feed order (newest first). `id` and `url` are
    the ORIGINAL Truth Social status id and URL (truth: namespace); the
    archive's own link is `archive_url`. `text` is the post body with HTML
    stripped; an image or video post reads "View image" / "View video", a
    bare link post reads the link. `post_type` is original/repost/quote/
    reply as the archive labels it. Raises ET.ParseError on broken XML;
    the fetcher turns that into an error string."""
    root = ET.fromstring(xml_text)
    out: List[Dict[str, Any]] = []
    for item in root.iter("item"):
        oid = (item.findtext("truth:originalId", namespaces=_NS) or "").strip()
        ourl = (item.findtext("truth:originalUrl", namespaces=_NS) or "").strip()
        archive_url = (item.findtext("link") or "").strip()
        text = strip_html(item.findtext("description")) or strip_html(item.findtext("title"))
        if not (oid or archive_url or text):
            continue
        out.append({
            "id": oid or archive_url,
            "url": ourl or archive_url,
            "archive_url": archive_url,
            "text": text,
            "title": text[:200],
            "source": TRUMP_SOURCE,
            "post_type": (item.findtext("archive:postType", namespaces=_NS) or "original").strip(),
            "published_at": _parse_rss_pubdate(item.findtext("pubDate")),
        })
    return out


def parse_official(xml_text: str, source: str) -> List[Dict[str, Any]]:
    """A WordPress RSS feed (whitehouse.gov, state.gov) -> news rows
    ({title, url, source, published_at, id}) in feed order. `id` is the
    item URL, so an edited title does not alert twice."""
    rows = parse_rss_items(xml_text, default_source=source)
    for r in rows:
        r["id"] = r.get("url") or r["title"]
    return rows


def fetch_trump_posts(*, limit: int = TRUMPSTRUTH_LIMIT,
                      timeout_s: float = FEED_TIMEOUT_S) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """The newest `limit` archived posts. ([], "social:trumpstruth:<why>") on
    any failure; never raises. No retry: the watcher polls again in a minute."""
    text, err = http_client.fetch_text(TRUMPSTRUTH_FEED_URL, params={"limit": limit}, timeout_s=timeout_s,
                                       retries=0)
    if err or not text:
        return [], f"social:trumpstruth:{err or 'empty'}"
    try:
        return parse_trumpstruth(text), None
    except ET.ParseError:
        return [], "social:trumpstruth:bad_xml"


def fetch_official_feeds(*, timeout_s: float = FEED_TIMEOUT_S) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Every OFFICIAL_FEEDS item, one list, plus one error string per feed
    that failed ("social:whitehouse.gov:timeout"). A failed feed costs only
    its own rows; never raises."""
    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    for source, url in OFFICIAL_FEEDS:
        text, err = http_client.fetch_text(url, timeout_s=timeout_s, retries=0)
        if err or not text:
            errors.append(f"social:{source}:{err or 'empty'}")
            continue
        try:
            items += parse_official(text, source)
        except ET.ParseError:
            errors.append(f"social:{source}:bad_xml")
    return items, errors


__all__ = ["FEED_TIMEOUT_S", "OFFICIAL_FEEDS", "STATE_PRESS_URL", "TRUMPSTRUTH_FEED_URL", "TRUMPSTRUTH_LIMIT",
           "TRUMP_SOURCE", "WHITEHOUSE_NEWS_URL", "fetch_official_feeds", "fetch_trump_posts", "parse_official",
           "parse_trumpstruth", "strip_html"]
