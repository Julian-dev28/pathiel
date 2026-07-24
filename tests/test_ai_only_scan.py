"""AI-only scan: the compact LLMglish blob, the tolerant verdict parser, the
analysis-dict shape the executor needs, the interval gate, and the shadow-vs-
place split. No network, no model — a fake brain returns canned text."""
from __future__ import annotations

import time

import pytest

from hermes_trader.agents import ai_only_scan as ao
from hermes_trader.agents.risk_gates import books_bypass_ai

NOW = time.time()


def _row(coin="BTC", mid=64000.0, prev=62000.0, vol=1.2e9, oi=800.0, funding=0.0001):
    return {"coin": coin, "midPx": mid, "markPx": mid, "prevDayPx": prev,
            "dayNtlVlm": vol, "openInterest": oi, "funding": funding}


# ── the blob ─────────────────────────────────────────────────────────────────
def test_compact_line_is_dense_and_schema_ordered():
    line = ao.compact_line(_row())
    assert line.startswith("BTC ")
    # d24 = (64000-62000)/62000 = +3.2%, vol 1200M, funding +1.0bps
    assert "+3.2" in line and "1200" in line and "+1.0" in line


def test_pct_24h_handles_missing_or_zero_prev():
    assert ao.pct_24h(_row(prev=0)) == 0.0
    assert ao.pct_24h(_row(mid=0, prev=100)) == 0.0
    assert ao.pct_24h(_row(mid=110, prev=100)) == pytest.approx(10.0)


def test_eligible_applies_a_safety_floor_never_a_signal_filter():
    uni = [_row("A", vol=5e6), _row("B", vol=1e9), _row("C", mid=0, prev=0, vol=1e9)]
    # min_volume 0 = raw board, but the priceless row C is always dropped
    got = ao.eligible(uni, {**ao.DEFAULTS, "min_volume_usd": 0})
    assert [r["coin"] for r in got] == ["B", "A"]      # volume-ranked, C gone


def test_eligible_volume_floor_and_cap():
    uni = [_row(str(i), vol=(10 - i) * 1e8) for i in range(10)]
    got = ao.eligible(uni, {**ao.DEFAULTS, "min_volume_usd": 5e8, "max_markets": 3})
    assert len(got) == 3                               # cap
    assert all(r["dayNtlVlm"] >= 5e8 for r in got)     # floor


def test_build_prompt_carries_the_schema_and_every_row():
    prompt = ao.build_prompt([_row("BTC"), _row("ETH")])
    assert "SCHEMA:" in prompt and "BTC" in prompt and "ETH" in prompt
    assert "2 markets" in prompt


# ── the parser ───────────────────────────────────────────────────────────────
def test_parse_verdicts_reads_long_and_short_lines():
    text = "BTC L 0.82 clean breakout\nETH S 0.71 topping\nEND"
    got = ao.parse_verdicts(text)
    assert [(g["coin"], g["verdict"], g["confidence"]) for g in got] == [
        ("BTC", "LONG", 0.82), ("ETH", "SHORT", 0.71)]
    assert got[0]["reason"] == "clean breakout"


def test_parse_verdicts_drops_junk_and_the_sentinel():
    text = "here are my picks\nBTC L 0.8 ok\ngarbage line\nEND\nETH X 0.5 bad side"
    got = ao.parse_verdicts(text)
    assert [g["coin"] for g in got] == ["BTC"]


def test_parse_verdicts_rejects_out_of_range_confidence():
    assert ao.parse_verdicts("BTC L 1.4 too high") == []
    assert ao.parse_verdicts("BTC L notanumber x") == []


def test_parse_verdicts_drops_hallucinated_symbols_when_valid_given():
    text = "BTC L 0.8 real\nFAKECOIN L 0.9 invented"
    got = ao.parse_verdicts(text, valid={"BTC"})
    assert [g["coin"] for g in got] == ["BTC"]


def test_parse_verdicts_keeps_colon_namespaced_symbols():
    got = ao.parse_verdicts("xyz:AAPL L 0.75 earnings", valid={"xyz:AAPL"})
    assert got[0]["coin"] == "xyz:AAPL"


# ── analysis dict ────────────────────────────────────────────────────────────
def test_to_analysis_is_shaped_for_the_executor_and_tagged_as_a_book():
    v = {"coin": "BTC", "verdict": "LONG", "side": "long", "confidence": 0.8, "reason": "x"}
    a = ao.to_analysis(v, _row(mid=100.0), {**ao.DEFAULTS, "stop_pct": 0.08, "tp_pct": 0.16})
    assert a["strategy_book"] == "ai_only"      # passes the books-only entry gate
    assert a["composite_score"] == 0.0          # no TA
    assert a["entryPx"] == 100.0
    assert a["stopPx"] == pytest.approx(92.0)   # long stop below
    assert a["tpPx"] == pytest.approx(116.0)
    assert a["id"] and a["confidence"] == 0.8
    # the executor reads the OVERRIDE keys, not bare leverage/equity_fraction
    assert a["leverage_override"] == 5
    assert a["strategy_book_equity_frac_override"] == pytest.approx(0.05)


def test_to_analysis_override_keys_track_config():
    v = {"coin": "BTC", "verdict": "LONG", "side": "long", "confidence": 0.8}
    a = ao.to_analysis(v, _row(mid=100.0),
                       {**ao.DEFAULTS, "leverage": 6, "equity_fraction_per_trade": 0.1})
    assert a["leverage_override"] == 6
    assert a["strategy_book_equity_frac_override"] == pytest.approx(0.1)


def test_to_analysis_flips_stop_and_tp_for_a_short():
    v = {"coin": "BTC", "verdict": "SHORT", "side": "short", "confidence": 0.8}
    a = ao.to_analysis(v, _row(mid=100.0), {**ao.DEFAULTS, "stop_pct": 0.08, "tp_pct": 0.16})
    assert a["stopPx"] == pytest.approx(108.0)   # short stop above
    assert a["tpPx"] == pytest.approx(84.0)


# ── interval gate ────────────────────────────────────────────────────────────
def test_due_respects_the_interval():
    cfg = {**ao.DEFAULTS, "interval_min": 30}
    assert ao.due(cfg, last_ts=NOW - 31 * 60, now=NOW) is True
    assert ao.due(cfg, last_ts=NOW - 10 * 60, now=NOW) is False


# ── run_once: shadow vs place ────────────────────────────────────────────────
class FakeBrain:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def complete(self, system, user, web_search=False):
        self.calls += 1
        self.last_user = user
        return self.reply


def test_run_once_is_one_batched_brain_call(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    brain = FakeBrain("BTC L 0.8 x\nETH S 0.75 y\nEND")
    recs = []
    out = ao.run_once([_row("BTC"), _row("ETH"), _row("SOL")],
                      {"ai_only_mode": {"place": False}}, brain=brain,
                      record_fn=lambda *a, **k: recs.append(k) or {})
    assert brain.calls == 1                      # N markets, ONE call
    assert out["picks"] == 2 and out["recorded"] == 2 and out["placed"] == 0
    assert {r["coin"] for r in recs} == {"BTC", "ETH"}


def test_run_once_shadow_mode_records_but_never_executes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    executed = []
    out = ao.run_once([_row("BTC")], {"ai_only_mode": {"place": False}},
                      brain=FakeBrain("BTC L 0.9 x\nEND"),
                      execute_fn=lambda a: executed.append(a) or {"executed": True},
                      record_fn=lambda *a, **k: {})
    assert out["recorded"] == 1 and out["placed"] == 0
    assert executed == []                         # place=false: nothing routed


def test_run_once_place_mode_routes_high_confidence_through_execute(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    executed = []

    def _exec(a):
        executed.append(a)
        return {"executed": True}

    out = ao.run_once([_row("BTC"), _row("ETH")],
                      {"ai_only_mode": {"place": True, "min_confidence": 0.7}},
                      brain=FakeBrain("BTC L 0.85 strong\nETH L 0.60 weak\nEND"),
                      execute_fn=_exec, record_fn=lambda *a, **k: {})
    # both recorded, only the >=0.70 one placed
    assert out["recorded"] == 2 and out["placed"] == 1
    assert [a["coin"] for a in executed] == ["BTC"]
    assert executed[0]["strategy_book"] == "ai_only"


def test_run_once_respects_allow_shorts_false(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    out = ao.run_once([_row("BTC")], {"ai_only_mode": {"allow_shorts": False}},
                      brain=FakeBrain("BTC S 0.9 down\nEND"), record_fn=lambda *a, **k: {})
    assert out["picks"] == 0


def test_run_once_survives_a_brain_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))

    class Boom:
        def complete(self, *a, **k):
            raise RuntimeError("cli died")

    out = ao.run_once([_row("BTC")], {}, brain=Boom(), record_fn=lambda *a, **k: {})
    assert out["picks"] == 0 and "error" in out


# ── maybe_run: the loop entrypoint ───────────────────────────────────────────
def test_maybe_run_is_a_noop_when_disabled(tmp_path):
    out = ao.maybe_run({"ai_only_mode": {"enabled": False}}, [_row("BTC")],
                       ts_path=str(tmp_path / "ts"))
    assert out is None


def test_maybe_run_respects_the_interval_and_persists_the_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    ts = str(tmp_path / "ts")
    cfg = {"ai_only_mode": {"enabled": True, "interval_min": 30, "place": False}}
    brain = FakeBrain("BTC L 0.8 x\nEND")
    first = ao.maybe_run(cfg, [_row("BTC")], brain=brain, now=NOW, ts_path=ts)
    assert first is not None and first["recorded"] == 1
    # a second call 10 min later is inside the interval -> no-op
    again = ao.maybe_run(cfg, [_row("BTC")], brain=brain, now=NOW + 600, ts_path=ts)
    assert again is None
    # 31 min later it fires again
    third = ao.maybe_run(cfg, [_row("BTC")], brain=brain, now=NOW + 31 * 60, ts_path=ts)
    assert third is not None


def test_maybe_run_never_raises_into_the_loop(tmp_path):
    class Boom:
        def complete(self, *a, **k):
            raise RuntimeError("x")
    out = ao.maybe_run({"ai_only_mode": {"enabled": True, "interval_min": 0}},
                       [_row("BTC")], brain=Boom(), now=NOW, ts_path=str(tmp_path / "ts"))
    assert out == {"error": "x"} or (isinstance(out, dict) and out.get("picks") == 0)


# ── books_bypass_ai ──────────────────────────────────────────────────────────
def test_books_bypass_ai_defaults_true():
    assert books_bypass_ai({}) is True
    assert books_bypass_ai({"books_bypass_ai": False}) is False
