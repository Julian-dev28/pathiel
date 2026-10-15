"""Tests for the runner gate's model+cohort admission (B.46).

The fade-short promote is unreachable under the general runner gate: 0/34
cohort calls reach conf 0.65 (p50 0.42, max 0.58) and 1/60 pass
structured_short (fade setups have no fresh impulse by construction). The
admission exempts a "<model>/<cohort-tag>" keyed entry from the runner gate
at a sanity conf floor — welded to the answering model, fail-open, absent
key = byte-identical general path. Covers the gate function directly plus
the seat->analysis cohort_tag carry.
"""

import json

import pytest

from hermes_trader.agents import executor


def _gate(**over):
    g = {
        "enabled": True,
        "min_confidence": 0.65,
        "min_composite": 30.0,
        "allow_shorts": True,
    }
    g.update(over)
    return {"runner_entry_gate": g}


def _fade_short_analysis(conf=0.40, **over):
    a = {
        "coin": "SKY",
        "side": "short",
        "confidence": conf,
        "composite_score": 0.0,
        "cohort_tag": "vwap-fade-short",
        "primary_model": "decision2-lux-9b",
    }
    a.update(over)
    return a


_ADM = {"decision2-lux-9b/vwap-fade-short": 0.35}


def test_admission_admits_low_conf_cohort_short(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="hermes_trader.agents.executor")
    reason = executor._runner_entry_block_reason(
        _fade_short_analysis(), _gate(cohort_admissions=dict(_ADM)))
    assert reason == ""
    assert "[gate][COHORT] ADMIT SKY SHORT" in caplog.text
    assert "decision2-lux-9b/vwap-fade-short" in caplog.text


def test_admission_absent_key_is_noop():
    reason = executor._runner_entry_block_reason(
        _fade_short_analysis(), _gate())
    assert reason == "runner_gate_blocked (confidence 0.40 < 0.65)"


def test_admission_model_mismatch_keeps_general_path():
    # the exemption is welded to the gauntleted model: nano in the seat
    # (or any other model) must NOT inherit it
    reason = executor._runner_entry_block_reason(
        _fade_short_analysis(primary_model="d3-nano"), _gate(cohort_admissions=dict(_ADM)))
    assert reason == "runner_gate_blocked (confidence 0.40 < 0.65)"


def test_admission_tagless_analysis_keeps_general_path():
    a = _fade_short_analysis()
    del a["cohort_tag"]
    reason = executor._runner_entry_block_reason(a, _gate(cohort_admissions=dict(_ADM)))
    assert reason == "runner_gate_blocked (confidence 0.40 < 0.65)"


def test_admission_below_cohort_floor_falls_through():
    reason = executor._runner_entry_block_reason(
        _fade_short_analysis(conf=0.30), _gate(cohort_admissions=dict(_ADM)))
    assert reason == "runner_gate_blocked (confidence 0.30 < 0.65)"


def test_admission_malformed_floor_fails_open_to_general_path():
    reason = executor._runner_entry_block_reason(
        _fade_short_analysis(),
        _gate(cohort_admissions={"decision2-lux-9b/vwap-fade-short": "abc"}))
    assert reason == "runner_gate_blocked (confidence 0.40 < 0.65)"


def test_admission_respects_allow_shorts_kill_switch():
    reason = executor._runner_entry_block_reason(
        _fade_short_analysis(),
        _gate(allow_shorts=False, cohort_admissions=dict(_ADM)))
    # admission must NOT fire: the entry falls to the general path (blocked
    # at the conf floor here; the shorts-disabled branch sits behind it)
    assert reason != ""
    assert "confidence 0.40 < 0.65" in reason


def test_admission_key_is_case_insensitive():
    a = _fade_short_analysis(cohort_tag="VWAP-Fade-Short",
                             primary_model="Decision2-Lux-9B")
    assert executor._runner_entry_block_reason(
        a, _gate(cohort_admissions=dict(_ADM))) == ""


# ── classic pipeline confidence gate (risk_gates) ─────────────────────────

from hermes_trader.agents import risk_gates


def _classic_ctx(conf=0.43, **over):
    kw = dict(confidence=conf, current_positions=[], trade_notional_usd=100.0,
              daily_pnl=0.0, market_volume_24h_usd=500e6, coin="ASTER",
              trade_side="short", has_binary_news_risk=False, equity=1000.0,
              total_open_notional=0.0, cohort_tag="vwap-fade-short",
              primary_model="decision2-lux-9b")
    kw.update(over)
    return risk_gates.GateContext(**kw)  # type: ignore[arg-type]


_CFG = {"min_ai_confidence": 0.65, "min_short_volume_usd": 20e6,
        "runner_entry_gate": {"cohort_admissions": dict(_ADM)}}


def test_classic_conf_gate_admits_cohort(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="hermes_trader.agents.risk_gates")
    res = risk_gates._cohort_conf_admission(
        _classic_ctx(), _CFG,
        risk_gates.confidence_gate(_classic_ctx(), 0.65), 0.65)
    assert res["pass"] is True
    assert res["via"] == "cohort_admission:decision2-lux-9b/vwap-fade-short"
    assert "conf-gate ADMIT ASTER SHORT" in caplog.text


def test_classic_conf_gate_absent_key_keeps_block():
    cfg = {"min_ai_confidence": 0.65}
    ctx = _classic_ctx()
    res = risk_gates._cohort_conf_admission(
        ctx, cfg, risk_gates.confidence_gate(ctx, 0.65), 0.65)
    assert res["pass"] is False and "0.43 < 0.65" in res["reason"]


def test_classic_conf_gate_model_mismatch_keeps_block():
    ctx = _classic_ctx(primary_model="d3-nano")
    res = risk_gates._cohort_conf_admission(
        ctx, _CFG, risk_gates.confidence_gate(ctx, 0.65), 0.65)
    assert res["pass"] is False


def test_classic_conf_gate_below_floor_keeps_block():
    ctx = _classic_ctx(conf=0.30)
    res = risk_gates._cohort_conf_admission(
        ctx, _CFG, risk_gates.confidence_gate(ctx, 0.65), 0.65)
    assert res["pass"] is False


def test_classic_conf_gate_malformed_floor_keeps_block():
    cfg = {"min_ai_confidence": 0.65,
           "runner_entry_gate": {"cohort_admissions":
                                 {"decision2-lux-9b/vwap-fade-short": "x"}}}
    ctx = _classic_ctx()
    res = risk_gates._cohort_conf_admission(
        ctx, cfg, risk_gates.confidence_gate(ctx, 0.65), 0.65)
    assert res["pass"] is False


def test_classic_conf_gate_tagless_keeps_block():
    ctx = _classic_ctx(cohort_tag=None)
    res = risk_gates._cohort_conf_admission(
        ctx, _CFG, risk_gates.confidence_gate(ctx, 0.65), 0.65)
    assert res["pass"] is False


def test_classic_eval_all_gates_wires_admission_short_liquidity_still_blocks():
    # end-to-end through eval_all_gates: cohort conf passes, but the thin-
    # market short-liquidity floor must STILL block (option A: liquidity
    # floor is NOT exempted)
    ctx = _classic_ctx(market_volume_24h_usd=4.3e6)
    out = risk_gates.eval_all_gates(ctx, _CFG)["results"]
    assert out["confidence"]["pass"] is True
    assert out["confidence"]["via"] == "cohort_admission:decision2-lux-9b/vwap-fade-short"
    assert out["short_liquidity"]["pass"] is False
    assert "thin market" in out["short_liquidity"]["reason"]


def test_classic_eval_all_gates_liquid_cohort_entry_clears_both():
    ctx = _classic_ctx(market_volume_24h_usd=500e6)
    out = risk_gates.eval_all_gates(ctx, _CFG)["results"]
    assert out["confidence"]["pass"] is True
    assert out["short_liquidity"]["pass"] is True


# ── seat -> analysis carry ────────────────────────────────────────────────

from hermes_trader.agents import decision_voice as dv
from hermes_trader.agents import research

import test_systemone_primary as tsp


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # same isolation as tsp's module fixture (autouse does not cross modules):
    # fresh CONFIG_PATH file + no env URL overrides leaking into the resolver
    import hermes_trader.agents.config_store as cs
    from pathlib import Path
    Path(cs.CONFIG_PATH).unlink(missing_ok=True)
    monkeypatch.setenv("HERMES_DV_FILE", str(tmp_path / "dv.jsonl"))
    monkeypatch.delenv("LLM_SYSTEMONE_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_SYSTEMONE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_DV_BASE_URL", raising=False)
    yield


def test_seat_attaches_cohort_tag_and_analysis_carries_it(monkeypatch):
    tsp._write_cfg({"engine": "systemone", "model": "decision2-lux-9b",
                    "base_url": "http://so.test:8080", "vwap_fields": True})
    answers = tsp._answers({"long": 0.05, "short": 0.8, "none": 0.15},
                           {0: 0.0, 1: 0.0, 2: 0.2, 3: 0.6, 4: 0.2}, trap=0.3)
    monkeypatch.setattr(dv, "call_systemone",
                        lambda *a, **k: (answers, 42, 30))
    tsp._patch_research_deps(monkeypatch)
    a = research.research("BTC", tsp._perception(
        triggers=[dict(tsp._VWAP_SHORT)]))
    assert a["verdict"] == "SHORT"
    assert a["cohort_tag"] == "vwap-fade-short"  # survives the whitelist


def test_seat_no_tag_for_counter_signal_entry(monkeypatch):
    tsp._write_cfg({"engine": "systemone", "model": "decision2-lux-9b",
                    "base_url": "http://so.test:8080", "vwap_fields": True})
    answers = tsp._answers({"long": 0.8, "short": 0.05, "none": 0.15},
                           {0: 0.0, 1: 0.0, 2: 0.2, 3: 0.6, 4: 0.2}, trap=0.3)
    monkeypatch.setattr(dv, "call_systemone",
                        lambda *a, **k: (answers, 42, 30))
    tsp._patch_research_deps(monkeypatch)
    a = research.research("BTC", tsp._perception(
        triggers=[dict(tsp._VWAP_SHORT)]))
    assert a["verdict"] == "LONG"  # counter to the fade-short signal
    assert not a.get("cohort_tag")  # weld class stays on the general path
