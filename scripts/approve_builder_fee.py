"""Approve pathiel's Hyperliquid builder fee once, signed with your master key.

A public pathiel install attaches a builder code to the orders it places for
you, once you have approved that fee. Hyperliquid stores the approval (a max
fee rate for one builder address) and rejects any order whose builder fee is
above it. Until you approve, every order goes out without the fee.

    python3 scripts/approve_builder_fee.py                  # dry run: shows what would be signed
    python3 scripts/approve_builder_fee.py --yes            # signs and sends it (hidden key prompt)
    python3 scripts/approve_builder_fee.py --max-fee-rate 0% --yes   # revoke

/pathiel:setup prints the exact lines for your machine. The disclosure is
docs/BUILDER_FEE.md.

The master key:
  * public install: typed at a hidden prompt, nothing else. A
    HYPERLIQUID_MASTER_PRIVATE_KEY in the environment is ignored with a
    warning (the MCP server reads the same env file; the master key must not
    sit there).
  * operator install: HYPERLIQUID_MASTER_PRIVATE_KEY, else the prompt.
  It is never printed, logged or written, and never taken as an argument.

Signing is the installed SDK's own (hyperliquid.utils.signing.
sign_approve_builder_fee), the call Exchange.approve_builder_fee makes, with
hyperliquidChain set from the resolved network.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPTS)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SCRIPTS)

MASTER_KEY_ENV = "HYPERLIQUID_MASTER_PRIVATE_KEY"
KEY_PROMPT = "Hyperliquid master private key (hidden, not stored): "
_RATE_RE = re.compile(r"^[0-9]+(\.[0-9]+)?%$")


# ── pure helpers ──────────────────────────────────────────────────────────────

def build_action(builder: str, max_fee_rate: str, nonce_ms: int) -> Dict[str, Any]:
    """The approveBuilderFee action, in the SDK's key order. The SDK signs the
    builder address as an EIP-712 address and the venue matches it lowercase."""
    return {"maxFeeRate": max_fee_rate, "builder": builder.lower(), "nonce": nonce_ms,
            "type": "approveBuilderFee"}


def parse_rate(rate: str) -> int:
    """'0.01%' -> 10 tenths of a basis point. Raises ValueError unless `rate`
    is a non-negative decimal percent that is an exact multiple of 0.001%."""
    text = str(rate).strip()
    if not _RATE_RE.match(text):
        raise ValueError(f"rate must look like 0.01% (a percent with the % sign), got {rate!r}")
    try:
        tenths = Decimal(text[:-1]) * 1000
    except InvalidOperation as e:
        raise ValueError(f"not a number: {rate!r}") from e
    if tenths != tenths.to_integral_value():
        raise ValueError(f"rate must be a multiple of 0.001%, got {rate!r}")
    return int(tenths)


def trading_account(env: Optional[Mapping[str, str]] = None) -> str:
    """The account whose orders carry the builder fee: HYPERLIQUID_MASTER_ADDRESS,
    else HYPERLIQUID_WALLET_ADDRESS (a user who trades with the master key
    directly). Lowercase; '' when neither is set."""
    e = os.environ if env is None else env
    raw = (e.get("HYPERLIQUID_MASTER_ADDRESS") or "").strip() \
        or (e.get("HYPERLIQUID_WALLET_ADDRESS") or "").strip()
    return raw.lower()


def fee_example(tenths_bp: int, notional_usd: int = 100) -> str:
    """'100 USD of notional costs 0.01 USD per fill' for 10 tenths of a bp."""
    cost = (Decimal(notional_usd) * Decimal(int(tenths_bp)) / Decimal(100000)).normalize()
    return f"{notional_usd} USD of notional costs {format(cost, 'f')} USD per fill"


def approve(master_key: str, builder: str, max_fee_rate: str, nonce_ms: int,
            is_mainnet: bool, post: Callable[[str, Dict[str, Any]], Any]) -> Any:
    """Sign approveBuilderFee with `master_key` and post it once. Returns the
    venue's reply. sign_approve_builder_fee adds signatureChainId and
    hyperliquidChain to the action in place, as the SDK sends it."""
    from eth_account import Account
    from hyperliquid.utils.signing import sign_approve_builder_fee
    wallet = Account.from_key(master_key)
    action = build_action(builder, max_fee_rate, nonce_ms)
    signature = sign_approve_builder_fee(wallet, action, bool(is_mainnet))
    payload = {"action": action, "nonce": nonce_ms, "signature": signature,
               "vaultAddress": None, "expiresAfter": None}
    return post("/exchange", payload)


POST_CONNECT_TIMEOUT_S = 3.0
POST_READ_TIMEOUT_S = 10.0


def post_once(path: str, payload: Dict[str, Any]) -> Any:
    """POST a signed action exactly once: a plain requests.post, not the
    shared hl_client session. That session's adapter retries POST on 502, 503
    and 504, and a retry replays the same nonce: the venue then rejects the
    replay and its error would be reported while the first post had landed.
    A non-200 reply comes back as an error reply; a network error raises."""
    import requests

    from pathiel.client import hl_client
    resp = requests.post(f"{hl_client.HL_API}{path}", json=payload,
                         timeout=(POST_CONNECT_TIMEOUT_S, POST_READ_TIMEOUT_S))
    if resp.status_code != 200:
        return {"status": "err", "response": f"HTTP {resp.status_code}: {(resp.text or '')[:200]}"}
    return resp.json()


def approval_in_place(approved: Optional[int], tenths: int) -> bool:
    """True when the venue's stored approval already gives what was asked:
    at least `tenths`, or exactly 0 for a revoke."""
    if approved is None:
        return False
    return approved == 0 if tenths == 0 else approved >= tenths


# ── output ────────────────────────────────────────────────────────────────────

_LABELS = (("network", "network"), ("account", "account"), ("builder", "builder"),
           ("max_fee_rate", "max fee rate"), ("example", "example"),
           ("approved_tenths_bp", "approved now (tenths of a bp)"))


def _emit(obj: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, default=str))
        return
    if obj.get("error"):
        print(f"refused: {obj['error']}")
        return
    if obj.get("dry_run"):
        print("pathiel builder fee approval: DRY RUN, nothing is signed or sent")
    else:
        print(f"pathiel builder fee approval: {'sent, venue said ok' if obj.get('ok') else 'FAILED'}")
    for key, label in _LABELS:
        if key in obj:
            print(f"  {label + ':':<31} {obj[key]}")
    if obj.get("note"):
        print(f"  note: {obj['note']}")
    if obj.get("dry_run"):
        print("To sign and send it, run the same line again with --yes in your own terminal. "
              "It asks for your master private key at a hidden prompt.")
    elif "reply" in obj:
        print(f"  venue reply: {json.dumps(obj['reply'], default=str)}")


def _warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr)


def _refuse(error: str, base: Dict[str, Any], as_json: bool) -> int:
    _emit({"ok": False, **base, "error": error}, as_json)
    return 1


# ── key source ────────────────────────────────────────────────────────────────

def _key_from_prompt(getpass_fn: Callable[[str], str], isatty: Callable[[], bool]) -> Optional[str]:
    if not isatty():
        return None
    try:
        return (getpass_fn(KEY_PROMPT) or "").strip() or None
    except (EOFError, KeyboardInterrupt):
        return None


def _master_key(public: bool, getpass_fn: Callable[[str], str],
                isatty: Callable[[], bool]) -> Optional[str]:
    env_key = (os.environ.get(MASTER_KEY_ENV) or "").strip()
    if public:
        if env_key:
            _warn(f"{MASTER_KEY_ENV} is set in the environment. A public install ignores it; "
                  "remove it from ~/.pathiel/.env.local (the trading process reads that file).")
        return _key_from_prompt(getpass_fn, isatty)
    if env_key:
        return env_key
    return _key_from_prompt(getpass_fn, isatty)


# ── main ──────────────────────────────────────────────────────────────────────

def _parse_args(argv: Optional[List[str]]) -> Optional[argparse.Namespace]:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-fee-rate", default=None,
                    help="percent, e.g. 0.01%% (default: the fee pathiel charges). 0%% revokes.")
    ap.add_argument("--yes", action="store_true", help="sign and send (default: dry run)")
    ap.add_argument("--json", action="store_true", help="print one JSON object")
    # parse_known_args, not parse_args: argparse echoes unknown arguments in
    # its error, and a pasted key must never be echoed back.
    args, unknown = ap.parse_known_args(argv)
    if unknown:
        print(f"error: {len(unknown)} unrecognized argument(s), not shown. The master key is "
              "never taken as an argument; --yes asks for it at a hidden prompt.", file=sys.stderr)
        return None
    return args


def main(argv: Optional[List[str]] = None, *,
         post: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
         fetch_max_fee: Optional[Callable[[str, str], int]] = None,
         getpass_fn: Optional[Callable[[str], str]] = None,
         isatty: Optional[Callable[[], bool]] = None) -> int:
    args = _parse_args(argv)
    if args is None:
        return 2
    as_json = bool(args.json)

    import _state_env
    _state_env.load_env_local(_ROOT)
    import contracts.runtime as runtime

    public = runtime.is_public()
    net = runtime.network()
    account = trading_account()
    builder = runtime.builder_address()
    base: Dict[str, Any] = {"network": net, "account": account or None, "builder": builder or None}

    if args.max_fee_rate is None and runtime.builder_fee_tenths_bp() <= 0:
        # The fee is waived (contracts/runtime BUILDER_FEE_WAIVED): there is no
        # rate to approve. Revoking an earlier approval stays possible.
        return _refuse("pathiel charges no builder fee right now: nothing to approve. To revoke "
                       "an approval you gave an earlier version: --max-fee-rate 0% --yes",
                       base, as_json)
    rate_in = args.max_fee_rate if args.max_fee_rate is not None \
        else runtime.fee_rate_str(runtime.builder_fee_tenths_bp())
    try:
        tenths = parse_rate(rate_in)
    except ValueError as e:
        return _refuse(str(e), base, as_json)
    rate = runtime.fee_rate_str(tenths)      # the canonical string is what gets signed
    base["max_fee_rate"] = rate

    # Refusal order: builder address, self address, rate cap, network drift.
    if not builder:
        return _refuse("no builder address configured (PATHIEL_BUILDER_ADDRESS or "
                       "contracts/runtime DEFAULT_BUILDER_ADDRESS): nothing to approve", base, as_json)
    if account and account == builder:
        return _refuse("the builder address is your own trading account: no fee applies, "
                       "nothing to approve", base, as_json)
    if tenths > runtime.MAX_PERP_BUILDER_FEE_TENTHS_BP:
        cap = runtime.fee_rate_str(runtime.MAX_PERP_BUILDER_FEE_TENTHS_BP)
        return _refuse(f"rate {rate} is above Hyperliquid's perp cap of {cap}", base, as_json)
    from pathiel.client import hl_client
    if hl_client.HL_API != runtime.api_url():
        return _refuse(f"network drift: this process would post to {hl_client.HL_API} but the "
                       f"resolved network is {net} ({runtime.api_url()}). Nothing sent.",
                       base, as_json)

    nonce = int(time.time() * 1000)
    if not args.yes:
        plan: Dict[str, Any] = {"dry_run": True, **base, "example": fee_example(tenths),
                                "action": build_action(builder, rate, nonce)}
        if not account:
            plan["note"] = ("no trading account configured (HYPERLIQUID_MASTER_ADDRESS); "
                            "--yes will refuse until it is set")
        if tenths == 0:
            plan["note"] = "0% revokes the approval: orders then carry no builder fee"
        _emit(plan, as_json)
        return 0

    # --yes: the key source, then the account match.
    if not account:
        return _refuse("no trading account configured: set HYPERLIQUID_MASTER_ADDRESS in "
                       "~/.pathiel/.env.local to the address that holds your funds", base, as_json)
    key = _master_key(public, getpass_fn or getpass.getpass,
                      isatty or (lambda: sys.stdin.isatty()))
    if not key:
        where = "the hidden prompt" if public else f"{MASTER_KEY_ENV} or the hidden prompt"
        return _refuse(f"no master key: it is read from {where}, which needs your own "
                       "terminal (stdin is not a TTY or nothing was typed). Nothing sent.",
                       base, as_json)
    try:
        from eth_account import Account
        signer = Account.from_key(key).address.lower()
    except Exception:   # noqa: BLE001 - the message could carry key text; never show it
        return _refuse("that is not a valid private key. Nothing sent.", base, as_json)
    if signer != account:
        return _refuse(f"the key belongs to {signer}, not the trading account {account}. "
                       "Use the master key of the account that holds your funds, not the "
                       "API wallet key. Nothing sent.", base, as_json)

    send = post if post is not None else post_once
    try:
        reply = approve(key, builder, rate, nonce, runtime.is_mainnet(), send)
    except Exception as e:   # noqa: BLE001 - a failed post is a result, not a crash
        reply = {"status": "err", "response": f"{type(e).__name__}: {e}"}
    finally:
        key = None
    ok = isinstance(reply, dict) and reply.get("status") == "ok"
    out: Dict[str, Any] = {"ok": ok, **base, "example": fee_example(tenths), "reply": reply}
    if fetch_max_fee is None:
        from pathiel.client.builder import fetch_max_builder_fee as fetch_max_fee
    # Read the approval back, also after an error: the reply can be an error
    # while the approval landed (a dropped connection after the venue stored
    # it). The read decides before a failure is reported.
    read_error: Optional[str] = None
    try:
        out["approved_tenths_bp"] = fetch_max_fee(account, builder)
    except Exception as e:   # noqa: BLE001
        out["approved_tenths_bp"] = None
        read_error = type(e).__name__
    if ok:
        if read_error:
            out["note"] = f"sent, but the confirming read failed ({read_error}); check `state` later"
    elif approval_in_place(out["approved_tenths_bp"], tenths):
        ok = out["ok"] = True
        out["note"] = ("the reply was an error, but Hyperliquid now holds an approval of "
                       f"{runtime.fee_rate_str(out['approved_tenths_bp'])} for this builder: "
                       "nothing more to do. Do not sign again.")
    else:
        out["note"] = ("the venue did not accept it; the reply says why. One known cause: the "
                       "builder address holds under 100 USDC of perps account value on this "
                       "network. Orders keep going out without the fee.")
        if read_error:
            out["note"] += (f" The read-back also failed ({read_error}): run "
                            "scripts/public_status.py --online before signing again.")
    _emit(out, as_json)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
