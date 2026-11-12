"""pathiel.client.builder: the builder fee decision is cache only and never
costs an order anything.

I2 (docs/overhaul/PUBLIC_PLUGIN_2026-11-12.md): builder state never fails,
blocks or delays an order. builder_for_order() must not touch the network, the
rate limiter or _fetch on the calling thread in any state; the approval read
runs in the background (the root conftest swaps _spawn for a recorder, so a
job only runs when a test calls it).
"""
from __future__ import annotations

import threading
import time

import pytest

from pathiel.client import builder

ADDR = "0x" + "b1" * 20
ACCT = "0x" + "a1" * 20

# Captured at collection, before the root conftest swaps _spawn for a recorder.
_REAL_SPAWN = builder._spawn


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FetchStub:
    """Records every call with the thread it ran on."""

    def __init__(self, value=10, exc: Exception | None = None):
        self.value, self.exc, self.calls = value, exc, []

    def __call__(self, user, b):
        self.calls.append((user, b, threading.current_thread()))
        if self.exc is not None:
            raise self.exc
        return self.value


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(builder, "_clock", c)
    return c


@pytest.fixture
def fetch(monkeypatch):
    f = FetchStub()
    monkeypatch.setattr(builder, "_fetch", f)
    return f


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", ADDR)
    monkeypatch.delenv("PATHIEL_BUILDER_FEE_TENTHS_BP", raising=False)
    monkeypatch.delenv("PATHIEL_NETWORK", raising=False)


def _run(jobs):
    while jobs:
        jobs.pop(0)()


def _warm(jobs, value=10, fetch_stub=None):
    """Fill the cache for ACCT with `value` through the real background job."""
    builder.builder_for_order(ACCT)
    if fetch_stub is not None:
        fetch_stub.value = value
    _run(jobs)


# ── decision table ────────────────────────────────────────────────────────────

def _state(name, monkeypatch, jobs, fetch, clock):
    """Put the builder in state `name`; return the account to order from."""
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", ADDR)
    if name == "operator_install":
        monkeypatch.delenv("PATHIEL_INSTALL")
    elif name == "no_builder_address":
        monkeypatch.delenv("PATHIEL_BUILDER_ADDRESS")
    elif name == "fee_zero":
        monkeypatch.setenv("PATHIEL_BUILDER_FEE_TENTHS_BP", "0")
    elif name == "self_trade":
        return ADDR.upper().replace("0X", "0x")
    elif name == "rejected_cooldown":
        _warm(jobs, 10, fetch)
        builder.note_rejection(ACCT, "Builder fee has not been approved.")
    elif name == "cache_cold":
        pass
    elif name == "approval_read_failed":
        fetch.exc = RuntimeError("timeout")
        _warm(jobs, 10, fetch)
    elif name == "not_approved":
        _warm(jobs, 5, fetch)
    elif name == "approved":
        _warm(jobs, 10, fetch)
    else:
        raise AssertionError(name)
    return ACCT


REASON_CODES = ["operator_install", "no_builder_address", "fee_zero", "self_trade",
                "rejected_cooldown", "cache_cold", "approval_read_failed", "not_approved",
                "approved"]


@pytest.mark.parametrize("code", REASON_CODES)
def test_decision_table(code, monkeypatch, builder_jobs, fetch, clock):
    acct = _state(code, monkeypatch, builder_jobs, fetch, clock)
    got = builder.builder_for_order(acct)
    d = builder.last_decision(acct)
    assert d["reason_code"] == code
    assert d["attached"] is (code == "approved")
    assert (got is not None) is (code == "approved")
    assert set(REASON_CODES) <= set(builder.REASONS)


def test_no_account_is_its_own_reason(public, builder_jobs, fetch):
    assert builder.builder_for_order("") is None
    assert builder.last_decision("")["reason_code"] == "no_account"
    assert builder_jobs == [] and fetch.calls == []


@pytest.mark.parametrize("code", REASON_CODES)
def test_builder_for_order_never_calls_fetch(code, monkeypatch, builder_jobs, fetch, clock):
    acct = _state(code, monkeypatch, builder_jobs, fetch, clock)
    before = len(fetch.calls)

    def _no_limiter(*a, **k):
        raise AssertionError("builder_for_order touched the rate limiter")

    import requests

    from pathiel.client import rate_limit
    monkeypatch.setattr(rate_limit.HL_LIMITER, "acquire", _no_limiter)
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("builder_for_order made a request")))
    for _ in range(3):
        builder.builder_for_order(acct)
        clock.t += 700          # cross every refresh window
    assert len(fetch.calls) == before, "fetch ran on the calling thread"
    for job in builder_jobs:     # anything scheduled is a job, held unrun
        assert callable(job)


def test_cold_cache_schedules_one_job_and_returns_none(public, builder_jobs, fetch, clock):
    assert builder.builder_for_order(ACCT) is None
    assert builder.last_decision(ACCT)["reason_code"] == "cache_cold"
    assert len(builder_jobs) == 1 and fetch.calls == []
    builder.builder_for_order(ACCT)
    builder.builder_for_order(ACCT)
    assert len(builder_jobs) == 1, "in flight: no second job"


def test_job_fills_the_cache_then_next_order_attaches(public, builder_jobs, fetch, clock):
    assert builder.builder_for_order(ACCT) is None
    _run(builder_jobs)
    assert fetch.calls and fetch.calls[0][:2] == (ACCT, ADDR)
    assert builder.builder_for_order(ACCT) == {"b": ADDR, "f": 10}
    d = builder.last_decision(ACCT)
    assert d == {"attached": True, "reason_code": "approved", "approved_tenths_bp": 10, "age_s": 0.0}
    assert builder_jobs == [], "fresh approved entry: no refresh yet"


@pytest.mark.parametrize("setup,window", [("approved", builder.REFRESH_APPROVED_S),
                                          ("not_approved", builder.REFRESH_UNAPPROVED_S),
                                          ("error", builder.REFRESH_ERROR_S)])
def test_refresh_windows_with_a_fake_clock(setup, window, public, builder_jobs, fetch, clock):
    assert (builder.REFRESH_APPROVED_S, builder.REFRESH_UNAPPROVED_S,
            builder.REFRESH_ERROR_S) == (600.0, 60.0, 30.0)
    if setup == "error":
        fetch.exc = RuntimeError("down")
    _warm(builder_jobs, 10 if setup == "approved" else 3, fetch)
    clock.t += window - 1
    builder.builder_for_order(ACCT)
    assert builder_jobs == [], f"{setup}: no refresh before {window}s"
    clock.t += 1
    builder.builder_for_order(ACCT)
    builder.builder_for_order(ACCT)
    assert len(builder_jobs) == 1, f"{setup}: exactly one refresh at {window}s"


def test_stale_approved_attaches_and_refreshes(public, builder_jobs, fetch, clock):
    _warm(builder_jobs, 10, fetch)
    clock.t += builder.REFRESH_APPROVED_S + 5
    assert builder.builder_for_order(ACCT) == {"b": ADDR, "f": 10}
    assert len(builder_jobs) == 1
    # the refresh fails: the last good value keeps attaching
    fetch.exc = RuntimeError("venue down")
    _run(builder_jobs)
    assert builder.builder_for_order(ACCT) == {"b": ADDR, "f": 10}


def test_older_than_stale_ok_is_cold(public, builder_jobs, fetch, clock):
    _warm(builder_jobs, 10, fetch)
    fetch.exc = RuntimeError("venue down")
    clock.t += builder.REFRESH_APPROVED_S + 1
    builder.builder_for_order(ACCT)
    _run(builder_jobs)                    # refresh fails; value kept
    clock.t += builder.STALE_OK_S
    assert builder.builder_for_order(ACCT) is None
    assert builder.last_decision(ACCT)["reason_code"] in ("cache_cold", "approval_read_failed")
    clock.t += builder.STALE_OK_S + 1
    assert builder.builder_for_order(ACCT) is None
    assert builder.last_decision(ACCT)["reason_code"] == "cache_cold"


def test_in_flight_dedupe(public, builder_jobs, fetch, clock):
    for _ in range(10):
        builder.builder_for_order(ACCT)
        builder.warm(ACCT)
    assert len(builder_jobs) == 1
    other = "0x" + "c3" * 20
    builder.builder_for_order(other)
    assert len(builder_jobs) == 2, "a different account is a different key"
    _run(builder_jobs)
    builder.builder_for_order(ACCT)
    assert builder_jobs == [], "landed and fresh: nothing to do inside the window"


def test_rejection_cooldown_then_cold(public, builder_jobs, fetch, clock):
    _warm(builder_jobs, 10, fetch)
    builder.note_rejection(ACCT, "Builder fee has not been approved.")
    assert builder.builder_for_order(ACCT) is None
    assert builder.last_decision(ACCT)["reason_code"] == "rejected_cooldown"
    assert builder_jobs == [], "no read during the cooldown"
    clock.t += builder.REJECT_COOLDOWN_S - 1
    assert builder.builder_for_order(ACCT) is None
    assert builder.last_decision(ACCT)["reason_code"] == "rejected_cooldown"
    clock.t += 1
    assert builder.builder_for_order(ACCT) is None
    assert builder.last_decision(ACCT)["reason_code"] == "cache_cold", "the entry was dropped"
    assert len(builder_jobs) == 1


# ── the read itself ───────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


@pytest.fixture
def post(monkeypatch):
    import requests

    from pathiel.client import hl_client
    calls = []

    def _post(url, json=None, timeout=None, **kw):
        calls.append({"url": url, "json": json, "timeout": timeout, "kw": kw})
        return post.reply

    from pathiel.client import rate_limit
    post.reply = _Resp(10)
    post.calls = calls
    monkeypatch.setattr(requests, "post", _post)
    # a full bucket, so a busy suite cannot turn a read into a limiter refusal
    monkeypatch.setattr(rate_limit.HL_LIMITER, "acquire", lambda *a, **k: True)
    monkeypatch.setattr(hl_client, "_get_session", lambda: (_ for _ in ()).throw(
        AssertionError("the approval read used the shared session")))
    return post


def test_fetch_uses_one_plain_post_with_1s_2s_timeout(post):
    from pathiel.client import hl_client
    assert builder.fetch_max_builder_fee(ACCT, ADDR) == 10
    assert len(post.calls) == 1
    c = post.calls[0]
    assert c["url"] == f"{hl_client.HL_API}/info"
    assert c["json"] == {"type": "maxBuilderFee", "user": ACCT, "builder": ADDR}
    assert c["timeout"] == (1.0, 2.0) == (builder.CONNECT_TIMEOUT_S, builder.READ_TIMEOUT_S)
    assert c["kw"] == {}


def test_fetch_does_not_use_the_shared_session(post, monkeypatch):
    from pathiel.client import rate_limit
    seen = {}

    def _acquire(weight, max_wait=10.0, *, background=False, label=""):
        seen.update(weight=weight, max_wait=max_wait, background=background, label=label)
        return True

    monkeypatch.setattr(rate_limit.HL_LIMITER, "acquire", _acquire)
    builder.fetch_max_builder_fee(ACCT, ADDR)       # _get_session raises if touched
    assert seen == {"weight": rate_limit.endpoint_weight("maxBuilderFee"), "max_wait": 1.0,
                    "background": True, "label": "maxBuilderFee<builder"}
    monkeypatch.setattr(rate_limit.HL_LIMITER, "acquire", lambda *a, **k: False)
    n = len(post.calls)
    with pytest.raises(RuntimeError, match="rate budget"):
        builder.fetch_max_builder_fee(ACCT, ADDR)
    assert len(post.calls) == n, "a refused limiter sends nothing"


@pytest.mark.parametrize("body,status", [("10", 200), (True, 200), (10.0, 200), (None, 200),
                                         ({"err": 1}, 200), (10, 500), (10, 429)])
def test_non_integer_body_is_a_read_failure(body, status, post, public, builder_jobs, clock,
                                            monkeypatch):
    post.reply = _Resp(body, status)
    with pytest.raises(Exception):
        builder.fetch_max_builder_fee(ACCT, ADDR)
    # through the refresh job: stored as a read failure, never an approval
    monkeypatch.setattr(builder, "_fetch", builder.fetch_max_builder_fee)
    builder.builder_for_order(ACCT)
    _run(builder_jobs)
    assert builder.builder_for_order(ACCT) is None
    assert builder.last_decision(ACCT)["reason_code"] == "approval_read_failed"


def test_zero_approval_is_an_int_answer(post):
    post.reply = _Resp(0)
    assert builder.fetch_max_builder_fee(ACCT, ADDR) == 0


# ── the dict the SDK signs ────────────────────────────────────────────────────

def test_builder_dict_key_order_and_int_fee(public, builder_jobs, fetch, clock, monkeypatch):
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", ADDR.upper().replace("0X", "0x"))
    monkeypatch.setenv("PATHIEL_BUILDER_FEE_TENTHS_BP", "7")
    _warm(builder_jobs, 50, fetch)
    b = builder.builder_for_order(ACCT)
    assert list(b.keys()) == ["b", "f"]
    assert type(b["f"]) is int and b["f"] == 7
    assert b["b"] == ADDR and b["b"] == b["b"].lower()


def test_builder_dict_is_fresh_each_call(public, builder_jobs, fetch, clock):
    _warm(builder_jobs, 10, fetch)
    first = builder.builder_for_order(ACCT)
    first["b"] = "MUTATED"
    first["f"] = 99
    second = builder.builder_for_order(ACCT)
    assert second == {"b": ADDR, "f": 10} and second is not first


def test_approval_above_the_fee_attaches_the_fee_not_the_approval(public, builder_jobs, fetch,
                                                                   clock):
    _warm(builder_jobs, 100, fetch)
    assert builder.builder_for_order(ACCT) == {"b": ADDR, "f": 10}


# ── status ────────────────────────────────────────────────────────────────────

def test_status_refresh_is_bounded_by_the_hard_deadline(public, monkeypatch):
    gate = threading.Event()

    def _slow(user, b):
        gate.wait(5)
        return 10

    monkeypatch.setattr(builder, "_fetch", _slow)
    monkeypatch.setattr(builder, "HARD_DEADLINE_S", 0.2)
    t0 = time.monotonic()
    try:
        st = builder.builder_status(ACCT, refresh=True)
        elapsed = time.monotonic() - t0
    finally:
        gate.set()
    assert elapsed < 0.2 + 0.5, elapsed
    assert st["reason_code"] == "approval_read_failed" and st["attaches"] is False


def test_status_refresh_reads_once_synchronously(public, fetch, builder_jobs):
    st = builder.builder_status(ACCT, refresh=True)
    assert len(fetch.calls) == 1 and fetch.calls[0][2] is not threading.current_thread()
    assert st["attaches"] is True and st["approved_tenths_bp"] == 10
    assert builder_jobs == []


def test_status_shape_and_counters(public, builder_jobs, fetch, clock):
    st = builder.builder_status(ACCT)
    assert set(st) == {"enabled", "address", "fee_tenths_bp", "fee_rate", "network",
                       "approved_tenths_bp", "attaches", "reason_code", "reason", "counters"}
    assert st["enabled"] is True and st["address"] == ADDR
    assert st["fee_tenths_bp"] == 10 and st["fee_rate"] == "0.01%"
    assert st["network"] == "testnet" and st["reason_code"] == "cache_cold"
    assert st["counters"] == {"with_builder": 0, "without_builder": 0, "retried": 0, "refused": 0}
    assert len(builder_jobs) == 1, "a cache view warms the cache"
    _run(builder_jobs)
    builder.count("with_builder")
    builder.count("retried")
    st = builder.builder_status(ACCT)
    assert st["attaches"] is True and st["approved_tenths_bp"] == 10
    assert st["counters"] == {"with_builder": 1, "without_builder": 0, "retried": 1, "refused": 0}
    assert st["reason"] == builder.REASONS["approved"]


def test_status_on_the_operator_install_is_disabled(monkeypatch, builder_jobs):
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", ADDR)
    st = builder.builder_status(ACCT)
    assert st["enabled"] is False and st["reason_code"] == "operator_install"
    assert st["network"] == "mainnet" and builder_jobs == []


def test_never_raises_on_a_broken_fetch(public, builder_jobs, monkeypatch, clock):
    for exc in (RuntimeError("x"), OSError("y"), ValueError("z"), TypeError("w")):
        monkeypatch.setattr(builder, "_fetch", FetchStub(exc=exc))
        builder.reset()
        builder.builder_for_order(ACCT)
        _run(builder_jobs)
        assert builder.builder_for_order(ACCT) is None
    monkeypatch.setattr(builder, "_spawn", lambda job: (_ for _ in ()).throw(RuntimeError("no thread")))
    builder.reset()
    assert builder.builder_for_order(ACCT) is None
    assert builder.warm(ACCT) is False
    monkeypatch.setattr(builder, "_decide", lambda *a: 1 / 0)
    assert builder.builder_for_order(ACCT) is None


def test_warm_is_a_noop_for_operator_and_unset_address(monkeypatch, builder_jobs, fetch):
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", ADDR)
    assert builder.warm(ACCT) is False                       # operator
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    monkeypatch.delenv("PATHIEL_BUILDER_ADDRESS")
    assert builder.warm(ACCT) is False                       # no address
    monkeypatch.setenv("PATHIEL_BUILDER_ADDRESS", ADDR)
    monkeypatch.setenv("PATHIEL_BUILDER_FEE_TENTHS_BP", "0")
    assert builder.warm(ACCT) is False                       # fee 0
    monkeypatch.delenv("PATHIEL_BUILDER_FEE_TENTHS_BP")
    assert builder.warm("") is False and builder.warm(None) is False
    assert builder.warm(ADDR) is False                       # self trade
    assert builder_jobs == [] and fetch.calls == []
    assert builder.warm(ACCT) is True and len(builder_jobs) == 1
    assert builder.warm(ACCT) is False                       # already in flight


def test_default_spawn_starts_a_daemon_thread():
    seen = {}
    done = threading.Event()

    def job():
        t = threading.current_thread()
        seen.update(name=t.name, daemon=t.daemon)
        done.set()

    _REAL_SPAWN(job)
    assert done.wait(2)
    assert seen == {"name": "pathiel-builder-refresh", "daemon": True}


def test_cache_is_keyed_by_network(public, builder_jobs, fetch, clock, monkeypatch):
    _warm(builder_jobs, 10, fetch)
    assert builder.builder_for_order(ACCT) is not None
    monkeypatch.setenv("PATHIEL_NETWORK", "mainnet")
    assert builder.builder_for_order(ACCT) is None
    assert builder.last_decision(ACCT)["reason_code"] == "cache_cold"
