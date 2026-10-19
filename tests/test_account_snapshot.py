"""pathiel.client.account_snapshot + the limiter's background priority.

The bug (2026-10-19): every dashboard poll did its own fresh account read (12
requests, 24 weight, on a 2 weight/s budget), reads took 6-12 s, polls piled
up and held the browser's connections, and clicking Desks stalled. A clicked
BTC refresh sat behind a 213 s background rebuild. These pin the fix: one
shared read, served at once, re-read in the background, cheap between
sweeps, never replaced by a failed or partial read, and background work that
yields to the foreground.

Offline: FETCH and LIST_DEXES are swapped for fakes; no test reaches the venue.
"""
from __future__ import annotations

import threading
import time

import pytest

from pathiel.client import account_snapshot as snap
from pathiel.client import rate_limit
from pathiel.client.rate_limit import TokenBucket, background_priority, carry_priority

USER = "0x" + "ab" * 20
DEXES = ["xyz", "flx", "vntl", "hyna", "km"]


def _state(main=0.01, xyz=15.18, km=0.0, positions=(), answered=None):
    dex_equity = {"": main, "xyz": xyz, "flx": 0.0, "vntl": 0.0, "hyna": 0.0, "km": km}
    answered = set(dex_equity) if answered is None else set(answered) | {""}
    dex_equity = {d: v for d, v in dex_equity.items() if d in answered}
    return {"equity": sum(dex_equity.values()), "dex_equity": dex_equity,
            "queried_dexes": answered, "asset_positions": list(positions)}


class FakeVenue:
    """Records every read: which dexes it asked, strict or not, and on which
    priority it ran. `script` is a queue of states (or exceptions) to return."""

    def __init__(self, *script, delay=0.0):
        self.script = list(script)
        self.calls = []
        self.delay = delay
        self.release = None          # a threading.Event the read waits on, if set

    def __call__(self, user, include_hip3, hip3_dexes=None, strict=False):
        self.calls.append({"user": user, "dexes": hip3_dexes, "strict": strict,
                           "background": rate_limit.in_background()})
        if self.release is not None:
            self.release.wait(5)
        if self.delay:
            time.sleep(self.delay)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def venue(monkeypatch):
    def install(*script, delay=0.0):
        v = FakeVenue(*script, delay=delay)
        monkeypatch.setattr(snap, "FETCH", v)
        monkeypatch.setattr(snap, "LIST_DEXES", lambda: list(DEXES))
        return v
    return install


@pytest.fixture
def clock(monkeypatch):
    """time.monotonic for the snapshot module, moved by hand."""
    now = {"t": 1000.0}
    monkeypatch.setattr(snap.time, "monotonic", lambda: now["t"])
    return now


# ── freshness ────────────────────────────────────────────────────────────────

def test_a_fresh_snapshot_is_served_without_a_read(venue, clock):
    v = venue(_state())
    first = snap.account_state(USER)
    clock["t"] += snap.fresh_s() - 1
    second = snap.account_state(USER)
    assert len(v.calls) == 1
    assert first["equity"] == second["equity"] == pytest.approx(15.19)
    assert second["snapshot_age_s"] == pytest.approx(snap.fresh_s() - 1)


def test_an_older_snapshot_is_served_at_once_and_reread_in_the_background(venue, clock):
    v = venue(_state(xyz=15.18), _state(xyz=20.0))
    snap.account_state(USER)
    clock["t"] += snap.fresh_s() + 1
    v.release = threading.Event()             # hold the background read open
    served = snap.account_state(USER)
    assert served["equity"] == pytest.approx(15.19), "stale-while-revalidate serves the old read"
    v.release.set()
    assert snap.wait_idle(USER)
    assert len(v.calls) == 2 and v.calls[1]["background"] is True
    assert snap.account_state(USER)["equity"] == pytest.approx(20.01)


def test_ten_callers_on_a_stale_snapshot_start_one_read(venue, clock):
    v = venue(_state())
    snap.account_state(USER)
    clock["t"] += snap.fresh_s() + 1
    v.release = threading.Event()
    for _ in range(10):
        snap.account_state(USER)
    v.release.set()
    assert snap.wait_idle(USER)
    assert len(v.calls) == 2


def test_ten_cold_callers_share_one_read(venue):
    v = venue(_state(), delay=0.2)
    out = []
    threads = [threading.Thread(target=lambda: out.append(snap.account_state(USER))) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(out) == 10 and len(v.calls) == 1
    assert v.calls[0]["background"] is False, "a caller waiting on it reads at its own priority"


def test_a_snapshot_too_old_to_show_makes_the_caller_wait(venue, clock):
    v = venue(_state(xyz=15.18), _state(xyz=3.0))
    snap.account_state(USER)
    clock["t"] += snap.max_stale_s() + 1
    assert snap.account_state(USER)["equity"] == pytest.approx(3.01)
    assert len(v.calls) == 2 and v.calls[1]["background"] is False


def test_max_age_zero_always_reads_and_never_serves_stale(venue, clock):
    v = venue(_state(xyz=15.18), _state(xyz=7.0), RuntimeError("venue down"))
    snap.account_state(USER)
    assert snap.account_state(USER, max_age_s=0)["equity"] == pytest.approx(7.01)
    clock["t"] += 1
    with pytest.raises(snap.AccountReadError):
        snap.account_state(USER, max_age_s=0)     # the last read is 1 s old: not good enough
    assert len(v.calls) == 3


# ── what a read asks, and what it may replace ───────────────────────────────

def test_reads_between_sweeps_ask_only_the_dexes_that_held_money(venue, clock):
    pos = [{"position": {"coin": "vntl:SPACEX", "szi": "1"}}]
    v = venue(_state(km=0.004, positions=pos))
    snap.account_state(USER)
    assert v.calls[0]["dexes"] == DEXES, "the first read sweeps every dex"
    clock["t"] += snap.fresh_s() + 1
    snap.account_state(USER)                   # served at once; the re-read is the cheap one
    assert snap.wait_idle(USER)
    assert v.calls[1]["dexes"] == ["km", "vntl", "xyz"], "money (even dust) or a position"
    assert all(c["strict"] for c in v.calls), "a main/spot read that never landed must raise"


def test_a_full_sweep_runs_again_after_sweep_s(venue, clock, monkeypatch):
    monkeypatch.setenv("PATHIEL_ACCOUNT_SWEEP_S", "600")
    v = venue(_state())
    snap.account_state(USER)
    clock["t"] += snap.max_stale_s() + 1       # 301 s: a cheap read
    snap.account_state(USER)
    clock["t"] += 400                          # 701 s since the sweep: sweep again
    snap.account_state(USER)
    assert [c["dexes"] for c in v.calls] == [DEXES, ["xyz"], DEXES]


def test_a_failed_read_keeps_the_previous_snapshot(venue, clock):
    v = venue(_state(xyz=15.18), RuntimeError("rate budget exhausted"))
    snap.account_state(USER)
    clock["t"] += snap.fresh_s() + 1
    assert snap.account_state(USER)["equity"] == pytest.approx(15.19)
    assert snap.wait_idle(USER)
    assert snap.account_state(USER)["equity"] == pytest.approx(15.19), "a failure never reads as $0"
    assert snap.wait_idle(USER)
    assert len(v.calls) >= 2


def test_a_read_that_lost_a_funded_dex_is_thrown_away(venue, clock, caplog):
    v = venue(_state(xyz=15.18), _state(answered=["flx"]))   # xyz did not answer
    snap.account_state(USER)
    clock["t"] += snap.fresh_s() + 1
    snap.account_state(USER)
    assert snap.wait_idle(USER)
    assert snap.account_state(USER)["equity"] == pytest.approx(15.19), "not $0.01"
    assert snap.wait_idle(USER)
    assert "held money last read and did not answer" in caplog.text
    assert len(v.calls) == 3, "rejected, so the next caller re-reads rather than trusting it"


def test_too_old_to_show_and_the_reread_failed_is_an_error_not_old_money(venue, clock):
    venue(_state(xyz=15.18), RuntimeError("venue down"))
    snap.account_state(USER)
    clock["t"] += snap.max_stale_s() + 1
    with pytest.raises(snap.AccountReadError, match="venue down"):
        snap.account_state(USER)


def test_with_nothing_to_fall_back_on_a_failed_read_raises(venue):
    venue(RuntimeError("venue down"))
    with pytest.raises(snap.AccountReadError, match="venue down"):
        snap.account_state(USER)


def test_a_dex_that_did_not_answer_the_sweep_is_asked_again_next_read(venue, clock):
    v = venue(_state(answered=["xyz", "flx", "vntl", "hyna"]), _state())   # km silent
    snap.account_state(USER)
    clock["t"] += snap.fresh_s() + 1
    snap.account_state(USER)
    assert snap.wait_idle(USER)
    assert v.calls[1]["dexes"] == ["km", "xyz"]


def test_snapshots_are_per_address_and_per_scope(venue):
    v = venue(_state())
    snap.account_state(USER)
    snap.account_state(USER.upper().replace("0X", "0x"))     # same address, other case
    snap.account_state(USER, include_hip3=False)
    snap.account_state("0x" + "cd" * 20)
    assert len(v.calls) == 3
    assert v.calls[1]["dexes"] is None, "a main-only read asks no HIP-3 dex"


def test_no_address_is_an_error_not_a_read(venue):
    v = venue(_state())
    with pytest.raises(snap.AccountReadError):
        snap.account_state("")
    assert v.calls == []


# ── the limiter: background yields, and says what it spent ──────────────────

def test_background_leaves_the_reserve_for_the_foreground():
    b = TokenBucket(capacity=60, refill_per_sec=0.0, background_reserve=30)
    assert b.acquire(20, max_wait=0.01, background=True) is True     # 60 -> 40
    assert b.acquire(20, max_wait=0.01, background=True) is False    # would leave 20 < 30
    assert b.acquire(20, max_wait=0.01) is True                      # foreground may: 40 -> 20
    assert b.acquire(20, max_wait=0.01) is True                      # 20 -> 0
    stats = b.stats()
    assert stats["granted_weight"] == {"background": {"unknown": 20}, "foreground": {"unknown": 40}}
    assert stats["refused_requests"] == {"background": {"unknown": 1}}


def test_a_reserve_bigger_than_the_bucket_never_locks_background_out():
    b = TokenBucket(capacity=40, refill_per_sec=0.0, background_reserve=100)
    assert b.acquire(20, max_wait=0.01, background=True) is True


def test_background_waits_for_the_reserve_to_refill():
    b = TokenBucket(capacity=60, refill_per_sec=200.0, background_reserve=30)
    assert b.acquire(60, max_wait=0.01) is True                      # drain it
    t0 = time.monotonic()
    assert b.acquire(20, max_wait=2.0, background=True) is True      # needs 50 back
    assert time.monotonic() - t0 >= 0.2


def test_default_reserve_is_half_the_bucket():
    assert TokenBucket(capacity=60, refill_per_sec=2).stats()["background_reserve"] == 30


def test_priority_is_thread_local_and_carried_into_pools_only_on_purpose():
    seen = {}
    with background_priority():
        assert rate_limit.in_background() is True
        plain = threading.Thread(target=lambda: seen.setdefault("plain", rate_limit.in_background()))
        carried = threading.Thread(target=carry_priority(
            lambda: seen.setdefault("carried", rate_limit.in_background())))
        plain.start(); carried.start(); plain.join(); carried.join()
    assert rate_limit.in_background() is False
    assert seen == {"plain": False, "carried": True}


def test_the_hl_client_tells_the_limiter_the_priority_and_request_type(monkeypatch):
    from pathiel.client import hl_client
    seen = []
    monkeypatch.setattr(hl_client._HL_LIMITER, "acquire",
                        lambda w, max_wait, **kw: seen.append(kw) or False)
    with background_priority():
        assert hl_client._http_post("/info", {"type": "candleSnapshot"}) is None
    assert hl_client._http_post("/info", {"type": "clearinghouseState"}) is None
    assert seen == [{"background": True, "label": "candleSnapshot"},
                    {"background": False, "label": "clearinghouseState"}]


# ── fetch_account_state: the two new options, and the old default ───────────

class _Info:
    def __init__(self, monkeypatch, fail=()):
        from pathiel.client import hl_client
        self.asked = []
        self.fail = set(fail)

        def strict(path, payload, timeout=5):
            key = (payload["type"], payload.get("dex", ""))
            self.asked.append(key)
            if key in self.fail:
                raise hl_client.HLRequestError(f"rate budget exhausted for {key}")
            if payload["type"] == "spotClearinghouseState":
                return {"balances": []}
            value = {"": "0.01", "xyz": "15.18"}.get(payload.get("dex", ""), "0")
            return {"marginSummary": {"accountValue": value}, "assetPositions": []}
        monkeypatch.setattr(hl_client, "_http_post_strict", strict)
        monkeypatch.setattr("pathiel.client.universe.list_hip3_dexes", lambda: list(DEXES))


def test_hip3_dexes_limits_the_read_to_those_dexes(monkeypatch):
    from pathiel.client.hl_client import fetch_account_state
    info = _Info(monkeypatch)
    state = fetch_account_state(USER, include_hip3=True, hip3_dexes=["xyz"])
    assert sorted(d for t, d in info.asked if t == "clearinghouseState") == ["", "xyz"]
    assert state["equity"] == pytest.approx(15.19) and state["queried_dexes"] == {"", "xyz"}


def test_without_hip3_dexes_every_listed_dex_is_read(monkeypatch):
    from pathiel.client.hl_client import fetch_account_state
    info = _Info(monkeypatch)
    fetch_account_state(USER, include_hip3=True)
    assert sorted(d for t, d in info.asked if t == "clearinghouseState") == sorted([""] + DEXES)


def test_strict_raises_when_the_main_read_never_landed(monkeypatch):
    from pathiel.client.hl_client import HLRequestError, fetch_account_state
    _Info(monkeypatch, fail={("clearinghouseState", "")})
    with pytest.raises(HLRequestError):
        fetch_account_state(USER, include_hip3=True, strict=True)


def test_the_default_still_reads_a_failed_main_as_empty_for_the_loop(monkeypatch):
    """The trading loop and the executor depend on the old contract; strict
    is opt-in for display reads only."""
    from pathiel.client.hl_client import fetch_account_state
    _Info(monkeypatch, fail={("clearinghouseState", "")})
    state = fetch_account_state(USER, include_hip3=True)
    assert state["dex_equity"][""] == 0.0 and state["dex_equity"]["xyz"] == pytest.approx(15.18)


def test_the_dex_fan_out_keeps_the_callers_priority(monkeypatch):
    from pathiel.client.hl_client import fetch_account_state
    seen = []
    _Info(monkeypatch)
    from pathiel.client import hl_client
    inner = hl_client._http_post_strict
    monkeypatch.setattr(hl_client, "_http_post_strict",
                        lambda p, payload, timeout=5: seen.append(rate_limit.in_background())
                        or inner(p, payload, timeout))
    with background_priority():
        fetch_account_state(USER, include_hip3=True)
    assert seen and all(seen), "pool threads must not turn background reads into foreground ones"
