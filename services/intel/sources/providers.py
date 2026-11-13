"""Streaming news providers for the headline watcher: one shape, many feeds.

A provider pushes NEW articles (a WebSocket, a webhook, or a polling loop
that remembers what it already sent) into the EXISTING headline pipeline
(services/intel/headline_watch.news_alerts -> record -> the watcher's
tick-wake and the headline blackout). It never judges a headline itself.

Every provider yields the same dict (`NewsItem`), a superset of the row
`news_alerts` already consumes from the RSS parsers
(sources/news.parse_rss_items: title, url, source, published_at):

    title          str, the headline
    url            str, canonical link (also the dedup id)
    id             str, provider article id or the url
    source         str, the outlet as the provider names it ("reuters.com")
    published_at   datetime (tz-aware) or None
    received_at    datetime (tz-aware), when THIS process saw it
    provider       str, "finlight", "treenews", ...
    sentiment      "positive" | "negative" | "neutral" | None (the provider's)
    sentiment_confidence   float 0..1 or None
    entities       list of {name, ticker, ...} dicts, [] when none
    summary        str or None

The first adapter is sources/finlight.py. A Tree News or X/Telegram adapter
subclasses BaseNewsProvider the same way: implement `run` (push items to
`on_item` until `stop` is set) and, when the feed can be polled, `fetch_recent`.
"""
from __future__ import annotations

import abc
import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

NewsItem = Dict[str, Any]
OnItem = Callable[[NewsItem], Any]

SENTIMENTS = ("positive", "negative", "neutral")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: Any) -> Optional[datetime]:
    """A tz-aware datetime from a datetime, an ISO string or an epoch (s or ms)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e11:          # milliseconds
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_sentiment(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in SENTIMENTS:
        return s
    if s in ("bullish", "pos"):
        return "positive"
    if s in ("bearish", "neg"):
        return "negative"
    return None


def normalize_item(*, title: str, url: str = "", source: str = "", published_at: Any = None,
                   provider: str, sentiment: Any = None, sentiment_confidence: Any = None,
                   entities: Optional[List[Dict[str, Any]]] = None, summary: Optional[str] = None,
                   item_id: Optional[str] = None, received_at: Optional[datetime] = None) -> NewsItem:
    """One NewsItem. Pure. Never raises on a missing optional field."""
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    url = str(url or "").strip()
    conf: Optional[float]
    try:
        conf = None if sentiment_confidence is None else max(0.0, min(1.0, float(sentiment_confidence)))
    except (TypeError, ValueError):
        conf = None
    return {
        "title": title,
        "url": url,
        "id": str(item_id or url or hashlib.sha1(title.lower().encode()).hexdigest()[:16]),
        "source": str(source or "unknown").strip() or "unknown",
        "published_at": _dt(published_at),
        "received_at": received_at or utcnow(),
        "provider": provider,
        "sentiment": normalize_sentiment(sentiment),
        "sentiment_confidence": conf,
        "entities": [e for e in (entities or []) if isinstance(e, dict)],
        "summary": (str(summary).strip() or None) if summary else None,
    }


def dedup_key(item: NewsItem) -> str:
    """The provider-level dedup key: id/url first, else the normalized title."""
    return str(item.get("id") or item.get("url") or "") or hashlib.sha1(
        re.sub(r"\W+", " ", str(item.get("title") or "").lower()).strip().encode()).hexdigest()[:16]


class BaseNewsProvider(abc.ABC):
    """A push source of NewsItems. `run` is the long-lived loop; `mode` says
    what transport is active ("ws", "rest", "down", "idle") for the log line."""

    name: str = "base"

    def __init__(self) -> None:
        self.mode: str = "idle"
        self.last_error: Optional[str] = None
        self.received: int = 0

    @abc.abstractmethod
    async def run(self, on_item: OnItem, stop: asyncio.Event) -> None:
        """Push every NEW item to `on_item` until `stop` is set. Must never
        raise out of a transport error: log, back off, reconnect."""

    async def fetch_recent(self, since: Optional[datetime] = None) -> List[NewsItem]:
        """One polled fetch of recent items, newest first. Providers that only
        stream return []."""
        return []


__all__ = ["BaseNewsProvider", "NewsItem", "OnItem", "SENTIMENTS", "dedup_key", "normalize_item",
           "normalize_sentiment", "utcnow"]
