"""scripts/approve_builder_fee.py: the one-time builder fee approval.

Offline. Every key is a throwaway from eth_account.Account.create(), every
post is a recorder, and the one subprocess runs a dry run under the public_env
fixture (dead proxies, a temp env file). Signatures are checked with the
installed SDK's own recover_user_from_user_signed_action.

    .venv/bin/python -m pytest -q tests/test_approve_builder_fee.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from eth_account import Account
from hyperliquid.utils import signing

import contracts.runtime as runtime
from pathiel.client import hl_client

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "approve_builder_fee.py"
_spec = importlib.util.spec_from_file_location("approve_builder_fee", SCRIPT)
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)

BUILDER = "0xB1b1B1b1b1B1b1b1B1B1b1b1b1b1b1b1b1B1b1B1"
BUILDER_LC = BUILDER.lower()
TYPES = [{"name": "hyperliquidChain", "type": "string"},
         {"name": "maxFeeRate", "type": "string"},
         {"name": "builder", "type": "address"},
         {"name": "nonce", "type": "uint64"}]
PRIMARY = "HyperliquidTransaction:ApproveBuilderFee"
OK_REPLY = {"status": "ok", "response": {"type": "default"}}


class Post:
    def __init__(self, reply=None, raises=None):
        self.calls = []
        self.reply = OK_REPLY if reply is None else reply
        self.raises = raises

    def __call__(self, path, payload):
        self.calls.append((path, copy.deepcopy(payload)))
        if self.raises:
            raise self.raises
        return self.reply


class Prompt:
    def __init__(self, answer=""):
        self.calls = 0
        self.answer = answer

    def __call__(self, prompt):
        self.calls += 1
        return self.answer


def _never(*_a, **_k):
    raise AssertionError("must not be called")


@pytest.fixture
def master(monkeypatch):
    """A fresh master wallet as the trading account, a builder address,
    operator install on mainnet, no env key, no env file read."""
    acct = Account.create()
    monkeypatch.setattr("_state_env.load_env_local", lambda *a, **k: None)
    for k in runtime.RUNTIME_ENV_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", BUILDER)
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", acct.address)
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", "")
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", "")
    monkeypatch.setattr(hl_client, "HL_API", runtime.MAINNET_API_URL)
    return acct


def _net(monkeypatch, network=None, public=False):
    if public:
        monkeypatch.setenv("PATHIEL_INSTALL", "public")
    if network:
        monkeypatch.setenv("PATHIEL_NETWORK", network)
    monkeypatch.setattr(hl_client, "HL_API", runtime.api_url())


def _run(capsys, argv, **kw):
    kw.setdefault("post", _never)
    kw.setdefault("fetch_max_fee", _never)
    kw.setdefault("getpass_fn", _never)
    kw.setdefault("isatty", lambda: True)
    code = ab.main(["--json", *argv], **kw)
    cap = capsys.readouterr()
    lines = [ln for ln in cap.out.splitlines() if ln.strip()]
    return code, (json.loads(lines[-1]) if lines else {}), cap.out + cap.err, cap.err


def _key_texts(acct):
    raw = acct.key.hex().removeprefix("0x")
    return (raw, "0x" + raw)


def _assert_no_key(text, *accts):
    for acct in accts:
        for k in _key_texts(acct):
            assert k not in text


def _recover(payload, is_mainnet):
    return signing.recover_user_from_user_signed_action(
        dict(payload["action"]), payload["signature"], TYPES, PRIMARY, is_mainnet)


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_build_action_exact_shape():
    a = ab.build_action(BUILDER, "0.01%", 1_794_288_221_486)
    assert a == {"maxFeeRate": "0.01%", "builder": BUILDER_LC, "nonce": 1_794_288_221_486,
                 "type": "approveBuilderFee"}
    assert list(a) == ["maxFeeRate", "builder", "nonce", "type"]   # the SDK's own order


@pytest.mark.parametrize("rate,tenths", [("0.01%", 10), ("0.001%", 1), ("0.1%", 100), ("0%", 0),
                                          ("0.010%", 10), ("1%", 1000)])
def test_parse_rate_table(rate, tenths):
    assert ab.parse_rate(rate) == tenths


@pytest.mark.parametrize("bad", ["0.0005%", "1", "abc", "-0.01%", "0.01", "%", "", "1e-2%"])
def test_parse_rate_rejects(bad):
    with pytest.raises(ValueError):
        ab.parse_rate(bad)


def test_trading_account_prefers_master_then_wallet():
    assert ab.trading_account({"HYPERLIQUID_MASTER_ADDRESS": "0xAB" + "0" * 38,
                               "HYPERLIQUID_WALLET_ADDRESS": "0xCD" + "0" * 38}) == "0xab" + "0" * 38
    assert ab.trading_account({"HYPERLIQUID_MASTER_ADDRESS": " ",
                               "HYPERLIQUID_WALLET_ADDRESS": "0xCD" + "0" * 38}) == "0xcd" + "0" * 38
    assert ab.trading_account({}) == ""


def test_fee_example_dollars():
    assert ab.fee_example(10) == "100 USD of notional costs 0.01 USD per fill"
    assert ab.fee_example(1) == "100 USD of notional costs 0.001 USD per fill"
    assert ab.fee_example(0) == "100 USD of notional costs 0 USD per fill"


# ── dry run ───────────────────────────────────────────────────────────────────

def test_dry_run_sends_nothing_and_needs_no_key(master, monkeypatch, capsys):
    _net(monkeypatch, public=True)                      # public default: testnet
    monkeypatch.setattr(ab, "_master_key", _never)      # no key source is even consulted
    code, out, text, _err = _run(capsys, [], isatty=_never)
    assert code == 0
    assert out["dry_run"] is True
    assert out["network"] == "testnet"
    assert out["account"] == master.address.lower()
    assert out["builder"] == BUILDER_LC
    assert out["max_fee_rate"] == "0.01%"
    assert out["example"] == "100 USD of notional costs 0.01 USD per fill"
    assert out["action"]["type"] == "approveBuilderFee" and out["action"]["maxFeeRate"] == "0.01%"
    _assert_no_key(text, master)


def test_dry_run_human_output_names_everything(master, monkeypatch, capsys):
    assert ab.main([], post=_never, fetch_max_fee=_never, getpass_fn=_never) == 0
    out = capsys.readouterr().out
    for word in ("DRY RUN", "mainnet", BUILDER_LC, master.address.lower(), "0.01%", "--yes"):
        assert word in out


def test_dry_run_runs_standalone_in_a_public_env(public_env):
    acct = Account.create()
    env = public_env(PATHIEL_BUILDER_ADDRESS=BUILDER, HYPERLIQUID_MASTER_ADDRESS=acct.address)
    proc = subprocess.run([sys.executable, str(SCRIPT), "--json"], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["dry_run"] is True and out["network"] == "testnet"
    assert out["builder"] == BUILDER_LC and out["max_fee_rate"] == "0.01%"


# ── --yes ─────────────────────────────────────────────────────────────────────

def test_yes_signs_for_testnet_and_recovers_to_the_account(master, monkeypatch, capsys):
    _net(monkeypatch, public=True)
    post, prompt = Post(), Prompt(master.key.hex())
    code, out, text, _ = _run(capsys, ["--yes"], post=post, getpass_fn=prompt,
                              fetch_max_fee=lambda u, b: 10)
    assert code == 0 and out["ok"] is True and out["network"] == "testnet"
    assert prompt.calls == 1
    assert len(post.calls) == 1
    path, p = post.calls[0]
    assert path == "/exchange"
    a = p["action"]
    expect = ab.build_action(BUILDER, "0.01%", p["nonce"])
    assert {k: a[k] for k in expect} == expect
    assert a["hyperliquidChain"] == "Testnet" and a["signatureChainId"] == "0x66eee"
    assert set(a) == set(expect) | {"hyperliquidChain", "signatureChainId"}
    assert p["vaultAddress"] is None and p["expiresAfter"] is None and p["nonce"] == a["nonce"]
    assert _recover(p, False).lower() == master.address.lower()
    assert _recover(p, True).lower() != master.address.lower()   # not valid on mainnet
    _assert_no_key(text + json.dumps(p), master)


def test_yes_signs_for_mainnet_chain_field(master, monkeypatch, capsys):
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", master.key.hex())
    post = Post()
    code, out, _text, _ = _run(capsys, ["--yes"], post=post, fetch_max_fee=lambda u, b: 10)
    assert code == 0 and out["network"] == "mainnet"
    _path, p = post.calls[0]
    assert p["action"]["hyperliquidChain"] == "Mainnet"
    assert _recover(p, True).lower() == master.address.lower()


def test_public_install_ignores_the_env_key_and_prompts(master, monkeypatch, capsys):
    other = Account.create()
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", other.key.hex())
    _net(monkeypatch, public=True)
    post, prompt = Post(), Prompt(master.key.hex())
    code, _out, text, _ = _run(capsys, ["--yes"], post=post, getpass_fn=prompt,
                               fetch_max_fee=lambda u, b: 10)
    assert code == 0 and prompt.calls == 1 and len(post.calls) == 1
    assert _recover(post.calls[0][1], False).lower() == master.address.lower()
    _assert_no_key(text, master, other)


def test_public_install_warns_when_the_env_key_is_set(master, monkeypatch, capsys):
    other = Account.create()
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", other.key.hex())
    _net(monkeypatch, public=True)
    _code, _out, text, err = _run(capsys, ["--yes"], post=Post(), getpass_fn=Prompt(master.key.hex()),
                                  fetch_max_fee=lambda u, b: 10)
    warns = [ln for ln in err.splitlines() if ln.startswith("WARN")]
    assert len(warns) == 1 and "HYPERLIQUID_MASTER_PRIVATE_KEY" in warns[0]
    _assert_no_key(text, master, other)


def test_operator_install_uses_the_env_key(master, monkeypatch, capsys):
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", master.key.hex())
    post = Post()
    code, _out, text, err = _run(capsys, ["--yes"], post=post, getpass_fn=_never,
                                 isatty=lambda: False, fetch_max_fee=lambda u, b: 10)
    assert code == 0 and len(post.calls) == 1 and "WARN" not in err
    assert _recover(post.calls[0][1], True).lower() == master.address.lower()
    _assert_no_key(text, master)


def test_wallet_address_fallback_for_a_master_key_trader(master, monkeypatch, capsys):
    """A user who trades with the master key directly has only
    HYPERLIQUID_WALLET_ADDRESS; the same key approves."""
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", "")
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", master.address)
    _net(monkeypatch, public=True)
    post = Post()
    code, out, _t, _ = _run(capsys, ["--yes"], post=post, getpass_fn=Prompt(master.key.hex()),
                            fetch_max_fee=lambda u, b: 10)
    assert code == 0 and out["account"] == master.address.lower() and len(post.calls) == 1


def test_confirming_read_after_success(master, monkeypatch, capsys):
    _net(monkeypatch, public=True)
    reads = []
    code, out, _t, _ = _run(capsys, ["--yes"], post=Post(), getpass_fn=Prompt(master.key.hex()),
                            fetch_max_fee=lambda u, b: reads.append((u, b)) or 10)
    assert code == 0
    assert reads == [(master.address.lower(), BUILDER_LC)]
    assert out["approved_tenths_bp"] == 10


def test_confirming_read_failure_still_exits_0(master, monkeypatch, capsys):
    _net(monkeypatch, public=True)

    def boom(u, b):
        raise TimeoutError("slow")

    code, out, _t, _ = _run(capsys, ["--yes"], post=Post(), getpass_fn=Prompt(master.key.hex()),
                            fetch_max_fee=boom)
    assert code == 0 and out["ok"] is True and out["approved_tenths_bp"] is None
    assert "confirming read failed" in out["note"]


def test_venue_error_reply_exits_1(master, monkeypatch, capsys):
    _net(monkeypatch, public=True)
    post = Post(reply={"status": "err", "response": "Builder has insufficient balance to be approved."})
    code, out, text, _ = _run(capsys, ["--yes"], post=post, getpass_fn=Prompt(master.key.hex()),
                              fetch_max_fee=lambda u, b: 0)
    assert code == 1 and out["ok"] is False and len(post.calls) == 1
    assert "insufficient balance" in text and out["approved_tenths_bp"] == 0
    post2 = Post(raises=RuntimeError("HTTP POST /exchange failed: timeout"))

    def read_fails(u, b):
        raise TimeoutError("slow")

    code, out, _t, _ = _run(capsys, ["--yes"], post=post2, getpass_fn=Prompt(master.key.hex()),
                            fetch_max_fee=read_fails)
    assert code == 1 and out["ok"] is False and len(post2.calls) == 1
    assert "public_status.py --online" in out["note"]


def test_error_reply_with_the_approval_stored_reports_success(master, monkeypatch, capsys):
    """Final review: a 502 after the venue stored the approval used to be
    retried by the shared session's adapter; the replayed nonce was refused
    and the script printed FAILED. Now one post, then the read-back decides."""
    _net(monkeypatch, public=True)
    post = Post(reply={"status": "err", "response": "Invalid nonce: duplicate nonce"})
    reads = []
    code, out, _t, _ = _run(capsys, ["--yes"], post=post, getpass_fn=Prompt(master.key.hex()),
                            fetch_max_fee=lambda u, b: reads.append(1) or 10)
    assert code == 0 and out["ok"] is True and out["approved_tenths_bp"] == 10
    assert len(post.calls) == 1 and reads == [1]
    assert "Do not sign again" in out["note"]


@pytest.mark.parametrize("approved,tenths,want", [
    (None, 10, False), (0, 10, False), (5, 10, False), (10, 10, True), (25, 10, True),
    (0, 0, True), (10, 0, False),
])
def test_approval_in_place_table(approved, tenths, want):
    assert ab.approval_in_place(approved, tenths) is want


def test_default_post_is_one_plain_post_not_the_retrying_session(master, monkeypatch, capsys):
    import requests

    _net(monkeypatch, public=True)
    monkeypatch.setattr(hl_client, "_get_session", _never)
    monkeypatch.setattr(hl_client, "_http_post_strict", _never)
    sent = []

    class Resp:
        status_code = 502
        text = "Bad Gateway"

        def json(self):
            raise ValueError("not json")

    def fake_post(url, json=None, timeout=None):
        sent.append((url, timeout))
        return Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    code = ab.main(["--json", "--yes"], getpass_fn=Prompt(master.key.hex()),
                   isatty=lambda: True, fetch_max_fee=lambda u, b: 0)
    assert code == 1
    assert sent == [(f"{runtime.TESTNET_API_URL}/exchange",
                     (ab.POST_CONNECT_TIMEOUT_S, ab.POST_READ_TIMEOUT_S))]
    last = json.loads([ln for ln in capsys.readouterr().out.splitlines() if ln.strip()][-1])
    assert last["reply"]["response"].startswith("HTTP 502")


def test_revoke_rate_zero_builds_a_zero_action(master, monkeypatch, capsys):
    _net(monkeypatch, public=True)
    code, out, _t, _ = _run(capsys, ["--max-fee-rate", "0%"])
    assert code == 0 and out["action"]["maxFeeRate"] == "0%" and "revokes" in out["note"]
    post = Post()
    code, _out, _t, _ = _run(capsys, ["--max-fee-rate", "0%", "--yes"], post=post,
                             getpass_fn=Prompt(master.key.hex()), fetch_max_fee=lambda u, b: 0)
    assert code == 0 and post.calls[0][1]["action"]["maxFeeRate"] == "0%"
    assert _recover(post.calls[0][1], False).lower() == master.address.lower()


def test_rate_is_signed_in_canonical_form(master, monkeypatch, capsys):
    _net(monkeypatch, public=True)
    post = Post()
    _run(capsys, ["--max-fee-rate", "0.010%", "--yes"], post=post,
         getpass_fn=Prompt(master.key.hex()), fetch_max_fee=lambda u, b: 10)
    assert post.calls[0][1]["action"]["maxFeeRate"] == "0.01%"


# ── refusals: exit 1, nothing sent, no key material ──────────────────────────

def _assert_refused(code, out, text, post, *accts):
    assert code == 1
    assert out["ok"] is False and out["error"]
    assert post.calls == []
    _assert_no_key(text, *accts)


def test_refuses_without_a_builder_address(master, monkeypatch, capsys):
    monkeypatch.setattr(runtime, "DEFAULT_BUILDER_ADDRESS", "")
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", "")
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", master.key.hex())
    post = Post()
    code, out, text, _ = _run(capsys, ["--yes"], post=post)
    _assert_refused(code, out, text, post, master)
    assert "no builder address" in out["error"]


def test_refuses_when_builder_is_the_account(master, monkeypatch, capsys):
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", master.address)
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", master.key.hex())
    post = Post()
    code, out, text, _ = _run(capsys, ["--yes"], post=post)
    _assert_refused(code, out, text, post, master)
    assert "own trading account" in out["error"]


def test_refuses_a_rate_above_the_cap(master, monkeypatch, capsys):
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", master.key.hex())
    post = Post()
    code, out, text, _ = _run(capsys, ["--max-fee-rate", "0.101%", "--yes"], post=post)
    _assert_refused(code, out, text, post, master)
    assert "cap" in out["error"]
    code, out, _t, _ = _run(capsys, ["--max-fee-rate", "0.1%"])      # exactly the cap is fine
    assert code == 0


def test_refuses_on_network_drift(master, monkeypatch, capsys):
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")       # HL_API stays on mainnet
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", master.key.hex())
    post = Post()
    code, out, text, _ = _run(capsys, ["--yes"], post=post)
    _assert_refused(code, out, text, post, master)
    assert "network drift" in out["error"]
    code, out, _t, _ = _run(capsys, [])                     # the dry run refuses too
    assert code == 1 and "network drift" in out["error"]


def test_refuses_a_key_for_another_account(master, monkeypatch, capsys):
    """The user pastes the API wallet key instead of the master key."""
    api_wallet = Account.create()
    _net(monkeypatch, public=True)
    post = Post()
    code, out, text, _ = _run(capsys, ["--yes"], post=post, getpass_fn=Prompt(api_wallet.key.hex()))
    _assert_refused(code, out, text, post, master, api_wallet)
    assert api_wallet.address.lower() in out["error"]


def test_refuses_an_invalid_key(master, monkeypatch, capsys):
    _net(monkeypatch, public=True)
    post = Post()
    code, out, text, _ = _run(capsys, ["--yes"], post=post, getpass_fn=Prompt("0x1234nothex"))
    _assert_refused(code, out, text, post, master)
    assert "1234nothex" not in text


def test_refuses_without_key_or_tty(master, monkeypatch, capsys):
    # public, env key set, no TTY: the env key is not a fallback
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", master.key.hex())
    _net(monkeypatch, public=True)
    post = Post()
    code, out, text, _ = _run(capsys, ["--yes"], post=post, getpass_fn=_never, isatty=lambda: False)
    _assert_refused(code, out, text, post, master)
    assert "no master key" in out["error"]
    # operator, no env key, no TTY
    monkeypatch.setenv("PATHIEL_INSTALL", "operator")
    monkeypatch.setenv("PATHIEL_NETWORK", "mainnet")
    monkeypatch.setattr(hl_client, "HL_API", runtime.MAINNET_API_URL)
    monkeypatch.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", "")
    code, out, text, _ = _run(capsys, ["--yes"], post=post, getpass_fn=_never, isatty=lambda: False)
    _assert_refused(code, out, text, post, master)
    # an empty answer at the prompt
    code, out, _t, _ = _run(capsys, ["--yes"], post=post, getpass_fn=Prompt(""))
    assert code == 1 and post.calls == []


def test_refuses_without_a_trading_account_before_prompting(master, monkeypatch, capsys):
    monkeypatch.setenv("HYPERLIQUID_MASTER_ADDRESS", "")
    _net(monkeypatch, public=True)
    post = Post()
    code, out, text, _ = _run(capsys, ["--yes"], post=post, getpass_fn=_never)
    _assert_refused(code, out, text, post, master)
    assert "HYPERLIQUID_MASTER_ADDRESS" in out["error"]


def test_output_never_contains_the_key(master, monkeypatch, capsys):
    _net(monkeypatch, public=True)
    texts = []
    for argv, kw in ((["--yes"], {"post": Post(), "fetch_max_fee": lambda u, b: 10}),
                     (["--yes"], {"post": Post(reply={"status": "err", "response": "x"})}),
                     (["--yes"], {"post": Post(raises=RuntimeError("boom"))})):
        _c, _o, text, _ = _run(capsys, argv, getpass_fn=Prompt(master.key.hex()), **kw)
        texts.append(text)
    for human in (True, False):
        argv = ["--yes"] if human else ["--yes", "--json"]
        ab.main(argv, post=Post(), fetch_max_fee=lambda u, b: 10,
                getpass_fn=Prompt(master.key.hex()), isatty=lambda: True)
        cap = capsys.readouterr()
        texts.append(cap.out + cap.err)
    _assert_no_key("\n".join(texts), master)


def test_a_pasted_key_argument_is_never_echoed(master, capsys):
    for argv in ([master.key.hex()], ["--key", master.key.hex()], ["--yes", master.key.hex()]):
        assert ab.main(argv, post=_never, getpass_fn=_never) == 2
        cap = capsys.readouterr()
        _assert_no_key(cap.out + cap.err, master)


# ── the disclosure doc ────────────────────────────────────────────────────────

def test_builder_fee_doc_has_no_claims_or_banned_style(copy_lint):
    text = (ROOT / "docs" / "BUILDER_FEE.md").read_text(encoding="utf-8")
    assert copy_lint.claim_hits(text) == []
    assert copy_lint.style_hits(text) == []
    for must in ("scripts/approve_builder_fee.py", "0.01%", "0.02%", "0.1%",
                 "PATHIEL_BUILDER_FEE_TENTHS_BP=0", "--max-fee-rate 0%", "100 USDC",
                 "~/.pathiel/.env.local", "testnet", "mainnet", "`state`"):
        assert must in text, must


def test_builder_fee_doc_rate_matches_the_runtime_default():
    text = (ROOT / "docs" / "BUILDER_FEE.md").read_text(encoding="utf-8")
    fee = runtime.DEFAULT_BUILDER_FEE_TENTHS_BP
    assert f"`f = {fee}`" in text
    assert runtime.fee_rate_str(fee) in text
    assert runtime.fee_rate_str(2 * fee) in text      # the round trip
