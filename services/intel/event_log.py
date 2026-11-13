"""The streaming headline event log: one JSON line per enriched event, a
console line, and an optional webhook.

    $PATHIEL_STATE_DIR/intel/finlight_events.jsonl   (env.state_dir)

Each line: provider, mode (ws/rest), title, source, published, the
provider's sentiment, the Stage 1 label, the Stage 2 JSON (event_score),
latency in ms (published -> received, received -> scored) and the book
snapshot (book_guard) when one was taken.

The webhook (ALERT_WEBHOOK_URL) is OFF unless the variable is set. Discord
reads `content`, Slack reads `text`; a Telegram bot URL
(https://api.telegram.org/bot<token>/sendMessage?chat_id=<id>) gets
{chat_id, text}. Nothing is sent without the URL; a failed post is logged
and dropped, never retried into a storm.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from services.intel import env

logger = logging.getLogger(__name__)

WEBHOOK_ENV = "ALERT_WEBHOOK_URL"
EVENTS_FILE = "finlight_events.jsonl"
WEBHOOK_TIMEOUT_S = 5.0


def events_path() -> str:
    return os.path.join(env.state_dir(), "intel", EVENTS_FILE)


def _utc(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_event(alert: Dict[str, Any], *, mode: str, stage2: Dict[str, Any], scored_ms: int,
                book: Optional[Dict[str, Any]] = None, entities: Any = None) -> Dict[str, Any]:
    """The log row for one Stage 1 hit. Pure."""
    pub = alert.get("published_ms")
    rec = alert.get("received_ms") or alert.get("at_ms")
    return {
        "provider": alert.get("provider") or "finlight", "mode": mode,
        "title": alert.get("title"), "source": alert.get("source"), "url": alert.get("url"),
        "published": _utc(pub), "received": _utc(rec), "scored": _utc(scored_ms),
        "sentiment": alert.get("sentiment"), "entities": entities or [],
        "stage1": alert.get("label"), "direction": alert.get("direction"), "group": alert.get("group"),
        "stage2": stage2,
        "latency_ms": {"published_to_received": (rec - pub) if (pub is not None and rec is not None) else None,
                       "received_to_scored": (scored_ms - rec) if rec is not None else None},
        "book": book,
    }


def append_event(event: Dict[str, Any], path: Optional[str] = None) -> str:
    """Append one line; returns the path. Creates the directory."""
    path = path or events_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(event, default=str) + "\n")
    return path


def console_line(event: Dict[str, Any]) -> str:
    """`FINLIGHT ws 2026-11-14T10:01:02Z [trigger:chokepoints_maritime] chokepoint_disruption bullish 0.85 confirmed lag 4s: Tanker struck ... (reuters.com) | xyz:BRENTOIL spread ...`"""
    s2 = event.get("stage2") or {}
    lat = (event.get("latency_ms") or {}).get("published_to_received")
    lag = f" lag {lat / 1000:.0f}s" if isinstance(lat, (int, float)) else ""
    book = ""
    if event.get("book"):
        from services.intel.book_guard import summary_line
        book = " | " + summary_line(event["book"])
    return (f"FINLIGHT {event.get('mode')} {event.get('received')} [{event.get('stage1')}] {s2.get('event_type')} "
            f"{s2.get('expected_oil_direction')} {s2.get('confidence_score')} "
            f"{'confirmed' if s2.get('is_confirmed') else 'unconfirmed'}{lag}: "
            f"{(event.get('title') or '')[:120]} ({event.get('source')}){book}")


def webhook_url() -> Optional[str]:
    return (os.environ.get(WEBHOOK_ENV) or "").strip() or None


def webhook_body(url: str, line: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """The POST body for `url`. Pure."""
    u = urlparse(url)
    if u.hostname and u.hostname.endswith("api.telegram.org"):
        chat = (parse_qs(u.query).get("chat_id") or [""])[0]
        return {"chat_id": chat, "text": line}
    return {"content": line[:1900], "text": line, "event": {k: event.get(k) for k in
                                                              ("title", "source", "stage1", "stage2", "received")}}


def post_webhook(event: Dict[str, Any], url: Optional[str] = None, *, post: Any = None) -> bool:
    """Send the console line to the webhook when ALERT_WEBHOOK_URL (or `url`)
    is set. False when off or failed. Never raises, never sends without a URL."""
    url = url or webhook_url()
    if not url:
        return False
    line = console_line(event)
    body = webhook_body(url, line, event)
    try:
        if post is None:
            import httpx
            resp = httpx.post(url, json=body, timeout=WEBHOOK_TIMEOUT_S)
            resp.raise_for_status()
        else:
            post(url, body)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[event_log] webhook failed: %s", f"{type(exc).__name__}"[:80])
        return False


def now_ms() -> int:
    return int(time.time() * 1000)


__all__ = ["EVENTS_FILE", "WEBHOOK_ENV", "append_event", "build_event", "console_line", "events_path",
           "post_webhook", "webhook_body", "webhook_url"]
