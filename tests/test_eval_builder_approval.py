"""Eval: the builder fee approval does what the disclosure says, scenario by scenario.

Fifteen offline scenarios through scripts/approve_builder_fee.py main() (and
one through scripts/approve_api_wallet.py), each with a throwaway key and a
recording post. Outcome measured, not code paths:

  * dry run: 0 posts, no key source consulted; a public install with no
    network set prints the mainnet approval (2026-11-12), testnet on opt-in
  * --yes on testnet / mainnet (public default and operator) / revoke /
    public prompt: exactly 1 post whose signature recovers to the trading
    account on the right chain
  * public install: the env master key is used 0 times (the prompt is)
  * 7 refusals: 0 posts each, exit 1, no key text in the output, including
    the shipped builder address approving itself
  * approve_api_wallet on testnet: hyperliquidChain 'Testnet', recovers

Threshold 15/15. Run:

    .venv/bin/python -m pytest -q -s tests/test_eval_builder_approval.py
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
from eth_account import Account
from hyperliquid.utils import signing

import contracts.runtime as runtime
from pathiel.client import hl_client

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_eval_{name}", ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ab = _load("approve_builder_fee")
aw = _load("approve_api_wallet")

BUILDER = "0x" + "c3" * 20
SHIPPED_BUILDER = "0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985"   # contracts/runtime default
FEE_TYPES = [{"name": "hyperliquidChain", "type": "string"}, {"name": "maxFeeRate", "type": "string"},
             {"name": "builder", "type": "address"}, {"name": "nonce", "type": "uint64"}]
FEE_PRIMARY = "HyperliquidTransaction:ApproveBuilderFee"
AGENT_TYPES = [{"name": "hyperliquidChain", "type": "string"}, {"name": "agentAddress", "type": "address"},
               {"name": "agentName", "type": "string"}, {"name": "nonce", "type": "uint64"}]
AGENT_PRIMARY = "HyperliquidTransaction:ApproveAgent"


class _Post:
    def __init__(self):
        self.calls: List[Any] = []

    def __call__(self, path, payload):
        self.calls.append((path, copy.deepcopy(payload)))
        return {"status": "ok", "response": {"type": "default"}}


class _Prompt:
    def __init__(self, answer: str):
        self.answer, self.calls = answer, 0

    def __call__(self, _prompt):
        self.calls += 1
        return self.answer


def _scenario(mp: pytest.MonkeyPatch, *, public: bool, network: str = "", drift: bool = False,
              builder: str = BUILDER, account: str = "", env_key: str = "") -> None:
    import _state_env
    mp.setattr(_state_env, "load_env_local", lambda *a, **k: None)
    for k in runtime.RUNTIME_ENV_VARS:
        mp.delenv(k, raising=False)
    mp.setattr(runtime, "DEFAULT_BUILDER_ADDRESS", "")
    if public:
        mp.setenv("PATHIEL_INSTALL", "public")
    if network:
        mp.setenv("PATHIEL_NETWORK", network)
    mp.setenv("PATHIEL_BUILDER_ADDRESS", builder)
    mp.setenv("HYPERLIQUID_MASTER_ADDRESS", account)
    mp.setenv("HYPERLIQUID_WALLET_ADDRESS", "")
    mp.setenv("HYPERLIQUID_MASTER_PRIVATE_KEY", env_key)
    api = runtime.MAINNET_API_URL if drift and runtime.network() == "testnet" else runtime.api_url()
    mp.setattr(hl_client, "HL_API", api)


def _call(fn, argv, **kw) -> Dict[str, Any]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fn(argv, **kw)
    return {"code": code, "text": out.getvalue() + err.getvalue()}


def _no_key(text: str, *accts) -> bool:
    return not any(a.key.hex().removeprefix("0x") in text for a in accts)


def _recovers(payload, types, primary, is_mainnet, acct) -> bool:
    who = signing.recover_user_from_user_signed_action(dict(payload["action"]), payload["signature"],
                                                       types, primary, is_mainnet)
    return who.lower() == acct.address.lower()


def _yes_row(name, mp, *, public, network, chain, argv=(), env_key_acct=None, prompt_acct=None,
             master=None, want_rate="0.01%") -> Dict[str, Any]:
    _scenario(mp, public=public, network=network, account=master.address,
              env_key=env_key_acct.key.hex() if env_key_acct else "")
    post = _Post()
    prompt = _Prompt(prompt_acct.key.hex() if prompt_acct else "")
    r = _call(ab.main, ["--json", "--yes", *argv], post=post, getpass_fn=prompt,
              isatty=lambda: True, fetch_max_fee=lambda u, b: ab.parse_rate(want_rate))
    ok = (r["code"] == 0 and len(post.calls) == 1 and post.calls[0][0] == "/exchange")
    if ok:
        p = post.calls[0][1]
        ok = (p["action"]["hyperliquidChain"] == chain
              and p["action"]["maxFeeRate"] == want_rate
              and _recovers(p, FEE_TYPES, FEE_PRIMARY, chain == "Mainnet", master)
              and _no_key(r["text"], master, *(a for a in (env_key_acct,) if a)))
    if public and env_key_acct is not None:
        # the env key would sign as env_key_acct; a recovery to master proves it was not used
        ok = ok and prompt.calls == 1
    got = f"posts={len(post.calls)} exit={r['code']}"
    return {"check": name, "got": got, "want": f"posts=1 exit=0 chain={chain}", "ok": bool(ok)}


def _refusal_row(name, mp, *, argv=("--yes",), prompt_acct=None, isatty=True, master,
                 **scenario) -> Dict[str, Any]:
    _scenario(mp, **scenario)
    post = _Post()
    prompt = _Prompt(prompt_acct.key.hex() if prompt_acct else master.key.hex())
    r = _call(ab.main, ["--json", *argv], post=post, getpass_fn=prompt, isatty=lambda: isatty,
              fetch_max_fee=lambda u, b: 0)
    extra = [prompt_acct] if prompt_acct else []
    ok = r["code"] == 1 and post.calls == [] and _no_key(r["text"], master, *extra)
    return {"check": name, "got": f"posts={len(post.calls)} exit={r['code']}",
            "want": "posts=0 exit=1", "ok": bool(ok)}


def run() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    master = Account.create()
    other = Account.create()

    for network, label in (("", "dry run (public, no network set: mainnet)"),
                           ("testnet", "dry run (public, testnet opt-in)")):
        with pytest.MonkeyPatch.context() as mp:
            _scenario(mp, public=True, network=network, account=master.address)
            post = _Post()

            def no_key(*_a, **_k):
                raise AssertionError("dry run consulted a key source")

            mp.setattr(ab, "_master_key", no_key)
            r = _call(ab.main, ["--json"], post=post, getpass_fn=no_key, isatty=no_key,
                      fetch_max_fee=no_key)
            out = json.loads(r["text"].strip().splitlines()[-1])
            ok = (r["code"] == 0 and post.calls == [] and out.get("dry_run") is True
                  and out.get("network") == (network or "mainnet")
                  and out.get("max_fee_rate") == "0.01%")
            rows.append({"check": label, "got": f"posts={len(post.calls)} exit={r['code']} "
                                                f"network={out.get('network')}",
                         "want": f"posts=0 exit=0 network={network or 'mainnet'}", "ok": bool(ok)})

    with pytest.MonkeyPatch.context() as mp:
        rows.append(_yes_row("--yes on testnet (public, prompt)", mp, public=True, network="testnet",
                             chain="Testnet", prompt_acct=master, master=master))
    with pytest.MonkeyPatch.context() as mp:
        rows.append(_yes_row("--yes on mainnet (public default, prompt)", mp, public=True, network="",
                             chain="Mainnet", prompt_acct=master, master=master))
    with pytest.MonkeyPatch.context() as mp:
        rows.append(_yes_row("--yes on mainnet (operator, env key)", mp, public=False, network="",
                             chain="Mainnet", env_key_acct=master, master=master))
    with pytest.MonkeyPatch.context() as mp:
        rows.append(_yes_row("revoke 0%", mp, public=True, network="testnet", chain="Testnet",
                             argv=("--max-fee-rate", "0%"), prompt_acct=master, master=master,
                             want_rate="0%"))
    with pytest.MonkeyPatch.context() as mp:
        rows.append(_yes_row("public: env master key used 0 times", mp, public=True, network="testnet",
                             chain="Testnet", env_key_acct=other, prompt_acct=master, master=master))

    with pytest.MonkeyPatch.context() as mp:
        rows.append(_refusal_row("refuse: no builder address", mp, master=master, public=True,
                                 builder="", account=master.address))
    with pytest.MonkeyPatch.context() as mp:
        rows.append(_refusal_row("refuse: builder is the account", mp, master=master, public=True,
                                 builder=master.address, account=master.address))
    with pytest.MonkeyPatch.context() as mp:
        rows.append(_refusal_row("refuse: the shipped builder approving itself", mp, master=master,
                                 public=True, builder=SHIPPED_BUILDER, account=SHIPPED_BUILDER))
    with pytest.MonkeyPatch.context() as mp:
        rows.append(_refusal_row("refuse: rate above 0.1%", mp, master=master, public=True,
                                 argv=("--yes", "--max-fee-rate", "0.2%"), account=master.address))
    with pytest.MonkeyPatch.context() as mp:
        rows.append(_refusal_row("refuse: network drift", mp, master=master, public=True,
                                 network="testnet", drift=True, account=master.address))
    with pytest.MonkeyPatch.context() as mp:
        rows.append(_refusal_row("refuse: key of another account", mp, master=master, public=True,
                                 prompt_acct=other, account=master.address))
    with pytest.MonkeyPatch.context() as mp:
        rows.append(_refusal_row("refuse: no key and no tty", mp, master=master, public=True,
                                 isatty=False, account=master.address, env_key=master.key.hex()))

    with pytest.MonkeyPatch.context() as mp:
        _scenario(mp, public=False, network="testnet", account=master.address, env_key=master.key.hex())
        post = _Post()
        r = _call(aw.main, ["--address", other.address, "--send"], post=post)
        ok = r["code"] == 0 and len(post.calls) == 1
        if ok:
            p = post.calls[0][1]
            ok = (p["action"]["hyperliquidChain"] == "Testnet"
                  and _recovers(p, AGENT_TYPES, AGENT_PRIMARY, False, master)
                  and _no_key(r["text"], master))
        rows.append({"check": "approve_api_wallet on testnet chain",
                     "got": f"posts={len(post.calls)} exit={r['code']}",
                     "want": "posts=1 chain=Testnet", "ok": bool(ok)})
    return rows


def test_builder_approval_eval_meets_threshold(scorecard):
    card = scorecard("builder_approval", run())
    assert card["total"] == 15
    assert card["passed"] == 15, [r for r in card["rows"] if not r["ok"]]
