"""Playwright e2e for the Desk page's ("/") resume-after-hidden behavior —
oil_desk.js's startOilDesk()/loadAll(), against the real oil_desk.html via
tests/browser/oil_desk_fixture_app.py.

2026-10-24 QA finding, reproduced against the live server through the
claude-in-chrome extension: a background (not OS-focused) tab loaded "/"
and every section — The call, Wallets, Levels, Headlines — sat on its
"Loading..." spinner forever. Zero of loadCall/loadLine/loadWallets/
loadLevels/loadLog's requests fired; only GET /api/session and GET
/api/desks/active did (loadActiveDesks(), which runs before the hidden
check). document.hidden was true in that tab the whole time (never OS
focus) so loadAll()'s own guard ("a hidden tab must not spend the venue
budget") correctly skipped the fetches — that guard is deliberate and
stays. The gap was the resume path: oil_desk.html prerenders same-origin
links (its own <script type="speculationrules">), and a prerendered page's
activation is not guaranteed to fire 'visibilitychange' (a documented
Speculation-Rules gotcha — the event the spec guarantees is
'prerenderingchange'). startOilDesk() only listened for 'visibilitychange',
so a page that started life prerendered-and-hidden could end up visible to
the reader with nothing ever having loaded, and nothing left to trigger a
retry. The fix adds a 'prerenderingchange' listener alongside the existing
one, both resuming the same way.

Chromium only, boots its own server on this agent's assigned port band
(8710-8719, per SWARM_RULES section 1/2). Deselected from the default
commit gate (too slow for <2s); run with:

    .venv/bin/python -m pytest -q -m browser \
        tests/browser/test_oil_desk_visibility_browser.py
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

# Overrides document.hidden/visibilityState/prerendering to a controllable
# simulated value BEFORE any of the page's own scripts run (Playwright's
# add_init_script runs ahead of every navigation's own <script> tags) — the
# same technique tests/browser/test_desks_browser.py's
# test_polls_never_stack_and_stop_in_a_hidden_tab uses for pollEvery, here
# applied to the real page instead of an ad-hoc inline one.
_SIM_VISIBILITY_INIT_SCRIPT = """
window.__simHidden = true;
Object.defineProperty(document, 'hidden', { configurable: true, get: () => window.__simHidden });
Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => window.__simHidden ? 'hidden' : 'visible' });
"""


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
def server():
    port = _free_port_in_band()
    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.oil_desk_fixture_app", str(port), "match"],
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
        pytest.fail(f"oil desk fixture server did not come up on {url}")
    yield url
    proc.terminate()
    proc.wait(timeout=20)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def test_a_page_that_loads_hidden_fetches_nothing_until_it_resumes(browser, server):
    """Pins the OTHER half of the guard: a hidden page must not spend the
    venue budget. Without this, a fix for the resume path alone could
    regress into fetching unconditionally."""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.add_init_script(_SIM_VISIBILITY_INIT_SCRIPT)
    api_calls = []
    # /api/desks/active is loadActiveDesks() (which desk(s) to render at
    # all) — a cheap, non-venue config read that runs before the hidden
    # check by design, same as every page's own GET /api/session. The
    # budget-sensitive reads are loadAll()'s own five: the call, the line,
    # wallets, levels and the log — those are what a hidden tab must skip.
    page.on("request", lambda r: api_calls.append(r.url)
            if ("/api/desks/" in r.url and not r.url.endswith("/api/desks/active")) or "/api/accounts" in r.url
            else None)
    try:
        page.goto(server + "/", wait_until="load")
        page.wait_for_timeout(600)
        assert api_calls == [], f"a hidden page must not fetch desk/account data: {api_calls}"
        assert page.locator("#call-loading").is_visible()
        assert page.locator("#call-card").is_hidden()
    finally:
        page.context.close()


def test_prerendering_change_resumes_a_page_that_loaded_hidden(browser, server):
    """The activation path 'visibilitychange' cannot be relied on for."""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.add_init_script(_SIM_VISIBILITY_INIT_SCRIPT)
    page.goto(server + "/", wait_until="load")
    page.wait_for_timeout(600)
    page.locator("#call-card").wait_for(state="hidden")   # still stuck, confirms the setup

    page.evaluate("window.__simHidden = false; document.dispatchEvent(new Event('prerenderingchange'));")

    page.locator("#call-card").wait_for(state="visible", timeout=10000)
    page.locator("#wallets-list .card").first.wait_for(state="visible", timeout=10000)
    page.context.close()


def test_visibilitychange_still_resumes_a_page_that_loaded_hidden(browser, server):
    """The pre-existing path — a genuine tab-switch back — must keep working."""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.add_init_script(_SIM_VISIBILITY_INIT_SCRIPT)
    page.goto(server + "/", wait_until="load")
    page.wait_for_timeout(600)
    page.locator("#call-card").wait_for(state="hidden")

    page.evaluate("window.__simHidden = false; document.dispatchEvent(new Event('visibilitychange'));")

    page.locator("#call-card").wait_for(state="visible", timeout=10000)
    page.context.close()
