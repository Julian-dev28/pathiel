"""Dedicated failure-isolation tests for services/intel/api.py: one dead
or slow or buggy source must never take down the rest of the packet, and
must always be named in sources_failed — never silently empty."""
from __future__ import annotations

import time

import pytest

from contracts.strategies.models import IntelPacket
from services.intel import api, http_client
from services.intel.sources import news
from services.intel.tests.conftest import T0


@pytest.fixture(autouse=True)
def mock_everything_ok(monkeypatch):
    """Baseline: every source returns a clean empty success so a single
    test can override just the one fetcher it cares about."""
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: ({"articles": [], "data": [],
                                                                      "results": [], "events": [],
                                                                      "series": []}, None))
    monkeypatch.setattr(http_client, "fetch_text", lambda *a, **k: ("<rss><channel></channel></rss>", None))


def test_one_atomic_fetcher_raising_does_not_sink_the_packet(oil_spec, monkeypatch):
    def boom(spec):
        raise RuntimeError("a latent bug, not a network error")

    # api.py's build_packet reads news.ATOMIC_FETCHERS as a tuple of
    # function OBJECTS captured once at import time — patching the
    # module-level name `news.fetch_oilprice` after the fact does not
    # change what's already inside that tuple, so the tuple itself must
    # be replaced for the mock to actually take effect.
    patched = tuple(boom if fn is news.fetch_oilprice else fn for fn in news.ATOMIC_FETCHERS)
    monkeypatch.setattr(news, "ATOMIC_FETCHERS", patched)
    packet = api.build_packet(oil_spec, now=T0)  # must not raise
    assert isinstance(packet, IntelPacket)
    assert any("unexpected" in f and "RuntimeError" in f for f in packet.sources_failed)


def test_multiple_simultaneous_failures_all_recorded(oil_spec, monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "simulated_down"))
    monkeypatch.setattr(http_client, "fetch_text", lambda *a, **k: (None, "simulated_down"))
    packet = api.build_packet(oil_spec, now=T0)
    assert isinstance(packet, IntelPacket)
    # news x2 text sources, policy x2 (federal_register, ofac) + congress missing-key,
    # predictions x2, positioning x1 — all independently failed, all named.
    assert len(packet.sources_failed) >= 5


def test_slow_source_is_cut_off_by_the_global_deadline_not_blocking_forever(oil_spec, monkeypatch):
    monkeypatch.setattr(api, "FETCH_DEADLINE_S", 0.2)

    def slow_fetch(spec):
        time.sleep(2.0)
        return [], None

    patched = tuple(slow_fetch if fn is news.fetch_google_news else fn for fn in news.ATOMIC_FETCHERS)
    monkeypatch.setattr(news, "ATOMIC_FETCHERS", patched)
    t0 = time.time()
    packet = api.build_packet(oil_spec, now=T0)
    elapsed = time.time() - t0
    assert elapsed < 1.5, "build_packet must respect the deadline, not wait for the slow source"
    assert any("timeout_budget_exceeded" in f for f in packet.sources_failed)


def test_packet_still_has_calendar_catalysts_during_total_outage(oil_spec, monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "down"))
    monkeypatch.setattr(http_client, "fetch_text", lambda *a, **k: (None, "down"))
    packet = api.build_packet(oil_spec, now=T0)
    assert len(packet.catalysts) > 0  # pure deterministic compute, no network needed
    kinds = {c.kind for c in packet.catalysts}
    assert "eia_weekly" in kinds


def test_sources_failed_is_never_silently_empty_when_a_key_is_missing(oil_spec, monkeypatch):
    monkeypatch.delenv("CONGRESS_API_KEY", raising=False)
    packet = api.build_packet(oil_spec, now=T0)
    assert any("CONGRESS_API_KEY" in f for f in packet.sources_failed)


def test_build_packet_result_is_identical_shape_regardless_of_failures(oil_spec, monkeypatch):
    """A packet built during a total outage must still satisfy the exact
    same contract (IntelPacket) as a healthy one — no special-cased
    degraded shape."""
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "down"))
    monkeypatch.setattr(http_client, "fetch_text", lambda *a, **k: (None, "down"))
    packet = api.build_packet(oil_spec, now=T0)
    import json
    IntelPacket.model_validate(json.loads(packet.model_dump_json()))  # round-trips cleanly
