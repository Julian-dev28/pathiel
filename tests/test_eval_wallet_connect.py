"""Eval: does "connect your Hyperliquid wallet" connect, and does every safety rule hold?

The outcome this unit exists for: a plugin user goes from no key to a
working API wallet with one signature, and the agent key never leaves the
machine. This drives the real flow end to end:

  * the venue is FakeVenue (services/accounts/tests/wallet_fakes.py) behind a
    real HTTP server on 127.0.0.1, so the production HttpVenue posts to it;
    it recovers the signer with the installed SDK's own function, the way
    Hyperliquid judges an approveAgent
  * the "browser" is plain urllib: it GETs the page, reads the typed data
    from the page's config block, signs it with a throwaway master key on
    the wallet's chain, and POSTs {address, signature, chainId}

Rows (every one must pass):

  connects on mainnet / on testnet               done
  approveAgent posts per connect                  1
  venue recovers the browser's account            yes
  env file key belongs to the agent address       yes
  env file keeps every other line                 yes
  env file mode                                   600
  agent key in HTTP responses, stdout, stderr     0
  agent key in tool / CLI results                 0
  typed data on the page == the SDK's (2 nets)    yes
  wrong token / wrong Host / no Origin / foreign Origin    404 / 421 / 403 / 403
  second POST while the first runs                409
  timeout                                         failed, file unchanged
  operator install                                refused, file and dir unchanged
  signer mismatch                                 nothing posted, file unchanged
  venue refusal                                   file unchanged
  existing key                                    refused before a page exists
  two flows on one env file                       second is busy
  page loads external script or style             0
  page CSP pins the inline script and style       yes

    .venv/bin/python -m pytest -q -s tests/test_eval_wallet_connect.py
"""
from __future__ import annotations

import importlib.util
import json
import re
import stat
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from hyperliquid.utils.signing import user_signed_payload

ROOT = Path(__file__).resolve().parents[1]
_fspec = importlib.util.spec_from_file_location(
    "pathiel_wallet_fakes_eval", ROOT / "services" / "accounts" / "tests" / "wallet_fakes.py")
F = importlib.util.module_from_spec(_fspec)
_fspec.loader.exec_module(F)

TEMPLATE = ("# pathiel keys\n{net}HYPERLIQUID_WALLET_ADDRESS=\nHYPERLIQUID_PRIVATE_KEY=\n"
            "HYPERLIQUID_MASTER_ADDRESS=\n\nEIA_API_KEY=keep-me\n")


def _row(check: str, got: Any, want: Any, ok: bool) -> Dict[str, Any]:
    return {"check": check, "got": got, "want": want, "ok": bool(ok)}


def _snap(p: Path):
    st = p.stat()
    return p.read_bytes(), st.st_mtime_ns


class World:
    """One public install: env file, fake venue server, network."""

    def __init__(self, tmp: Path, monkeypatch, network: str, name: str):
        from services.accounts import connect_wallet as cw
        self.cw = cw
        self.network = network
        self.env_file = tmp / name / ".pathiel" / ".env.local"
        self.env_file.parent.mkdir(parents=True)
        self.env_file.write_text(TEMPLATE.format(
            net="PATHIEL_NETWORK=testnet\n" if network == "testnet" else ""))
        monkeypatch.setenv("PATHIEL_INSTALL", "public")
        monkeypatch.setenv("PATHIEL_NETWORK", network)
        monkeypatch.setenv("PATHIEL_ENV_FILE", str(self.env_file))
        monkeypatch.delenv("PATHIEL_NETWORK_SOURCE", raising=False)
        self.venue = F.FakeVenue(network=network)
        self.server = F.FakeVenueServer(self.venue)

    def flow(self, **kw):
        kw.setdefault("confirm_wait_s", 0)
        return self.cw.ConnectFlow(self.env_file, venue=self.cw.HttpVenue(self.server.url), **kw)

    def close(self):
        self.server.close()


def _serve(flow):
    url = flow.start()
    t = threading.Thread(target=flow.serve, daemon=True)
    t.start()
    return t, url


def _sdk_typed(agent: str, nonce: int, network: str, chain_id: int) -> Dict[str, Any]:
    action = {"agentAddress": agent, "agentName": "pathiel-plugin", "nonce": nonce,
              "signatureChainId": hex(chain_id),
              "hyperliquidChain": "Mainnet" if network == "mainnet" else "Testnet"}
    return user_signed_payload("HyperliquidTransaction:ApproveAgent", F.APPROVE_AGENT_FIELDS, action)


def run(tmp: Path, monkeypatch, capfd) -> List[Dict[str, Any]]:
    from services.accounts import connect_wallet as cw
    import contracts.wallet_connect as contract
    rows: List[Dict[str, Any]] = []
    secrets_seen: List[str] = []
    http_texts: List[str] = []
    result_texts: List[str] = []

    # ── 1. the happy path, both networks ──
    for network in ("mainnet", "testnet"):
        w = World(tmp, monkeypatch, network, f"happy-{network}")
        try:
            flow = w.flow()
            key = flow._ConnectFlow__key
            secrets_seen.append(key)
            t, url = _serve(flow)
            master = Account.create()
            chain_id = 42161 if network == "mainnet" else 421614
            status, headers, page = F.http("GET", url)
            http_texts += [page, json.dumps(headers)]
            cfg = F.page_config(page)
            typed = json.loads(json.dumps(cfg["typedData"]))
            typed["domain"]["chainId"] = chain_id
            ours = encode_typed_data(full_message=typed)
            sdk = encode_typed_data(full_message=_sdk_typed(flow.agent_address, typed["message"]["nonce"],
                                                            network, chain_id))
            rows.append(_row(f"typed data on the page == SDK's ({network})",
                             "yes" if (ours.header, ours.body) == (sdk.header, sdk.body) else "no",
                             "yes", (ours.header, ours.body) == (sdk.header, sdk.body)))
            code, reply, page2 = F.browser_connect(url, master, chain_id=chain_id)
            http_texts += [json.dumps(reply), page2]
            t.join(15)
            res = flow.result()
            result_texts.append(json.dumps(res))
            rows.append(_row(f"connects on {network}", res.get("state"), "done",
                             res.get("ok") is True and res.get("state") == "done"))
            vu = res.get("valid_until")
            rows.append(_row(f"approval end date reported ({network})", vu, "a date",
                             bool(vu) and vu in (res.get("next") or "")))
            rows.append(_row(f"approveAgent posts per connect ({network})", len(w.venue.exchange_posts),
                             1, len(w.venue.exchange_posts) == 1))
            rows.append(_row(f"venue recovers the browser's account ({network})",
                             "yes" if w.venue.recovered == [master.address.lower()] else w.venue.recovered,
                             "yes", w.venue.recovered == [master.address.lower()]))
            vals = cw.env_values(w.env_file.read_text())
            key_ok = (vals.get("HYPERLIQUID_PRIVATE_KEY") == key
                      and Account.from_key(key).address == vals.get("HYPERLIQUID_WALLET_ADDRESS")
                      and vals.get("HYPERLIQUID_MASTER_ADDRESS", "").lower() == master.address.lower())
            rows.append(_row(f"env file key belongs to the agent ({network})", "yes" if key_ok else "no",
                             "yes", key_ok))
            kept = vals.get("EIA_API_KEY") == "keep-me" and w.env_file.read_text().startswith("# pathiel keys\n")
            rows.append(_row(f"env file keeps every other line ({network})", "yes" if kept else "no",
                             "yes", kept))
            mode = oct(stat.S_IMODE(w.env_file.stat().st_mode))
            rows.append(_row(f"env file mode ({network})", mode, "0o600", mode == "0o600"))
        finally:
            w.close()

    # ── 2. the server's locks ──
    w = World(tmp, monkeypatch, "mainnet", "locks")
    try:
        gate = threading.Event()
        real = w.venue.exchange

        def slow(payload):
            gate.wait(10)
            return real(payload)

        w.venue.exchange = slow
        flow = w.flow()
        secrets_seen.append(flow._ConnectFlow__key)
        t, url = _serve(flow)
        root = f"http://127.0.0.1:{flow.port}"
        got = {
            "wrong token": F.http("GET", root + "/" + "A" * 43 + "/")[0],
            "wrong Host": F.http("GET", url, headers={"Host": "attacker.example"})[0],
        }
        _s, _h, page = F.http("GET", url)
        submit = root + F.page_config(page)["submitPath"]
        body = json.dumps({"address": "0x" + "1" * 40, "signature": "0x" + "1" * 130, "chainId": 1}).encode()
        ct = {"Content-Type": "application/json"}
        got["no Origin"] = F.http("POST", submit, body=body, headers=ct)[0]
        got["foreign Origin"] = F.http("POST", submit, body=body,
                                       headers={**ct, "Origin": "https://attacker.example"})[0]
        for check, want in (("wrong token", 404), ("wrong Host", 421), ("no Origin", 403),
                            ("foreign Origin", 403)):
            rows.append(_row(check, got[check], want, got[check] == want))
        first: Dict[str, Any] = {}
        th = threading.Thread(target=lambda: first.update(r=F.browser_connect(url, Account.create())))
        th.start()
        for _ in range(500):
            if flow.state == "working":
                break
            time.sleep(0.01)
        second = F.http("POST", submit, body=body, headers={**ct, "Origin": root})[0]
        gate.set()
        th.join(15)
        t.join(15)
        rows.append(_row("second POST while the first runs", second, 409, second == 409))
        http_texts.append(json.dumps(first.get("r", [None, {}])[1]))
    finally:
        gate.set()
        w.close()

    # ── 3. refusals that must leave the env file alone ──
    w = World(tmp, monkeypatch, "mainnet", "timeout")
    try:
        before = _snap(w.env_file)
        flow = w.flow(timeout_s=0.3)
        secrets_seen.append(flow._ConnectFlow__key)
        flow.start()
        res = flow.serve()
        result_texts.append(json.dumps(res))
        ok = res.get("code") == "timeout" and _snap(w.env_file) == before
        rows.append(_row("timeout", f"{res.get('state')}/{res.get('code')}", "failed/timeout", ok))
    finally:
        w.close()

    w = World(tmp, monkeypatch, "mainnet", "operator")
    try:
        monkeypatch.delenv("PATHIEL_INSTALL")
        before = _snap(w.env_file)
        outs = [json.loads(contract.mcp_handler()({})), json.loads(cw.handle_wallet_connect({}))]
        try:
            w.flow()
            raised = "none"
        except cw.ConnectError as e:
            raised = e.code
        untouched = _snap(w.env_file) == before and \
            sorted(p.name for p in w.env_file.parent.iterdir()) == [".env.local"]
        codes = {o.get("code") for o in outs} | {raised}
        rows.append(_row("operator install", f"{sorted(codes)}, untouched={untouched}",
                         "['operator_install'], untouched=True",
                         codes == {"operator_install"} and untouched))
        monkeypatch.setenv("PATHIEL_INSTALL", "public")
    finally:
        w.close()

    w = World(tmp, monkeypatch, "mainnet", "mismatch")
    try:
        before = _snap(w.env_file)
        flow = w.flow()
        secrets_seen.append(flow._ConnectFlow__key)
        t, url = _serve(flow)
        _c, reply, _p = F.browser_connect(url, Account.create(), report_address=Account.create().address)
        t.join(15)
        http_texts.append(json.dumps(reply))
        ok = reply.get("code") == "signer_mismatch" and not w.venue.exchange_posts and _snap(w.env_file) == before
        rows.append(_row("signer mismatch", f"{reply.get('code')}, posts={len(w.venue.exchange_posts)}",
                         "signer_mismatch, posts=0", ok))
    finally:
        w.close()

    w = World(tmp, monkeypatch, "mainnet", "refused")
    try:
        w.venue.mode = "refuse"
        before = _snap(w.env_file)
        flow = w.flow(confirm_tries=2)
        secrets_seen.append(flow._ConnectFlow__key)
        t, url = _serve(flow)
        _c, reply, _p = F.browser_connect(url, Account.create())
        t.join(15)
        http_texts.append(json.dumps(reply))
        ok = reply.get("code") == "venue_refused" and _snap(w.env_file) == before
        rows.append(_row("venue refusal", f"{reply.get('code')}, file unchanged={_snap(w.env_file) == before}",
                         "venue_refused, file unchanged=True", ok))
    finally:
        w.close()

    w = World(tmp, monkeypatch, "mainnet", "existing")
    try:
        w.env_file.write_text(w.env_file.read_text().replace(
            "HYPERLIQUID_PRIVATE_KEY=", "HYPERLIQUID_PRIVATE_KEY=0x" + "5" * 64))
        before = _snap(w.env_file)
        out = json.loads(cw.handle_wallet_connect({"open_browser": False}))
        result_texts.append(json.dumps(out))
        ok = out.get("code") == "key_exists" and "url" not in out and _snap(w.env_file) == before \
            and ("5" * 64) not in json.dumps(out)
        rows.append(_row("existing key", out.get("code"), "key_exists, no page", ok))
    finally:
        w.close()

    w = World(tmp, monkeypatch, "mainnet", "two")
    try:
        a = w.flow()
        secrets_seen.append(a._ConnectFlow__key)
        try:
            w.flow()
            second = "started"
        except cw.ConnectError as e:
            second = e.code
        a._cleanup()
        rows.append(_row("two flows on one env file", second, "busy", second == "busy"))
    finally:
        w.close()

    # ── 4. the page ──
    page = cw.render_page(network="mainnet", agent_address="0x" + "a" * 40,
                          typed_data={"domain": {}, "message": {}}, submit_path="/t/submit")
    external = len(re.findall(r"<(?:script|link|style|img|iframe)[^>]*\s(?:src|href)\s*=", page, re.I)) \
        + len(re.findall(r"https?://", page))
    rows.append(_row("page loads external script or style", external, 0, external == 0))
    csp_ok = (cw._csp_hash(cw.PAGE_SCRIPT) in cw.CSP and cw._csp_hash(cw.PAGE_STYLE) in cw.CSP
              and "default-src 'none'" in cw.CSP and "unsafe" not in cw.CSP)
    rows.append(_row("page CSP pins the inline script and style", "yes" if csp_ok else "no", "yes", csp_ok))

    # ── 5. the key, everywhere ──
    out, err = capfd.readouterr()
    leaks_http = sum(1 for k in secrets_seen for text in http_texts if F.contains_key(text, k))
    leaks_streams = sum(1 for k in secrets_seen if F.contains_key(out + err, k))
    leaks_results = sum(1 for k in secrets_seen for text in result_texts if F.contains_key(text, k))
    rows.append(_row("agent key in HTTP responses", leaks_http, 0, leaks_http == 0))
    rows.append(_row("agent key in stdout/stderr", leaks_streams, 0, leaks_streams == 0))
    rows.append(_row("agent key in tool and flow results", leaks_results, 0, leaks_results == 0))
    rows.append(_row("agent keys checked", len(secrets_seen), ">= 7", len(secrets_seen) >= 7))
    return rows


def test_eval_wallet_connect_meets_every_target(tmp_path, monkeypatch, capfd, scorecard):
    from services.accounts import connect_wallet as cw
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    cw.reset_for_tests()
    try:
        rows = run(tmp_path, monkeypatch, capfd)
    finally:
        cw.reset_for_tests()
    card = scorecard("wallet_connect", rows)
    failed = [r for r in card["rows"] if not r["ok"]]
    assert not failed, f"wallet connect eval: {card['passed']}/{card['total']}; failed: {failed}"


@pytest.mark.parametrize("bad", ["not-json", ""])
def test_the_eval_browser_fails_loudly_on_a_page_without_config(bad):
    with pytest.raises(AssertionError):
        F.page_config(bad)
