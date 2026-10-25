"""End-to-end proof that the accounts/wallet flow works in a real browser.

Deselected from the commit gate (`-m browser`) — boots Chromium, which is
nowhere near the gate's <2s budget. Run before a deploy:

    .venv/bin/python -m pytest -m browser services/wallet_ui/tests/ -q

Replaces test_wallet_flow_browser.py, which drove the SIWE sign-in flow this
bundle no longer has (contracts/accounts/models.py's docstring: connecting a
Hyperliquid account IS the identity now, there is no separate sign-in step).

Every test here runs against a real FastAPI app (fake_accounts_server.py)
serving the contract routes, and a real EIP-1193 provider announced over
EIP-6963 whose signatures come from actual eth_account signing in Python
(wallet_harness.py) — not a server or wallet this suite pretends to have.
"""
from __future__ import annotations

import json

import pytest
from eth_account import Account as EthAccount

# The browser lane is deselected by default (pyproject addopts) but pytest
# still COLLECTS this module, and CI does not install playwright: the bare
# import broke collection for the whole gate on 2026-10-24. Skip the module
# at collection time instead, the way tests/browser/ imports it lazily.
pytest.importorskip("playwright", reason="browser lane only: pip install playwright")
from playwright.sync_api import Page, sync_playwright  # noqa: E402

from services.wallet_ui.tests.fake_accounts_server import build_app  # noqa: E402
from services.wallet_ui.tests.wallet_harness import (
    ORIGIN,
    console_error_collector,
    goto,
    install_backend,
    install_mock_wallet,
)

pytestmark = pytest.mark.browser


def short_address(address: str) -> str:
    """Mirrors src/lib/format.ts's shortAddress exactly — see
    test_contract_sync.py for why duplicating a one-liner like this is safe
    to leave unguarded (it's too small to drift unnoticed, unlike the regex
    patterns and CONTRACT_VERSION that file does check)."""
    return f"{address[:6]}…{address[-4:]}"


@pytest.fixture(scope="module")
def browser():
    # One sync_playwright() context for this module, closed before pytest
    # moves on — see tests/browser/conftest.py's `browser` fixture for why
    # that discipline matters (exactly one live context per process).
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = context.new_page()
    yield pg
    context.close()


@pytest.fixture
def errors(page):
    return console_error_collector(page)


@pytest.fixture(autouse=True)
def _assert_no_console_errors(errors):
    """Every test in this file gets this for free — the "zero console
    errors" requirement, enforced structurally rather than repeated in each
    test body."""
    yield
    assert not errors, f"console/page errors during test: {errors}"


def setup(page: Page, **wallet_kwargs):
    """Fresh backend + mock wallet + request log, navigated and ready.
    Returns (app, client, key, requests) — most tests only need `key`."""
    app = build_app()
    client = install_backend(page, app)
    key = install_mock_wallet(page, **wallet_kwargs)
    requests: list[tuple[str, str]] = []
    page.on("request", lambda r: requests.append((r.method, r.url)))
    goto(page)
    return app, client, key, requests


def open_choose(page: Page) -> None:
    page.get_by_role("button", name="Connect", exact=True).click()
    page.get_by_role("dialog").first.wait_for()


def go_to_connect_stepper(page: Page) -> None:
    open_choose(page)
    page.locator(".pw-card", has_text="Connect your Hyperliquid wallet").click()


def ensure_menu_open(page: Page) -> None:
    """Clicks the account chip only if its dropdown is not already open.

    The chip's own click handler TOGGLES the menu, and several menu actions
    (Manage, committing a rename) deliberately leave it open so a second
    action can follow without reopening it — only a completed switch closes
    it. An unconditional chip.click() here would sometimes open the menu and
    sometimes slam it shut, depending on what the previous action did.
    """
    if page.locator(".pw-menu").count() == 0:
        page.locator(".pw-chip").click()
        page.locator(".pw-menu").wait_for()


def connect_step1(page: Page, wallet_name: str = "Mock Wallet") -> None:
    go_to_connect_stepper(page)
    page.get_by_role("button", name="Connect wallet", exact=True).click()
    # RainbowKit's own picker, on top of ours — matched by the wallet's
    # announced name, which appears nowhere else on the page.
    page.get_by_text(wallet_name, exact=False).click()
    page.get_by_text("Connected", exact=True).wait_for()


# ── 1. connect → authorize → done ───────────────────────────────────────

def test_connect_authorize_done(page):
    _, _, key, requests = setup(page)
    connect_step1(page)

    page.get_by_role("button", name="Approve", exact=True).click()
    page.get_by_text("Trading key authorized").wait_for(timeout=10000)
    assert short_address(key.address) in page.locator(".pw-step-done").inner_text()

    page.get_by_role("button", name="Done", exact=True).click()
    assert page.locator(".pw-dialog").count() == 0

    chip = page.locator(".pw-chip")
    chip.wait_for()
    chip_text = chip.inner_text()
    # .pw-tag is CSS text-transform:uppercase; innerText() reflects the
    # rendered text, not the DOM's literal "Trade".
    assert "TRADE" in chip_text
    assert "$15.21" in chip_text

    events = page.evaluate("window.__accountChangedEvents")
    assert events, "pathiel:account-changed never fired"
    last = events[-1]
    assert last["can_trade"] is True
    assert last["needs_onboarding"] is False
    assert last["accounts_count"] == 1
    assert last["address"].lower() == key.address.lower()

    assert any(u.endswith("/api/accounts/agent/prepare") for _, u in requests)
    assert any(u.endswith("/api/accounts/agent/confirm") for _, u in requests)


# ── 2. the user rejecting the signature ─────────────────────────────────

def test_user_rejection_then_retry_succeeds(page):
    setup(page)
    connect_step1(page)

    page.evaluate("window.__mock.rejectNextSign = true")
    page.get_by_role("button", name="Approve", exact=True).click()
    page.get_by_text("You closed the signature request.").wait_for()

    page.get_by_role("button", name="Try again", exact=True).click()
    page.get_by_text("Trading key authorized").wait_for(timeout=10000)


# ── 3. an expired request re-prepares automatically, exactly once ──────

def test_expired_request_reprepares_automatically_once(page):
    _, _, key, requests = setup(page)

    confirm_failures = {"n": 0}

    def flaky_confirm(route):
        if route.request.url.endswith("/api/accounts/agent/confirm") and confirm_failures["n"] == 0:
            confirm_failures["n"] += 1
            route.fulfill(
                status=400, content_type="application/json",
                body=json.dumps({"detail": "This signing request expired. Generate a new one."}),
            )
        else:
            route.fallback()

    page.route(f"{ORIGIN}/api/accounts/agent/confirm", flaky_confirm)

    connect_step1(page)
    page.get_by_role("button", name="Approve", exact=True).click()
    # No visible error at any point — the retry is silent and automatic.
    page.get_by_text("Trading key authorized").wait_for(timeout=10000)
    assert page.locator(".pw-banner-down").count() == 0

    assert confirm_failures["n"] == 1
    prepare_calls = [u for m, u in requests if u.endswith("/api/accounts/agent/prepare")]
    confirm_calls = [u for m, u in requests if u.endswith("/api/accounts/agent/confirm")]
    assert len(prepare_calls) == 2, "expected the original prepare plus exactly one silent re-prepare"
    assert len(confirm_calls) == 2, "expected the failed confirm plus exactly one retry"


def test_expired_twice_surfaces_a_manual_retry(page):
    """The "once" in "re-prepare automatically once" — a second expiry in a
    row has to stop and ask, not loop forever."""
    setup(page)

    def always_expired(route):
        if route.request.url.endswith("/api/accounts/agent/confirm"):
            route.fulfill(
                status=400, content_type="application/json",
                body=json.dumps({"detail": "This signing request expired. Generate a new one."}),
            )
        else:
            route.fallback()

    page.route(f"{ORIGIN}/api/accounts/agent/confirm", always_expired)

    connect_step1(page)
    page.get_by_role("button", name="Approve", exact=True).click()
    page.get_by_text("expired", exact=False).wait_for(timeout=10000)
    assert page.get_by_role("button", name="Try again", exact=True).is_visible()


def test_network_error_on_confirm_shows_retry_and_recovers(page):
    """A transport-level failure (DNS/offline/connection reset), not a
    response the server sent — lib/api.ts's request() wraps the fetch()
    rejection itself into ApiError('network'), separately from any HTTP
    status the "expired" tests above simulate."""
    setup(page)

    aborted = {"done": False}

    def abort_once(route):
        if route.request.url.endswith("/api/accounts/agent/confirm") and not aborted["done"]:
            aborted["done"] = True
            route.abort("failed")
        else:
            route.fallback()

    page.route(f"{ORIGIN}/api/accounts/agent/confirm", abort_once)

    connect_step1(page)
    page.get_by_role("button", name="Approve", exact=True).click()
    page.get_by_text("Could not reach pathiel", exact=False).wait_for(timeout=10000)

    page.get_by_role("button", name="Try again", exact=True).click()
    page.get_by_text("Trading key authorized").wait_for(timeout=10000)
    assert aborted["done"] is True


# ── 4. chain-switch prompt ───────────────────────────────────────────────

def test_chain_mismatch_prompts_a_switch_then_proceeds(page):
    # fake_accounts_server's ApproveAgent domain is always chainId 42161;
    # start the mock wallet on a different chain so the mismatch is real.
    setup(page, chain_id=1)
    connect_step1(page)

    page.get_by_role("button", name="Approve", exact=True).click()
    page.get_by_text("wrong network", exact=False).wait_for()
    page.get_by_role("button", name="Switch network", exact=True).click()
    page.get_by_text("Trading key authorized").wait_for(timeout=10000)


# ── 5. API-key validation and success, and the key never leaks ─────────

def test_apikey_validation_then_success_and_key_never_leaks(page):
    setup(page)
    all_console: list[str] = []
    page.on("console", lambda m: all_console.append(m.text))

    open_choose(page)
    page.locator(".pw-card", has_text="Use an API key").click()

    # Invalid submission: blocked client-side, no request fired.
    page.get_by_role("button", name="Add account", exact=True).click()
    page.get_by_text("Enter a valid wallet address", exact=False).wait_for()

    main_addr = EthAccount.create().address
    agent_pk = EthAccount.create().key.hex()
    page.get_by_label("Main wallet address").fill(main_addr)
    page.get_by_label("API wallet private key").fill(agent_pk)

    requests: list[tuple[str, str]] = []
    page.on("request", lambda r: requests.append((r.method, r.url)))

    page.get_by_role("button", name="Add account", exact=True).click()
    page.get_by_text("Account added").wait_for()
    assert any(m == "POST" and u.endswith("/api/accounts/import") for m, u in requests)

    needle = agent_pk[2:] if agent_pk.startswith("0x") else agent_pk  # the raw secret, bare
    html = page.content()
    storage_dump = page.evaluate(
        "JSON.stringify(Object.entries(localStorage)) + JSON.stringify(Object.entries(sessionStorage))"
    )
    assert needle not in html, "the private key leaked into the DOM"
    assert needle not in storage_dump, "the private key leaked into local/session storage"
    assert all(needle not in msg for msg in all_console), "the private key leaked into the console"


def test_apikey_server_rejection_shows_a_banner(page):
    setup(page)

    def reject_import(route):
        route.fulfill(
            status=400, content_type="application/json",
            body=json.dumps({"detail": "Hyperliquid could not verify that key."}),
        )

    page.route(f"{ORIGIN}/api/accounts/import", reject_import)

    open_choose(page)
    page.locator(".pw-card", has_text="Use an API key").click()
    page.get_by_label("Main wallet address").fill(EthAccount.create().address)
    page.get_by_label("API wallet private key").fill(EthAccount.create().key.hex())
    page.get_by_role("button", name="Add account", exact=True).click()
    page.get_by_text("could not verify that key", exact=False).wait_for()


# ── 6. watch flow ────────────────────────────────────────────────────────

def test_watch_flow(page):
    _, _, _, requests = setup(page)
    open_choose(page)
    page.locator(".pw-card", has_text="Just watch an address").click()

    page.get_by_role("button", name="Watch this address", exact=True).click()
    page.get_by_text("Enter a valid wallet address", exact=False).wait_for()

    watched = EthAccount.create().address
    page.get_by_label("Address to watch").fill(watched)
    page.get_by_role("button", name="Watch this address", exact=True).click()
    page.get_by_text("Now watching").wait_for()
    assert any(u.endswith("/api/accounts/watch") for _, u in requests)

    page.get_by_role("button", name="Done", exact=True).click()
    chip_text = page.locator(".pw-chip").inner_text()
    assert "WATCH" in chip_text


# ── 7. the switcher: switch, rename, remove ─────────────────────────────

def test_switcher_switch_rename_and_remove(page):
    app = build_app()
    client = install_backend(page, app)
    first = client.post("/api/accounts/watch", json={"address": EthAccount.create().address, "label": "First"}).json()
    second = client.post("/api/accounts/watch", json={"address": EthAccount.create().address, "label": "Second"}).json()
    goto(page)

    chip = page.locator(".pw-chip")
    chip.wait_for()
    assert "First" in chip.inner_text()
    assert first["is_active"] is True and second["is_active"] is False

    # switch
    ensure_menu_open(page)
    page.locator(".pw-menu-item", has_text="Second").click()
    page.wait_for_timeout(200)
    assert "Second" in chip.inner_text()

    # rename the now-active account (switching above closed the menu)
    ensure_menu_open(page)
    page.get_by_role("menuitemcheckbox", name="Manage accounts", exact=False).click()
    page.get_by_role("button", name="Rename Second", exact=True).click()
    rename_input = page.locator(".pw-input-inline")
    rename_input.fill("Second renamed")
    rename_input.press("Enter")
    page.wait_for_timeout(200)
    assert "Second renamed" in chip.inner_text()

    # remove the OTHER (inactive) account — menu is still open from above
    ensure_menu_open(page)
    page.get_by_role("button", name="Rename First", exact=True).wait_for()  # still in managing mode
    page.get_by_role("button", name="Remove First", exact=True).click()
    page.get_by_text("Remove First?").wait_for()
    page.get_by_role("button", name="Yes, remove", exact=True).click()
    page.wait_for_timeout(200)

    remaining = client.get("/api/accounts").json()
    assert len(remaining) == 1
    assert remaining[0]["label"] == "Second renamed"


# ── 8. pathiel:account-changed event shape ──────────────────────────────

def test_account_changed_event_matches_session_info_shape(page):
    setup(page)
    open_choose(page)
    page.locator(".pw-card", has_text="Just watch an address").click()
    page.get_by_label("Address to watch").fill(EthAccount.create().address)
    page.get_by_role("button", name="Watch this address", exact=True).click()
    page.get_by_text("Now watching").wait_for()

    events = page.evaluate("window.__accountChangedEvents")
    assert events
    detail = events[-1]
    assert set(detail.keys()) == {
        "local_mode", "address", "active_account_id", "accounts_count", "can_trade", "needs_onboarding",
    }
    assert detail["local_mode"] is True
    assert detail["accounts_count"] == 1
    assert detail["needs_onboarding"] is False
    # watch-only: active, but not trade-capable.
    assert detail["can_trade"] is False


# ── 9. keyboard-only navigation ──────────────────────────────────────────

def test_keyboard_only_navigation(page):
    setup(page)

    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.classList.contains('pw-connect-btn')")

    page.keyboard.press("Enter")
    page.get_by_role("dialog").first.wait_for()
    assert page.evaluate("!!document.activeElement.closest('.pw-dialog')")

    focusable_count = page.evaluate(
        "document.querySelectorAll("
        "'.pw-dialog a[href], .pw-dialog button:not([disabled]), .pw-dialog input'"
        ").length"
    )
    assert focusable_count > 0
    for _ in range(focusable_count + 2):  # at least one full lap
        page.keyboard.press("Tab")
        assert page.evaluate("!!document.activeElement.closest('.pw-dialog')"), (
            "focus escaped the dialog while tabbing forward"
        )

    page.keyboard.press("Shift+Tab")
    assert page.evaluate("!!document.activeElement.closest('.pw-dialog')"), (
        "focus escaped the dialog on shift+tab"
    )

    page.keyboard.press("Escape")
    assert page.locator(".pw-dialog").count() == 0
    assert page.evaluate("document.activeElement.classList.contains('pw-connect-btn')"), (
        "focus did not return to the trigger after closing"
    )


# ── 10. 375px viewport, no horizontal scroll ────────────────────────────

def test_no_horizontal_scroll_at_375px(page):
    page.set_viewport_size({"width": 375, "height": 700})
    setup(page)

    def overflow_px() -> int:
        return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")

    assert overflow_px() <= 0, "baseline page already overflows at 375px"

    for card_text in ("Connect your Hyperliquid wallet", "Use an API key", "Just watch an address"):
        open_choose(page)
        page.locator(".pw-card", has_text=card_text).click()
        page.wait_for_timeout(100)
        assert overflow_px() <= 0, f"{card_text}: horizontal overflow at 375px"
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)


def test_no_horizontal_scroll_with_account_chip_at_375px(page):
    page.set_viewport_size({"width": 375, "height": 700})
    app = build_app()
    client = install_backend(page, app)
    client.post("/api/accounts/watch", json={
        "address": EthAccount.create().address, "label": "A fairly long watch label",
    })
    goto(page)

    def overflow_px() -> int:
        return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")

    page.locator(".pw-chip").wait_for()
    assert overflow_px() <= 0, "header chip overflows at 375px"
    page.locator(".pw-chip").click()
    page.locator(".pw-menu").wait_for()
    assert overflow_px() <= 0, "open account menu overflows at 375px"


# ── 11. zero console errors across a full combined journey ─────────────

def test_full_journey_has_zero_console_errors(page):
    """The autouse fixture checks this for every test; this one is a long,
    representative combined journey so the requirement has one test whose
    name says exactly what it proves."""
    setup(page)
    connect_step1(page)
    page.get_by_role("button", name="Approve", exact=True).click()
    page.get_by_text("Trading key authorized").wait_for(timeout=10000)
    page.get_by_role("button", name="Done", exact=True).click()

    ensure_menu_open(page)
    page.get_by_role("menuitem", name="Add account", exact=False).click()
    page.locator(".pw-card", has_text="Just watch an address").click()
    page.get_by_label("Address to watch").fill(EthAccount.create().address)
    page.get_by_role("button", name="Watch this address", exact=True).click()
    page.get_by_text("Now watching").wait_for()
    page.get_by_role("button", name="Done", exact=True).click()

    ensure_menu_open(page)
    page.get_by_role("menuitemcheckbox", name="Manage accounts", exact=False).click()
    # The trade account's menu row shows its label "Main", not its address.
    page.get_by_role("button", name="Rename Main", exact=True).click()
    page.locator(".pw-input-inline").fill("Main renamed")
    page.locator(".pw-input-inline").press("Enter")
    page.wait_for_timeout(200)

    ensure_menu_open(page)
    page.get_by_role("menuitem", name="Disconnect", exact=False).click()
