"""Playwright harness shared by test_wallet_account_flow_browser.py.

Two jobs:

1. Serve the whole page — document, the REAL built pathiel/static/wallet.js
   and wallet.css, and /api/* — entirely through `page.route`, with no real
   server/port involved. The /api/* branch forwards into a real FastAPI app
   (fake_accounts_server.build_app()) via Starlette's TestClient, so routing
   and Pydantic validation are real; only the network transport is faked.
   This is faster and more hermetic than booting uvicorn, and the one-
   sync_playwright()-context-per-module rule other browser suites in this
   repo follow (see tests/browser/conftest.py) is moot here since nothing
   needs its own `browser` fixture shared across files.

2. Inject an EIP-1193 provider announced over EIP-6963, so wagmi's native
   wallet discovery (services/wallet_ui/README.md) finds it exactly the way
   it would find a real extension. Signing is real too: eth_signTypedData_v4
   calls back into Python (page.expose_function) to sign with a freshly
   generated eth_account key, so the browser test proves the bundle handed
   the wallet the UNMODIFIED server payload — a tampered payload signs to a
   different hash and fake_accounts_server's confirm route rejects it.
"""
from __future__ import annotations

import json
from pathlib import Path

from eth_account import Account as EthAccount
from eth_account.messages import encode_typed_data
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import Page, Route

ROOT = Path(__file__).resolve().parents[3]
STATIC = ROOT / "pathiel" / "static"
ORIGIN = "http://wallet.test"

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>wallet harness</title>
<link rel="stylesheet" href="/static/pathiel.css" />
<link rel="stylesheet" href="/static/wallet.css" />
</head>
<body data-theme="dark">
<div class="masthead">
  <div class="masthead-inner">
    <span class="brand">pathiel</span>
    <div class="masthead-right"><div id="wallet-connect-root"></div></div>
  </div>
</div>
<div id="page-body"></div>
<script>
  window.__accountChangedEvents = [];
  document.addEventListener('pathiel:account-changed', (e) => {
    window.__accountChangedEvents.push(e.detail);
  });
</script>
<script src="/static/wallet.js"></script>
</body>
</html>"""

_CONTENT_TYPES = {".js": "application/javascript", ".css": "text/css"}


def install_backend(page: Page, app: FastAPI) -> TestClient:
    """Routes every request on `page` to the fake document/static files/API.
    Returns the TestClient in case a test wants to assert against server
    state directly (most should go through the page instead)."""
    client = TestClient(app)

    def handle(route: Route) -> None:
        req = route.request
        path = req.url[len(ORIGIN):] or "/"
        path = path.split("?", 1)[0]

        if path in ("/", "/index.html"):
            route.fulfill(status=200, content_type="text/html", body=INDEX_HTML)
            return

        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            f = STATIC / rel
            if f.is_file():
                route.fulfill(status=200, path=str(f), content_type=_CONTENT_TYPES.get(f.suffix))
            else:
                route.fulfill(status=404, body=f"not found: {rel}")
            return

        if path == "/favicon.ico":
            route.fulfill(status=204, body="")
            return

        if path.startswith("/api/"):
            body = req.post_data_buffer
            headers = {"content-type": "application/json"} if body else {}
            resp = client.request(req.method, path, content=body, headers=headers)
            route.fulfill(
                status=resp.status_code,
                content_type=resp.headers.get("content-type", "application/json"),
                body=resp.content,
            )
            return

        route.fulfill(status=404, body=f"not found: {path}")

    page.route(f"{ORIGIN}/**", handle)
    return client


def goto(page: Page) -> None:
    page.goto(f"{ORIGIN}/", wait_until="load")


_SHIM_TEMPLATE = r"""
(function () {
  const ADDRESS = "__ADDRESS__";
  let chainId = __CHAIN_ID__;
  let connected = __INITIALLY_CONNECTED__;
  window.__mock = { rejectNextSign: false, lastMethod: null };

  const handlers = {};
  function on(event, handler) { (handlers[event] = handlers[event] || []).push(handler); }
  function removeListener(event, handler) {
    if (!handlers[event]) return;
    handlers[event] = handlers[event].filter((h) => h !== handler);
  }
  function emit(event, payload) { (handlers[event] || []).slice().forEach((h) => h(payload)); }

  const provider = {
    isMockWallet: true,
    request: async ({ method, params }) => {
      window.__mock.lastMethod = method;
      switch (method) {
        case 'eth_requestAccounts':
          connected = true;
          emit('accountsChanged', [ADDRESS]);
          return [ADDRESS];
        case 'eth_accounts':
          return connected ? [ADDRESS] : [];
        case 'eth_chainId':
          return '0x' + chainId.toString(16);
        case 'wallet_switchEthereumChain': {
          chainId = parseInt(params[0].chainId, 16);
          emit('chainChanged', '0x' + chainId.toString(16));
          return null;
        }
        case 'wallet_requestPermissions':
        case 'wallet_getPermissions':
          return [];
        case 'eth_signTypedData_v4': {
          if (window.__mock.rejectNextSign) {
            window.__mock.rejectNextSign = false;
            const err = new Error('User rejected the request.');
            err.code = 4001;
            throw err;
          }
          return await window.__pyroSign(params[1]);
        }
        default:
          throw new Error('mock wallet: unsupported method ' + method);
      }
    },
    on,
    removeListener,
  };

  const detail = Object.freeze({
    info: {
      uuid: '__UUID__',
      name: '__NAME__',
      rdns: 'test.pathiel.mockwallet',
      icon: 'data:image/svg+xml;base64,PHN2Zy8+',
    },
    provider,
  });
  window.addEventListener('eip6963:requestProvider',
    () => window.dispatchEvent(new CustomEvent('eip6963:announceProvider', { detail })));
  window.dispatchEvent(new CustomEvent('eip6963:announceProvider', { detail }));
})();
"""


def install_mock_wallet(
    page: Page,
    *,
    chain_id: int = 42161,
    initially_connected: bool = False,
    name: str = "Mock Wallet",
    uuid: str = "00000000-mock-wallet-0000-000000000001",
) -> "EthAccount":
    """Creates a fresh test key, exposes a Python signer bound to it, and
    injects the EIP-6963 shim before any page script runs. Call before
    `goto`. Returns the eth_account Account so tests can assert against its
    `.address`.
    """
    key = EthAccount.create()

    def pyro_sign(typed_data_json: str) -> str:
        parsed = json.loads(typed_data_json)
        signable = encode_typed_data(full_message=parsed)
        signed = EthAccount.sign_message(signable, private_key=key.key)
        return "0x" + signed.signature.hex()

    page.expose_function("__pyroSign", pyro_sign)

    script = (
        _SHIM_TEMPLATE
        .replace("__ADDRESS__", key.address)
        .replace("__CHAIN_ID__", str(chain_id))
        .replace("__INITIALLY_CONNECTED__", "true" if initially_connected else "false")
        .replace("__UUID__", uuid)
        .replace("__NAME__", name)
    )
    page.add_init_script(script)
    return key


def console_error_collector(page: Page) -> list:
    """Attach once per test; returns the (live) list of captured messages.

    Excludes Chromium's own "Failed to load resource: the server responded
    with a status of 4xx/5xx" logging. That message is the browser's network
    panel noting an HTTP status code, emitted for EVERY non-2xx response
    whether or not the page's JS caught the resulting rejection — several
    tests here deliberately make the fake server return 400 to prove the
    bundle's error UI, and that is a passing test, not a console error. A
    real bug (an uncaught rejection, a console.error the bundle's own code
    made, a React warning) shows up as a different message and is still
    caught.
    """
    errors: list = []

    def on_console(m) -> None:
        if m.type != "error":
            return
        if "Failed to load resource" in m.text:
            return
        errors.append(f"console.error: {m.text}")

    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", on_console)
    return errors
