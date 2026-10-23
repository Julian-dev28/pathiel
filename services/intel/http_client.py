"""The one place services/intel touches the network.

Every fetcher in sources/*.py goes through `fetch_json` or `fetch_text`.
Contract (SWARM_RULES + the intel brief, both say this explicitly):

    timeout, one retry, never raises to the caller.

A failure returns `(None, "<short reason>")`. The caller is responsible for
appending that reason to `IntelPacket.sources_failed` — this module has no
idea which desk or which packet is being built, so it cannot do that itself.
Never silently return an empty success; a dead source is always visible.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

DEFAULT_UA = "pathiel-intel/1.0 (+https://github.com/; read-only research fetch)"
_DEFAULT_TIMEOUT = httpx.Timeout(connect=4.0, read=6.0, write=4.0, pool=4.0)
# Retry once on these — a second attempt against a dead DNS entry or a TLS
# handshake failure is not going to behave differently, but a timeout, a
# dropped connection, or a transient 5xx/429 plausibly will.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RETRY_BACKOFF_S = 1.0


def _should_retry_status(status: int) -> bool:
    return status in _RETRYABLE_STATUS


def _do_request(method: str, url: str, *, params: Optional[Dict[str, Any]],
                 headers: Optional[Dict[str, str]], timeout: httpx.Timeout,
                 json_body: Optional[Dict[str, Any]] = None) -> httpx.Response:
    hdrs = {"User-Agent": DEFAULT_UA}
    if headers:
        hdrs.update(headers)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.request(method, url, params=params, headers=hdrs, json=json_body)
    return resp


def _fetch(url: str, *, method: str = "GET", params: Optional[Dict[str, Any]] = None,
           json_body: Optional[Dict[str, Any]] = None,
           headers: Optional[Dict[str, str]] = None,
           timeout_s: Optional[float] = None,
           retries: int = 1) -> Tuple[Optional[httpx.Response], Optional[str]]:
    timeout = _DEFAULT_TIMEOUT if timeout_s is None else httpx.Timeout(timeout_s)
    attempts = retries + 1
    last_err: Optional[str] = None
    for attempt in range(attempts):
        try:
            resp = _do_request(method, url, params=params, headers=headers, timeout=timeout,
                                json_body=json_body)
        except httpx.TimeoutException:
            last_err = "timeout"
        except httpx.HTTPError as exc:
            last_err = f"{type(exc).__name__}"
        except Exception as exc:  # pragma: no cover - belt and suspenders, never raise
            last_err = f"unexpected:{type(exc).__name__}"
        else:
            if resp.status_code < 400:
                return resp, None
            last_err = f"http_{resp.status_code}"
            if not _should_retry_status(resp.status_code):
                return None, last_err
        if attempt < attempts - 1:
            time.sleep(_RETRY_BACKOFF_S)
    return None, last_err or "unknown_error"


def fetch_json(url: str, *, params: Optional[Dict[str, Any]] = None,
                headers: Optional[Dict[str, str]] = None,
                timeout_s: Optional[float] = None,
                retries: int = 1) -> Tuple[Optional[Any], Optional[str]]:
    """GET `url`, parse JSON. Returns (data, None) or (None, "<reason>"). Never raises."""
    resp, err = _fetch(url, params=params, headers=headers, timeout_s=timeout_s, retries=retries)
    if resp is None:
        logger.warning(f"[intel.http] GET {url} failed: {err}")
        return None, err
    try:
        return resp.json(), None
    except Exception as exc:
        logger.warning(f"[intel.http] GET {url} returned non-JSON: {exc}")
        return None, "bad_json"


def fetch_text(url: str, *, params: Optional[Dict[str, Any]] = None,
                headers: Optional[Dict[str, str]] = None,
                timeout_s: Optional[float] = None,
                retries: int = 1) -> Tuple[Optional[str], Optional[str]]:
    """GET `url`, return the raw body text. Returns (text, None) or (None, "<reason>")."""
    resp, err = _fetch(url, params=params, headers=headers, timeout_s=timeout_s, retries=retries)
    if resp is None:
        logger.warning(f"[intel.http] GET {url} failed: {err}")
        return None, err
    return resp.text, None


def fetch_json_post(url: str, json_body: Dict[str, Any], *,
                     headers: Optional[Dict[str, str]] = None,
                     timeout_s: Optional[float] = None,
                     retries: int = 1) -> Tuple[Optional[Any], Optional[str]]:
    """POST `json_body` as JSON, parse the JSON response. Same contract as
    `fetch_json`: never raises, returns (data, None) or (None, "<reason>").
    Added for Hyperliquid's /info endpoint (services/intel/sources/venue.py),
    which is POST-only — every other source in this service is a plain GET,
    so this is the one exception to "fetch_json/fetch_text are the only two
    network entry points", not a second code path duplicating the retry/
    timeout/never-raise contract above."""
    resp, err = _fetch(url, method="POST", json_body=json_body, headers=headers,
                        timeout_s=timeout_s, retries=retries)
    if resp is None:
        logger.warning(f"[intel.http] POST {url} failed: {err}")
        return None, err
    try:
        return resp.json(), None
    except Exception as exc:
        logger.warning(f"[intel.http] POST {url} returned non-JSON: {exc}")
        return None, "bad_json"
