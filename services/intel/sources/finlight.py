"""Finlight (finlight.me): real-time oil and geopolitics headlines for the
headline watcher. Operator, 2026-11-14: cut the headline lag from the RSS
poll's ~120 s to seconds, through the EXISTING pipeline (headline_watch).

Transport, in order:
  1. WebSocket wss://wss.finlight.me, the query below. Every article the
     server pushes is one NewsItem, seconds after publication.
  2. REST, POST https://api.finlight.me/v2/articles (the SDK's
     ArticleService.fetch_articles), when the WebSocket is refused for the
     key's plan (finlight.me/pricing, read 2026-11-14: the free "Launchpad"
     plan has no WebSocket; Pro Light and up do), when the handshake is
     refused (401/403), or when no admit arrives in WS_PROBE_S. A polling
     loop every `poll_s` (FINLIGHT_POLL_S, default 15) asks for the last
     MAX_AGE_MIN of articles and pushes the ones it has not pushed before.

What the free plan delivers (finlight.me/pricing and the smoke test of
2026-11-13 20:40Z): "Launchpad" has a 12-hour data delay (the newest
article by createdAt was 08:25Z), no sentiment, no entities, no WebSocket
(the handshake answers HTTP 403). A 12-hour-old headline never passes
headline_watch's 45-minute age filter, so on the free plan this adapter
alerts on NOTHING and spends nothing but its polls; the watcher's status
line shows the newest published age so the delay is visible. Pro Light
($29/month) removes the delay and adds the WebSocket and entities;
sentiment starts at Pro Standard ($99). The Stage 2 rule scorer works
without sentiment (it only adjusts confidence).

Rate limits (the same pricing page): plans are quotas per MONTH, 5,000
requests free, 10,000 / 50,000 / 150,000 paid. A 15 s poll is 172,800
requests a month, over every plan, so the poll interval has a floor:
`FINLIGHT_REST_MONTHLY_CAP` (default 5,000, the free plan) spread over a
31-day month at 90% leaves 576 s between polls; `effective_poll_s` is the
larger of the two and the mode log line says which won. Set the cap to the
plan's number and the floor moves: Pro Scale (150,000) allows 19 s. On the
WebSocket the quota is "dispatches" (articles pushed), which this narrow
query stays well under. A 429 doubles the interval up to REST_MAX_BACKOFF_S.

Why this module owns the WebSocket loop instead of calling
finlight_client.WebSocketClient.connect(): that method never returns. It
catches EVERY handshake error (a 401/403 for a plan without WebSocket
included) and reconnects forever, logging each attempt, so a caller cannot
tell "no WebSocket on this plan" from "the network blinked" and can never
fall back. The wire protocol is small and read from the SDK's
_base_websocket_client.py (2.4.0): headers x-api-key and x-client-version,
one JSON request (GetArticlesWebSocketParams plus a clientNonce), then
messages with "action" in admit / pong / sendArticle / error / preempted /
admin_kick, and a {"action": "ping"} every 25 s. The SDK's models
(GetArticlesWebSocketParams, Article) and its REST client are used as is.

The key: FINLIGHT_API_KEY from the environment (scripts/_state_env.
load_env_local puts .env.local there). It is never logged, never printed,
never part of an exception message: `redact` scrubs it from every error
string this module records, and tests/test_finlight.py checks the logs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from services.intel.sources.providers import BaseNewsProvider, NewsItem, OnItem, normalize_item, utcnow

logger = logging.getLogger(__name__)

KEY_ENV = "FINLIGHT_API_KEY"
POLL_ENV = "FINLIGHT_POLL_S"
CAP_ENV = "FINLIGHT_REST_MONTHLY_CAP"
DEFAULT_POLL_S = 15.0
DEFAULT_MONTHLY_CAP = 5000            # the free plan (finlight.me/pricing, 2026-11-14)
BUDGET_SHARE = 0.9                    # leave 10% of the month's quota for the smoke test and by-hand calls
WS_PROBE_S = 20.0                     # no admit inside this: the plan has no WebSocket, fall back
WS_RETRY_REST_S = 1800.0              # while on REST, try the WebSocket again this often
REST_MAX_BACKOFF_S = 300.0
REST_PAGE_SIZE = 50
LOOKBACK_MIN = 45                     # headline_watch.MAX_AGE_MIN: older never alerts anyway
WS_PING_S = 25.0
WS_PONG_TIMEOUT_S = 60.0
WS_BACKOFF_BASE_S = 0.5
WS_BACKOFF_MAX_S = 30.0
WS_URL = "wss://wss.finlight.me"
CLIENT_VERSION = "python/finlight-client@2.4.0 pathiel-intel"

QUERY = ('(oil OR crude OR WTI OR Brent OR OPEC OR tanker OR "Strait of Hormuz" OR "Red Sea" OR UKMTO '
         'OR ceasefire OR "peace talks" OR refinery)')

# A server "error" message or a close reason that means the plan, not the
# network, refused us. Matched lower-case.
_TIER_WORDS = ("plan", "tier", "subscription", "upgrade", "not included", "not allowed", "permission",
               "forbidden", "unauthorized", "unauthorised", "invalid api key", "api key", "quota", "blocked")
_TIER_STATUS = {401, 402, 403}
_TIER_CLOSE_CODES = {1008, 4002}


class TierError(RuntimeError):
    """The WebSocket is not available to this key (plan or permission)."""


def api_key() -> Optional[str]:
    v = (os.environ.get(KEY_ENV) or "").strip()
    return v or None


def redact(text: Any, key: Optional[str] = None) -> str:
    """`text` with the API key (and its first 8 characters, which the server
    may echo) replaced by ***. Pure."""
    s = str(text)
    k = key if key is not None else api_key()
    if k:
        s = s.replace(k, "***")
        if len(k) >= 8:
            s = s.replace(k[:8], "***")
    return s


def effective_poll_s(poll_s: Optional[float] = None, monthly_cap: Optional[int] = None,
                     share: float = BUDGET_SHARE) -> Dict[str, Any]:
    """The REST interval actually used and why: max(requested, budget floor).
    Pure. The floor is a 31-day month over `share` of `monthly_cap`."""
    try:
        req = float(poll_s if poll_s is not None else os.environ.get(POLL_ENV) or DEFAULT_POLL_S)
    except (TypeError, ValueError):
        req = DEFAULT_POLL_S
    req = max(1.0, req)
    try:
        cap = int(monthly_cap if monthly_cap is not None else os.environ.get(CAP_ENV) or DEFAULT_MONTHLY_CAP)
    except (TypeError, ValueError):
        cap = DEFAULT_MONTHLY_CAP
    floor = (31 * 86400.0) / max(1.0, cap * share) if cap > 0 else 0.0
    eff = max(req, floor)
    return {"requested_s": req, "floor_s": round(floor, 1), "effective_s": round(eff, 1), "monthly_cap": cap,
            "why": "budget floor" if floor > req else "requested"}


def article_to_item(a: Any, received_at: Optional[datetime] = None) -> NewsItem:
    """A finlight_client.models.Article (or its dict) -> NewsItem. Pure."""
    g = (lambda k: a.get(k)) if isinstance(a, dict) else (lambda k: getattr(a, k, None))
    companies = g("companies") or []
    entities = []
    for c in companies:
        cg = (lambda k: c.get(k)) if isinstance(c, dict) else (lambda k: getattr(c, k, None))
        entities.append({"name": cg("name"), "ticker": cg("ticker"), "confidence": cg("confidence"),
                         "country": cg("country"), "sector": cg("sector")})
    return normalize_item(title=g("title") or "", url=g("link") or "", source=g("source") or "",
                          published_at=g("publishDate"), provider="finlight", sentiment=g("sentiment"),
                          sentiment_confidence=g("confidence"), entities=entities, summary=g("summary"),
                          item_id=g("link") or None, received_at=received_at)


def is_tier_error(exc: BaseException) -> bool:
    """A handshake refused by status 401/402/403, a close code 1008/4002, or
    a message naming the plan. Pure."""
    if isinstance(exc, TierError):
        return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in _TIER_STATUS:
        return True
    code = getattr(exc, "code", None)
    if code in _TIER_CLOSE_CODES:
        return True
    msg = str(exc).lower()
    return any(w in msg for w in _TIER_WORDS)


def _ssl_context() -> Any:
    """A TLS context with certifi's CA bundle. A python.org build on macOS
    (this machine, 3.13) ships no system CAs in ssl's default context:
    requests (the REST path) carries certifi and worked, websockets used the
    bare default and failed CERTIFICATE_VERIFY_FAILED (smoke test,
    2026-11-14). Falls back to the default context without certifi."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


class FinlightProvider(BaseNewsProvider):
    """WebSocket first, REST polling as the fallback. `ws_connect` and
    `rest_fetch` are injectable so the tests use fakes; the defaults open the
    real transports."""

    name = "finlight"

    def __init__(self, key: Optional[str] = None, *, poll_s: Optional[float] = None,
                 monthly_cap: Optional[int] = None, query: str = QUERY,
                 ws_connect: Optional[Callable[..., Any]] = None,
                 rest_fetch: Optional[Callable[..., Any]] = None,
                 ws_probe_s: float = WS_PROBE_S, ws_retry_s: float = WS_RETRY_REST_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._key = key or api_key()
        self.query = query
        self.poll = effective_poll_s(poll_s, monthly_cap)
        self.ws_probe_s = ws_probe_s
        self.ws_retry_s = ws_retry_s
        self._ws_connect = ws_connect or self._real_ws_connect
        self._rest_fetch = rest_fetch or self._real_rest_fetch
        self._clock = clock
        self._sent: Dict[str, float] = {}       # dedup id -> monotonic time first pushed
        self.rest_calls = 0
        self.ws_fallback_reason: Optional[str] = None
        self._api: Any = None

    # ── public ──────────────────────────────────────────────────────────

    @property
    def has_key(self) -> bool:
        return bool(self._key)

    def status(self) -> Dict[str, Any]:
        return {"provider": self.name, "mode": self.mode, "received": self.received, "rest_calls": self.rest_calls,
                "poll": self.poll, "last_error": self.last_error, "ws_fallback_reason": self.ws_fallback_reason}

    async def run(self, on_item: OnItem, stop: asyncio.Event) -> None:
        """WebSocket until it is refused for the plan, then REST until `stop`.
        While on REST the WebSocket is probed again every ws_retry_s."""
        if not self._key:
            self.mode = "down"
            self.last_error = f"{KEY_ENV} not set"
            logger.warning("[finlight] no key: provider off")
            return
        while not stop.is_set():
            try:
                await self._run_ws(on_item, stop)        # returns only on a tier error or stop
            except TierError as exc:
                self.ws_fallback_reason = redact(exc, self._key)
                logger.warning("[finlight] mode=rest (websocket unavailable: %s), poll every %ss (%s)",
                               self.ws_fallback_reason, self.poll["effective_s"], self.poll["why"])
            if stop.is_set():
                break
            await self._run_rest(on_item, stop, until=self._clock() + self.ws_retry_s)
        self.mode = "idle"

    async def fetch_recent(self, since: Optional[datetime] = None) -> List[NewsItem]:
        """One REST page of the query since `since` (default LOOKBACK_MIN
        ago), newest first, as NewsItems. Counts against rest_calls."""
        if not self._key:
            return []
        since = since or (utcnow() - timedelta(minutes=LOOKBACK_MIN))
        self.rest_calls += 1
        articles = await self._rest_fetch(self.query, since)
        now = utcnow()
        items = [article_to_item(a, now) for a in articles]
        items.sort(key=lambda i: i.get("published_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items

    # ── transports ──────────────────────────────────────────────────────

    def _push(self, item: NewsItem, on_item: OnItem) -> bool:
        """Push once per id; True when pushed. The dedup table forgets ids
        older than 6 hours so it cannot grow for a week."""
        k = item.get("id") or item.get("url") or item.get("title")
        if not k or not item.get("title"):
            return False
        now = self._clock()
        if k in self._sent:
            return False
        self._sent[k] = now
        if len(self._sent) > 5000:
            cut = now - 6 * 3600
            self._sent = {i: t for i, t in self._sent.items() if t >= cut}
        self.received += 1
        try:
            on_item(item)
        except Exception as exc:  # noqa: BLE001 - a consumer bug must not stop the stream
            logger.warning("[finlight] on_item failed: %s", redact(exc, self._key))
        return True

    async def _run_ws(self, on_item: OnItem, stop: asyncio.Event) -> None:
        """One session after another: a clean close reconnects at once, a
        transport error after an exponential delay (0.5 s doubling to 30 s),
        a plan refusal raises TierError to the caller (REST takes over)."""
        delay = WS_BACKOFF_BASE_S
        while not stop.is_set():
            self.mode = "ws:connecting"
            try:
                await self._ws_connect(self._key, self.query, self._ws_on_article(on_item), stop,
                                       self.ws_probe_s, self._mark_ws_up)
                delay = WS_BACKOFF_BASE_S
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - the transport may fail any way it likes
                if is_tier_error(exc):
                    self.mode = "rest"
                    raise TierError(redact(exc, self._key)) from None
                self._note_error(exc)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, WS_BACKOFF_MAX_S)

    def _mark_ws_up(self) -> None:
        if self.mode != "ws":
            logger.info("[finlight] mode=ws (admitted)")
        self.mode = "ws"

    def _ws_on_article(self, on_item: OnItem) -> Callable[[Any], None]:
        def handle(article: Any) -> None:
            self._push(article_to_item(article, utcnow()), on_item)
        return handle

    def _note_error(self, exc: BaseException) -> None:
        self.last_error = redact(f"{type(exc).__name__}: {exc}", self._key)[:200]
        logger.warning("[finlight] transport error, backing off: %s", self.last_error)

    async def _run_rest(self, on_item: OnItem, stop: asyncio.Event, *, until: float) -> None:
        """Poll until `stop` or the monotonic time `until` (then the caller
        retries the WebSocket). A failed poll backs off, doubling up to
        REST_MAX_BACKOFF_S; a 429 does the same."""
        self.mode = "rest"
        interval = float(self.poll["effective_s"])
        backoff = interval
        while not stop.is_set() and self._clock() < until:
            started = self._clock()
            try:
                for item in reversed(await self.fetch_recent()):   # oldest first: feed order
                    self._push(item, on_item)
                backoff = interval
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001
                self._note_error(exc)
                backoff = min(backoff * 2, REST_MAX_BACKOFF_S)
            wait = max(0.0, backoff - (self._clock() - started))
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass

    # ── the real transports (not exercised by the gate tests) ───────────

    def _client(self) -> Any:
        if self._api is None:
            from finlight_client import ApiConfig, FinlightApi
            # retry_count=0: the SDK retries a 429 three times with a 1 s
            # backoff, which spends quota; _run_rest backs off itself.
            self._api = FinlightApi(ApiConfig(api_key=self._key or "", retry_count=0, timeout=8000))
        return self._api

    async def _real_rest_fetch(self, query: str, since: datetime) -> List[Any]:
        from finlight_client.models import GetArticlesParams
        params = GetArticlesParams(query=query, includeEntities=True, orderBy="publishDate", order="DESC",
                                   pageSize=REST_PAGE_SIZE, language="en",
                                   **{"from": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        try:
            resp = await asyncio.to_thread(self._client().articles.fetch_articles, params)
        except Exception as exc:  # noqa: BLE001 - requests' HTTPError carries the URL, never the header
            raise RuntimeError(redact(f"rest {type(exc).__name__}: {exc}", self._key)) from None
        return list(resp.articles)

    async def _real_ws_connect(self, key: str, query: str, on_article: Callable[[Any], None],
                               stop: asyncio.Event, probe_s: float, on_admit: Callable[[], None]) -> None:
        """One WebSocket session, the SDK's protocol (module docstring).
        Returns when the socket closes cleanly or `stop` is set; raises
        TierError for a plan refusal, anything else for a transport error."""
        from finlight_client.models import Article, GetArticlesWebSocketParams
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidStatus
        headers = {"x-api-key": key, "x-client-version": CLIENT_VERSION}
        try:
            ws_cm = connect(WS_URL, additional_headers=headers, open_timeout=probe_s, ssl=_ssl_context())
            ws = await ws_cm.__aenter__()
        except InvalidStatus as exc:
            status = exc.response.status_code
            if status in _TIER_STATUS:
                raise TierError(f"websocket handshake refused with HTTP {status}") from None
            raise RuntimeError(f"websocket handshake failed with HTTP {status}") from None
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(redact(f"websocket connect {type(exc).__name__}: {exc}", key)) from None
        admitted = False
        try:
            payload = GetArticlesWebSocketParams(query=query, includeEntities=True, language="en").model_dump()
            payload["clientNonce"] = str(uuid.uuid4())
            await ws.send(json.dumps(payload))
            last_pong = time.monotonic()
            next_ping = last_pong + WS_PING_S
            deadline = time.monotonic() + probe_s
            while not stop.is_set():
                now = time.monotonic()
                if not admitted and now > deadline:
                    raise TierError(f"no admit from the websocket in {probe_s:g}s")
                if now >= next_ping:
                    await ws.send(json.dumps({"action": "ping", "t": int(time.time() * 1000)}))
                    next_ping = now + WS_PING_S
                if now - last_pong > WS_PONG_TIMEOUT_S:
                    raise RuntimeError("websocket pong timeout")
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                action = msg.get("action")
                if action == "pong":
                    last_pong = time.monotonic()
                elif action == "admit":
                    admitted = True
                    last_pong = time.monotonic()
                    on_admit()
                elif action == "sendArticle":
                    try:
                        on_article(Article.model_validate(msg.get("data") or {}))
                    except Exception as exc:  # noqa: BLE001 - one bad article
                        logger.debug("[finlight] bad article payload: %s", redact(exc, key))
                elif action == "error":
                    data = msg.get("data") or msg.get("error") or "unknown error"
                    text = redact(str(data), key)
                    if any(w in text.lower() for w in _TIER_WORDS):
                        raise TierError(f"websocket error: {text[:160]}")
                    raise RuntimeError(f"websocket error: {text[:160]}")
                elif action in ("preempted", "admin_kick"):
                    raise RuntimeError(f"websocket {action}")
        except ConnectionClosed as exc:
            code = getattr(getattr(exc, "rcvd", None), "code", None)
            if code in _TIER_CLOSE_CODES:
                raise TierError(f"websocket closed with code {code}") from None
            raise RuntimeError(f"websocket closed with code {code}") from None
        finally:
            try:
                await ws_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass


__all__ = ["DEFAULT_MONTHLY_CAP", "DEFAULT_POLL_S", "KEY_ENV", "QUERY", "FinlightProvider", "TierError",
           "api_key", "article_to_item", "effective_poll_s", "is_tier_error", "redact"]
