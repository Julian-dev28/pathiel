"""5-minute up/down AI reader: the rolling-window math, live-momentum context,
the two-provider verdict parse, the full analyze path, and the dashboard cache.
Integration across claude_cli + openrouter brain shapes. No network — fake
http_get, fake klines, injected brain."""
from __future__ import annotations

import json
import time

import pytest

from services.polymarket_scout import ledger, updown


# ── rolling window math (deterministic) ──────────────────────────────────────
def test_window_start_floors_to_5min():
    # 16:11:06 -> window starts 16:10:00
    t = time.mktime(time.strptime("2026-07-25 16:11:06", "%Y-%m-%d %H:%M:%S"))
    # use UTC-agnostic: just assert it's a 300s boundary <= t and t-start < 300
    s = updown.window_start(1784995866)
    assert s % 300 == 0 and 0 <= 1784995866 - s < 300
    assert s == 1784995800


def test_current_slug_tracks_the_clock():
    assert updown.current_slug("btc", 1784995866) == "btc-updown-5m-1784995800"
    assert updown.current_slug("ETH", 1784995866) == "eth-updown-5m-1784995800"
    # 5 min later -> next window
    assert updown.current_slug("btc", 1784995866 + 300) == "btc-updown-5m-1784996100"


def test_current_market_uses_the_live_slug():
    seen = {}

    def http(url):
        seen["url"] = url
        return [{"id": "9", "question": "BTC up?", "endDate": "2026-07-25T16:15:00Z",
                 "outcomePrices": json.dumps(["0.51", "0.49"])}]
    m = updown.current_market("btc", 1784995866, http_get=http)
    assert "btc-updown-5m-1784995800" in seen["url"]
    assert m["id"] == "9"


def test_current_market_none_on_empty():
    assert updown.current_market("btc", 1784995866, http_get=lambda u: []) is None


# ── price context ────────────────────────────────────────────────────────────
def _klines(closes):
    # binance kline: [openT, o, h, l, c, ...]; we use h=idx2, l=idx3, c=idx4
    return [[0, c, c + 5, c - 5, c, 0] for c in closes]


def test_price_context_computes_momentum_and_range():
    closes = [100.0] * 10 + [100, 101, 102, 103, 104, 105]  # rising into the close
    ctx = updown.price_context("btc", runner=lambda u: _klines(closes))
    assert ctx["price"] == 105.0
    assert ctx["chg_5m_pct"] > 0                 # up over the last 5
    assert ctx["range_pos_15m"] > 0.6            # in the upper half of the 15m range
    assert ctx["last6_closes"][-1] == 105.0


def test_price_context_unknown_asset_is_none():
    assert updown.price_context("pepe", runner=lambda u: _klines([1] * 16)) is None


def test_price_context_too_few_bars_is_none():
    assert updown.price_context("btc", runner=lambda u: _klines([1, 2, 3])) is None


# ── verdict parse, both provider shapes ──────────────────────────────────────
def test_parse_reads_the_last_up_prob_object():
    body = ('search noise {"up_prob": 0.1}\n'
            'final: {"verdict":"UP","up_prob":0.63,"reasoning":"ticking up"}')
    v = updown._parse(body)
    assert v["up_prob"] == 0.63 and v["verdict"] == "UP" and v["reasoning"] == "ticking up"


def test_parse_down_and_clamps():
    assert updown._parse('{"up_prob":0.0,"reasoning":"x"}')["up_prob"] == 0.01
    assert updown._parse('{"up_prob":1.0}')["up_prob"] == 0.99
    assert updown._parse('{"up_prob":0.2}')["verdict"] == "DOWN"


def test_parse_rejects_garbage():
    assert updown._parse("") is None
    assert updown._parse('{"nope":1}') is None
    assert updown._parse('{"up_prob":"banana"}') is None


# ── full analyze, integration across providers ───────────────────────────────
class ClaudeCliBrain:
    """ai_brain.ClaudeCliBrain shape: returns text ending in the verdict JSON."""
    provider = "claude_cli"

    def __init__(self, up):
        self.up = up

    def complete(self, system, user, web_search=False):
        assert web_search is False               # 5-min horizon: never web search
        return f'Reasoning about the tape.\n{{"verdict":"UP","up_prob":{self.up},"reasoning":"momentum up"}}'


class OpenRouterBrain:
    provider = "openrouter"

    def __init__(self, up):
        self.up = up

    def complete(self, system, user, web_search=False):
        return f'{{"verdict":"DOWN","up_prob":{self.up},"reasoning":"fading"}}'


def _mkt():
    return [{"id": "42", "question": "BTC up in 5m?", "endDate": "2026-07-25T16:15:00Z",
             "outcomePrices": json.dumps(["0.505", "0.495"])}]


@pytest.mark.parametrize("brain,exp_prob,exp_side", [
    (ClaudeCliBrain(0.58), 0.58, "UP"),
    (OpenRouterBrain(0.34), 0.34, "DOWN"),
])
def test_analyze_full_path_both_providers(tmp_path, monkeypatch, brain, exp_prob, exp_side):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    rising = _klines([100.0] * 10 + [100, 101, 102, 103, 104, 105])
    out = updown.analyze("btc", brain=brain, now=1784995866,
                         http_get=lambda u: _mkt(), kline_runner=lambda u: rising,
                         record=True)
    assert out["up_prob"] == exp_prob and out["verdict"] == exp_side
    assert out["mkt_up"] == 0.505
    assert out["edge"] == pytest.approx(exp_prob - 0.505, abs=1e-6)
    assert out["context"]["price"] == 105.0
    # recorded to the updown_5m lane, shadow-flagged
    rows = ledger.load()
    assert len(rows) == 1
    assert ledger.row_lane(rows[0]) == "updown_5m"
    assert rows[0]["meta"]["shadow"] is True
    assert rows[0]["side"] == ("YES" if exp_side == "UP" else "NO")


def test_analyze_no_record_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    updown.analyze("btc", brain=ClaudeCliBrain(0.6), now=1784995866,
                   http_get=lambda u: _mkt(),
                   kline_runner=lambda u: _klines([100.0] * 16), record=False)
    assert ledger.load() == []


def test_analyze_survives_no_price_data(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    out = updown.analyze("btc", brain=ClaudeCliBrain(0.6), now=1784995866,
                         http_get=lambda u: _mkt(), kline_runner=lambda u: [])
    assert out["up_prob"] is None and "no live price data" in out["reasoning"]
    assert ledger.load() == []                   # never recorded a dataless read


def test_analyze_survives_a_brain_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))

    class Boom:
        def complete(self, *a, **k):
            raise RuntimeError("cli died")
    out = updown.analyze("btc", brain=Boom(), now=1784995866,
                         http_get=lambda u: _mkt(),
                         kline_runner=lambda u: _klines([100.0] * 16))
    assert out["up_prob"] is None and "brain error" in out["reasoning"]


# ── dashboard cache ──────────────────────────────────────────────────────────
def test_refresh_writes_cache_and_load_reads_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    updown.refresh(["btc"], brain=ClaudeCliBrain(0.55), now=1784995866,
                   record=False, http_get=lambda u: _mkt(),
                   kline_runner=lambda u: _klines([100.0] * 16))
    b = updown.load(now=1784995866)              # read as of the same window
    assert b["status"] == "ok" and len(b["reads"]) == 1
    r = b["reads"][0]
    assert r["asset"] == "BTC" and r["up_prob"] == 0.55
    # window closes at 16:15:00 == 1784996100; at now=...866 (16:11) it is NOT stale
    assert r["stale"] is False and r["seconds_left"] > 0
    # after the window closes it flips stale
    assert updown.load(now=1784996200)["reads"][0]["stale"] is True


def test_load_without_cache_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    assert updown.load()["status"] == "empty"


def test_updown_lane_is_registered():
    assert "updown_5m" in ledger.LANES


# ── on-demand analyze endpoint (like the card Analyze button) ────────────────
def test_updown_analyze_endpoint_forces_a_fresh_read(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_OPERATOR_TOKEN", "s3cret")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hermes_trader import dashboard as db

    # patch refresh so no network/model is touched; prove the endpoint calls it
    called = {}

    def fake_refresh(assets, record=True):
        called["assets"] = assets
        called["record"] = record
        return {"generated_at": 1, "reads": [{"asset": "BTC", "up_prob": 0.6,
                                              "verdict": "UP", "mkt_up": 0.5,
                                              "edge": 0.1, "reasoning": "x"}]}
    monkeypatch.setattr(updown, "refresh", fake_refresh)

    app = FastAPI(); db.register_routes(app); client = TestClient(app)
    # gated
    assert client.post("/api/dashboard/updown/analyze").status_code == 401
    r = client.post("/api/dashboard/updown/analyze?asset=btc",
                    headers={"X-Operator-Token": "s3cret"})
    assert r.status_code == 200
    assert r.json()["up_prob"] == 0.6
    assert called["assets"] == ["btc"] and called["record"] is True   # shadow records
