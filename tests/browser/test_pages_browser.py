"""Per-page contract: every page ux-qa owns loads clean in a real browser.

For each of /, /activity, /analytics, /news, /trends, at both 1280x800 and
375x812:
  - the page loads (HTTP 200, no navigation error)
  - zero console errors, beyond the one documented, cross-team exception
    (see tests/browser/_shared.py's _EXPECTED_CONSOLE_ERROR)
  - zero failed requests, zero unexpected HTTP error responses
  - no horizontal scroll at 375px
  - the nav is present, identical across pages, and carries the Desks link
    second (right after Dashboard)
  - one page-specific element that answers "the one thing that matters" is
    visible above the fold

Deselected from the commit gate (-m browser): boots a server and drives
Chromium. Run with:
  .venv/bin/python -m pytest -m browser tests/browser/test_pages_browser.py -q
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

# Bare import, not `from tests.browser._shared import ...`: a dotted import
# forces Python to resolve `tests` as an importable package (pythonpath=["."]
# plus no tests/__init__.py makes it an implicit PEP 420 namespace package),
# which then collides with services/pathiel_data_api/tests — a REAL package
# (it has __init__.py) that also happens to be named "tests". Both conftest
# .py files end up registered under the identical plugin name "tests.conftest"
# and pytest refuses the second one outright. pytest's own rootdir-prepend
# collection already puts this file's directory on sys.path with no __init__.py
# anywhere above it, so the bare import below resolves _shared.py directly —
# no package name is ever created, so there is nothing to collide with.
from _shared import load_page  # noqa: E402

pytestmark = pytest.mark.browser

PAGES = ("/", "/activity", "/analytics", "/news", "/trends")
VIEWPORTS = {"desktop": (1280, 800), "mobile": (375, 812)}

# The one element each page leads with — the ADHD-design mandate this whole
# sweep worked to: one decision per screen, the call first. A page whose key
# element is not above the fold has failed that mandate regardless of
# whatever else is on it.
ABOVE_FOLD_ELEMENT = {
    "/": "#kpi-equity",             # net asset value — the hero number
    "/activity": "#session-strip",  # 6h scans/candidates/opened/closed/equity
    "/analytics": "#panel-funnel",  # the decision funnel, first panel
    "/news": "#news-strip",         # reads today / breaking / last read
    "/trends": "#hl-strip",         # the regime KPI row (or its honest empty state)
}

EXPECTED_NAV = [
    ("/", "Dashboard"), ("/desks", "Desks"), ("/activity", "Activity"),
    ("/news", "News"), ("/analytics", "Markets"), ("/trends", "Trends"),
]


@pytest.fixture(params=PAGES)
def page_path(request) -> str:
    return request.param


@pytest.fixture(params=list(VIEWPORTS))
def viewport_name(request) -> str:
    return request.param


def test_page_loads_clean(browser, demo_server, page_path, viewport_name):
    width, height = VIEWPORTS[viewport_name]
    result = load_page(browser, demo_server, page_path, width, height)
    try:
        assert not result.console_errors, (
            f"{page_path} @ {viewport_name}: unexpected console errors: {result.console_errors}")
        assert not result.failed_requests, (
            f"{page_path} @ {viewport_name}: failed requests: {result.failed_requests}")
        assert not result.http_error_responses, (
            f"{page_path} @ {viewport_name}: unexpected HTTP errors: {result.http_error_responses}")
        if width <= 375:
            assert not result.horizontal_scroll, (
                f"{page_path} @ {viewport_name}: document {result.scroll_width}px wide "
                f"against a {result.client_width}px viewport")
    finally:
        result.page.context.close()


def test_nav_present_and_consistent_with_desks(browser, demo_server, page_path):
    result = load_page(browser, demo_server, page_path)
    try:
        links = [(n["nav"], n["text"]) for n in result.nav_links]
        assert links == EXPECTED_NAV, f"{page_path}: nav = {links}"
    finally:
        result.page.context.close()


def test_key_element_visible_above_the_fold(browser, demo_server, page_path):
    width, height = VIEWPORTS["desktop"]
    result = load_page(browser, demo_server, page_path, width, height)
    try:
        sel = ABOVE_FOLD_ELEMENT[page_path]
        el = result.page.locator(sel)
        assert el.count() > 0, f"{page_path}: {sel} not found"
        assert el.first.is_visible(), f"{page_path}: {sel} exists but is not visible"
        box = el.first.bounding_box()
        assert box is not None and box["y"] < height, (
            f"{page_path}: {sel} sits below the {height}px fold (y={box and box['y']})")
    finally:
        result.page.context.close()
