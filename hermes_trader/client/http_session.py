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

logger = logging.getLogger(__name__)


def _set_session_timeout(client, timeout_s: float = 10.0):
    """Give an SDK client's requests.Session a DEFAULT read timeout. The SDK
    ships timeout=None (audit 2026-07-10: one hung read froze the DSL monitor
    for 15 minutes on 06-30 — 'read timeout=None' in the traceback). Best-effort:
    if the SDK's internals change shape, the client still works, just unwrapped."""
    try:
        sess = getattr(client, "session", None)
        if sess is None:
            return client
        _orig = sess.request

        def _req(method, url, **kw):
            kw.setdefault("timeout", timeout_s)
            return _orig(method, url, **kw)

        sess.request = _req
    except Exception as exc:
        # This monkeypatch exists BECAUSE of the 15-minute-hang incident
        # named above. If it silently fails to apply, that exact bug is back
        # with zero trace — the client "still works," just unprotected.
        logger.warning(f"[http_session] could not install read timeout, "
                       f"client is UNPROTECTED against a hung read: {exc}")
    return client
