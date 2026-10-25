"""Playwright e2e for wave-2 onboarding (2026-10-17): the first-run welcome
card, the "how much risk" wizard on "/", and the /account page — against
the fixture-backed app in tests/browser/onboarding_fixture_app.py (the real
pathiel.dashboard routes, the real contracts/accounts + contracts/risk
fixtures, a small internally-consistent risk-preview stand-in for anything
the committed fixtures don't cover exactly).

2026-10-25: the wallet picker and its pathiel:account-changed event are
gone (docs/WALLET_UI_RESTORE.md), so the first-run tests that drove the
wizard through a stubbed window.PathielWallet went with them. What is left
is the Account page and the shared chrome.

Chromium only, boots its own server on this agent's assigned port band
(8840-8849, per this agent's task brief). Deselected from the default
commit gate (too slow for <2s); run with:

    PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m pytest -q -m browser \
        tests/browser/test_onboarding_browser.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import Browser, Page, sync_playwright  # noqa: E402

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
PORT_BAND = range(8840, 8850)   # this agent's assigned band — never a random OS-ephemeral port
DESKTOP_VP = {"width": 1280, "height": 800}
MOBILE_VP = {"width": 375, "height": 812}

SESSION_READY = json.loads((ROOT / "contracts" / "accounts" / "fixtures" / "session_ready.json").read_text())


def _free_port_in_band() -> int:
    for port in PORT_BAND:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port in 8840-8849 — another agent may be holding them all")


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    port = _free_port_in_band()
    env = dict(os.environ)
    env["PATHIEL_STATE_DIR"] = tempfile.mkdtemp(prefix="onboarding-e2e-state-")
    # This fixture mounts the REAL dashboard.py routes (db.register_routes),
    # whose house-account endpoints (/api/dashboard/summary etc.) 401 for an
    # anonymous caller by design (2026-09-04 leak fix) unless this escape
    # hatch is set — same flag tests/browser/conftest.py's demo_server sets,
    # for the same reason: every visit here is anonymous, and that is not
    # what this suite is testing.
    env["PATHIEL_PUBLIC_DASHBOARD"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import uvicorn; uvicorn.run('tests.browser.onboarding_fixture_app:create_app', "
         f"factory=True, host='127.0.0.1', port={port}, log_level='warning')"],
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
        pytest.fail("onboarding fixture server did not come up on " + url)
    yield url
    proc.terminate()
    proc.wait(timeout=20)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    # A local fixture, not tests/browser/conftest.py's: that one is module-
    # scoped specifically so it never overlaps a SECOND module-scoped
    # instance (see its own docstring) — this file is that second instance,
    # same pattern test_desks_browser.py already uses for the same reason.
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def seed(server: str, scenario: str) -> None:
    urllib.request.urlopen(
        urllib.request.Request(
            server + "/api/_test/seed", method="POST",
            data=json.dumps({"scenario": scenario}).encode(),
            headers={"Content-Type": "application/json"},
        ), timeout=5,
    ).read()


def open_page(browser: Browser, viewport: dict | None = None) -> tuple[Page, list[str]]:
    """A fresh context plus a live list of every console error / uncaught
    exception — same "zero console errors" contract
    test_desks_browser.py's open_page() checks."""
    context = browser.new_context(viewport=viewport or DESKTOP_VP)
    page = context.new_page()
    issues: list[str] = []
    page.on("pageerror", lambda e: issues.append(f"pageerror: {e}"))
    page.on("console", lambda m: issues.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    return page, issues


# ── /account: preset switching + advanced fields, no reload ─────────────────

def test_account_page_preset_switch_and_advanced_fields_update_preview_live(server, browser):
    seed(server, "connected")
    page, issues = open_page(browser)
    loads = []
    page.on("load", lambda: loads.append(1))
    page.goto(server + "/account", wait_until="load", timeout=20000)

    page.locator("#risk-preset-seg .fsel").first.wait_for(state="visible", timeout=5000)
    assert page.locator("#risk-preset-seg .fsel").count() == 4
    page.locator("#risk-max-positions").filter(has_text="1").wait_for(timeout=5000)   # starter, equity $15.21

    page.locator('.fsel[data-preset="pro"]').click()
    page.locator("#risk-max-positions").filter(has_text="5").wait_for(timeout=5000)
    assert page.locator("#feasibility-body tr").count() >= 1

    # The 4 advanced fields ship collapsed (Build step 4); open the
    # <details> before interacting with them, same as a real visitor would.
    page.locator("#risk-advanced summary").click()
    page.locator("#adv-max-pos").wait_for(state="visible", timeout=5000)

    # Advanced override: typing a max-positions of 2 should re-preview to 2,
    # live, with NO reload anywhere in this interaction.
    page.locator("#adv-max-pos").fill("2")
    page.locator("#risk-max-positions").filter(has_text="2").wait_for(timeout=5000)

    assert len(loads) == 1, f"the page reloaded {len(loads)} times — the editor must update in place"
    assert not issues, f"console issues on /account: {issues}"
    page.context.close()


def test_account_page_feasibility_table_reflects_the_preview(server, browser):
    seed(server, "connected")
    page, issues = open_page(browser)
    page.goto(server + "/account", wait_until="load", timeout=20000)
    page.locator("#feasibility-body tr").first.wait_for(state="visible", timeout=5000)
    rows_before = page.locator("#feasibility-body tr").all_inner_texts()
    assert any("news_surge_short" in r for r in rows_before)

    page.locator('.fsel[data-preset="pro"]').click()
    page.locator("#risk-max-positions").filter(has_text="5").wait_for(timeout=5000)
    rows_after = page.locator("#feasibility-body tr").all_inner_texts()
    assert rows_after != rows_before, "the feasibility table did not change when the preset changed"

    assert not issues, f"console issues reading the feasibility table: {issues}"
    page.context.close()


def test_account_page_shows_a_migrated_custom_profile_plainly(server, browser):
    """Integration note (wave-2 integrator, 2026-10-17): a migrated legacy
    config (services/risk/migrate.py) resolves preset_applied to a real
    tier NAME but keeps its own overridden numbers — Julien's real shape
    (~15.75% risk/trade, 12.1% daily, 3x leverage, 3 positions; verified
    against the real migrate_legacy + derive_limits in
    test_dashboard_pages.py) must show those REAL numbers, make "Custom"
    obvious, and switch to a clean preset in one click."""
    seed(server, "julien_custom")
    page, issues = open_page(browser)
    page.goto(server + "/account", wait_until="load", timeout=20000)

    page.locator("#risk-custom-note").wait_for(state="visible", timeout=5000)
    assert "Custom" in page.locator("#risk-custom-note").inner_text()
    # No preset button is highlighted while showing Custom — highlighting
    # "starter" here would claim the account is on starter's defaults
    # (3% risk) when the real number is ~15.75%.
    assert page.locator(".fsel.on").count() == 0

    assert "15.75" in page.locator("#risk-risk-usd").inner_text() or \
        "2.39" in page.locator("#risk-risk-usd").inner_text(), \
        page.locator("#risk-risk-usd").inner_text()
    adv_risk = page.locator("#adv-risk-pct").input_value()
    assert adv_risk.startswith("15.75") or adv_risk.startswith("15.74"), adv_risk

    # One click away from Custom, to a clean preset.
    page.locator('.fsel[data-preset="starter"]').click()
    page.locator("#risk-custom-note").wait_for(state="hidden", timeout=5000)
    assert "on" in (page.locator('.fsel[data-preset="starter"]').get_attribute("class") or "")

    assert not issues, f"console issues on a migrated custom profile: {issues}"
    page.context.close()


def test_account_page_shows_the_bump_risk_note_plainly(server, browser):
    """Integration note (wave-2 integrator, 2026-10-17): starter allows
    bumping to the exchange minimum up to 15% risk — at low equity that is
    a double-digit percent of the account on one stop-out, and
    services/risk/derive.py's own notes field already says so
    plainly ("<books> sized up to the exchange minimum (risk allowed up
    to N% for this)."). This has to actually render, not just exist in
    the API response — it was rendered nowhere before this fix."""
    seed(server, "julien_custom")   # equity $15.19, ~15.75% risk/trade -> bumped books
    page, issues = open_page(browser)
    page.goto(server + "/account", wait_until="load", timeout=20000)
    page.locator("#risk-notes .preview-note").first.wait_for(state="visible", timeout=5000)
    notes_text = page.locator("#risk-notes").inner_text()
    assert "Daily pause" in notes_text, notes_text
    assert "exchange minimum" in notes_text, (
        f"the bump-risk note is missing from the preview headline area: {notes_text!r}")
    assert not issues, f"console issues reading the preview notes: {issues}"
    page.context.close()


def test_account_page_security_note_shows_only_for_the_env_sourced_account(server, browser):
    """Build step 4: 'a small Security note when a blocker says the main
    key is present.' The real services/accounts/summary.py's four
    blockers never mention a key at all — account.source == "env" (the
    legacy .env.local path, Julien's actual current live setup) is the
    real signal checked, not a blocker string."""
    seed(server, "env_account")
    page, issues = open_page(browser)
    page.goto(server + "/account", wait_until="load", timeout=20000)
    note = page.locator("#security-note")
    note.wait_for(state="visible", timeout=5000)
    assert "main wallet key" in note.inner_text()

    seed(server, "connected")   # source="wallet_sign" — no main key on this box
    page2, issues2 = open_page(browser)
    page2.goto(server + "/account", wait_until="load", timeout=20000)
    page2.locator("#acct-label").filter(has_text="Main").wait_for(timeout=5000)
    assert page2.locator("#security-note").is_hidden()

    assert not issues and not issues2, f"console issues: {issues + issues2}"
    page.context.close()
    page2.context.close()


# ── cross-cutting: nav, no sign-in, mobile, console ──────────────────────────

ALL_FIXTURE_PAGES = ("/", "/account")


def test_account_nav_link_on_every_page(server, browser):
    seed(server, "connected")
    page, issues = open_page(browser)
    for path in ALL_FIXTURE_PAGES:
        page.goto(server + path, wait_until="load", timeout=20000)
        page.locator('nav.tabs-main a[data-nav="/account"]').wait_for(state="attached", timeout=5000)
        assert page.locator('nav.tabs-main a[data-nav="/account"]').inner_text() == "Account", path
    assert not issues, f"console issues walking every page's nav: {issues}"
    page.context.close()


def test_no_sign_in_ui_anywhere(server, browser):
    seed(server, "fresh")
    page, _ = open_page(browser)
    for path in ALL_FIXTURE_PAGES:
        page.goto(server + path, wait_until="load", timeout=20000)
        assert page.locator("#auth-chip").count() == 0, path
        assert page.locator("#auth-gate").count() == 0, path
        assert page.locator("text=Connect wallet").count() == 0, (
            f"{path}: the old sign-in chip's exact copy survives")
    page.context.close()


def test_375px_no_horizontal_scroll(server, browser):
    for scenario, path in (("fresh", "/"), ("connected", "/"), ("connected", "/account"), ("low_equity", "/")):
        seed(server, scenario)
        page, issues = open_page(browser, MOBILE_VP)
        page.goto(server + path, wait_until="load", timeout=20000)
        page.wait_for_timeout(500)
        dims = page.evaluate(
            "() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})")
        assert dims["sw"] <= dims["cw"] + 1, (
            f"{scenario} {path} @ 375px: document {dims['sw']}px wide, viewport {dims['cw']}px")
        assert not issues, f"{scenario} {path} @ 375px console issues: {issues}"
        page.context.close()


def test_zero_console_errors_across_every_page_and_state(server, browser):
    for scenario in ("fresh", "connected", "low_equity", "watch"):
        seed(server, scenario)
        for path in ALL_FIXTURE_PAGES:
            page, issues = open_page(browser)
            page.goto(server + path, wait_until="load", timeout=20000)
            page.wait_for_timeout(600)
            assert not issues, f"{scenario} {path}: console issues: {issues}"
            page.context.close()
