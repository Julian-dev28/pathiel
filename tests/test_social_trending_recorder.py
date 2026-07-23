"""Gate tests for social_trending_recorder — parse, throttle, dedup, gating. No network."""
import io
import json

import pytest

from hermes_trader.agents import social_trending_recorder as rec


_CANNED = {
    "coins": [
        {"item": {"symbol": "pengu", "name": "Pudgy Penguins", "market_cap_rank": 114,
                  "score": 1, "data": {"price_btc": 0.0000003}}},
        {"item": {"symbol": "hype", "name": "Hyperliquid", "market_cap_rank": 10, "score": 3}},
        {"item": {"symbol": "", "name": "blank", "score": 9}},  # dropped: empty symbol
    ]
}


def test_fetch_trending_parses(monkeypatch):
    monkeypatch.setattr(rec.urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(json.dumps(_CANNED).encode()))
    # urlopen is used as a context manager
    class _CM(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(rec.urllib.request, "urlopen",
                        lambda *a, **k: _CM(json.dumps(_CANNED).encode()))
    rows = rec.fetch_trending()
    assert [r["symbol"] for r in rows] == ["PENGU", "HYPE"]  # uppercased, blank dropped
    assert rows[0]["score"] == 1 and rows[1]["rank"] == 10


def test_fetch_returns_empty_on_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(rec.urllib.request, "urlopen", _boom)
    assert rec.fetch_trending() == []


def test_universe_mids_k_prefix_and_skips_equities():
    uni = [{"coin": "BTC", "midPx": 100.0}, {"coin": "kPEPE", "midPx": 0.001},
           {"coin": "xyz:AAPL", "midPx": 200.0}]
    mids = rec._universe_mids(uni)
    assert mids["BTC"] == 100.0
    assert mids["KPEPE"] == 0.001 and mids["PEPE"] == 0.001  # k-alias
    assert "XYZ:AAPL" not in mids and "AAPL" not in mids     # equities skipped


def test_disabled_records_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(rec, "_STATE_FILE", str(tmp_path / "s.json"))
    n = rec.maybe_record([], {"social_trending": {"enabled": False}})
    assert n == 0


def test_throttle_and_dedup(monkeypatch, tmp_path):
    monkeypatch.setattr(rec, "_STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(rec, "fetch_trending",
                        lambda *a, **k: [{"symbol": "PENGU", "name": "P", "rank": 1, "score": 0,
                                          "price_btc": 0.0},
                                         {"symbol": "HYPE", "name": "H", "rank": 2, "score": 1,
                                          "price_btc": 0.0}])
    written = []
    monkeypatch.setattr(rec.shadow_ledger, "record",
                        lambda book, **kw: written.append((book, kw["coin"], kw["side"])))
    cfg = {"social_trending": {"enabled": True, "poll_hours": 1.0, "dedup_hours": 24.0,
                               "min_score": 999, "horizon_days": 1.0}}
    uni = [{"coin": "HYPE", "midPx": 40.0}]

    n1 = rec.maybe_record(uni, cfg)
    assert n1 == 2 and len(written) == 2
    assert all(w[2] == "long" for w in written)           # recorded as LONG
    # immediate second call: throttled by poll_hours
    n2 = rec.maybe_record(uni, cfg)
    assert n2 == 0

    # force the poll clock open but keep last_seen: dedup must suppress same coins
    state = json.load(open(rec._STATE_FILE))
    state["last_poll_ms"] = 0
    json.dump(state, open(rec._STATE_FILE, "w"))
    n3 = rec.maybe_record(uni, cfg)
    assert n3 == 0                                          # both coins still inside dedup window
