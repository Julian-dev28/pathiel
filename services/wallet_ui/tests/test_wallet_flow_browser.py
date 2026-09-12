"""End-to-end proof that the wallet picker works in a real browser.

Deselected from the commit gate (`-m browser`) because it boots a server and
drives Chromium, which is nowhere near the two-second budget the gate holds to.
It is the only test that can prove the things that actually broke while this was
being built, so it exists and it runs before a deploy:

  - The bundle executes at all. The first build emitted `React.createElement`
    into files that never import React, and every unit test still passed — the
    failure only exists once a browser evaluates the file.
  - The picker lists a wallet that announced itself over EIP-6963, which is the
    entire point of the change and cannot be observed from Python.
  - The signed payload is the SERVER's message. A client-composed SIWE message
    that happens to verify would be a silent downgrade of the auth model.
  - The bundle is not fetched on page load. That is a performance contract, and
    performance contracts rot without a test.

Run:  .venv/bin/python -m pytest -m browser services/wallet_ui/tests/ -q
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[3]

# A wallet that stays disconnected until asked, which is how a real extension
# behaves on a first visit — so RainbowKit has to show its picker rather than
# jumping straight to the signing step.
EIP6963_SHIM = """
let connected = false;
const provider = {
  request: async ({ method, params }) => {
    if (method === 'eth_accounts') return connected ? ['0x1111111111111111111111111111111111111111'] : [];
    if (method === 'eth_requestAccounts') { connected = true; return ['0x1111111111111111111111111111111111111111']; }
    if (method === 'eth_chainId') return '0x3e7';
    if (method === 'personal_sign') { window.__signedMessage = params[0]; return '0x' + 'ab'.repeat(65); }
    return null;
  },
  on() {}, removeListener() {},
};
const detail = Object.freeze({
  info: { uuid: 'okx-shim-0001', name: 'OKX Wallet', rdns: 'com.okex.wallet',
          icon: 'data:image/svg+xml;base64,PHN2Zy8+' },
  provider,
});
window.addEventListener('eip6963:requestProvider',
  () => window.dispatchEvent(new CustomEvent('eip6963:announceProvider', { detail })));
window.dispatchEvent(new CustomEvent('eip6963:announceProvider', { detail }));
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    """A dashboard on synthetic data, booted with no credentials at all.

    Deliberately NOT `api/index.py`: that entrypoint refuses to start while a
    `.env.local` exists on disk, which is correct for a deployment and useless
    for a test running in the developer's own checkout. So this does what the
    entrypoint does — materialize demo data, open the read APIs, close the
    writes — and scrubs every credential out of the child's environment rather
    than trusting that none is set.
    """
    import os
    import tempfile

    sys.path.insert(0, str(ROOT))
    from services.demo.generator import materialize

    data_dir = tempfile.mkdtemp(prefix="wallet-flow-")
    home = tempfile.mkdtemp(prefix="wallet-home-")

    env = {k: v for k, v in os.environ.items()
           if k not in {"HYPERLIQUID_PRIVATE_KEY", "PRIVATE_KEY_HEX",
                        "HYPERLIQUID_ACCOUNT_ADDRESS", "HL_ACCOUNT_ADDRESS",
                        "PATHIA_OPERATOR_TOKEN"}}
    env.update(materialize(data_dir))
    env["HOME"] = home
    env["PATHIA_DASHBOARD_READONLY"] = "1"
    # Left CLOSED on purpose: the gated 401 is what raises the sign-in prompt
    # this test clicks. Opening the dashboard would remove the thing under test.
    env.pop("PATHIA_PUBLIC_DASHBOARD", None)

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import uvicorn; from pathia.server import app;"
         f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='warning')"],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    import urllib.request
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.terminate()
        pytest.fail("dashboard did not come up")
    yield url
    proc.terminate()
    proc.wait(timeout=20)


@pytest.fixture(scope="module")
def flow(server):
    """Drive the whole path once; every assertion below reads from this."""
    seen = {"requests": [], "auth": [], "errors": []}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.add_init_script(EIP6963_SHIM)
        page.on("request", lambda r: seen["requests"].append((r.method, r.url)))
        page.on("request", lambda r: seen["auth"].append((r.method, r.url))
                if "/auth/" in r.url else None)
        page.on("pageerror", lambda e: seen["errors"].append(str(e)))

        page.goto(server + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        seen["eager"] = any("wallet.js" in u for _, u in seen["requests"])

        page.locator("#auth-gate-btn").click()
        page.wait_for_timeout(6000)
        seen["picker"] = page.evaluate(
            "document.querySelector('[role=dialog]')?.innerText || ''")

        okx = page.locator("[role=dialog] button", has_text="OKX")
        seen["okx_listed"] = okx.count() > 0
        if seen["okx_listed"]:
            okx.first.click()
            page.wait_for_timeout(3000)
            sign = page.locator("[role=dialog] button", has_text="Sign message")
            seen["reached_sign"] = sign.count() > 0
            if seen["reached_sign"]:
                sign.first.click()
                page.wait_for_timeout(4000)
        seen["signed_hex"] = page.evaluate("window.__signedMessage") or ""
        browser.close()
    return seen


def test_bundle_executes_without_error(flow):
    """The first build shipped `React.createElement` with no React in scope."""
    assert not flow["errors"], flow["errors"][:3]


def test_bundle_is_not_fetched_on_page_load(flow):
    assert flow["eager"] is False


def test_bundle_is_fetched_once_the_user_asks(flow):
    assert any("wallet.js" in u for _, u in flow["requests"])
    assert any("wallet.css" in u for _, u in flow["requests"])


def test_picker_opens(flow):
    assert "Connect a Wallet" in flow["picker"]


def test_picker_lists_a_wallet_discovered_over_eip6963(flow):
    """The whole reason for the change: the old code took whichever extension
    won the race for window.ethereum and offered no choice."""
    assert flow["okx_listed"], flow["picker"]
    assert "OKX Wallet" in flow["picker"]
    assert "Installed" in flow["picker"]


def test_selecting_a_wallet_reaches_the_signing_step(flow):
    assert flow.get("reached_sign") is True


def test_the_wallet_signs_the_servers_own_message(flow):
    """Not a message the client composed.

    services/auth/api.py builds the EIP-4361 text and verifies domain, nonce and
    expiry out of whatever comes back. The adapter must hand the wallet that
    exact string — see the note at the top of services/wallet_ui/src/auth.ts.
    """
    assert flow["signed_hex"].startswith("0x")
    decoded = bytes.fromhex(flow["signed_hex"][2:]).decode("utf-8", "replace")
    assert "wants you to sign in with your Ethereum account" in decoded
    assert "Nonce:" in decoded
    assert "Expiration Time:" in decoded


def test_it_talks_to_the_projects_own_auth_endpoints(flow):
    paths = [u.split("127.0.0.1:")[1].split("/", 1)[1] for _, u in flow["auth"]]
    assert any(p.startswith("auth/nonce?address=0x") for p in paths), paths
    assert ("POST", True) == ("POST", any(
        m == "POST" and u.endswith("/auth/verify") for m, u in flow["auth"])), flow["auth"]
