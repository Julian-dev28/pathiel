"""Shared HTTP-session hardening for Hyperliquid SDK clients.

Split out of `exchange.py` (2026-08-30) so `hl_client.py` can use it without
importing `exchange.py` — the two previously formed an import cycle broken
only by a deferred import inside `hl_client.init_info()`. This function has
no dependency on anything else in either module: it's a pure duck-typed
wrapper over `client.session.request`, so it belongs in its own leaf module
regardless of the cycle.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

# A dead pooled socket shows up as one of these; anything else (a 4xx/5xx the
# SDK raises, a JSON error) is a real answer and must not be retried blind.
_RETRY_ON = (requests.exceptions.Timeout, requests.exceptions.ConnectionError)

# Passed to the SDK constructors themselves (Info/Exchange(timeout=...)):
# their __init__ already makes HTTP calls (meta, spot meta, every perp dex)
# BEFORE _set_session_timeout can wrap the session, and with the SDK default
# timeout=None one of those can hang forever. 2026-10-31: during a
# Hyperliquid 502 storm scripts/thesis_due.py hung 23 minutes in Info().
SDK_TIMEOUT_S = 10.0


def _set_session_timeout(client, timeout_s: float = 10.0, connect_s: float = 5.0):
    """Give an SDK client's requests.Session a DEFAULT (connect, read) timeout,
    and a fresh socket after a timeout. The SDK ships timeout=None (audit
    2026-07-10: one hung read froze the DSL monitor for 15 minutes on 06-30 —
    'read timeout=None' in the traceback). Best-effort: if the SDK's internals
    change shape, the client still works, just unwrapped.

    The retry-on-fresh-socket part is from 2026-10-23: after a network outage
    the long-lived MCP server took ~15 minutes per exchange call while curl
    from the same machine answered instantly. requests keeps pooled keep-alive
    connections; ones that died during the outage hang every later call until
    the read timeout fires, and the pool hands them out again. Closing the
    session's adapters drops the pool, so the one retry runs on a new socket.
    """
    try:
        sess = getattr(client, "session", None)
        if sess is None:
            return client
        _orig = sess.request

        def _req(method, url, **kw):
            # NOT setdefault: hyperliquid.api.API.post passes timeout=self.timeout
            # explicitly, and that is None, so the key is always present and a
            # setdefault never fired. The guard did nothing from 2026-07-10 until
            # the copycat dry run hit "read timeout=None" on 2026-10-02.
            if kw.get("timeout") is None:
                kw["timeout"] = (connect_s, timeout_s)
            try:
                return _orig(method, url, **kw)
            except _RETRY_ON as exc:
                logger.warning(f"[http_session] {type(exc).__name__} on {method} {url}; "
                               f"dropping pooled connections and retrying once")
                try:
                    sess.close()          # adapters re-create their pools on the next request
                except Exception:
                    pass
                return _orig(method, url, **kw)

        sess.request = _req
    except Exception as exc:
        # This monkeypatch exists BECAUSE of the 15-minute-hang incident
        # named above. If it silently fails to apply, that exact bug is back
        # with zero trace — the client "still works," just unprotected.
        logger.warning(f"[http_session] could not install read timeout, "
                       f"client is UNPROTECTED against a hung read: {exc}")
    return client
