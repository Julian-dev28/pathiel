"""services.accounts.connect_wallet: the plugin's wallet connect flow.

Gate tests, offline: every venue call goes to services/accounts/tests/
wallet_fakes.FakeVenue, which judges the signature with the installed SDK's
own recover function. The page is driven by a urllib "browser" on 127.0.0.1.
Env files live in tmp_path; nothing here reads ~/.pathiel or a checkout's
.env.local.
"""
from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
from pathlib import Path

import pytest
from eth_account import Account

import contracts.wallet_connect as contract
from services.accounts import agent_approval as aa
from services.accounts import connect_wallet as cw
from services.accounts.tests.wallet_fakes import (FakeVenue, browser_connect, contains_key, http,
                                                  page_config)

TEMPLATE = (
    "# pathiel keys\n"
    "# PATHIEL_NETWORK=testnet\n"
    "HYPERLIQUID_WALLET_ADDRESS=\n"
    "HYPERLIQUID_PRIVATE_KEY=\n"
    "HYPERLIQUID_MASTER_ADDRESS=\n"
    "\n"
    "EIA_API_KEY=abc123\n"
)


@pytest.fixture(autouse=True)
def _public(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    monkeypatch.setenv("PATHIEL_NETWORK", "mainnet")
    monkeypatch.delenv("PATHIEL_NETWORK_SOURCE", raising=False)
    cw.reset_for_tests()
    yield
    cw.reset_for_tests()


@pytest.fixture
def env_file(tmp_path):
    p = tmp_path / "home" / ".pathiel" / ".env.local"
    p.parent.mkdir(parents=True)
    p.write_text(TEMPLATE, encoding="utf-8")
    os.chmod(p, 0o644)
    return p


def _flow(env_file, venue=None, **kw):
    kw.setdefault("confirm_wait_s", 0)
    return cw.ConnectFlow(env_file, venue=venue if venue is not None else FakeVenue(), **kw)


def _run(flow):
    """Start serving in a thread; returns (thread, url)."""
    url = flow.start()
    t = threading.Thread(target=flow.serve, daemon=True)
    t.start()
    return t, url


def _key_of(flow) -> str:
    return flow._ConnectFlow__key


def _snapshot(path: Path):
    st = path.stat()
    return path.read_bytes(), st.st_mtime_ns, stat.S_IMODE(st.st_mode)


# ── operator install: refused before any file is touched ─────────────────────

def test_operator_install_refuses_before_reading_or_writing(env_file, monkeypatch):
    monkeypatch.delenv("PATHIEL_INSTALL")
    before = _snapshot(env_file)
    monkeypatch.setattr(cw, "_read_env", lambda p: pytest.fail("read the env file on operator"))
    monkeypatch.setattr(cw, "acquire_lock", lambda *a, **k: pytest.fail("took a lock on operator"))
    with pytest.raises(cw.ConnectError) as e:
        cw.ConnectFlow(env_file, venue=FakeVenue())
    assert e.value.code == "operator_install"
    assert _snapshot(env_file) == before
    assert sorted(p.name for p in env_file.parent.iterdir()) == [".env.local"]


def test_operator_install_mcp_handler_refuses(env_file, monkeypatch):
    monkeypatch.delenv("PATHIEL_INSTALL")
    monkeypatch.setenv("PATHIEL_ENV_FILE", str(env_file))
    before = _snapshot(env_file)
    for params in ({}, {"replace": True}, {"status": True}, {"cancel": True}):
        for handler in (contract.mcp_handler(), cw.handle_wallet_connect):
            out = json.loads(handler(params))
            assert out["ok"] is False and out["code"] == "operator_install", (params, out)
    assert _snapshot(env_file) == before
    assert sorted(p.name for p in env_file.parent.iterdir()) == [".env.local"]


def test_checkout_env_file_is_refused(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "scripts" / "pathiel-mcp-server.py").write_text("# server\n")
    env = checkout / ".env.local"
    env.write_text("HYPERLIQUID_PRIVATE_KEY=\n")
    before = _snapshot(env)
    with pytest.raises(cw.ConnectError) as e:
        _flow(env)
    assert e.value.code == "repo_env_file"
    assert _snapshot(env) == before
    code_root = Path(cw.__file__).resolve().parents[2] / ".env.local"
    assert cw._is_checkout_env_file(code_root)


# ── preflight ────────────────────────────────────────────────────────────────

def test_network_mismatch_when_the_file_changed_after_start(env_file, monkeypatch):
    env_file.write_text(TEMPLATE.replace("# PATHIEL_NETWORK=testnet", "PATHIEL_NETWORK=testnet"))
    with pytest.raises(cw.ConnectError) as e:
        _flow(env_file)                              # process says mainnet, file says testnet
    assert e.value.code == "network_mismatch" and "Reconnect pathiel" in e.value.message
    monkeypatch.setenv("PATHIEL_NETWORK_SOURCE", "environment")   # the shell chose: it wins
    _flow(env_file)._cleanup()


def test_existing_key_is_refused_unless_replace(env_file):
    env_file.write_text(TEMPLATE.replace("HYPERLIQUID_PRIVATE_KEY=", "HYPERLIQUID_PRIVATE_KEY=0x" + "1" * 64))
    before = _snapshot(env_file)
    with pytest.raises(cw.ConnectError) as e:
        _flow(env_file)
    assert e.value.code == "key_exists" and "1" * 64 not in e.value.message
    assert _snapshot(env_file) == before
    _flow(env_file, replace=True)._cleanup()


def test_two_flows_on_one_env_file_the_second_is_busy(env_file):
    first = _flow(env_file)
    with pytest.raises(cw.ConnectError) as e:
        _flow(env_file)
    assert e.value.code == "busy"
    first._cleanup()
    _flow(env_file)._cleanup()                       # released: a new one may start


def test_a_stale_lock_from_a_dead_process_is_taken_over(env_file):
    lock = cw._lock_path(env_file)
    lock.write_text(json.dumps({"pid": 2 ** 22 + 12345, "created": time.time()}))
    flow = _flow(env_file)
    assert json.loads(lock.read_text())["pid"] == os.getpid()
    flow._cleanup()
    assert not lock.exists()


# ── the env file ─────────────────────────────────────────────────────────────

def test_merge_keeps_every_other_line_and_sets_the_three_in_place():
    vals = {"HYPERLIQUID_PRIVATE_KEY": "0xK", "HYPERLIQUID_WALLET_ADDRESS": "0xA",
            "HYPERLIQUID_MASTER_ADDRESS": "0xM"}
    out = cw.merged_env_text(TEMPLATE, vals, "header")
    assert out == TEMPLATE.replace("HYPERLIQUID_WALLET_ADDRESS=", "HYPERLIQUID_WALLET_ADDRESS=0xA") \
        .replace("HYPERLIQUID_PRIVATE_KEY=", "HYPERLIQUID_PRIVATE_KEY=0xK") \
        .replace("HYPERLIQUID_MASTER_ADDRESS=", "HYPERLIQUID_MASTER_ADDRESS=0xM")
    assert "header" not in out                      # nothing appended: every key had a line


def test_merge_appends_missing_drops_duplicates_keeps_export_and_crlf():
    vals = {"HYPERLIQUID_PRIVATE_KEY": "0xK", "HYPERLIQUID_WALLET_ADDRESS": "0xA",
            "HYPERLIQUID_MASTER_ADDRESS": "0xM"}
    old = ("FOO=1\r\nexport HYPERLIQUID_PRIVATE_KEY=old\r\n# HYPERLIQUID_WALLET_ADDRESS=commented\r\n"
           "HYPERLIQUID_PRIVATE_KEY=older\r\nBAR=2")
    out = cw.merged_env_text(old, vals, "added")
    assert out.startswith("FOO=1\r\nexport HYPERLIQUID_PRIVATE_KEY=0xK\r\n"
                          "# HYPERLIQUID_WALLET_ADDRESS=commented\r\nBAR=2\r\n")
    assert "older" not in out and "old\r" not in out
    assert out.endswith("# added\r\nHYPERLIQUID_WALLET_ADDRESS=0xA\r\nHYPERLIQUID_MASTER_ADDRESS=0xM\r\n")
    assert cw.env_values(out)["HYPERLIQUID_PRIVATE_KEY"] == "0xK"


def test_merge_into_an_empty_or_missing_file():
    out = cw.merged_env_text("", {"A": "1", "B": "2"}, "hdr")
    assert out == "# hdr\nA=1\nB=2\n"


def test_write_is_mode_600_atomic_and_refuses_an_existing_key(env_file):
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o644
    replaced = cw.write_credentials(env_file, "0x" + "ab" * 32, "0x" + "a" * 40, "0x" + "b" * 40,
                                    replace=False, network="mainnet")
    assert replaced is False
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert sorted(p.name for p in env_file.parent.iterdir()) == [".env.local"]   # no temp left
    text = env_file.read_text()
    assert "EIA_API_KEY=abc123" in text and "# PATHIEL_NETWORK=testnet" in text
    with pytest.raises(cw.ConnectError) as e:
        cw.write_credentials(env_file, "0x" + "cd" * 32, "0x" + "c" * 40, "0x" + "d" * 40,
                             replace=False, network="mainnet")
    assert e.value.code == "key_exists"
    assert ("ab" * 32) in env_file.read_text() and ("cd" * 32) not in env_file.read_text()
    assert cw.write_credentials(env_file, "0x" + "cd" * 32, "0x" + "c" * 40, "0x" + "d" * 40,
                                replace=True, network="mainnet") is True
    assert ("cd" * 32) in env_file.read_text() and ("ab" * 32) not in env_file.read_text()


def test_write_creates_a_missing_file_mode_600(tmp_path):
    p = tmp_path / "new" / ".env.local"
    cw.write_credentials(p, "0x" + "ab" * 32, "0x" + "a" * 40, "0x" + "b" * 40,
                         replace=False, network="testnet")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700


def test_write_through_a_symlink_keeps_the_link(env_file, tmp_path):
    link = tmp_path / "link.env"
    link.symlink_to(env_file)
    cw.write_credentials(link, "0x" + "ab" * 32, "0x" + "a" * 40, "0x" + "b" * 40,
                         replace=False, network="mainnet")
    assert link.is_symlink() and ("ab" * 32) in env_file.read_text()


# ── the full flow against the fake venue ────────────────────────────────────

@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_full_flow_writes_the_agent_and_posts_once(env_file, monkeypatch, network):
    monkeypatch.setenv("PATHIEL_NETWORK", network)
    monkeypatch.setenv("PATHIEL_NETWORK_SOURCE", "environment")
    venue = FakeVenue(network=network)
    flow = _flow(env_file, venue)
    key = _key_of(flow)
    t, url = _run(flow)
    master = Account.create()
    code, reply, page = browser_connect(url, master, chain_id=1)
    t.join(10)
    assert code == 200 and reply["ok"] is True, reply
    assert reply["master_address"].lower() == master.address.lower()
    assert "env_file" not in reply                      # the page gets nothing it does not show
    assert len(venue.exchange_posts) == 1
    action = venue.exchange_posts[0]["action"]
    assert action == {"type": "approveAgent", "agentAddress": flow.agent_address,
                      "agentName": "pathiel-plugin", "nonce": action["nonce"], "signatureChainId": "0x1",
                      "hyperliquidChain": "Mainnet" if network == "mainnet" else "Testnet"}
    assert venue.recovered == [master.address.lower()]
    vals = cw.env_values(env_file.read_text())
    assert vals["HYPERLIQUID_PRIVATE_KEY"] == key
    assert Account.from_key(key).address == flow.agent_address
    assert vals["HYPERLIQUID_WALLET_ADDRESS"] == flow.agent_address
    assert vals["HYPERLIQUID_MASTER_ADDRESS"].lower() == master.address.lower()
    assert vals["EIA_API_KEY"] == "abc123"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    res = flow.result()
    assert res["state"] == "done" and contract.result_problems(res, (key,)) == []
    for text in (json.dumps(reply), page, json.dumps(res)):
        assert not contains_key(text, key)
    assert not cw._lock_path(env_file).exists()
    assert _key_of(flow) is None                         # dropped from memory


def test_signature_from_another_account_is_refused_and_nothing_is_sent(env_file):
    venue = FakeVenue()
    flow = _flow(env_file, venue)
    before = _snapshot(env_file)
    t, url = _run(flow)
    signer, claimed = Account.create(), Account.create()
    code, reply, _ = browser_connect(url, signer, report_address=claimed.address)
    t.join(10)
    assert reply["ok"] is False and reply["code"] == "signer_mismatch", reply
    assert venue.exchange_posts == []
    assert _snapshot(env_file) == before


@pytest.mark.parametrize("mode,code", [("refuse", "venue_refused"), ("silent", "not_confirmed"),
                                       ("unreachable", "venue_unreachable")])
def test_a_venue_failure_writes_nothing(env_file, mode, code):
    venue = FakeVenue(mode=mode)
    flow = _flow(env_file, venue, confirm_tries=2)
    key = _key_of(flow)
    before = _snapshot(env_file)
    t, url = _run(flow)
    _c, reply, _ = browser_connect(url, Account.create())
    t.join(10)
    assert reply["ok"] is False and reply["code"] == code, reply
    assert len(venue.exchange_posts) == 1                # posted once, never retried
    assert _snapshot(env_file) == before
    assert not contains_key(json.dumps(reply) + json.dumps(flow.result()), key)
    assert flow.result()["state"] == "failed"


def test_a_dropped_reply_after_the_venue_stored_it_still_connects(env_file):
    venue = FakeVenue(mode="lands_then_drops")
    flow = _flow(env_file, venue)
    t, url = _run(flow)
    _c, reply, _ = browser_connect(url, Account.create())
    t.join(10)
    assert reply["ok"] is True, reply                  # the /info read decides
    assert len(venue.exchange_posts) == 1


def test_a_key_added_while_the_page_was_open_stops_before_sending(env_file):
    venue = FakeVenue()
    flow = _flow(env_file, venue)
    t, url = _run(flow)
    env_file.write_text(TEMPLATE.replace("HYPERLIQUID_PRIVATE_KEY=", "HYPERLIQUID_PRIVATE_KEY=0x" + "2" * 64))
    _c, reply, _ = browser_connect(url, Account.create())
    t.join(10)
    assert reply["code"] == "key_exists" and venue.exchange_posts == []
    assert ("2" * 64) in env_file.read_text()


# ── the local server ─────────────────────────────────────────────────────────

def test_wrong_token_wrong_host_and_wrong_origin_are_refused(env_file):
    venue = FakeVenue()
    flow = _flow(env_file, venue)
    t, url = _run(flow)
    root = f"http://127.0.0.1:{flow.port}"
    try:
        assert http("GET", root + "/" + "x" * 43 + "/")[0] == 404
        assert http("GET", root + "/")[0] == 404
        assert http("GET", url, headers={"Host": "evil.example:%d" % flow.port})[0] == 421
        assert http("GET", url, headers={"Host": "127.0.0.1"})[0] == 421
        assert http("GET", url, headers={"Host": f"localhost:{flow.port}"})[0] == 200
        status, headers, page = http("GET", url)
        submit = root + page_config(page)["submitPath"]
        body = json.dumps({"address": "0x" + "1" * 40, "signature": "0x" + "1" * 130,
                           "chainId": 1}).encode()
        ct = {"Content-Type": "application/json"}
        assert http("POST", submit, body=body, headers=ct)[0] == 403                      # no Origin
        assert http("POST", submit, body=body, headers={**ct, "Origin": "http://evil.example"})[0] == 403
        assert http("POST", submit, body=body,
                    headers={**ct, "Origin": f"http://localhost:{flow.port}"})[0] == 403  # Host is 127.0.0.1
        assert http("POST", submit, body=body,
                    headers={**ct, "Origin": root, "Host": "evil.example"})[0] == 421
        assert http("POST", root + "/wrong/submit", body=body, headers={**ct, "Origin": root})[0] == 404
        big = b"{" + b" " * (cw.MAX_BODY_BYTES + 10) + b"}"
        assert http("POST", submit, body=big, headers={**ct, "Origin": root})[0] == 413
        assert flow.state == "waiting"                  # none of those used the page
        assert venue.exchange_posts == []
    finally:
        flow.cancel()
        t.join(5)


def test_the_page_is_single_use(env_file):
    gate = threading.Event()
    venue = FakeVenue()
    real_exchange = venue.exchange

    def slow_exchange(payload):
        gate.wait(10)
        return real_exchange(payload)

    venue.exchange = slow_exchange
    flow = _flow(env_file, venue)
    t, url = _run(flow)
    results = {}
    first = threading.Thread(target=lambda: results.update(first=browser_connect(url, Account.create())))
    first.start()
    for _ in range(200):
        if flow.state == "working":
            break
        time.sleep(0.01)
    assert flow.state == "working"
    root = f"http://127.0.0.1:{flow.port}"
    assert http("GET", url)[0] == 410                       # page gone while the first is in flight
    body = json.dumps({"address": "0x" + "1" * 40, "signature": "0x" + "1" * 130, "chainId": 1}).encode()
    code, _h, text = http("POST", url + "submit", body=body,
                          headers={"Content-Type": "application/json", "Origin": root})
    assert code == 409 and "already used" in text
    gate.set()
    first.join(10)
    t.join(10)
    assert results["first"][1]["ok"] is True
    assert flow.state == "done"
    with pytest.raises(OSError):
        http("GET", url, timeout=2)                         # the server is closed after use


def test_the_page_times_out_and_writes_nothing(env_file):
    flow = _flow(env_file, timeout_s=0.3)
    before = _snapshot(env_file)
    url = flow.start()
    res = flow.serve()
    assert res["ok"] is False and res["code"] == "timeout" and res["state"] == "failed"
    assert _snapshot(env_file) == before
    assert not cw._lock_path(env_file).exists()
    with pytest.raises(OSError):
        http("GET", url, timeout=2)


def test_cancel_stops_a_waiting_flow(env_file):
    flow = _flow(env_file)
    t, url = _run(flow)
    assert flow.cancel() is True
    t.join(5)
    assert flow.result()["state"] == "cancelled" and flow.result()["code"] == "cancelled"
    assert flow.cancel() is False


# ── the page ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_page_has_the_csp_no_external_code_and_the_plain_words(env_file, monkeypatch, network):
    monkeypatch.setenv("PATHIEL_NETWORK", network)
    monkeypatch.setenv("PATHIEL_NETWORK_SOURCE", "environment")
    flow = _flow(env_file)
    key = _key_of(flow)
    t, url = _run(flow)
    try:
        status, headers, page = http("GET", url)
    finally:
        flow.cancel()
        t.join(5)
    assert status == 200
    csp = headers["Content-Security-Policy"]
    assert csp == cw.CSP
    for directive in ("default-src 'none'", "connect-src 'self'", "frame-ancestors 'none'",
                      "base-uri 'none'", "form-action 'none'"):
        assert directive in csp
    assert "unsafe-inline" not in csp and "http" not in csp
    assert cw._csp_hash(cw.PAGE_SCRIPT) in csp and cw._csp_hash(cw.PAGE_STYLE) in csp
    assert f"<script>{cw.PAGE_SCRIPT}</script>" in page and f"<style>{cw.PAGE_STYLE}</style>" in page
    assert not re.search(r"<(script|link|img|iframe)[^>]*\s(src|href)\s*=", page, re.I)
    assert not re.search(r"https?://", page)
    assert headers["X-Frame-Options"] == "DENY" and headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "This key can trade but never withdraw." in page
    assert "pathiel never sees your main key." in page
    assert flow.agent_address in page
    if network == "mainnet":
        assert '<span class="net mainnet">MAINNET</span>' in page and "--red" in page
    else:
        assert '<span class="net testnet">TESTNET</span>' in page
    assert "eip6963:requestProvider" in page and "window.ethereum" in page
    assert "eth_signTypedData_v4" in page and "eth_chainId" in page
    cfg = page_config(page)
    assert cfg["typedData"]["message"]["hyperliquidChain"] == ("Mainnet" if network == "mainnet" else "Testnet")
    assert cfg["typedData"]["message"]["agentAddress"] == flow.agent_address
    assert not contains_key(page, key) and not contains_key(json.dumps(dict(headers)), key)


def test_page_copy_has_no_em_dash():
    page = cw.render_page(network="mainnet", agent_address="0x" + "a" * 40,
                          typed_data=aa.build_typed_data("0x" + "a" * 40, 1), submit_path="/t/submit")
    assert "—" not in page


# ── the MCP tool ─────────────────────────────────────────────────────────────

def test_mcp_tool_starts_reports_and_finishes(env_file, monkeypatch, capfd):
    monkeypatch.setenv("PATHIEL_ENV_FILE", str(env_file))
    venue = FakeVenue()
    opened = []
    monkeypatch.setattr(cw, "_VENUE_FACTORY", lambda: venue)
    monkeypatch.setattr(cw, "_OPENER", lambda url: opened.append(url) or True)
    monkeypatch.setattr(cw, "CONFIRM_WAIT_S", 0)
    handler = contract.mcp_handler()
    assert json.loads(handler({"status": True}))["state"] == "none"
    started = json.loads(handler({}))
    assert started["ok"] is True and started["state"] == "waiting" and opened == [started["url"]]
    again = json.loads(handler({}))
    assert again["url"] == started["url"] and "already in progress" in again["next"]
    flow = cw._CURRENT
    key = _key_of(flow)
    _c, reply, _ = browser_connect(started["url"], Account.create())
    for _ in range(300):
        done = json.loads(handler({"status": True}))
        if done["state"] == "done":
            break
        time.sleep(0.01)
    assert done["ok"] is True and done["state"] == "done", done
    assert "Reconnect pathiel in /mcp" in done["next"]
    assert contract.result_problems(done, (key,)) == []
    out, err = capfd.readouterr()
    for text in (out, err, json.dumps(started), json.dumps(again), json.dumps(reply)):
        assert not contains_key(text, key)
    assert out == ""                                      # stdout is the MCP stream
    # a finished flow does not block a new one; the key now set does
    refused = json.loads(handler({}))
    assert refused["ok"] is False and refused["code"] == "key_exists"
    assert "replace=true" in refused["next"]


def test_mcp_tool_cancel_and_open_browser_false(env_file, monkeypatch):
    monkeypatch.setenv("PATHIEL_ENV_FILE", str(env_file))
    monkeypatch.setattr(cw, "_VENUE_FACTORY", lambda: FakeVenue())
    opened = []
    monkeypatch.setattr(cw, "_OPENER", lambda url: opened.append(url) or True)
    started = json.loads(cw.handle_wallet_connect({"open_browser": False}))
    assert started["state"] == "waiting" and opened == []
    cancelled = json.loads(cw.handle_wallet_connect({"cancel": True}))
    assert cancelled["state"] == "cancelled"
    assert json.loads(cw.handle_wallet_connect({"cancel": True}))["next"] == "Nothing waiting to cancel."


def test_contract_result_problems_catches_a_key():
    assert contract.result_problems({"ok": True, "state": "done"}) == []
    assert contract.result_problems({"ok": True, "state": "done", "error": "0x" + "ab" * 32})
    assert contract.result_problems({"ok": False, "state": "failed", "code": "nope"})
    assert contract.result_problems({"ok": True, "state": "done", "extra": 1})
    assert contract.result_problems({"ok": True, "state": "done", "next": "abc"}, ("0xABC",))


def test_serve_after_close_waits_for_an_in_flight_submission(env_file):
    """Ctrl-C on the CLI while the venue call runs: the second serve() must
    return the real result, not 'working'."""
    gate = threading.Event()
    venue = FakeVenue()
    real = venue.exchange
    venue.exchange = lambda p: (gate.wait(10), real(p))[1]
    flow = _flow(env_file, venue)
    t, url = _run(flow)
    th = threading.Thread(target=lambda: browser_connect(url, Account.create()), daemon=True)
    th.start()
    for _ in range(300):
        if flow.state == "working":
            break
        time.sleep(0.01)
    flow._httpd.server_close()
    flow._httpd = None                      # what the first serve()'s finally leaves behind
    threading.Timer(0.2, gate.set).start()
    res = flow.serve()
    assert res["state"] == "done" and res["ok"] is True
    th.join(5)
    t.join(5)
