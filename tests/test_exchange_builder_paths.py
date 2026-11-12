"""The builder fee on every order path in pathiel.client.exchange.

Four functions reach the SDK: place_hl_order (IOC entries and closes),
place_hl_trigger_order (stops and targets), place_hl_limit_order and
place_hl_bracket_order (bulk_orders, normalTpsl). Every place_bracket,
close_position, bracket_sweep and set_target call goes through one of them.

What is pinned here:
  * public + approved + warm cache: every path signs a builder {'b', 'f'};
  * every other builder state: the order goes out without one, at once;
  * a builder order the venue placed nothing for is resent once without it,
    whatever the wording; exceptions are never resent;
  * a stop after a filled market entry is never lost to builder state.
"""
from __future__ import annotations

import threading

import pytest
import requests
from hyperliquid.utils.error import ClientError

from pathiel.agents import bracket
from pathiel.client import builder
from pathiel.client import exchange as ex

ADDR = "0x" + "b1" * 20
ACCT = "0x" + "a1" * 20

FILLED = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"filled": {"oid": 11, "avgPx": "100.0", "totalSz": "1.0"}}]}}}
RESTING = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"resting": {"oid": 12}}]}}}
BULK_OK = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"resting": {"oid": 13}}, "waitingForFill", "waitingForFill"]}}}
BUILDER_ERR = {"status": "err", "response": "Builder fee has not been approved."}
MARGIN_ERR = {"status": "err", "response": "Insufficient margin to place order."}


def _ok_reply(kind):
    return {"ioc": FILLED, "trigger": RESTING, "limit": RESTING, "bracket": BULK_OK}[kind]


class FakeSdk:
    """The SDK's order/bulk_orders signatures, builder included. `reply(kind,
    builder)` decides each answer; the SDK lowercases builder['b'] in place, so
    this fake does too."""

    def __init__(self, reply=None):
        self.reply = reply or (lambda kind, b: _ok_reply(kind))
        self.calls = []
        self.received = []

    def _kind(self, order_type, n=1):
        if n == 3:
            return "bracket"
        if "trigger" in order_type:
            return "trigger"
        return "ioc" if order_type["limit"]["tif"] == "Ioc" else "limit"

    def order(self, name, is_buy, sz, limit_px, order_type, reduce_only=False, cloid=None,
              builder=None):
        return self._record(self._kind(order_type), builder)

    def bulk_orders(self, order_requests, builder=None, grouping="na"):
        assert grouping == "normalTpsl"
        return self._record("bracket", builder)

    def _record(self, kind, b):
        self.received.append(b)
        self.calls.append({"kind": kind, "builder": None if b is None else dict(b),
                           "keys": None if b is None else list(b.keys())})
        if b is not None:
            b["b"] = b["b"].lower()      # what the real SDK does to the caller's dict
        out = self.reply(kind, b)
        if isinstance(out, BaseException):
            raise out
        return out


PATHS = {
    "ioc": lambda: ex.place_hl_order(True, 1.0, 100.0, "BTC"),
    "trigger": lambda: ex.place_hl_trigger_order(True, 1.0, 95.0, "sl", "BTC"),
    "limit": lambda: ex.place_hl_limit_order("BTC", True, 1.0, 99.0),
    "bracket": lambda: ex.place_hl_bracket_order("BTC", True, 1.0, 99.0, 95.0, 110.0),
}


class Fetch:
    def __init__(self, value=10):
        self.value, self.exc, self.threads = value, None, []

    def __call__(self, user, b):
        self.threads.append(threading.current_thread())
        if self.exc:
            raise self.exc
        return self.value


@pytest.fixture
def sdk(monkeypatch):
    fake = FakeSdk()
    monkeypatch.setattr(ex, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(ex, "get_coin_index", lambda c: (0, 2, 0))
    monkeypatch.setattr(ex, "_ioc_cross_price", lambda coin, is_buy, mid: mid * 1.01)
    monkeypatch.setattr(ex, "_make_exchange", lambda: fake)
    monkeypatch.setattr(ex, "_resolve_identity", lambda: ("0x" + "11" * 32, ACCT, None, False))
    return fake


@pytest.fixture
def fetch(monkeypatch):
    f = Fetch()
    monkeypatch.setattr(builder, "_fetch", f)
    return f


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", ADDR)


def _run(jobs):
    while jobs:
        jobs.pop(0)()


@pytest.fixture
def approved(public, fetch, builder_jobs):
    builder.builder_for_order(ACCT)
    _run(builder_jobs)
    assert builder.builder_for_order(ACCT) == {"b": ADDR, "f": 10}
    builder_jobs.clear()
    return fetch


# ── attach on every path ──────────────────────────────────────────────────────

@pytest.mark.parametrize("path", list(PATHS))
def test_public_approved_all_four_paths_carry_builder(path, sdk, approved, builder_jobs):
    out = PATHS[path]()
    assert out["ok"], out
    assert len(sdk.calls) == 1 and sdk.calls[0]["kind"] == path
    assert sdk.calls[0]["builder"] == {"b": ADDR, "f": 10}
    note = out["builder"]
    assert note["attached"] is True and note["reason_code"] == "approved"
    assert note["fee_tenths_bp"] == 10 and note["fee_rate"] == "0.01%" and note["address"] == ADDR
    assert note["retried_without_builder"] is False
    assert builder_jobs == []


@pytest.mark.parametrize("path", list(PATHS))
def test_sdk_receives_keys_b_then_f(path, sdk, approved, monkeypatch):
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", ADDR.upper().replace("0X", "0x"))
    PATHS[path]()
    PATHS[path]()
    for call in sdk.calls:
        assert call["keys"] == ["b", "f"]
        assert type(call["builder"]["f"]) is int
        assert call["builder"]["b"] == call["builder"]["b"].lower()
    a, b = sdk.received
    assert a is not b, "a fresh dict per order: the SDK mutates what it gets"


@pytest.mark.parametrize("path", list(PATHS))
def test_public_cold_cache_places_without_builder(path, sdk, public, fetch, builder_jobs):
    out = PATHS[path]()
    assert out["ok"]
    assert [c["builder"] for c in sdk.calls] == [None]
    assert out["builder"]["attached"] is False and out["builder"]["reason_code"] == "cache_cold"
    assert fetch.threads == [], "no read on the calling thread"
    assert len(builder_jobs) == 1, "the read is scheduled, not run"


@pytest.mark.parametrize("path", list(PATHS))
def test_public_unapproved_places_without_builder(path, sdk, public, fetch, builder_jobs):
    fetch.value = 9                      # approved below the fee
    builder.builder_for_order(ACCT)
    _run(builder_jobs)
    out = PATHS[path]()
    assert out["ok"] and [c["builder"] for c in sdk.calls] == [None]
    assert out["builder"]["reason_code"] == "not_approved"
    assert out["builder"]["attached"] is False


def test_address_unset_schedules_nothing(sdk, monkeypatch, fetch, builder_jobs):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    monkeypatch.delenv("PATHIEL_BUILDER_ADDRESS", raising=False)
    for path in PATHS:
        out = PATHS[path]()
        assert out["ok"] and out["builder"]["reason_code"] == "no_builder_address"
        assert out["builder"]["address"] == ""
    assert all(c["builder"] is None for c in sdk.calls) and len(sdk.calls) == 4
    assert builder_jobs == [] and fetch.threads == []


def test_account_equal_to_builder_never_attaches(sdk, public, fetch, builder_jobs, monkeypatch):
    monkeypatch.setattr(ex, "_resolve_identity",
                        lambda: ("0x" + "11" * 32, ADDR.upper().replace("0X", "0x"), None, False))
    for path in PATHS:
        out = PATHS[path]()
        assert out["builder"]["reason_code"] == "self_trade"
    assert all(c["builder"] is None for c in sdk.calls)
    assert builder_jobs == [] and fetch.threads == []


def test_no_identity_means_no_builder_and_the_order_still_goes(sdk, public, monkeypatch,
                                                              builder_jobs):
    def _boom():
        raise RuntimeError("HYPERLIQUID_PRIVATE_KEY not set")
    monkeypatch.setattr(ex, "_resolve_identity", _boom)
    out = PATHS["ioc"]()
    assert out["ok"] and sdk.calls[0]["builder"] is None
    assert out["builder"]["reason_code"] == "no_account"


# ── the venue says no ─────────────────────────────────────────────────────────

def _reject_with_builder(err):
    def reply(kind, b):
        return err if b is not None else _ok_reply(kind)
    return reply


@pytest.mark.parametrize("path", list(PATHS))
def test_venue_builder_rejection_resends_once_without(path, sdk, approved, builder_jobs):
    sdk.reply = _reject_with_builder(BUILDER_ERR)
    out = PATHS[path]()
    assert out["ok"], out
    assert [c["builder"] is not None for c in sdk.calls] == [True, False]
    assert out["builder"]["retried_without_builder"] is True
    assert out["builder"]["attached"] is False
    # cooldown: the next order sends no builder at all, one SDK call
    PATHS[path]()
    assert [c["builder"] is not None for c in sdk.calls] == [True, False, False]
    assert builder.last_decision(ACCT)["reason_code"] == "rejected_cooldown"
    assert builder.builder_status(ACCT)["counters"]["retried"] == 1


@pytest.mark.parametrize("path", list(PATHS))
def test_unrelated_wording_error_with_builder_is_resent_without(path, sdk, approved):
    sdk.reply = _reject_with_builder(MARGIN_ERR)
    out = PATHS[path]()
    assert out["ok"]
    assert [c["builder"] is not None for c in sdk.calls] == [True, False]


def test_cooldown_only_when_resend_places_or_text_names_builder(sdk, approved, builder_jobs):
    # both sends fail, no builder wording: a real refusal, not the builder; no cooldown
    sdk.reply = lambda kind, b: MARGIN_ERR
    out = PATHS["ioc"]()
    assert not out["ok"] and len(sdk.calls) == 2
    assert builder.builder_for_order(ACCT) is not None, "no cooldown"
    # both fail and the first names the builder: cooldown
    sdk.calls.clear()
    sdk.reply = lambda kind, b: BUILDER_ERR if b is not None else MARGIN_ERR
    PATHS["ioc"]()
    assert len(sdk.calls) == 2
    assert builder.builder_for_order(ACCT) is None
    assert builder.last_decision(ACCT)["reason_code"] == "rejected_cooldown"


def test_cooldown_starts_when_the_resend_places(sdk, approved):
    sdk.reply = _reject_with_builder(MARGIN_ERR)
    PATHS["limit"]()
    assert builder.builder_for_order(ACCT) is None
    assert builder.last_decision(ACCT)["reason_code"] == "rejected_cooldown"


def test_bulk_all_legs_error_resends(sdk, approved):
    all_err = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
        {"error": "Order has invalid price."}, {"error": "x"}, {"error": "y"}]}}}
    sdk.reply = lambda kind, b: all_err if b is not None else BULK_OK
    out = PATHS["bracket"]()
    assert out["ok"] and len(sdk.calls) == 2 and sdk.calls[1]["builder"] is None
    assert out["builder"]["retried_without_builder"] is True


def test_single_leg_error_with_builder_resends(sdk, approved):
    single_err = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
        {"error": "Order could not immediately match against any resting orders."}]}}}
    sdk.reply = lambda kind, b: single_err if b is not None else FILLED
    out = PATHS["ioc"]()
    assert out["ok"] and [c["builder"] is not None for c in sdk.calls] == [True, False]


@pytest.mark.parametrize("statuses", [
    [{"resting": {"oid": 1}}, {"error": "x"}, {"error": "y"}],
    [{"filled": {"oid": 1, "totalSz": "1", "avgPx": "1"}}],
    [{"error": "x"}, "waitingForFill", "waitingForFill"],
    ["waitingForFill"],
])
def test_no_resend_when_a_leg_rested_or_filled(statuses, sdk, approved):
    reply = {"status": "ok", "response": {"type": "order", "data": {"statuses": statuses}}}
    sdk.reply = lambda kind, b: reply
    PATHS["bracket"]()
    PATHS["ioc"]()
    assert len(sdk.calls) == 2 and all(c["builder"] for c in sdk.calls)


@pytest.mark.parametrize("reply", [
    {"status": "ok", "response": {"type": "order", "data": {"statuses": []}}},
    {"status": "ok", "response": {"type": "order"}},
    {"status": "ok"},
    "a string reply",
    None,
])
def test_no_resend_on_ok_with_empty_statuses(reply, sdk, approved):
    sdk.reply = lambda kind, b: reply
    PATHS["ioc"]()
    assert len(sdk.calls) == 1


@pytest.mark.parametrize("exc", [
    requests.exceptions.Timeout("read timed out"),
    requests.exceptions.ConnectionError("reset"),
    ClientError(422, None, "Builder fee has not been approved.", {}),
    RuntimeError("builder something"),
])
def test_exceptions_are_never_resent(exc, sdk, approved):
    sdk.reply = lambda kind, b: exc
    for path in PATHS:
        out = PATHS[path]()
        assert out["ok"] is False
    assert len(sdk.calls) == 4, "one send per order, never a second"
    assert builder.builder_for_order(ACCT) is not None, "an exception starts no cooldown"


def test_no_builder_means_exactly_one_send(sdk, public, fetch, builder_jobs):
    # cold cache: no builder; even a reply naming the builder is returned as is
    sdk.reply = lambda kind, b: BUILDER_ERR
    for path in PATHS:
        out = PATHS[path]()
        assert out["ok"] is False and "Builder fee" in out["error"]
    assert len(sdk.calls) == 4 and all(c["builder"] is None for c in sdk.calls)


def test_nothing_placed_table():
    assert ex._nothing_placed({"status": "err", "response": "x"}) is True
    assert ex._nothing_placed({"status": "ok", "response": {"data": {"statuses": [
        {"error": "a"}]}}}) is True
    assert ex._nothing_placed({"status": "ok", "response": {"data": {"statuses": []}}}) is False
    assert ex._nothing_placed({"status": "ok", "response": {"data": {"statuses": [
        {"error": "a"}, "waitingForFill"]}}}) is False
    assert ex._nothing_placed({"status": "ok", "response": "weird"}) is False
    assert ex._nothing_placed("err") is False and ex._nothing_placed(None) is False
    assert ex._nothing_placed({}) is True      # no 'ok' status: nothing was placed


# ── a stop after a filled market entry ────────────────────────────────────────

@pytest.fixture
def market_bracket(monkeypatch, sdk):
    monkeypatch.setattr(bracket, "TARGET_OFFSET_FRAC", 0.0)
    monkeypatch.setattr(ex, "get_hl_price", lambda coin: 100.0)
    monkeypatch.setattr(ex, "resolve_user_address", lambda: ACCT)
    monkeypatch.setattr(ex, "_position_size_on_coin", lambda user, coin: 0.0)
    monkeypatch.setattr(ex, "_resting_orders_for_coin", lambda user, coin: [])
    monkeypatch.setattr(ex, "cancel_stranded_reduce_only_orders", lambda coin: 0)
    monkeypatch.setattr(ex, "set_leverage", lambda *a, **k: {"ok": True})

    def run():
        return bracket.place_bracket("BTC", entry_px=None, stop_px=95.0, tp_px=115.0,
                                     size=1.0, leverage=3, ex=ex)
    return run


def test_stop_after_market_entry_survives_a_builder_rejection(market_bracket, sdk, approved):
    sdk.reply = _reject_with_builder(BUILDER_ERR)      # every builder order refused
    out = market_bracket()
    assert out["ok"] and out["placed"] and out["filled_sz"] == 1.0, out
    assert "FILLED WITHOUT A STOP" not in str(out)
    assert out["result"]["sl"]["ok"] and out["result"]["tp"]["ok"]
    kinds = [(c["kind"], c["builder"] is not None) for c in sdk.calls]
    # the entry is resent; the cooldown then sends the stop and target bare
    assert kinds == [("ioc", True), ("ioc", False), ("trigger", False), ("trigger", False)]


def test_every_leg_lands_on_its_own_resend(market_bracket, sdk, approved, monkeypatch):
    monkeypatch.setattr(builder, "note_rejection", lambda account, text: None)   # no cooldown
    sdk.reply = _reject_with_builder(MARGIN_ERR)
    out = market_bracket()
    assert out["ok"] and "FILLED WITHOUT A STOP" not in str(out)
    kinds = [(c["kind"], c["builder"] is not None) for c in sdk.calls]
    assert kinds == [("ioc", True), ("ioc", False), ("trigger", True), ("trigger", False),
                     ("trigger", True), ("trigger", False)]
    for leg in ("entry", "sl", "tp"):
        assert out["result"][leg]["builder"]["retried_without_builder"] is True


# ── the note ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", list(PATHS))
def test_result_gains_builder_key_only_on_public(path, sdk, monkeypatch, builder_jobs):
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", ADDR)
    out = PATHS[path]()
    assert "builder" not in out, "operator result shape unchanged"
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    out = PATHS[path]()
    assert set(out["builder"]) == {"attached", "fee_tenths_bp", "fee_rate", "address",
                                   "reason_code", "retried_without_builder"}


def test_error_bracket_reply_carries_the_note_on_public(sdk, approved):
    sdk.reply = lambda kind, b: MARGIN_ERR
    out = PATHS["bracket"]()
    assert out["ok"] is False and "Insufficient margin" in out["error"]
    assert out["builder"]["retried_without_builder"] is True
