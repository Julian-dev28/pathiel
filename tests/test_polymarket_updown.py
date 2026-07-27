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


def test_price_context_stacks_1s_1m_5m_layers():
    ctx = updown.price_context("btc", runner=lambda u: _klines([100.0 + i for i in range(16)]))
    assert ctx["resolution"] == "1s+1m+5m"
    assert "s1" in ctx and "m1" in ctx and "m5" in ctx
    assert "chg_10s" in ctx["s1"] and ctx["s1"]["trend3"] in ("up", "down", "mixed", "flat")
    assert "chg_1m" in ctx["m1"] and ctx["m1"]["trend3"] in ("up", "down", "mixed", "flat")
    # the 15m trend is the last-3 5m-bar direction
    assert "chg_15m" in ctx["m5"] and ctx["m5"]["trend3_15m"] in ("up", "down", "mixed", "flat")


def test_trend3_reads_last_three_bar_direction():
    assert updown._trend3([1, 2, 3, 4, 5]) == "up"
    assert updown._trend3([5, 4, 3, 2, 1]) == "down"
    assert updown._trend3([1, 5, 1, 5, 1]) in ("mixed", "down")
    assert updown._trend3([1, 2]) == "flat"          # too few bars


def test_build_prompt_presents_every_timeframe():
    ctx = updown.price_context("btc", runner=lambda u: _klines([100.0 + i for i in range(16)]))
    p = updown.build_prompt(ctx, 0.55)
    assert "1s  (last minute)" in p and "1m  (last 16m)" in p and "5m  (last hour)" in p
    assert "trend(3 bars)=" in p and "trend(last 3 5m bars = 15m)=" in p


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
    b = updown.load(now=1784995866, refresh_price=False)   # cache-only, no network
    assert b["status"] == "ok" and len(b["reads"]) == 1
    r = b["reads"][0]
    assert r["asset"] == "BTC" and r["up_prob"] == 0.55
    # window closes at 16:15:00 == 1784996100; at now=...866 (16:11) it is NOT stale
    assert r["stale"] is False and r["seconds_left"] > 0
    # after the window closes it flips stale
    assert updown.load(now=1784996200, refresh_price=False)["reads"][0]["stale"] is True


def test_load_without_cache_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    assert updown.load(refresh_price=False)["status"] == "empty"


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


# ── LIVE Hyperliquid execution (operator-armed) ──────────────────────────────
def _read(up_prob, verdict, price=64000.0, end="2026-07-25T16:15:00Z", slug="btc-updown-5m-1"):
    return {"up_prob": up_prob, "verdict": verdict, "mkt_up": 0.5, "edge": 0.0,
            "context": {"price": price}, "end_date": end, "slug": slug,
            "reasoning": "x"}


def test_lean_is_signed_distance_from_coinflip():
    assert updown.lean(_read(0.7, "UP")) == pytest.approx(0.2)
    assert updown.lean(_read(0.4, "DOWN")) == pytest.approx(-0.1)
    assert updown.lean({"up_prob": None}) == 0.0


def test_live_should_trade_needs_a_real_lean():
    cfg = {**updown.LIVE_DEFAULTS, "min_lean": 0.06}
    assert updown.live_should_trade(_read(0.57, "UP"), cfg) is True     # 0.07 >= 0.06
    assert updown.live_should_trade(_read(0.54, "UP"), cfg) is False    # 0.04 < 0.06
    assert updown.live_should_trade(_read(0.42, "DOWN"), cfg) is True   # |−0.08|


def test_to_hl_analysis_long_up_short_down():
    cfg = {**updown.LIVE_DEFAULTS, "coin": "BTC", "leverage": 3, "equity_frac": 0.02,
           "stop_pct": 0.01, "tp_pct": 0.015}
    up = updown.to_hl_analysis(_read(0.65, "UP", price=100.0), cfg)
    assert up["coin"] == "BTC" and up["verdict"] == "LONG" and up["side"] == "long"
    assert up["strategy_book"] == "updown_5m"
    assert up["leverage_override"] == 3
    assert up["strategy_book_equity_frac_override"] == pytest.approx(0.02)
    assert up["stopPx"] == pytest.approx(99.0) and up["tpPx"] == pytest.approx(101.5)
    dn = updown.to_hl_analysis(_read(0.35, "DOWN", price=100.0), cfg)
    assert dn["verdict"] == "SHORT" and dn["stopPx"] == pytest.approx(101.0)
    assert dn["tpPx"] == pytest.approx(98.5)


def test_live_maybe_run_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    assert updown.live_maybe_run({"updown_live": {"enabled": False}}) is None


def _patch_analyze(monkeypatch, read):
    monkeypatch.setattr(updown, "analyze", lambda *a, **k: read)


def test_live_maybe_run_opens_a_perp_on_a_clear_lean(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    _patch_analyze(monkeypatch, _read(0.62, "UP", price=64000.0))
    placed = []
    cfg = {"updown_live": {"enabled": True, "place": True, "min_lean": 0.06, "coin": "BTC"}}
    out = updown.live_maybe_run(cfg, positions=[], now=1784995866,
                                execute_fn=lambda a: placed.append(a) or {"executed": True})
    assert out["opened"] is True and len(placed) == 1
    assert placed[0]["verdict"] == "LONG" and placed[0]["strategy_book"] == "updown_5m"
    # window_end persisted so the next call can flatten
    assert updown._read_live_state().get("window_end")


def test_live_maybe_run_skips_a_weak_lean(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    _patch_analyze(monkeypatch, _read(0.53, "UP"))       # lean 0.03 < 0.06
    placed = []
    out = updown.live_maybe_run({"updown_live": {"enabled": True, "place": True}},
                                positions=[], now=1784995866,
                                execute_fn=lambda a: placed.append(a) or {"executed": True})
    assert out["opened"] is False and placed == []


def test_live_maybe_run_place_false_records_but_does_not_open(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    _patch_analyze(monkeypatch, _read(0.7, "UP"))
    placed = []
    out = updown.live_maybe_run({"updown_live": {"enabled": True, "place": False}},
                                positions=[], now=1784995866,
                                execute_fn=lambda a: placed.append(a) or {"executed": True})
    assert out["opened"] is False and placed == []       # place=false: no order


def test_live_maybe_run_acts_once_per_window(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    _patch_analyze(monkeypatch, _read(0.7, "UP"))
    placed = []
    cfg = {"updown_live": {"enabled": True, "place": True}}
    ex = lambda a: placed.append(a) or {"executed": True}
    updown.live_maybe_run(cfg, positions=[], now=1784995866, execute_fn=ex)
    # same window, seconds later -> no second open
    out2 = updown.live_maybe_run(cfg, positions=[], now=1784995900, execute_fn=ex)
    assert len(placed) == 1 and out2["opened"] is False


def test_live_maybe_run_flattens_at_window_close(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    _patch_analyze(monkeypatch, _read(0.7, "UP", end="2026-07-25T16:15:00Z"))
    ex = lambda a: {"executed": True}
    # open in the 16:10 window (position end = 16:15 = 1784996100)
    updown.live_maybe_run({"updown_live": {"enabled": True, "place": True}},
                          positions=[], now=1784995866, execute_fn=ex)
    closed = []
    # now past 16:15 with a BTC position open -> flatten
    out = updown.live_maybe_run(
        {"updown_live": {"enabled": True, "place": True}},
        positions=[{"coin": "BTC", "position": {"szi": "0.01"}}],
        now=1784996200, execute_fn=ex, close_fn=lambda c: closed.append(c))
    assert out["flattened"] is True and closed == ["BTC"]


def test_live_maybe_run_does_not_stack_windows(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    _patch_analyze(monkeypatch, _read(0.7, "UP"))
    placed = []
    # a BTC position is already open, new window -> should NOT open a second
    out = updown.live_maybe_run(
        {"updown_live": {"enabled": True, "place": True}},
        positions=[{"coin": "BTC", "position": {"szi": "0.01"}}],
        now=1784996400, execute_fn=lambda a: placed.append(a) or {"executed": True})
    assert placed == [] and out.get("skipped") == "position already open"



# ── resolution-aware read (strong verdicts) ──────────────────────────────────
def test_randomwalk_up_prob_is_decisive_when_clearly_up_or_down():
    assert updown.randomwalk_up_prob(20.0, 2.0, 10) > 0.95     # clearly above open, little time
    assert updown.randomwalk_up_prob(-20.0, 2.0, 10) < 0.05    # clearly below
    assert updown.randomwalk_up_prob(0.0, 2.0, 90) == 0.5      # at the open
    assert 0.45 < updown.randomwalk_up_prob(1.0, 2.0, 250) < 0.55   # tiny lead, lots of time


def test_randomwalk_handles_zero_vol_and_no_time():
    assert updown.randomwalk_up_prob(5.0, 0.0, 90) == 1.0      # up, no vol -> stays up
    assert updown.randomwalk_up_prob(-5.0, 2.0, 0) == 0.0      # down at the buzzer
    assert updown.randomwalk_up_prob(0.0, 0.0, 0) == 0.5


def test_price_context_computes_position_vs_window_open():
    # 5m bar OPENS at 100 (window open) but price is now 110 -> above open
    def runner(url):
        if "interval=5m" in url:
            return [[0, 100.0, 111.0, 99.0, 100.0, 0]] * 3 + [[0, 100.0, 111.0, 99.0, 110.0, 0]]
        return _klines([108.0, 109.0, 110.0] * 6)      # 1s/1m: price ~110
    ctx = updown.price_context("btc", runner=runner, seconds_left=60)
    assert ctx["window_open"] == 100.0            # the 5m bar's open
    assert ctx["vs_open_pct"] > 0                 # price (110) above open (100)
    assert ctx["seconds_left"] == 60
    assert ctx["drift_prob_up"] > 0.5             # above open -> leans UP


def test_build_prompt_leads_with_resolution_and_pushes_a_decisive_call():
    ctx = updown.price_context("btc", runner=lambda u: _klines([100.0 + i for i in range(16)]),
                               seconds_left=60)
    p = updown.build_prompt(ctx, 0.55)
    assert "RESOLUTION:" in p and "window-open" in p
    assert "random-walk P(UP)=" in p
    assert "COMMIT:" in p and "NOT a coin flip" in p


# ── decisive "bonafide" verdicts ─────────────────────────────────────────────
def test_call_label_is_decisive_across_the_range():
    assert updown.call_label(0.90) == "STRONG UP"
    assert updown.call_label(0.62) == "LEAN UP"
    assert updown.call_label(0.50) == "TOSS-UP"
    assert updown.call_label(0.38) == "LEAN DOWN"
    assert updown.call_label(0.12) == "STRONG DOWN"
    assert updown.call_label(None) == "—"


def test_sys_prompt_demands_commitment_and_bans_hedging():
    assert "DECISIVE" in updown._SYS
    assert "BANNED" in updown._SYS and "coin flip" in updown._SYS
    assert "do NOT drift back toward 0.50" in updown._SYS.replace("Do", "do")


def test_analyze_attaches_the_call_label(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    out = updown.analyze("btc", brain=ClaudeCliBrain(0.85), now=1784995866,
                         http_get=lambda u: _mkt(),
                         kline_runner=lambda u: _klines([100.0] * 16))
    assert out["call"] == "STRONG UP" and out["up_prob"] == 0.85
