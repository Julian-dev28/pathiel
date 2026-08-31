"""Typed Python SDK for the Pathia Data API.

A thin, dependency-light client over the public HTTP contract:

    from pathia_client import PathiaDataClient

    hc = PathiaDataClient("https://data.pathia.example", api_key="sk_live_...")
    bars = hc.ohlc("BTC", interval="1d", limit=100)   # -> list[dict]
    mom  = hc.momentum("BTC", lookback=7)             # -> dict
    hc.close()

Design notes
------------
* Bearer auth on every call (``Authorization: Bearer <api_key>``).
* Returns the *unwrapped* payload — the server wraps successful bodies in an
  ``{"data": ...}`` envelope; the SDK hands you ``data`` directly.
* Non-2xx responses raise :class:`PathiaAPIError`, populated from the RFC7807
  ``application/problem+json`` body (``title`` / ``detail`` / ``status`` / ...)
  when the server sends one, so callers get the machine-readable problem, not a
  bare status code. 429s expose ``retry_after``.
* Built on ``httpx`` (already a project dependency). Usable as a context manager
  so the underlying connection pool is closed deterministically.

For a fully-generated client that tracks every endpoint automatically, see
``sdk/README.md`` (``openapi-python-client`` against ``app.openapi()``). This
hand-written client stays intentionally small and covers the three data
endpoints plus health.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

__all__ = ["PathiaDataClient", "PathiaAPIError"]


class PathiaAPIError(Exception):
    """Raised on any non-2xx response.

    Attributes
    ----------
    status_code : int
        The HTTP status returned.
    title : str
        RFC7807 ``title`` (short, human-readable summary), or a fallback.
    detail : str | None
        RFC7807 ``detail`` (explanation specific to this occurrence), if any.
    problem : dict
        The full parsed problem+json body (empty dict if the body wasn't JSON).
    retry_after : float | None
        Parsed ``Retry-After`` seconds on a 429, else ``None``.
    """

    def __init__(
        self,
        status_code: int,
        title: str,
        detail: Optional[str] = None,
        problem: Optional[dict[str, Any]] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.problem = problem or {}
        self.retry_after = retry_after
        msg = f"HTTP {status_code}: {title}"
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)


class PathiaDataClient:
    """Client for the Pathia Data API.

    Parameters
    ----------
    base_url : str
        Root URL of the API, e.g. ``"https://data.pathia.example"``. A trailing
        slash is fine; it's normalized away.
    api_key : str
        Bearer token issued for your tenant.
    timeout : float
        Per-request timeout in seconds (default 10).
    session : httpx.Client | None
        Inject your own client (retries, proxies, custom transport for tests).
        If omitted, one is created and owned by this instance.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 10.0,
        session: Optional[httpx.Client] = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not api_key:
            raise ValueError("api_key is required")
        self.base_url = base_url.rstrip("/")
        self._owns_client = session is None
        self._client = session or httpx.Client(timeout=timeout)
        self._auth = {"Authorization": f"Bearer {api_key}"}

    # -- context manager --------------------------------------------------- #
    def __enter__(self) -> "PathiaDataClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client if this instance owns it."""
        if self._owns_client:
            self._client.close()

    # -- internals --------------------------------------------------------- #
    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> Optional[float]:
        raw = resp.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None  # HTTP-date form; callers can read the header themselves

    def _request(self, path: str, *, auth: bool = True, params: Optional[dict] = None) -> Any:
        headers = dict(self._auth) if auth else {}
        headers["Accept"] = "application/json, application/problem+json"
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self._client.get(self.base_url + path, params=clean, headers=headers)

        if resp.is_success:
            return resp.json()

        # Error path — parse the RFC7807 problem document if there is one.
        problem: dict[str, Any] = {}
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                problem = parsed
        except ValueError:
            pass
        title = str(problem.get("title") or resp.reason_phrase or "request failed")
        detail = problem.get("detail")
        raise PathiaAPIError(
            status_code=resp.status_code,
            title=title,
            detail=str(detail) if detail is not None else None,
            problem=problem,
            retry_after=self._parse_retry_after(resp),
        )

    @staticmethod
    def _unwrap(body: Any) -> Any:
        """Return the ``data`` envelope payload, or the body itself if unwrapped."""
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    # -- endpoints --------------------------------------------------------- #
    def health(self) -> dict[str, Any]:
        """Liveness probe. No auth. Returns the raw health document."""
        body = self._request("/health", auth=False)
        return body if isinstance(body, dict) else {"status": body}

    def ohlc(self, ticker: str, interval: str = "1d", limit: int = 100) -> list[dict]:
        """OHLCV bars for ``ticker`` at ``interval``, oldest-first.

        Each bar: ``{"t": int_ms, "o", "h", "l", "c", "v": float}``.
        Backed by our own Hyperliquid / xyz-token candles.
        """
        body = self._request(
            f"/api/stock/{ticker}/ohlc",
            params={"interval": interval, "limit": limit},
        )
        return self._unwrap(body)

    def net_flow(self, ticker: str, date: Optional[str] = None) -> dict[str, Any]:
        """Options net premium flow for ``ticker`` on ``date`` (YYYY-MM-DD).

        LICENSED-DATA ADAPTER SLOT: this raises :class:`PathiaAPIError` with
        ``status_code == 501`` unless the operator has wired a licensed
        options-flow upstream. We never fabricate flow we don't license.
        """
        body = self._request(
            f"/api/stock/{ticker}/net-flow",
            params={"date": date},
        )
        return self._unwrap(body)

    def momentum(self, ticker: str, lookback: int = 7) -> dict[str, Any]:
        """First-party momentum signal: trailing ``lookback``-bar return of
        ``ticker``, computed from our own candles.

        Returns ``{"coin", "lookback", "trailing_return"}`` (decimal fraction:
        ``0.05`` == +5%).
        """
        body = self._request(
            f"/api/stock/{ticker}/momentum",
            params={"lookback": lookback},
        )
        return self._unwrap(body)


if __name__ == "__main__":  # pragma: no cover - tiny smoke CLI
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Pathia Data API smoke client")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--api-key", default="demo-token")
    ap.add_argument("--ticker", default="BTC")
    args = ap.parse_args()

    with PathiaDataClient(args.base_url, args.api_key) as hc:
        print("health:", json.dumps(hc.health()))
        print("ohlc[0]:", json.dumps(hc.ohlc(args.ticker, limit=3)[:1]))
        print("momentum:", json.dumps(hc.momentum(args.ticker, lookback=7)))
        try:
            print("net_flow:", json.dumps(hc.net_flow(args.ticker)))
        except PathiaAPIError as e:
            print(f"net_flow (expected {e.status_code}): {e}")
