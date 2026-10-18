"""Playwright e2e for the Desks pages (/desks, /desks/{id}), against the
fixture-backed app in tests/browser/desks_fixture_app.py — the real contract
models, oil's real fixtures, three synthesized desks covering empty/stale/
short-side, and a dedicated err500 path. Chromium only, boots its own server
on this agent's assigned port band (8710-8719, per SWARM_RULES section 1/2).

Deselected from the default commit gate (too slow for <2s); run with:

    PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m pytest -q -m browser \
        tests/browser/test_desks_browser.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import Page, sync_playwright  # noqa: E402

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
PORT_BAND = range(8710, 8720)   # this agent's assigned band — never a random OS-ephemeral port
DESKTOP_VP = {"width": 1280, "height": 800}
MOBILE_VP = {"width": 375, "height": 812}
FOLD_SELECTORS = ["#call-one-liner", "#call-side-badge", "#call-leverage", "#call-invalidation"]


def _free_port_in_band() -> int:
    for port in PORT_BAND:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port in 8710-8719 — another agent may be holding them all")


@pytest.fixture(scope="module")
def server():
    port = _free_port_in_band()
    env = dict(os.environ)
    env["PATHIEL_STATE_DIR"] = tempfile.mkdtemp(prefix="desks-e2e-state-")
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import uvicorn; uvicorn.run('tests.browser.desks_fixture_app:create_app', "
         f"factory=True, host='127.0.0.1', port={port}, log_level='warning')"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            urllib.request.urlopen(url + "/api/desks", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.terminate()
        pytest.fail("desks fixture server did not come up on " + url)
    yield url
    proc.terminate()
    proc.wait(timeout=20)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_page(browser, viewport=None) -> tuple[Page, list[str]]:
    """A fresh page plus a live list of every console error / uncaught
    exception it raises, for the "zero console errors" requirement."""
    page = browser.new_page(viewport=viewport) if viewport else browser.new_page()
    issues: list[str] = []
    page.on("pageerror", lambda e: issues.append(f"pageerror: {e}"))
    page.on("console", lambda m: issues.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    return page, issues


def assert_above_fold(page: Page, viewport_height: int, selectors=FOLD_SELECTORS) -> None:
    for sel in selectors:
        loc = page.locator(sel)
        loc.wait_for(state="visible", timeout=5000)
        box = loc.bounding_box()
        assert box is not None, f"{sel} has no bounding box"
        bottom = box["y"] + box["height"]
        assert bottom <= viewport_height, (
            f"{sel} bottom={bottom:.0f} exceeds viewport height {viewport_height} — needs a scroll")


# ── grid ──────────────────────────────────────────────────────────────────────

def test_grid_renders_all_cards(server, browser):
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks", wait_until="networkidle")
    page.wait_for_selector("#desks-grid:not([hidden])", timeout=5000)
    cards = page.locator(".desk-card")
    assert cards.count() == 4
    ids = {page.locator(".desk-card").nth(i).get_attribute("data-desk-id") for i in range(4)}
    assert ids == {"oil", "btc", "ai", "gas"}
    # oil's card must show its real fixture call, not a placeholder
    oil_card = page.locator('.desk-card[data-desk-id="oil"]')
    assert "Long WTI at 5x" in oil_card.locator(".dc-call").inner_text()
    assert not issues, issues
    page.close()


def test_grid_empty_desk_card_says_no_call_yet(server, browser):
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks", wait_until="networkidle")
    ai_card = page.locator('.desk-card[data-desk-id="ai"]')
    assert "No call yet" in ai_card.locator(".dc-call").inner_text()
    assert not issues, issues
    page.close()


# ── the fold ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("viewport", [DESKTOP_VP, MOBILE_VP], ids=["1280x800", "375x812"])
def test_oil_call_is_above_the_fold(server, browser, viewport):
    page, issues = open_page(browser, viewport)
    page.goto(server + "/desks/oil", wait_until="networkidle")
    page.wait_for_selector("#call-card:not([hidden])", timeout=5000)
    assert_above_fold(page, viewport["height"])
    assert not issues, issues
    page.close()


@pytest.mark.parametrize("viewport", [DESKTOP_VP, MOBILE_VP], ids=["1280x800", "375x812"])
def test_gas_call_is_above_the_fold_even_while_stale(server, browser, viewport):
    """gas carries a visible stale banner above the call card — the extra
    chrome most likely to push content past the fold, so this is the sharper
    version of the check above."""
    page, _ = open_page(browser, viewport)
    page.goto(server + "/desks/gas", wait_until="networkidle")
    page.wait_for_selector("#call-card:not([hidden])", timeout=5000)
    assert page.locator("#stale-banner").is_visible()
    assert_above_fold(page, viewport["height"])
    page.close()


def test_no_horizontal_scroll_at_375(server, browser):
    """Checks scroll width only. /desks/err500 is deliberately included —
    the error state must not introduce its own horizontal scroll either —
    but it legitimately logs a console error for the 500 itself, which is a
    separate concern already covered by test_api_500_shows_the_error_state
    and test_zero_console_errors_across_every_state (neither of which visits
    err500 while asserting a clean console)."""
    page, _ = open_page(browser, MOBILE_VP)
    for path in ("/desks", "/desks/oil", "/desks/ai", "/desks/gas", "/desks/err500"):
        page.goto(server + path, wait_until="networkidle")
        page.wait_for_timeout(150)
        scroll_w = page.evaluate("document.documentElement.scrollWidth")
        inner_w = page.evaluate("window.innerWidth")
        assert scroll_w <= inner_w, f"{path}: scrollWidth={scroll_w} > innerWidth={inner_w}"
    page.close()


# ── leverage switch ──────────────────────────────────────────────────────────

def test_leverage_switch_shows_fixture_numbers_long(server, browser):
    """oil's long ladder, straight from contracts/strategies/fixtures/oil_analytics.json."""
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/oil", wait_until="networkidle")
    page.wait_for_selector("#lev-card:not([hidden])", timeout=5000)

    page.check("#lev-opt-5")
    assert page.inner_text("#lev-liq-px") == "81.510"
    assert page.inner_text("#lev-p-liq") == "3.2%"
    assert page.inner_text("#lev-ev") == "+5.8%"
    assert "Hold" in page.inner_text("#lev-verdict")

    page.check("#lev-opt-10")
    assert page.inner_text("#lev-liq-px") == "91.700"
    assert page.inner_text("#lev-p-liq") == "28.4%"
    assert "Dip only" in page.inner_text("#lev-verdict")

    page.check("#lev-opt-20")
    assert page.inner_text("#lev-liq-px") == "96.790"
    assert page.inner_text("#lev-p-liq") == "64.7%"
    assert page.inner_text("#lev-ev") == "-14.9%"
    assert "Avoid" in page.inner_text("#lev-verdict")
    assert not issues, issues
    page.close()


def test_leverage_switch_shows_fixture_numbers_short(server, browser):
    """gas is the only desk with a short call — proves the ladder's side-
    matching filter picks short rungs, not just defaulting to long."""
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/gas", wait_until="networkidle")
    page.wait_for_selector("#lev-card:not([hidden])", timeout=5000)

    page.check("#lev-opt-5")
    assert page.inner_text("#lev-liq-px") == "3.776"
    assert page.inner_text("#lev-liq-dist") == "+18.0%"   # price must RISE to liquidate a short
    page.check("#lev-opt-20")
    assert page.inner_text("#lev-liq-px") == "3.344"
    assert "Avoid" in page.inner_text("#lev-verdict")
    assert not issues, issues
    page.close()


def test_leverage_switch_is_keyboard_operable(server, browser):
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/oil", wait_until="networkidle")
    page.wait_for_selector("#lev-card:not([hidden])", timeout=5000)
    assert page.is_checked("#lev-opt-5")

    page.focus("#lev-opt-5")
    page.keyboard.press("ArrowRight")
    assert page.is_checked("#lev-opt-10")
    assert page.inner_text("#lev-liq-px") == "91.700"   # repainted with no click at all

    page.keyboard.press("ArrowRight")
    assert page.is_checked("#lev-opt-20")
    assert page.inner_text("#lev-liq-px") == "96.790"

    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    assert page.is_checked("#lev-opt-5")
    assert page.inner_text("#lev-liq-px") == "81.510"
    assert not issues, issues
    page.close()


def test_leverage_switch_makes_no_network_call(server, browser):
    """The measurable outcome: a leverage switch updates every figure with no
    network call (and therefore comfortably under the 50ms budget — a
    pure DOM write against data already in memory has no realistic way to
    miss a wall-clock budget measured in tens of milliseconds)."""
    page, _ = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/oil", wait_until="networkidle")
    page.wait_for_selector("#lev-card:not([hidden])", timeout=5000)

    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))
    page.check("#lev-opt-10")
    page.wait_for_timeout(100)
    page.check("#lev-opt-20")
    page.wait_for_timeout(100)
    assert page.inner_text("#lev-liq-px") == "96.790"
    assert requests == [], f"leverage switch triggered network requests: {requests}"
    page.close()


def test_leverage_switch_works_before_any_thesis_exists(server, browser):
    """ai has no call yet — the switch must still work as a practice tool."""
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/ai", wait_until="networkidle")
    page.wait_for_selector("#call-empty:not([hidden])", timeout=5000)
    page.wait_for_selector("#lev-card:not([hidden])", timeout=5000)
    page.check("#lev-opt-10")
    assert page.inner_text("#lev-liq-px") != "—"
    assert not issues, issues
    page.close()


# ── scenarios / catalysts / bull-bear ────────────────────────────────────────

def test_scenarios_render_with_bar_and_list(server, browser):
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/oil", wait_until="networkidle")
    page.wait_for_selector("#scenario-list .scenario-row", timeout=5000)
    assert page.locator("#scenario-bar .scenario-seg").count() == 4
    assert page.locator("#scenario-list .scenario-row").count() == 4
    assert "Hormuz shut" in page.inner_text("#scenario-list")
    assert not issues, issues
    page.close()


def test_catalyst_chips_show_countdown_and_both_times_on_hover(server, browser):
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/oil", wait_until="networkidle")
    page.wait_for_selector("#catalyst-chips .catalyst-chip", timeout=5000)
    chip = page.locator("#catalyst-chips .catalyst-chip").first
    assert "in " in chip.locator(".catalyst-when").inner_text()
    title = chip.get_attribute("title")
    assert title and "local" in title and "UTC" in title
    assert not issues, issues
    page.close()


def test_bull_bear_points_link_to_sources(server, browser):
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/oil", wait_until="networkidle")
    page.wait_for_selector("#bull-col .bb-point", timeout=5000)
    assert page.locator("#bull-col .bb-sources a").count() >= 1
    # the bear point cites h3+p1, neither of which is in thesis.sources —
    # only intel — proving the source lookup falls back to intel correctly.
    assert page.locator("#bear-col .bb-sources a").count() >= 1
    assert not issues, issues
    page.close()


# ── make your own call ───────────────────────────────────────────────────────

def test_bad_input_shows_inline_errors_and_never_posts(server, browser):
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/oil", wait_until="networkidle")
    page.wait_for_selector("#user-thesis-form", timeout=5000)

    posted = []
    page.on("request", lambda r: posted.append(r.url) if r.method == "POST" and "/theses" in r.url else None)

    # one_liner empty, confidence left at default (valid), invalidation empty
    page.click("#ut-submit")
    assert page.inner_text("#ut-one-liner-error").strip() != ""
    assert page.inner_text("#ut-invalidation-error").strip() != ""
    assert page.locator("#user-thesis-form").is_visible()
    assert posted == [], "a client-side validation failure must never reach the server"

    # fix one_liner but push confidence out of range via direct value set, add a bad scenario split
    page.fill("#ut-one-liner", "Test call")
    page.fill("#ut-invalidation", "test")
    page.click("#ut-add-scenario")
    row = page.locator("#ut-scenarios .scenario-edit-row").first
    row.locator(".se-name").fill("only one")
    row.locator(".se-prob").fill("0.5")
    row.locator(".se-lo").fill("0")
    row.locator(".se-hi").fill("0.1")
    row.locator(".se-desc").fill("desc")
    page.click("#ut-submit")
    assert "sum to" in page.inner_text("#ut-scenarios-error")
    assert posted == [], "scenario-sum validation failure must also be caught client-side"
    assert not issues, issues
    page.close()


def test_user_thesis_submit_appears_in_history(server, browser):
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/oil", wait_until="networkidle")
    page.wait_for_selector("#user-thesis-form", timeout=5000)

    marker = "E2E drill call — unique marker 7f3a"
    page.fill("#ut-one-liner", marker)
    page.fill("#ut-invalidation", "price closes back above 103")
    page.select_option("#ut-side", "short")
    page.fill("#ut-confidence", "0.65")
    page.click("#ut-submit")

    page.wait_for_selector("#ut-success:not([hidden])", timeout=5000)
    page.wait_for_selector(f"#history-body >> text={marker}", timeout=5000)
    first_row = page.locator("#history-body tr").first
    assert marker in first_row.inner_text()
    assert "user" in first_row.inner_text().lower()
    assert "pending" in first_row.inner_text().lower()   # never auto-graded on submit
    assert not issues, issues
    page.close()


# ── refresh ──────────────────────────────────────────────────────────────────

def test_refresh_shows_skipped_state(server, browser):
    """oil's 1d move is tiny — deterministically below its own
    material_move_pct threshold, so this always skips."""
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/oil", wait_until="networkidle")
    page.click("#refresh-btn")
    page.wait_for_selector("#refresh-status.is-skipped", timeout=5000)
    assert "skipped: no material move" in page.inner_text("#refresh-status")
    assert not issues, issues
    page.close()


def test_refresh_shows_done_state_with_new_thesis(server, browser):
    """btc's 1d move is large — deterministically exceeds its
    material_move_pct threshold, so this always runs through to done."""
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/btc", wait_until="networkidle")

    # btc's thesis was built moments before this test runs, so "Called <1m
    # ago" reads identically before and after a refresh — too coarse to prove
    # a re-render happened. Watch for the actual mechanism instead: pollJob's
    # "done" branch re-fetches the brief and calls renderDesk() with it.
    refetches = []
    page.on("request", lambda r: refetches.append(r.url) if r.url.endswith("/api/desks/btc") else None)

    page.click("#refresh-btn")
    page.wait_for_selector("#refresh-status.is-done", timeout=10000)
    assert "new thesis" in page.inner_text("#refresh-status")
    assert "tokens" in page.inner_text("#refresh-status")
    page.wait_for_timeout(200)   # the re-fetch is awaited right after the state flips, not before
    assert refetches, "refresh reaching 'done' must trigger a brief re-fetch so the page re-renders"
    assert not issues, issues
    page.close()


# ── error / empty states ─────────────────────────────────────────────────────

def test_api_500_shows_the_error_state(server, browser):
    page, _ = open_page(browser, DESKTOP_VP)   # a 500 legitimately logs a console error; not asserted here
    page.goto(server + "/desks/err500", wait_until="networkidle")
    page.wait_for_selector("#desk-error:not([hidden])", timeout=5000)
    assert page.locator("#desk-content").is_hidden()
    assert page.locator("#desk-retry").is_visible()
    page.close()


def test_empty_desk_shows_the_empty_state(server, browser):
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/ai", wait_until="networkidle")
    page.wait_for_selector("#call-empty:not([hidden])", timeout=5000)
    assert page.locator("#call-card").is_hidden()
    assert "No call yet" in page.inner_text("#call-empty")
    assert not issues, issues
    page.close()


def test_stale_state_shows_the_banner_with_a_reason(server, browser):
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/gas", wait_until="networkidle")
    page.wait_for_selector("#stale-banner:not([hidden])", timeout=5000)
    text = page.inner_text("#stale-banner")
    assert "Stale" in text
    assert "analytics" in text and "intel" in text and "thesis" in text
    assert not issues, issues
    page.close()


def test_grid_loading_then_error_state_on_a_dead_backend(server, browser):
    """A request to an unreachable API must land on the grid's own error
    state with a retry — not a blank page or an uncaught exception."""
    page, _ = open_page(browser, DESKTOP_VP)
    page.route("**/api/desks", lambda route: route.abort())
    page.goto(server + "/desks", wait_until="domcontentloaded")
    page.wait_for_selector("#desks-error:not([hidden])", timeout=5000)
    assert page.locator("#desks-grid").is_hidden()
    assert page.locator("#desks-retry").is_visible()
    page.close()


# ── details ───────────────────────────────────────────────────────────────────

def test_details_section_is_collapsed_by_default_and_opens(server, browser):
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/oil", wait_until="networkidle")
    page.wait_for_selector("#desk-details", timeout=5000)
    assert page.evaluate("document.getElementById('desk-details').open") is False
    page.click("#desk-details summary")
    assert page.evaluate("document.getElementById('desk-details').open") is True
    assert "xyz:CL" in page.inner_text("#snapshot-table") or "WTI" in page.inner_text("#snapshot-table")
    assert not issues, issues
    page.close()


# ── zero console errors, broadly ─────────────────────────────────────────────

def test_zero_console_errors_across_every_state(server, browser):
    page, issues = open_page(browser, DESKTOP_VP)
    for path in ("/desks", "/desks/oil", "/desks/btc", "/desks/ai", "/desks/gas"):
        page.goto(server + path, wait_until="networkidle")
        page.wait_for_timeout(200)
    # interact a little: leverage switch, open details, add a scenario row
    page.goto(server + "/desks/oil", wait_until="networkidle")
    page.check("#lev-opt-20")
    page.click("#desk-details summary")
    page.click("#ut-add-scenario")
    page.wait_for_timeout(200)
    assert not issues, issues
    page.close()



# ── "Your size" (2026-10-18) ──────────────────────────────────────────────────

def test_your_size_line_sizes_the_call_and_hides_with_amounts(server, browser):
    """The call card says how big THIS account's position would be, from the
    risk service, and "Hide amounts" masks the dollars like every other cash
    figure. The fixture app answers /api/risk/size with the real size_trade
    at a $15.21 starter account."""
    page, issues = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/oil", wait_until="networkidle")
    line = page.locator("#call-size")
    line.wait_for(state="visible", timeout=5000)
    text = line.inner_text()
    assert text.startswith("Your size:") and "margin" in text and "Stop-out costs $" in text
    page.click("#discreet-toggle")
    assert "$" not in line.inner_text()          # dollars masked, percentages stay
    page.click("#discreet-toggle")
    assert not issues, issues
    page.close()


def test_no_size_line_without_a_call(server, browser):
    page, _ = open_page(browser, DESKTOP_VP)
    page.goto(server + "/desks/ai", wait_until="networkidle")
    assert page.locator("#call-size").is_hidden()
    page.close()
