"""Tests for the systemone PRIMARY-engine seat (llm.primary_engine switch).

The decision model (Clef / Decision-2.0 / d3 family, llama.cpp
POST /v1/systemone) can replace the chat LLM as the entry verdict engine.
Covers: the config resolver (default = "llm", fail-open degradation, hot
read, env URL override), the research() branch (default path untouched —
LLM called, systemone machinery never touched; systemone path — verdict
mapping, archive shape, failure-PASS + ai_down with NO LLM fallback,
held CLOSE, VETO), and verdict-shape parity with parse_verdict.
No network: call_systemone is monkeypatched like the DV observer tests.
"""

import json

import pytest

from hermes_trader.agents import decision_voice as dv
from hermes_trader.agents import research
from hermes_trader.models.types import Candle


# ── helpers (mirror tests/test_decision_voice.py) ─────────────────────────

def _candles(n: int = 40) -> list:
    base = 100.0
    return [
        Candle(t=1_700_000_000_000 + i * 3_600_000, o=base, h=base + 1,
               l=base - 1, c=base + (i % 5) * 0.2, v=1000)
        for i in range(n)
    ]


def _perception(**over):
    p = {
        "id": "pid-so-1", "coin": "BTC", "mid": 50_000.0,
        "composite_score": 62.0, "daily_move_pct": 3.1,
        "triggers": [
            {"name": "momentumBurst", "fired": True, "reason": "vol surge"},
        ],
    }
    p.update(over)
    return p


def _answers(direction_probs, conviction_probs, trap=0.1, news=0.0, close=None):
    a = {
        "direction": {"type": "choice", "probabilities": direction_probs,
                      "choice": max(direction_probs, key=direction_probs.get),
                      "confidence": 0.7},
        "conviction": {"type": "score", "probabilities": conviction_probs,
                       "score": 2.0, "confidence": 0.6, "legend": []},
        "trap": {"type": "noul", "noul": trap},
        "news_negative": {"type": "noul", "noul": news},
    }
    if close is not None:
        a["close_now"] = {"type": "noul", "noul": close}
    return a


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Fresh agent-config per test (CONFIG_PATH-direct pattern) + isolated DV
    file so a leaked observer row can't pollute assertions."""
    import hermes_trader.agents.config_store as cs
    from pathlib import Path
    Path(cs.CONFIG_PATH).unlink(missing_ok=True)
    monkeypatch.setenv("HERMES_DV_FILE", str(tmp_path / "dv.jsonl"))
    monkeypatch.delenv("LLM_SYSTEMONE_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_SYSTEMONE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_DV_BASE_URL", raising=False)
    yield


def _write_cfg(slot):
    import hermes_trader.agents.config_store as cs
    llm = {"primary_engine": slot} if slot is not None else {}
    with open(cs.CONFIG_PATH, "w") as f:
        json.dump({"mode": "SHADOW", "llm": llm}, f)


def _patch_research_deps(monkeypatch, verdict_json='x\n{"verdict":"PASS","confidence":0.2}'):
    """Standard research() dep patching + call capture."""
    calls = {"llm": 0, "systemone": 0}
    monkeypatch.setattr(research, "fetch_hl_candles", lambda *a, **k: _candles())
    monkeypatch.setattr(research, "_fetch_funding_rate", lambda c: "0.01%/hr")
    monkeypatch.setattr(research, "_fetch_news", lambda c: "no news")
    monkeypatch.setattr(research, "resolve_user_address", lambda: None)
    monkeypatch.setattr(research, "build_prior_call_block", lambda *a, **k: "")
    monkeypatch.setattr(research, "build_rvol_block", lambda c: "")

    def llm(sp, um):
        calls["llm"] += 1
        return verdict_json, None
    monkeypatch.setattr(research, "_call_ai", llm)

    real = dv.call_systemone

    def counting(*a, **k):
        calls["systemone"] += 1
        return real(*a, **k)
    monkeypatch.setattr(dv, "call_systemone", counting)
    return calls


def _capture_archive(monkeypatch):
    rows = []
    monkeypatch.setattr(research.prompt_log, "record_call", lambda rec: rows.append(rec))
    return rows


# ── config resolver ───────────────────────────────────────────────────────

def test_default_no_block_is_llm_engine():
    assert dv.primary_engine_config() == {"engine": "llm"}


def test_malformed_slot_degrades_to_llm():
    _write_cfg({"engine": "banana"})
    assert dv.primary_engine_config() == {"engine": "llm"}
    import hermes_trader.agents.config_store as cs
    with open(cs.CONFIG_PATH, "w") as f:
        json.dump({"mode": "SHADOW", "llm": {"primary_engine": "not-a-dict"}}, f)
    assert dv.primary_engine_config() == {"engine": "llm"}
    # unreadable config file -> fail-open, never raises
    with open(cs.CONFIG_PATH, "w") as f:
        f.write("{not json")
    assert dv.primary_engine_config() == {"engine": "llm"}


def test_systemone_missing_model_or_url_is_inert(monkeypatch, caplog):
    _write_cfg({"engine": "systemone", "base_url": "http://x:8080"})  # no model
    assert dv.primary_engine_config() == {"engine": "llm"}
    _write_cfg({"engine": "systemone", "model": "d2"})  # no url anywhere
    assert dv.primary_engine_config() == {"engine": "llm"}
    assert "INERT" in caplog.text  # an intended flip that no-ops logs LOUDLY


def test_env_url_overrides_config_base_url(monkeypatch):
    _write_cfg({"engine": "systemone", "model": "d2", "base_url": "http://cfg:8080"})
    monkeypatch.setenv("LLM_SYSTEMONE_BASE_URL", "http://env:8081")
    cfg = dv.primary_engine_config()
    assert cfg["engine"] == "systemone" and cfg["base_url"] == "http://env:8081"


def test_hot_read_follows_config_file():
    _write_cfg({"engine": "llm"})
    assert dv.primary_engine_config() == {"engine": "llm"}
    _write_cfg({"engine": "systemone", "model": "d2", "base_url": "http://x:8080"})
    cfg = dv.primary_engine_config()
    assert cfg["engine"] == "systemone" and cfg["model"] == "d2"
    assert cfg["timeout_s"] == dv.DEFAULT_TIMEOUT_S
    assert cfg["max_state_chars"] == dv.DEFAULT_MAX_STATE_CHARS


# ── verdict-shape parity with parse_verdict ───────────────────────────────

def test_verdict_shape_parity():
    so = dv.verdict_from_answers(
        _answers({"long": 0.7, "short": 0.1, "none": 0.2}, {4: 1.0}),
        "BTC", _perception(),
    )
    llm = research.parse_verdict('{"verdict":"LONG","confidence":0.8}', "BTC", _perception())
    assert set(so.keys()) - {"_answers"} == set(llm.keys())


# ── research() branch: default path untouched ─────────────────────────────

def test_research_default_path_unchanged(monkeypatch):
    calls = _patch_research_deps(monkeypatch, 'clean trend\n{"verdict":"LONG","confidence":0.78,"side":"long"}')
    rows = _capture_archive(monkeypatch)
    a = research.research("BTC", _perception())
    assert a["verdict"] == "LONG" and a["side"] == "long"
    assert calls["llm"] == 1 and calls["systemone"] == 0
    prim = [r for r in rows if r.get("role") == "primary"]
    assert len(prim) == 1
    assert prim[0]["system_prompt"] is not None  # chat prompt archived as before


def test_research_default_when_slot_absent_from_llm_block(monkeypatch):
    # the LIVE config shape (llm.primary/duelist/decision_voice, no
    # primary_engine key) must keep the chat path byte-for-byte
    import hermes_trader.agents.config_store as cs
    with open(cs.CONFIG_PATH, "w") as f:
        json.dump({"mode": "LIVE", "llm": {
            "primary": {"model": "chat-model"},
            "duelist": {"model": ""},
            "decision_voice": {"model": "clef-flash"},
        }}, f)
    calls = _patch_research_deps(monkeypatch, 'x\n{"verdict":"PASS","confidence":0.3}')
    a = research.research("BTC", _perception())
    assert calls["llm"] == 1 and calls["systemone"] == 0
    assert a["verdict"] == "PASS"


# ── research() branch: systemone success ──────────────────────────────────

def _arm_systemone(monkeypatch, answers, held=False):
    _write_cfg({"engine": "systemone", "model": "d2-lux", "base_url": "http://so.test:8080"})
    seen = {}

    def fake_call(base_url, api_key, model, state, questions, timeout_s=30.0):
        seen["base_url"] = base_url
        seen["model"] = model
        seen["questions"] = questions
        seen["state"] = state
        return answers, 42, 30
    monkeypatch.setattr(dv, "call_systemone", fake_call)
    if held:
        monkeypatch.setattr(research, "resolve_user_address", lambda: "0xabc")
        monkeypatch.setattr(research, "fetch_account_state", lambda user, include_hip3=False: {
            "equity": "1000",
            "asset_positions": [{"position": {
                "coin": "BTC", "szi": "0.1", "positionValue": "5000",
                "entryPx": "49000", "unrealizedPnl": "100",
            }}],
        })
    return seen


def test_research_systemone_long(monkeypatch):
    answers = _answers({"long": 0.8, "short": 0.05, "none": 0.15},
                       {0: 0.0, 1: 0.0, 2: 0.2, 3: 0.6, 4: 0.2})
    seen = _arm_systemone(monkeypatch, answers)
    calls = _patch_research_deps(monkeypatch)
    rows = _capture_archive(monkeypatch)
    a = research.research("BTC", _perception())

    assert calls["llm"] == 0  # the 27B is NOT called
    assert a["verdict"] == "LONG" and a["side"] == "long"
    assert a["ai_down"] is False
    assert a["primary_model"] == "d2-lux"  # the model that ANSWERED
    assert seen["base_url"] == "http://so.test:8080" and seen["model"] == "d2-lux"
    assert "close_now" not in seen["questions"]  # not held

    prim = [r for r in rows if r.get("role") == "primary"]
    assert len(prim) == 1 and prim[0]["model"] == "d2-lux"
    assert prim[0]["system_prompt"] is None
    req = json.loads(prim[0]["user_message"])
    assert set(req) == {"state", "questions"} and req["state"]["coin"] == "BTC"
    assert json.loads(prim[0]["raw_response"]) == json.loads(json.dumps(answers))
    assert prim[0]["parsed"]["verdict"] == "LONG"


def test_research_systemone_veto(monkeypatch):
    answers = _answers({"long": 0.1, "short": 0.1, "none": 0.8},
                       {0: 0.4, 1: 0.3, 2: 0.3, 3: 0.0, 4: 0.0}, trap=0.8)
    _arm_systemone(monkeypatch, answers)
    _patch_research_deps(monkeypatch)
    a = research.research("BTC", _perception())
    assert a["verdict"] == "VETO"  # executor refuses VETO before any override


def test_research_systemone_held_close(monkeypatch):
    answers = _answers({"long": 0.1, "short": 0.1, "none": 0.8},
                       {0: 0.5, 1: 0.3, 2: 0.2, 3: 0.0, 4: 0.0}, close=0.7)
    _patch_research_deps(monkeypatch)
    # NOTE: _arm_systemone must run AFTER _patch_research_deps — the deps
    # helper stubs resolve_user_address to None, which would clobber the
    # held-book stub. (For the non-held tests order is irrelevant.)
    seen = _arm_systemone(monkeypatch, answers, held=True)
    a = research.research("BTC", _perception())
    assert "close_now" in seen["questions"]  # held -> question asked
    assert a["verdict"] == "CLOSE"
    # state carries the P&L context (B.39 close-voice fix rides the seat too)
    assert seen["state"]["held_position"]["entry_px"] == 49000.0


# ── research() branch: failure = PASS + ai_down, NO LLM fallback ──────────

def test_research_systemone_failure_pass_ai_down(monkeypatch, caplog):
    _write_cfg({"engine": "systemone", "model": "d2-lux", "base_url": "http://so.test:8080"})
    monkeypatch.setattr(dv, "call_systemone", lambda *a, **k: (None, 12, None))
    calls = _patch_research_deps(monkeypatch)
    rows = _capture_archive(monkeypatch)
    a = research.research("BTC", _perception())

    assert a["verdict"] == "PASS"
    assert a["ai_down"] is True  # error code, not an opinion
    assert calls["llm"] == 0     # NO LLM fallback — 27B stays unloaded
    assert "no LLM fallback" in caplog.text
    prim = [r for r in rows if r.get("role") == "primary"]
    assert prim and prim[0]["parsed"]["ai_down"] is True
    assert prim[0]["user_message"] is None  # no request was rendered
