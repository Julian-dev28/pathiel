"""Unit tests for services/intel/cache.py: TTL hit/miss, stale fallback on
failure, and the negative cache (recent-failure backoff) that makes the
"under 100ms cached" target hold even when a source is persistently down —
see cache.py's module docstring for the real measurement that caught this.

Every test passes a resolved directory (a pytest tmp_path subdirectory),
never a bare desk-id string — cache.py takes a directory on purpose (see
its module docstring: a desk-id-plus-live-env-lookup design is exactly
what let a real orphaned-thread bug write into the repo root during
development)."""
from __future__ import annotations

import os
import time

from services.intel import cache


def test_miss_then_hit(tmp_path):
    d = str(tmp_path / "oil")
    calls = []

    def fetch():
        calls.append(1)
        return {"x": 1}, None

    data, err = cache.get_or_fetch(d, "k1", 60.0, fetch)
    assert data == {"x": 1} and err is None and len(calls) == 1

    data2, err2 = cache.get_or_fetch(d, "k1", 60.0, fetch)
    assert data2 == {"x": 1} and err2 is None
    assert len(calls) == 1, "fresh cache hit must not call fetch_fn again"


def test_ttl_expiry_triggers_refetch(tmp_path):
    d = str(tmp_path / "oil")
    calls = []

    def fetch():
        calls.append(1)
        return {"n": len(calls)}, None

    cache.get_or_fetch(d, "k2", 0.05, fetch)
    time.sleep(0.08)
    data, err = cache.get_or_fetch(d, "k2", 0.05, fetch)
    assert err is None
    assert data == {"n": 2}
    assert len(calls) == 2


def test_failure_with_no_prior_cache_returns_none_and_error(tmp_path):
    d = str(tmp_path / "oil")
    data, err = cache.get_or_fetch(d, "k3", 60.0, lambda: (None, "boom"))
    assert data is None
    assert err == "boom"


def test_failure_falls_back_to_stale_success(tmp_path):
    d = str(tmp_path / "oil")
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": True}, None
        return None, "dead_now"

    data, err = cache.get_or_fetch(d, "k4", 0.05, fetch)
    assert data == {"ok": True} and err is None

    time.sleep(0.08)  # expire the TTL so the next call actually re-fetches
    data2, err2 = cache.get_or_fetch(d, "k4", 0.05, fetch)
    assert data2 == {"ok": True}, "a failed refresh must fall back to stale data, not go empty"
    assert err2 is not None and "dead_now" in err2


def test_negative_cache_skips_network_on_repeat_failure(tmp_path):
    """The core fix: once a source fails, a SECOND call within the backoff
    window must not call fetch_fn again — that is what turns a
    persistently-dead source from "pays its full timeout every single
    call" into "pays once, then answers instantly." See cache.py's
    _FAILURE_BACKOFF_S and the module docstring's account of the real
    12-second repeat-call measurement this fixes."""
    d = str(tmp_path / "oil")
    calls = []

    def fetch():
        calls.append(1)
        return None, "dead"

    data, err = cache.get_or_fetch(d, "k5", 60.0, fetch)
    assert data is None and err == "dead"
    assert len(calls) == 1

    data2, err2 = cache.get_or_fetch(d, "k5", 60.0, fetch)
    assert len(calls) == 1, "repeat failure within the backoff window must not re-fetch"
    assert data2 is None
    assert err2 is not None and "recent_failure" in err2 and "dead" in err2


def test_negative_cache_still_serves_stale_success_if_any(tmp_path):
    d = str(tmp_path / "oil")
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": 1}, None
        return None, "now_dead"

    cache.get_or_fetch(d, "k6", 0.05, fetch)
    time.sleep(0.08)
    data, err = cache.get_or_fetch(d, "k6", 0.05, fetch)  # this one fails + caches the failure
    assert data == {"ok": 1} and "now_dead" in err

    # Third call: TTL still expired, but failure backoff is fresh — must
    # short-circuit on the negative cache AND still serve the stale value.
    data3, err3 = cache.get_or_fetch(d, "k6", 0.05, fetch)
    assert calls["n"] == 2, "the negative cache must prevent a third live call"
    assert data3 == {"ok": 1}
    assert "recent_failure" in err3


def test_success_clears_a_prior_failure_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_FAILURE_BACKOFF_S", 0.05)
    d = str(tmp_path / "oil")
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        if calls["n"] == 1:
            return None, "flaky"
        return {"recovered": True}, None

    data, err = cache.get_or_fetch(d, "k7", 0.05, fetch)
    assert data is None and err == "flaky"

    time.sleep(0.08)  # TTL (irrelevant here, no success cache yet) and failure backoff both long gone
    data2, err2 = cache.get_or_fetch(d, "k7", 0.05, fetch)
    assert data2 == {"recovered": True} and err2 is None
    assert calls["n"] == 2

    # And a THIRD call, immediately, must hit the now-fresh success cache
    # rather than the (cleared) failure marker.
    data3, err3 = cache.get_or_fetch(d, "k7", 60.0, fetch)
    assert data3 == {"recovered": True} and err3 is None
    assert calls["n"] == 2, "success must clear the failure marker, and the fresh success cache hits"


def test_different_desks_do_not_share_cache(tmp_path):
    oil_dir, btc_dir = str(tmp_path / "oil"), str(tmp_path / "btc")
    cache.save(oil_dir, "shared_key", {"desk": "oil"})
    cache.save(btc_dir, "shared_key", {"desk": "btc"})
    assert cache.load_fresh(oil_dir, "shared_key", 60.0) == {"desk": "oil"}
    assert cache.load_fresh(btc_dir, "shared_key", 60.0) == {"desk": "btc"}


def test_atomic_write_leaves_no_tmp_file(tmp_path):
    d = str(tmp_path / "oil")
    cache.save(d, "atomic_check", {"a": 1})
    p = cache.path_for(d, "atomic_check")
    siblings = os.listdir(os.path.dirname(p))
    assert not any(s.endswith(".tmp") for s in siblings)


def test_a_straggler_job_writes_to_its_captured_directory_not_wherever_it_wakes_up(tmp_path):
    """Regression test for the exact bug this module's docstring
    describes: a worker that receives its directory as a plain string
    argument must keep using THAT directory even if something about the
    ambient environment changes between submission and completion — this
    is the whole point of resolving `desk_dir` once, synchronously, in
    api.py rather than letting cache.py look anything up lazily."""
    correct_dir = str(tmp_path / "isolated")
    cache.save(correct_dir, "k8", {"from": "the right place"})
    # Simulate "the environment changed after this job was submitted" —
    # cache.py must never notice, because it was never given a chance to
    # look at the environment in the first place.
    os.environ["PATHIEL_STATE_DIR"] = str(tmp_path / "wrong_place_entirely")
    try:
        assert cache.load_fresh(correct_dir, "k8", 60.0) == {"from": "the right place"}
    finally:
        del os.environ["PATHIEL_STATE_DIR"]
    assert not (tmp_path / "wrong_place_entirely").exists()
