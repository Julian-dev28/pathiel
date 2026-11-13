"""Fakes for the wallet connect tests and eval: a Hyperliquid venue and a browser.

Not a test module (no test_ prefix). services/accounts/tests import it as a
package module; tests/test_eval_wallet_connect.py loads it by path.

FakeVenue judges an approveAgent the way Hyperliquid does: it recovers the
signer from the action itself with the installed SDK's own
recover_user_from_user_signed_action (signatureChainId and hyperliquidChain
read from the action), and only then lists the agent under that account in
extraAgents / userRole. A payload the real venue would reject for a bad
signature is therefore rejected here too.

browser_connect() is the page's JavaScript in Python: GET the page, read the
typed data from its config block, set domain.chainId to the "wallet's"
chain, sign with a throwaway master key, POST {address, signature, chainId}.
It uses urllib with proxies off, so it only ever talks to 127.0.0.1.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

from eth_account import Account
from eth_account.messages import encode_typed_data
from hyperliquid.utils.signing import recover_user_from_user_signed_action

APPROVE_AGENT_FIELDS = [
    {"name": "hyperliquidChain", "type": "string"},
    {"name": "agentAddress", "type": "address"},
    {"name": "agentName", "type": "string"},
    {"name": "nonce", "type": "uint64"},
]

_CFG_RE = re.compile(r'<script id="cfg" type="application/json">(.*?)</script>', re.S)
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class FakeVenue:
    """modes:
      ok          verify, register, reply ok
      refuse      reply {"status": "err"} and register nothing
      lands_then_drops  register, then raise as if the connection dropped
      unreachable every call raises
      silent      reply ok and register nothing (never confirmed)
    """

    def __init__(self, mode: str = "ok", network: str = "mainnet"):
        self.mode = mode
        self.network = network
        self.exchange_posts: List[Dict[str, Any]] = []
        self.info_posts: List[Dict[str, Any]] = []
        self.agents: Dict[str, List[Dict[str, Any]]] = {}
        self.roles: Dict[str, Dict[str, Any]] = {}
        self.recovered: List[str] = []

    def exchange(self, payload: Dict[str, Any]) -> Any:
        self.exchange_posts.append(json.loads(json.dumps(payload)))
        if self.mode == "unreachable":
            raise ConnectionError("fake venue unreachable")
        action = dict(payload["action"])
        if action.get("type") != "approveAgent":
            return {"status": "err", "response": "unexpected action"}
        want_chain = "Mainnet" if self.network == "mainnet" else "Testnet"
        if action.get("hyperliquidChain") != want_chain:
            return {"status": "err", "response": "wrong hyperliquidChain for this venue"}
        if payload.get("nonce") != action.get("nonce"):
            return {"status": "err", "response": "nonce mismatch"}
        signer = recover_user_from_user_signed_action(
            action, payload["signature"], APPROVE_AGENT_FIELDS,
            "HyperliquidTransaction:ApproveAgent", action["hyperliquidChain"] == "Mainnet").lower()
        self.recovered.append(signer)
        if self.mode == "refuse":
            return {"status": "err", "response": "User or API Wallet does not exist."}
        if self.mode == "silent":
            return {"status": "ok", "response": {"type": "default"}}
        agent = action["agentAddress"].lower()
        self.agents.setdefault(signer, []).append(
            {"name": action["agentName"], "address": agent, "validUntil": 1893456000000})
        self.roles[agent] = {"role": "agent", "data": {"user": signer}}
        if self.mode == "lands_then_drops":
            raise ConnectionError("dropped after the venue stored it")
        return {"status": "ok", "response": {"type": "default"}}

    def info(self, payload: Dict[str, Any]) -> Any:
        self.info_posts.append(dict(payload))
        if self.mode == "unreachable":
            raise ConnectionError("fake venue unreachable")
        if payload.get("type") == "extraAgents":
            return list(self.agents.get(str(payload.get("user", "")).lower(), []))
        if payload.get("type") == "userRole":
            return self.roles.get(str(payload.get("user", "")).lower(), {"role": "missing"})
        return None


class FakeVenueServer:
    """FakeVenue behind a real HTTP server on 127.0.0.1, so HttpVenue (the
    production client) is the code that talks to it."""

    def __init__(self, venue: FakeVenue):
        self.venue = venue
        fake = venue

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                return

            def do_POST(self) -> None:   # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                try:
                    if self.path == "/exchange":
                        out = fake.exchange(body)
                    elif self.path == "/info":
                        out = fake.info(body)
                    else:
                        self.send_response(404)
                        self.end_headers()
                        return
                except ConnectionError:
                    self.send_response(502)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                        daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# ── the browser ──────────────────────────────────────────────────────────────

def http(method: str, url: str, *, body: Optional[bytes] = None,
         headers: Optional[Dict[str, str]] = None, timeout: float = 30) -> Tuple[int, Dict[str, str], str]:
    """(status, headers, text); an HTTP error status is a result, not a raise."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with _NO_PROXY.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read().decode("utf-8", "replace")


def page_config(page_html: str) -> Dict[str, Any]:
    m = _CFG_RE.search(page_html)
    if not m:
        raise AssertionError("no config block in the page")
    return json.loads(m.group(1))


def sign_typed(typed: Dict[str, Any], key: Any) -> str:
    sig = Account.sign_message(encode_typed_data(full_message=typed), private_key=key).signature.hex()
    return "0x" + sig.removeprefix("0x")


def browser_connect(url: str, master: Any, *, chain_id: int = 42161,
                    report_address: Optional[str] = None, origin: Optional[str] = "auto",
                    host: Optional[str] = None) -> Tuple[int, Dict[str, Any], str]:
    """What the page does. Returns (status, json reply, the page html)."""
    status, _h, page = http("GET", url)
    if status != 200:
        return status, {}, page
    cfg = page_config(page)
    typed = json.loads(json.dumps(cfg["typedData"]))
    typed["domain"]["chainId"] = chain_id
    signature = sign_typed(typed, master.key)
    base = url.split("/", 3)
    root = f"{base[0]}//{base[2]}"
    headers = {"Content-Type": "application/json"}
    if origin == "auto":
        headers["Origin"] = root
    elif origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    body = json.dumps({"address": (report_address or master.address).lower(),
                       "signature": signature, "chainId": chain_id}).encode()
    code, _h, text = http("POST", root + cfg["submitPath"], body=body, headers=headers)
    try:
        reply = json.loads(text)
    except ValueError:
        reply = {"raw": text}
    return code, reply, page


def key_forms(key: str) -> List[str]:
    """Every way a private key could be printed: with and without 0x, any case."""
    bare = key.lower().removeprefix("0x")
    return [bare, bare.upper(), "0x" + bare]


def contains_key(text: str, key: str) -> bool:
    low = (text or "").lower()
    return key.lower().removeprefix("0x") in low
