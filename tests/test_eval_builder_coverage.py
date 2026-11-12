"""Eval: builder fee coverage across every builder state and every order path.

Outcome measured: on a public install, the builder fee is signed exactly when
the user approved it, every order reaches the venue whatever the builder state,
no order waits on an approval read, and the operator's orders never carry a
builder. Offline: fake SDKs, a stub approval read, no thread started.

Matrix: 13 builder states x 4 order paths (52 rows) + 4 operator rows, plus
summary rows. Threshold: every row passes.

venue_refuses_known (final review, 2026-11-12): Hyperliquid refuses the
builder order for a reason of its own (margin here; an IOC no-match in the
field). One send, no resend, no cooldown, nothing charged.

    .venv/bin/python -m pytest -q -s tests/test_eval_builder_coverage.py

Writes $PATHIEL_EVAL_OUT/builder_coverage.json when that variable is set.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List

NAME = "builder_coverage"
ADDR = "0x" + "b1" * 20
ACCT = "0x" + "a1" * 20
NET = "testnet"

FILLED = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"filled": {"oid": 11, "avgPx": "100.0", "totalSz": "1.0"}}]}}}
RESTING = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"resting": {"oid": 12}}]}}}
BULK_OK = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"resting": {"oid": 13}}, "waitingForFill", "waitingForFill"]}}}
BUILDER_ERR = {"status": "err", "response": "Builder fee has not been approved."}
MARGIN_ERR = {"status": "err", "response": "Insufficient margin to place order."}
UNKNOWN_ERR = {"status": "err", "response": "Some wording the venue never used before."}

STATES = ["approved_warm", "approved_stale", "cold", "unapproved", "approval_below_fee",
          "read_error", "address_unset", "fee_zero", "account_equals_builder",
          "venue_rejects_naming_builder", "venue_rejects_unrelated_then_accepts",
          "venue_rejects_both", "venue_refuses_known"]
ATTACHING = {"approved_warm", "approved_stale", "venue_rejects_naming_builder",
             "venue_rejects_unrelated_then_accepts", "venue_rejects_both", "venue_refuses_known"}
# a cooldown is wanted after these (the venue named the builder, or the bare
# resend placed what the builder order could not)
COOLING = {"venue_rejects_naming_builder", "venue_rejects_unrelated_then_accepts"}
REJECTING = {"venue_rejects_naming_builder", "venue_rejects_unrelated_then_accepts",
             "venue_rejects_both"}
PATH_NAMES = ["ioc", "trigger", "limit", "bracket"]


def _ok_reply(kind: str) -> Dict[str, Any]:
    return {"ioc": FILLED, "trigger": RESTING, "limit": RESTING, "bracket": BULK_OK}[kind]


def _kind(order_type: Dict[str, Any]) -> str:
    if "trigger" in order_type:
        return "trigger"
    return "ioc" if order_type["limit"]["tif"] == "Ioc" else "limit"


class PublicSdk:
    def __init__(self, reply: Callable[[str, Any], Any]):
        self.reply, self.calls = reply, []

    def order(self, name, is_buy, sz, limit_px, order_type, reduce_only=False, cloid=None,
              builder=None):
        return self._rec(_kind(order_type), builder)

    def bulk_orders(self, order_requests, builder=None, grouping="na"):
        return self._rec("bracket", builder)

    def _rec(self, kind, b):
        self.calls.append(None if b is None else dict(b))
        return self.reply(kind, b)


class OperatorSdk:
    """Pre-builder signatures: a builder kwarg is a TypeError."""

    def __init__(self):
        self.calls = 0

    def order(self, name, is_buy, sz, limit_px, order_type, reduce_only=False, cloid=None):
        self.calls += 1
        return _ok_reply(_kind(order_type))

    def bulk_orders(self, order_requests, grouping="na"):
        self.calls += 1
        return BULK_OK


class _Patch:
    _UNSET = object()

    def __init__(self):
        self._undo: List[Any] = []

    def attr(self, obj, name, value):
        self._undo.append(("attr", obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def env(self, key, value):
        self._undo.append(("env", None, key, os.environ.get(key, self._UNSET)))
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def undo(self):
        for kind, obj, name, old in reversed(self._undo):
            if kind == "attr":
                setattr(obj, name, old)
            elif old is self._UNSET:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old
        self._undo.clear()


def _paths(ex) -> Dict[str, Callable[[], Dict[str, Any]]]:
    return {
        "ioc": lambda: ex.place_hl_order(True, 1.0, 100.0, "BTC"),
        "trigger": lambda: ex.place_hl_trigger_order(True, 1.0, 95.0, "sl", "BTC"),
        "limit": lambda: ex.place_hl_limit_order("BTC", True, 1.0, 99.0),
        "bracket": lambda: ex.place_hl_bracket_order("BTC", True, 1.0, 99.0, 95.0, 110.0),
    }


def _entry(value=None, age=0.0, error=None, now=0.0):
    return {"value": value, "fetched_at": (now - age) if value is not None else None,
            "error": error, "error_at": (now - age) if error else None}


def _one_public_row(state: str, path: str, ex, builder, p: _Patch) -> Dict[str, Any]:
    builder.reset()
    jobs: List[Any] = []
    inline: List[Any] = []
    main = threading.current_thread()
    p.attr(builder, "_spawn", jobs.append)
    p.attr(builder, "_fetch", lambda u, b: inline.append(threading.current_thread() is main) or 10)
    p.env("PATHIEL_INSTALL", "public")
    p.env("PATHIEL_NETWORK", NET)
    p.env("PATHIEL_BUILDER_ADDRESS", ADDR)
    p.env("PATHIEL_BUILDER_FEE_TENTHS_BP", None)
    account = ACCT
    now = builder._clock()
    key = (NET, ACCT, ADDR)
    if state in ("approved_warm", "venue_refuses_known") or state in REJECTING:
        builder._cache[key] = _entry(10, 0.0, now=now)
    elif state == "approved_stale":
        builder._cache[key] = _entry(10, builder.REFRESH_APPROVED_S + 100, now=now)
    elif state == "unapproved":
        builder._cache[key] = _entry(0, 0.0, now=now)
    elif state == "approval_below_fee":
        builder._cache[key] = _entry(5, 0.0, now=now)
    elif state == "read_error":
        builder._cache[key] = _entry(None, 0.0, error="Timeout: read timed out", now=now)
    elif state == "address_unset":
        p.env("PATHIEL_BUILDER_ADDRESS", None)
    elif state == "fee_zero":
        p.env("PATHIEL_BUILDER_FEE_TENTHS_BP", "0")
    elif state == "account_equals_builder":
        account = ADDR
    if state == "venue_rejects_naming_builder":
        reply = (lambda k, b: BUILDER_ERR if b is not None else _ok_reply(k))
    elif state == "venue_rejects_unrelated_then_accepts":
        reply = (lambda k, b: UNKNOWN_ERR if b is not None else _ok_reply(k))
    elif state == "venue_rejects_both":
        reply = (lambda k, b: UNKNOWN_ERR)
    elif state == "venue_refuses_known":
        reply = (lambda k, b: MARGIN_ERR if b is not None else _ok_reply(k))
    else:
        reply = (lambda k, b: _ok_reply(k))
    sdk = PublicSdk(reply)
    p.attr(ex, "_make_exchange", lambda: sdk)
    p.attr(ex, "_resolve_identity", lambda: ("0x" + "11" * 32, account, None, False))
    out = _paths(ex)[path]()
    note = out.get("builder") or {}
    got = {
        "reached_sdk": len(sdk.calls) >= 1,
        "sdk_calls": len(sdk.calls),
        "attached_first_send": bool(sdk.calls and sdk.calls[0] is not None),
        "first_builder_ok": (sdk.calls[0] == {"b": ADDR, "f": 10}) if sdk.calls and sdk.calls[0] else None,
        "last_send_bare": bool(sdk.calls) and sdk.calls[-1] is None if len(sdk.calls) > 1 else None,
        "inline_reads": sum(1 for on_main in inline if on_main),
        "placed": bool(out.get("ok")),
        "note_attached": note.get("attached"),
        "cooldown": (builder.builder_for_order(account) is None) if state in ATTACHING else None,
    }
    attaching = state in ATTACHING
    rejecting = state in REJECTING
    want = {
        "reached_sdk": True,
        "sdk_calls": 2 if rejecting else 1,
        "attached_first_send": attaching,
        "first_builder_ok": True if attaching else None,
        "last_send_bare": True if rejecting else None,
        "inline_reads": 0,
        "placed": state not in ("venue_rejects_both", "venue_refuses_known"),
        "note_attached": attaching and not rejecting and state != "venue_refuses_known",
        "cooldown": (state in COOLING) if attaching else None,
    }
    p.undo()
    builder.reset()
    return {"check": f"{state}/{path}", "got": got, "want": want, "ok": got == want}


def _one_operator_row(path: str, ex, builder, p: _Patch) -> Dict[str, Any]:
    builder.reset()
    jobs: List[Any] = []
    reads: List[Any] = []
    p.attr(builder, "_spawn", jobs.append)
    p.attr(builder, "_fetch", lambda u, b: reads.append(1) or 100)
    p.env("PATHIEL_INSTALL", None)
    p.env("PATHIEL_NETWORK", None)
    p.env("PATHIEL_BUILDER_ADDRESS", ADDR)
    for net in ("mainnet", "testnet"):
        builder._cache[(net, ACCT, ADDR)] = _entry(100, 0.0, now=builder._clock())
    sdk = OperatorSdk()
    p.attr(ex, "_make_exchange", lambda: sdk)
    p.attr(ex, "_resolve_identity", lambda: reads.append("identity") or ("0x" + "11" * 32, ACCT, None, False))
    out = _paths(ex)[path]()
    got = {"sdk_calls": sdk.calls, "builder_kwargs": 0 if out.get("ok") else "TypeError?",
           "jobs": len(jobs), "reads": len(reads), "builder_key": "builder" in out}
    want = {"sdk_calls": 1, "builder_kwargs": 0, "jobs": 0, "reads": 0, "builder_key": False}
    p.undo()
    builder.reset()
    return {"check": f"operator/{path}", "got": got, "want": want, "ok": got == want}


def run() -> Dict[str, Any]:
    from pathiel.client import builder
    from pathiel.client import exchange as ex

    p = _Patch()
    p.attr(ex, "_has_usable_credential", lambda: True)
    p.attr(ex, "get_coin_index", lambda c: (0, 2, 0))
    p.attr(ex, "_ioc_cross_price", lambda coin, is_buy, mid: mid * 1.01)
    rows: List[Dict[str, Any]] = []
    try:
        for state in STATES:
            for path in PATH_NAMES:
                inner = _Patch()
                try:
                    rows.append(_one_public_row(state, path, ex, builder, inner))
                finally:
                    inner.undo()
        for path in PATH_NAMES:
            inner = _Patch()
            try:
                rows.append(_one_operator_row(path, ex, builder, inner))
            finally:
                inner.undo()
    finally:
        p.undo()
        builder.reset()

    public = [r for r in rows if not r["check"].startswith("operator/")]
    approved_first = sum(1 for r in public if r["check"].split("/")[0] in ("approved_warm", "approved_stale")
                         and r["got"]["attached_first_send"])
    other_first = sum(1 for r in public if r["check"].split("/")[0] not in ATTACHING
                      and r["got"]["attached_first_send"])
    summary = [
        {"check": "attach on first send, approved warm+stale", "got": approved_first, "want": 8},
        {"check": "attach on first send, non-approved states", "got": other_first, "want": 0},
        {"check": "orders reaching the SDK", "got": sum(r["got"]["reached_sdk"] for r in public),
         "want": len(STATES) * len(PATH_NAMES)},
        {"check": "inline approval reads", "got": sum(r["got"]["inline_reads"] for r in public), "want": 0},
        {"check": "max SDK calls per order <= 2",
         "got": max(r["got"]["sdk_calls"] for r in public) <= 2, "want": True},
        {"check": "known venue refusals sent once, no cooldown",
         "got": sum(1 for r in public if r["check"].startswith("venue_refuses_known/")
                    and r["got"]["sdk_calls"] == 1 and r["got"]["cooldown"] is False),
         "want": len(PATH_NAMES)},
        {"check": "operator rows clean", "got": sum(r["ok"] for r in rows if r["check"].startswith("operator/")),
         "want": len(PATH_NAMES)},
    ]
    for s in summary:
        s["ok"] = s["got"] == s["want"]
    rows.extend(summary)
    return {"name": NAME, "passed": sum(1 for r in rows if r["ok"]), "total": len(rows), "rows": rows}


def _print(card: Dict[str, Any]) -> None:
    width = max(len(r["check"]) for r in card["rows"])
    print(f"\n{card['name']}: {card['passed']}/{card['total']}")
    for r in card["rows"]:
        mark = "ok  " if r["ok"] else "FAIL"
        detail = "" if r["ok"] else f"  got={r['got']} want={r['want']}"
        print(f"  {mark} {r['check']:<{width}}{detail}")


def _write(card: Dict[str, Any]) -> None:
    out = os.environ.get("PATHIEL_EVAL_OUT")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / f"{card['name']}.json").write_text(json.dumps(card, indent=2, default=str))


def test_builder_coverage_eval():
    card = run()
    _print(card)
    _write(card)
    assert card["total"] == 13 * 4 + 4 + 7
    assert card["passed"] == card["total"], [r for r in card["rows"] if not r["ok"]]
