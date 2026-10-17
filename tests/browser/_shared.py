"""Shared page-load helper for tests/browser/*.py.

Not a test file itself (no test_ prefix, so pytest does not collect it).
Drives one page load in a fresh browser context and returns everything the
per-page contract checks need: console errors, failed/slow requests, overflow
at the given viewport, and the nav structure. Built for the 2026-10-17 ux-qa
sweep, which found and fixed real bugs this way first as an ad-hoc crawl, then
hardened the crawl into this module so the same checks run on every commit
instead of only once by hand.
"""
from __future__ import annotations

import dataclasses
import time
from typing import Any

from playwright.sync_api import Browser, Page


@dataclasses.dataclass
class PageLoad:
    path: str
    console_errors: list[str]
    failed_requests: list[dict[str, Any]]
    http_error_responses: list[dict[str, Any]]
    api_durations_ms: list[float]
    scroll_width: int
    client_width: int
    nav_links: list[dict[str, str | None]]
    page: Page

    @property
    def horizontal_scroll(self) -> bool:
        return self.scroll_width > self.client_width + 1


# Every page is visited as an anonymous, never-signed-in browser. /auth/me
# depends on require_user (services/auth/api.py), which 401s for that caller
# by design — and a browser logs "Failed to load resource: <status>" for ANY
# non-2xx fetch response, independent of how gracefully the page's own JS
# handles it (verified empirically during the sweep: wrapping the same fetch
# in a try/catch that reads response.ok cleanly still produced the console
# line). No amount of fixing pathiel.js removes this one; only a status-code
# change in services/auth (outside this agent's ownership — see the ux-qa
# final report's "for the integrator" section) does. Until then this is the
# one allowed line, matched exactly so any OTHER console error still fails
# the test.
_EXPECTED_CONSOLE_ERROR = "Failed to load resource: the server responded with a status of 401 (Unauthorized)"


def load_page(browser: Browser, base_url: str, path: str,
              width: int = 1280, height: int = 800,
              settle_ms: int = 2500) -> PageLoad:
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()

    console_errors: list[str] = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

    requests: list[dict[str, Any]] = []
    starts: dict[Any, float] = {}
    page.on("request", lambda r: starts.__setitem__(r, time.time()))

    def on_finished(r: Any) -> None:
        try:
            resp = r.response()
            requests.append({
                "url": r.url, "status": resp.status if resp else None,
                "ok": resp.ok if resp else False,
                "duration_ms": (time.time() - starts.get(r, time.time())) * 1000,
            })
        except Exception:
            pass

    page.on("requestfinished", on_finished)
    failed: list[dict[str, Any]] = []
    page.on("requestfailed", lambda r: failed.append({"url": r.url, "failure": r.failure}))

    # "load", not "networkidle": "/" opens a persistent SSE connection
    # (/api/feed/stream, 15s keepalives) that never finishes, so
    # "networkidle" times out on every single load of that page — not a bug,
    # that is what a live feed is for. Found during the sweep's first crawl.
    page.goto(base_url + path, wait_until="load", timeout=20000)
    page.wait_for_timeout(settle_ms)

    dims = page.evaluate(
        "() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})")
    nav_links = page.evaluate("""
        () => Array.from(document.querySelectorAll('nav.tabs-main a[data-nav]')).map(a => ({
            href: a.getAttribute('href'), nav: a.getAttribute('data-nav'),
            text: (a.textContent || '').trim(),
        }))
    """)

    api_requests = [r for r in requests if "/api/" in r["url"]]
    result = PageLoad(
        path=path,
        console_errors=[e for e in console_errors if e != _EXPECTED_CONSOLE_ERROR],
        failed_requests=failed,
        http_error_responses=[r for r in requests if r["status"] and r["status"] >= 400
                              and not r["url"].endswith("/auth/me")],
        api_durations_ms=[r["duration_ms"] for r in api_requests if r["duration_ms"] is not None],
        scroll_width=dims["sw"], client_width=dims["cw"],
        nav_links=nav_links,
        page=page,
    )
    return result
