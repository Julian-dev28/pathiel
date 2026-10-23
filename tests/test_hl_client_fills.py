"""fetch_user_fills_by_time (pathiel/client/hl_client.py): pagination past
the 2000-row cap, de-duping by tid at the page boundary, the no-user/no-data
early-outs, and never raising on a failed page. Offline: _http_post is
monkeypatched, never the network. Built for services/strategies/trades.py's
default fills fetcher."""
from __future__ import annotations

import pathiel.client.hl_client as hl


def _fill(tid, t, coin="xyz:BRENTOIL"):
    return {"tid": tid, "time": t, "coin": coin, "side": "B", "px": "100", "sz": "1",
           "dir": "Open Long", "closedPnl": "0.0", "fee": "0.1"}


def test_no_user_returns_empty_without_calling_http(monkeypatch):
    calls = []
    monkeypatch.setattr(hl, "_http_post", lambda *a, **kw: calls.append(a) or [])
    assert hl.fetch_user_fills_by_time("", 0, 1000) == []
    assert calls == []


def test_single_page_under_the_cap(monkeypatch):
    rows = [_fill(1, 100), _fill(2, 200)]
    monkeypatch.setattr(hl, "_http_post", lambda path, payload, timeout=5: rows)
    out = hl.fetch_user_fills_by_time("0xabc", 0, 1000)
    assert [f["tid"] for f in out] == [1, 2]


def test_payload_carries_user_start_and_end(monkeypatch):
    seen = []

    def fake(path, payload, timeout=5):
        seen.append(payload)
        return []
    monkeypatch.setattr(hl, "_http_post", fake)
    hl.fetch_user_fills_by_time("0xabc", 111, 222)
    assert seen == [{"type": "userFillsByTime", "user": "0xabc", "startTime": 111, "endTime": 222}]


def test_end_time_defaults_to_now(monkeypatch):
    seen = []
    monkeypatch.setattr(hl, "_http_post", lambda path, payload, timeout=5: (seen.append(payload), [])[1])
    monkeypatch.setattr(hl.time, "time", lambda: 1_000.0)
    hl.fetch_user_fills_by_time("0xabc", 0)
    assert seen[0]["endTime"] == 1_000_000


def test_pages_forward_past_the_2000_row_cap_and_dedupes_the_boundary_row(monkeypatch):
    page1 = [_fill(i, 100 + i) for i in range(2000)]        # exactly a full page: keep paging
    boundary = page1[-1]                                     # time 2099, tid 1999
    page2 = [boundary] + [_fill(2000, 2100), _fill(2001, 2101)]
    pages = [page1, page2]
    calls = {"n": 0}

    def fetcher(path, payload, timeout=5):
        calls["n"] += 1
        return pages.pop(0) if pages else []
    monkeypatch.setattr(hl, "_http_post", fetcher)

    out = hl.fetch_user_fills_by_time("0xabc", 0, 10_000)
    tids = [f["tid"] for f in out]
    assert tids == list(range(2000)) + [2000, 2001]   # boundary row counted once
    assert calls["n"] == 2   # page2 had < 2000 rows, so no third page fetched


def test_stops_if_the_batch_never_advances_time(monkeypatch):
    """A batch whose max time does not exceed the current startTime would
    loop forever paging the same window; bail instead."""
    stuck = [_fill(i, 50) for i in range(2000)]   # a full page, but every row at the SAME time
    calls = {"n": 0}

    def fetcher(path, payload, timeout=5):
        calls["n"] += 1
        return stuck
    monkeypatch.setattr(hl, "_http_post", fetcher)
    out = hl.fetch_user_fills_by_time("0xabc", 50, 10_000)
    assert calls["n"] == 1
    assert len(out) == 2000


def test_a_non_list_response_stops_pagination_without_raising(monkeypatch):
    monkeypatch.setattr(hl, "_http_post", lambda path, payload, timeout=5: None)
    assert hl.fetch_user_fills_by_time("0xabc", 0, 1000) == []


def test_output_is_sorted_oldest_first(monkeypatch):
    rows = [_fill(2, 500), _fill(1, 100), _fill(3, 900)]
    monkeypatch.setattr(hl, "_http_post", lambda path, payload, timeout=5: rows)
    out = hl.fetch_user_fills_by_time("0xabc", 0, 1000)
    assert [f["time"] for f in out] == [100, 500, 900]


def test_max_pages_bounds_a_pathological_loop(monkeypatch):
    """Even if a buggy venue kept returning a full, time-advancing page
    forever, max_pages caps the work instead of hanging the caller."""
    counter = {"t": 0}

    def fetcher(path, payload, timeout=5):
        counter["t"] += 1
        return [_fill(i + counter["t"] * 2000, payload["startTime"] + i) for i in range(2000)]
    monkeypatch.setattr(hl, "_http_post", fetcher)
    out = hl.fetch_user_fills_by_time("0xabc", 0, 10**12, max_pages=3)
    assert counter["t"] == 3
    assert len(out) == 3 * 2000
