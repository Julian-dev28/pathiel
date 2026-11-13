"""The pool's one way to the venue: a POST to the public mainnet /info
endpoint. Read only. No key, no /exchange, no order path.

Mainnet always, whatever network the process trades on: the pool grades
mainnet fills only (contracts.pool.NETWORK). The request bodies are built in
contracts/pool (fills_request, orders_request, extra_agents_request,
user_role_request).

A failed read raises VenueError. It never returns an empty reply, so "could
not ask" can never be read as "no fills" or "not approved".
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

from contracts import runtime

TIMEOUT_S = 20.0
RETRY_WAIT_S = 2.0


class VenueError(RuntimeError):
    """The venue could not be read."""


def info_url() -> str:
    return runtime.MAINNET_API_URL + "/info"


def post_info(payload: Dict[str, Any], *, timeout: float = TIMEOUT_S,
              opener: Optional[Callable[..., Any]] = None,
              sleep: Callable[[float], None] = time.sleep) -> Any:
    """POST `payload` to /info and return the decoded JSON reply. One retry
    after RETRY_WAIT_S on a network error, a 429 or a 5xx; then VenueError.
    `opener` replaces urllib.request.urlopen (tests)."""
    open_ = opener or urllib.request.urlopen
    req = urllib.request.Request(info_url(), data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    last = ""
    for attempt in (1, 2):
        try:
            with open_(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code != 429 and e.code < 500:
                break
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
        if attempt == 1:
            sleep(RETRY_WAIT_S)
    raise VenueError(f"could not read {payload.get('type')} from Hyperliquid: {last}")
