"""Real-app end-to-end for the connect flow (integrator, 2026-10-17).

Everything is real except Hyperliquid itself: the dashboard served by
pathiel.server:app (onboarding wizard, real templates), the real built wallet
bundle (pathiel/static/wallet.js), a mock wallet that genuinely signs EIP-712
with a fresh key, and the real accounts / auth / risk backends with a real
encrypted vault in a temp state dir. Mocked: the approveAgent submission,
Hyperliquid's agent check, and the equity read.

The journey Julien asked for: open pathiel with nothing connected, connect a
wallet, authorize a trading key with one signature, pick a risk preset, done.
No sign-in step anywhere.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "wallet_ui" / "tests"))
from wallet_harness import console_error_collector, install_mock_wallet  # noqa: E402

ORIGIN = "http://pathiel.test"


@pytest.fixture
def real_app(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(state))
    monkeypatch.setenv("PATHIEL_VAULT_FAST_KDF", "1")
    monkeypatch.delenv("PATHIEL_VAULT_PASSPHRASE", raising=False)
    cfg = tmp_path / "agent-config.json"
    cfg.write_text("{}")
    monkeypatch.setenv("PATHIEL_AGENT_CONFIG_FILE", str(cfg))
    import pathiel.agents.config_store as cs
    monkeypatch.setattr(cs, "CONFIG_PATH", str(cfg))

    from services.auth import integration
    for m in integration._DEPLOYMENT_MARKERS:
        monkeypatch.delenv(m, raising=False)

    # A brand-new user: no legacy .env.local identity.
    from pathiel.client import exchange as ex
    for name, blank in (("PRIVATE_KEY_HEX", ""), ("HL_WALLET", ""), ("HL_MASTER", ""),
                        ("IS_AGENT", False), ("HL_ACCOUNT", "")):
        monkeypatch.setattr(ex, name, blank, raising=False)

    # The Hyperliquid boundary, and nothing else.
    from services.accounts import agent_approval, credentials, hl_verify, pending
    from services.accounts import api as accounts_api
    submitted = []
    monkeypatch.setattr(agent_approval, "submit_approve_agent",
                        lambda *a, **k: submitted.append((a, k)) or {"status": "ok"})
    monkeypatch.setattr(hl_verify, "is_agent_approved", lambda *a, **k: True)
    from pathiel.client import hl_client
    monkeypatch.setattr(hl_client, "fetch_account_state",
                        lambda *a, **k: {"equity": 15.21, "withdrawable": 15.21,
                                         "asset_positions": [], "positions": []})
    pending.clear_for_tests()
    credentials.invalidate_cache()
    accounts_api._ATTEMPTS.clear()

    import pathiel.server as srv
    yield srv.app, state, cfg, submitted
    pending.clear_for_tests()
    credentials.invalidate_cache()


def _route_everything_to(page, app):
    client = TestClient(app, client=("127.0.0.1", 52000))   # local mode

    def handle(route):
        req = route.request
        path = req.url[len(ORIGIN):] or "/"
        bare = path.split("?", 1)[0]
        # Not part of this journey, and not hermetic: the live feed is an
        # endless server-sent-event stream (forwarding it through a test
        # client blocks forever), and the house-account panels read
        # Hyperliquid. The pages must handle these being down anyway.
        if bare.startswith("/api/feed"):
            route.fulfill(status=204, body="")
            return
        if bare.startswith(("/api/dashboard", "/api/hl", "/api/desks")):
            route.fulfill(status=503, headers={"content-type": "application/json"},
                          body='{"detail": "not part of this test"}')
            return
        body = req.post_data_buffer
        headers = {"content-type": req.headers.get("content-type", "application/json")} if body else {}
        resp = client.request(req.method, path, content=body, headers=headers)
        route.fulfill(status=resp.status_code, headers={
            "content-type": resp.headers.get("content-type", "text/plain")}, body=resp.content)

    page.route(f"{ORIGIN}/**", handle)


def test_brand_new_user_connects_authorizes_and_picks_risk_with_no_sign_in(real_app):
    app, state, cfg, submitted = real_app
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = console_error_collector(page)
        wallet = install_mock_wallet(page, name="Mock Wallet")
        _route_everything_to(page, app)
        page.goto(f"{ORIGIN}/", wait_until="load")

        # 1. First run: one card, no sign-in anywhere.
        page.locator("#onboard-welcome").wait_for(state="visible", timeout=10000)
        assert page.get_by_text("Sign in", exact=True).count() == 0
        page.locator("#onb-connect").click()

        # 2. The wallet modal: connect, then authorize the trading key.
        page.locator(".pw-card", has_text="Connect your Hyperliquid wallet").click(timeout=15000)
        page.get_by_role("button", name="Connect wallet", exact=True).click()
        page.get_by_text("Mock Wallet", exact=False).first.click()
        page.get_by_text("Connected", exact=True).wait_for(timeout=10000)
        page.get_by_role("button", name="Approve", exact=True).click()
        page.get_by_text("Trading key authorized").wait_for(timeout=15000)
        page.get_by_role("button", name="Done", exact=True).click()

        # 3. Risk step, fed by the real risk engine at the mocked $15.21.
        page.locator("#onboard-risk").wait_for(state="visible", timeout=10000)
        page.locator('.preset-card[data-preset="starter"]').wait_for(timeout=10000)
        page.locator("#onb-headline").filter(has_text="position").wait_for(timeout=10000)
        assert "stop-out" in page.locator("#onb-headline").inner_text()
        page.locator('.preset-card[data-preset="starter"]').click()
        page.locator("#onb-save").click()
        page.locator("#onboard-done").wait_for(state="visible", timeout=10000)
        browser.close()

    # The backend state is real, not a fixture.
    accounts = json.loads((state / "accounts" / "accounts.json").read_text())
    rows = accounts.get("accounts", accounts) if isinstance(accounts, dict) else accounts
    flat = json.dumps(accounts).lower()
    assert wallet.address.lower() in flat, "the connected main address is stored"
    assert len(submitted) == 1, "approveAgent submitted exactly once"
    key_files = [f for f in (state / "accounts").rglob("*") if f.is_file() and f.name != "accounts.json"
                 and not f.name.startswith(".vault_secret")]
    assert key_files, "the agent key is in the vault"
    for f in key_files + [state / "accounts" / "accounts.json"]:
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600, f
        assert "private" not in f.read_text().lower() or "crypto" in f.read_text().lower()
    saved = json.loads(cfg.read_text())
    assert saved["risk_profile"]["preset"] == "starter"
    assert not errors, errors
    assert rows is not None
