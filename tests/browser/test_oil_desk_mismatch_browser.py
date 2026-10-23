"""Playwright e2e for the Desk page's ("/") thesis-vs-position mismatch
banner — oil_desk.js's renderPositionMismatch()/callPositionMismatch(),
wired to the real oil_desk.html against tests/browser/oil_desk_fixture_app.py.

2026-10-24 QA finding on the running server: the Desk page's call card
read "Still long Brent 109.24 at 5x. Stop 108.05..." while the Wallets
card (same page, same refresh) showed the real open position at 111.57,
10x, stop 110.9 — same side, same instrument, three numbers disagreeing.
A trader skimming the call text alone would size, and manage, a trade off
numbers that are not the order actually on the book. This suite pins the
fix: the Desk page now cross-checks the two sections it already fetches
(no extra request) and says so in place, right under the call text.

Chromium only, boots its own server on this agent's assigned port band
(8710-8719, per SWARM_RULES section 1/2). Deselected from the default
commit gate (too slow for <2s); run with:

    .venv/bin/python -m pytest -q -m browser \
        tests/browser/test_oil_desk_mismatch_browser.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
PORT_BAND = range(8710, 8720)


def _free_port_in_band() -> int:
    for port in PORT_BAND:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port in 8710-8719 — another agent may be holding them all")


@pytest.fixture
def server(request):
    scenario = request.param
    port = _free_port_in_band()
    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.oil_desk_fixture_app", str(port), scenario],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            urllib.request.urlopen(url + "/api/session", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.terminate()
        pytest.fail(f"oil desk fixture server ({scenario}) did not come up on {url}")
    yield url
    proc.terminate()
    proc.wait(timeout=20)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _load_desk(browser, url):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    issues: list[str] = []
    page.on("pageerror", lambda e: issues.append(f"pageerror: {e}"))
    page.on("console", lambda m: issues.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.goto(url + "/", wait_until="load")
    page.locator("#oil-desk-root").wait_for(state="visible", timeout=10000)
    page.locator("#call-card").wait_for(state="visible", timeout=10000)
    page.locator("#wallets-list .card").first.wait_for(state="visible", timeout=10000)
    return page, issues


@pytest.mark.parametrize("server", ["mismatch"], indirect=True)
def test_mismatch_banner_shows_both_sets_of_numbers(browser, server):
    page, issues = _load_desk(browser, server)
    try:
        banner = page.locator("#call-position-mismatch")
        banner.wait_for(state="visible", timeout=10000)
        text = banner.inner_text()
        # The thesis's own numbers (what the call text above already says)...
        assert "109.24" in text and "5x" in text and "108.05" in text
        # ...against the venue's own position (what the Wallets card below
        # already shows) — the whole point is both are visible at once.
        assert "111.57" in text and "10x" in text and "110.9" in text
        assert not issues, issues
    finally:
        page.context.close()


@pytest.mark.parametrize("server", ["match"], indirect=True)
def test_no_banner_when_position_matches_the_call(browser, server):
    page, issues = _load_desk(browser, server)
    try:
        assert page.locator("#call-position-mismatch").is_hidden()
        assert not issues, issues
    finally:
        page.context.close()


@pytest.mark.parametrize("server", ["flat"], indirect=True)
def test_no_banner_with_no_open_position(browser, server):
    page, issues = _load_desk(browser, server)
    try:
        assert page.get_by_text("No open positions").count() > 0
        assert page.locator("#call-position-mismatch").is_hidden()
        assert not issues, issues
    finally:
        page.context.close()
