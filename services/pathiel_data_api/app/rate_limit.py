"""Per-API-key token-bucket rate limiting as Starlette middleware.

Runs OUTSIDE the endpoint auth dependency (the ``Principal`` is resolved inside
the route, after this middleware has already decided to admit the request), so we
cannot depend on ``app.auth.Principal`` here. Instead we identify the caller by a
SHA-256 hash of the raw Bearer token — the same hashing scheme ``app.auth`` uses at
rest — computed inline to keep this module import-clean (importing ``app.auth`` would
pull in ``app.db``, which is a separate concern and may be absent in unit contexts).

Limits come from ``Settings``: refill = ``rate_limit_per_min / 60`` tokens/sec,
bucket capacity = ``rate_limit_burst``. Buckets live in an in-process dict keyed by
caller identity.

NOTE (production / multi-replica): this store is per-process. With more than one
replica each process keeps its own buckets, so the effective limit is N * configured.
For correct global limiting behind a load balancer, back the buckets with Redis
(e.g. an atomic INCR + PEXPIRE token-bucket Lua script keyed by identity) instead of
this dict. The identity key and the TokenBucket math below are storage-agnostic and
port directly.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from .config import Settings, get_settings
from .logging_setup import new_request_id

# Paths that never consume a token — liveness/readiness probes and docs must not be
# throttled or they will flap under load. Matched by exact path or path prefix.
DEFAULT_EXEMPT_PATHS: tuple[str, ...] = (
    "/health",
    "/healthz",
    "/livez",
    "/readyz",
    "/ping",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
)

# RFC 7807 type URIs. "about:blank" is the spec default; a distinct URI lets clients
# switch on the problem class without string-matching the title.
_RATE_LIMIT_TYPE = "about:blank"


class TokenBucket:
    """A single classic token bucket.

    ``tokens`` refills continuously at ``refill_per_sec`` up to ``capacity``. Each
    admitted request consumes one token. Time is monotonic seconds supplied by the
    caller so the bucket itself stays pure and unit-testable.
    """

    __slots__ = ("capacity", "refill_per_sec", "tokens", "updated")

    def __init__(self, capacity: float, refill_per_sec: float, now: float) -> None:
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self.tokens = float(capacity)  # start full so the first burst is allowed
        self.updated = now

    def _refill(self, now: float) -> None:
        elapsed = now - self.updated
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
            self.updated = now

    def consume(self, now: float, amount: float = 1.0) -> tuple[bool, float]:
        """Try to take ``amount`` tokens. Returns (allowed, tokens_remaining)."""
        self._refill(now)
        if self.tokens >= amount:
            self.tokens -= amount
            return True, self.tokens
        return False, self.tokens

    def retry_after_secs(self, amount: float = 1.0) -> float:
        """Seconds until ``amount`` tokens are available (0 if already available)."""
        needed = amount - self.tokens
        if needed <= 0:
            return 0.0
        if self.refill_per_sec <= 0:
            return 3600.0  # limiter effectively closed; ask client to back off hard
        return needed / self.refill_per_sec


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Token-bucket rate limiter, keyed per API key (falls back to client IP).

    Also owns per-request id lifecycle: it calls ``new_request_id()`` at the top of
    every request and stamps ``X-Request-Id`` onto the response. The id is also placed
    on ``request.state.request_id`` so downstream error handlers can recover it even if
    the ContextVar does not propagate across the middleware boundary.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Optional[Settings] = None,
        exempt_paths: Optional[tuple[str, ...]] = None,
    ) -> None:
        super().__init__(app)
        self._settings = settings  # None => resolve lazily via get_settings()
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()
        self._exempt = tuple(exempt_paths) if exempt_paths is not None else DEFAULT_EXEMPT_PATHS

    # -- helpers ---------------------------------------------------------------

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _is_exempt(self, path: str) -> bool:
        for p in self._exempt:
            if path == p or path.startswith(p + "/"):
                return True
        return False

    @staticmethod
    def _identity(request: Request) -> str:
        """Caller identity: hashed Bearer token if present, else client IP.

        Health/unauthenticated traffic with no token buckets by source IP so a single
        anonymous host cannot exhaust the service, while authenticated callers get an
        isolated bucket per key.
        """
        auth = request.headers.get("authorization", "")
        if auth[:7].lower() == "bearer ":
            token = auth[7:].strip()
            if token:
                return "key:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
        client = request.client
        ip = client.host if client is not None else "unknown"
        return "ip:" + ip

    def _problem_429(
        self,
        request: Request,
        rid: str,
        limit: int,
        remaining: float,
        retry_after_secs: float,
    ) -> JSONResponse:
        retry = max(1, int(math.ceil(retry_after_secs)))
        body = {
            "type": _RATE_LIMIT_TYPE,
            "title": "Too Many Requests",
            "status": 429,
            "detail": (
                "API rate limit exceeded for this key. "
                f"Retry after {retry} second(s)."
            ),
            "instance": request.url.path,
        }
        headers = {
            "Retry-After": str(retry),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(max(0, int(remaining))),
            "X-Request-Id": rid,
        }
        return JSONResponse(
            body,
            status_code=429,
            media_type="application/problem+json",
            headers=headers,
        )

    # -- ASGI dispatch ---------------------------------------------------------

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = new_request_id()
        # Persist on scope-backed state too: robust if the ContextVar copy does not
        # reach downstream handlers across the BaseHTTPMiddleware boundary.
        request.state.request_id = rid

        # Health/docs bypass the limiter entirely but still get a request id.
        if self._is_exempt(request.url.path):
            response = await call_next(request)
            response.headers["X-Request-Id"] = rid
            return response

        cfg = self._cfg()
        capacity = max(1, int(cfg.rate_limit_burst))       # bucket size = burst
        refill_per_sec = float(cfg.rate_limit_per_min) / 60.0
        identity = self._identity(request)
        now = time.monotonic()

        async with self._lock:
            bucket = self._buckets.get(identity)
            if bucket is None or bucket.capacity != capacity or bucket.refill_per_sec != refill_per_sec:
                # (Re)create if config changed at runtime so limits stay authoritative.
                bucket = TokenBucket(capacity, refill_per_sec, now)
                self._buckets[identity] = bucket
            allowed, remaining = bucket.consume(now)
            retry_after = 0.0 if allowed else bucket.retry_after_secs()

        if not allowed:
            return self._problem_429(request, rid, capacity, remaining, retry_after)

        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        response.headers["X-RateLimit-Limit"] = str(capacity)
        response.headers["X-RateLimit-Remaining"] = str(max(0, int(remaining)))
        return response
