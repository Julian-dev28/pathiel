"""Tests for the decision-voice observer (Clef-family /v1/systemone shadow).

Covers: dormancy gating (no model / no endpoint / disabled), the state and
question builders (close_now only when held, state bound), the answers ->
verdict mapping (all five verdict routes, confidence from the score scale),
the call contract (never raises, no row on failure), and the research()
integration (row written, analysis field, session event, prompt_log record).
No network: the endpoint is monkeypatched like the duel-store tests.
"""

import json

import pytest

from hermes_trader.agents import decision_voice as dv
from hermes_trader.agents import research
from hermes_trader.models.types import Candle


# ── helpers ────────────────────────────────────────────────────────────────

def _candles(n: int = 40) -> list:
    base = 100.0
    return [
        Candle(t=1_700_000_000_000 + i * 3_600_000, o=base, h=base + 1,
               l=base - 1, c=base + (i % 5) * 0.2, v=1000)
        for i in range(n)
    ]


def _perception(**over):
    p = {
        "id": "pid-dv-1", "coin": "BTC", "mid": 50_000.0,
        "composite_score": 62.0, "daily_move_pct": 3.1,
        "triggers": [
            {"name": "momentumBurst", "fired": True, "reason": "vol surge"},
            {"name": "dailyMover", "fired": False, "reason": ""},
        ],
    }
    p.update(over)
    return p


def _tf(bull: bool = True):
    return {
        "ema8": 101.0 if bull else 99.0,
        "ema21": 100.0,
        "rsi14": 61.0 if bull else 39.0,
        "atr14": 1.2, "adx14": 25.0,
        "last_close": 100.8, "last_time": 1_700_000_000_000,
    }


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
def _isolated_dv_file(tmp_path, monkeypatch):
    p = tmp_path / "dv.jsonl"
    monkeypatch.setenv("HERMES_DV_FILE", str(p))
    # Fresh agent-config per test: the config FILE persists across tests in
    # the conftest tmp dir, so a model written by one test would otherwise
    # leak into the next (dormancy assertions are order-dependent without
    # this).
    import hermes_trader.agents.config_store as cs
    from pathlib import Path
    Path(cs.CONFIG_PATH).unlink(missing_ok=True)
    yield p


@pytest.fixture
def _dv_on(monkeypatch, tmp_path):
    """Enable the decision voice against a fake endpoint (config slot + env).
    Owns the agent-config file for one test (CONFIG_PATH-direct pattern, with
    backup/restore like test_duel_store's agent_cfg fixture)."""
    import hermes_trader.agents.config_store as cs
    from pathlib import Path
    cfg_path = Path(cs.CONFIG_PATH)
    had = cfg_path.exists()
    backup = cfg_path.read_text() if had else None
    with open(cfg_path, "w") as f:
        json.dump({"mode": "SHADOW", "llm": {"decision_voice": {"model": "clef-flash"}}}, f)
    monkeypatch.setenv("LLM_DV_BASE_URL", "http://dv.test:8080")
    monkeypatch.delenv("LLM_DV_API_KEY", raising=False)
    yield
    if backup is None:
        cfg_path.unlink(missing_ok=True)
    else:
        cfg_path.write_text(backup)


# ── dormancy gating ────────────────────────────────────────────────────────

def test_dormant_with_no_config(monkeypatch):
    monkeypatch.delenv("LLM_DV_BASE_URL", raising=False)
    assert dv._dv_config() is None
    assert not dv.dv_enabled()


def test_dormant_model_without_endpoint(monkeypatch):
    import hermes_trader.agents.config_store as cs
    with open(cs.CONFIG_PATH, "w") as f:
        json.dump({"mode": "SHADOW", "llm": {"decision_voice": {"model": "clef-flash"}}}, f)
    monkeypatch.delenv("LLM_DV_BASE_URL", raising=False)
    assert dv._dv_config() is None


def test_dormant_endpoint_without_model(monkeypatch):
    monkeypatch.setenv("LLM_DV_BASE_URL", "http://dv.test:8080")
    assert dv._dv_config() is None


def test_explicit_disable_flag(monkeypatch):
    import hermes_trader.agents.config_store as cs
    with open(cs.CONFIG_PATH, "w") as f:
        json.dump({"mode": "SHADOW",
                   "llm": {"decision_voice": {"model": "clef-flash", "enabled": False}}}, f)
    monkeypatch.setenv("LLM_DV_BASE_URL", "http://dv.test:8080")
    assert dv._dv_config() is None


def test_config_resolves_with_both(monkeypatch):
    import hermes_trader.agents.config_store as cs
    with open(cs.CONFIG_PATH, "w") as f:
        json.dump({"mode": "LIVE",
                   "llm": {"decision_voice": {"model": "clef-flash", "timeout_s": 5}}}, f)
    monkeypatch.setenv("LLM_DV_BASE_URL", "http://192.168.1.16:8080/")
    cfg = dv._dv_config()
    assert cfg is not None
    assert cfg["model"] == "clef-flash"
    assert cfg["base_url"] == "http://192.168.1.16:8080/"
    assert cfg["timeout_s"] == 5.0


# ── state + schema builders ────────────────────────────────────────────────

def test_close_question_only_when_held():
    assert "close_now" not in dv.build_questions(held=False)
    assert "close_now" in dv.build_questions(held=True)
    # every question is one of the three llama.cpp decision types
    for q in dv.build_questions(held=True).values():
        assert q["type"] in ("choice", "score", "noul")


# ── B.45: news field + question ride ONLY a live feed ──────────────────────

def test_news_question_only_when_live():
    assert "news_negative" not in dv.build_questions(held=False)
    assert "news_negative" not in dv.build_questions(held=True, news_live=False)
    assert "news_negative" in dv.build_questions(held=False, news_live=True)


def test_news_field_dropped_when_feed_dead():
    for dead in ("no news", "", "  NO NEWS ", "none"):
        st = dv.build_state("BTC", _perception(), _tf(), _tf(), _tf(),
                            "0.0100%/hr", dead, [], win_rate=0.5, n_closes=3)
        assert "news_recent" not in st, dead
    st = dv.build_state("BTC", _perception(), _tf(), _tf(), _tf(),
                        "0.0100%/hr", "exchange halts BTC withdrawals", [],
                        win_rate=0.5, n_closes=3)
    assert st["news_recent"] == "exchange halts BTC withdrawals"


def test_news_live_helper():
    assert dv._news_live("some headline") is True
    assert dv._news_live("no news") is False
    assert dv._news_live("No News") is False
    assert dv._news_live("") is False
    assert dv._news_live(None) is False


def test_meta_fields_dropped_from_state():
    # B.45 per-field gauntlet: both meta fields price net-negative for the
    # decision seat — composite_score strip +1.67%, track_record add −11.96%.
    st = dv.build_state("BTC", _perception(), _tf(), _tf(), _tf(),
                        "0.0100%/hr", "no news", [], win_rate=0.63, n_closes=650)
    assert "composite_score" not in st
    assert "track_record" not in st
    # the rest of the schema is untouched
    assert st["mid"] == 50_000.0 and st["daily_move_pct"] == 3.1
    assert "tf_1h" in st and "triggers_fired" in st


def test_state_shape_and_held_block():
    st = dv.build_state(
        "BTC", _perception(), _tf(), _tf(), _tf(bull=False), "0.0100%/hr",
        "headline here", [{"coin": "BTC", "side": "long", "size_usd": 33.0,
                           "entry_px": 48_000.0, "unrealized_pnl_usd": -1.32}],
        win_rate=0.55, n_closes=20,
    )
    assert st["coin"] == "BTC" and st["mid"] == 50_000.0
    assert st["triggers_fired"] == ["momentumBurst"]
    assert st["tf_1h"]["ema8_above_ema21"] is True
    assert st["tf_1d"]["ema8_above_ema21"] is False
    assert st["held_position"]["side"] == "long"
    # B.39 close-voice audit fix: entry + PnL ride the state (signed for side)
    assert st["held_position"]["entry_px"] == 48_000.0
    assert st["held_position"]["unrealized_pnl_usd"] == -1.32
    assert st["held_position"]["pnl_pct_vs_entry"] == 4.17  # (50000-48000)/48000
    # JSON-serialisable (the systemone body is JSON)
    json.dumps(st)


def test_state_fade_names_stripped_fields_carried():
    """B.44: side-less fade names are danger-tokens for the decision model —
    they come OUT of triggers_fired and side-aware fields go IN."""
    p = _perception(triggers=[
        {"name": "momentumBurst", "fired": True, "reason": "vol surge"},
        {"name": "mfiExtreme", "fired": True, "reason": "long — MFI 12 <= 20 oversold",
         "side": "long", "kind": "oversold", "mfi_value": 12, "threshold": 20},
        {"name": "rsiDivergence", "fired": True, "reason": "short — bearish RSI divergence",
         "side": "short", "flavour": "bearish", "extreme_pct": 1.8},
    ])
    st = dv.build_state("BTC", p, _tf(), _tf(), _tf(), "0.0100%/hr", "n", [],
                        win_rate=0.5, n_closes=10)
    assert st["triggers_fired"] == ["momentumBurst"]
    assert st["mfi_extreme"] == {
        "signal": "mfi_oversold", "trade_side": "long",
        "expected_move": "price_reverts_from_volume_exhaustion",
        "mfi_value": 12, "threshold": 20,
    }
    assert st["rsi_divergence"] == {
        "signal": "rsi_divergence_bearish", "trade_side": "short",
        "expected_move": "momentum_reversal_from_divergence",
        "price_extreme_pct": 1.8,
    }
    json.dumps(st)


def test_state_fade_name_stripped_even_without_structured_keys():
    """Strip is unconditional (strip-only arm measured +40.5%): a fade name
    never rides triggers_fired even if its payload lacks the new keys."""
    p = _perception(triggers=[
        {"name": "mfiExtreme", "fired": True, "reason": "long — MFI 12 <= 20 oversold"},
    ])
    st = dv.build_state("BTC", p, _tf(), _tf(), _tf(), "0.0100%/hr", "n", [],
                        win_rate=0.5, n_closes=10)
    assert st["triggers_fired"] == []
    assert "mfi_extreme" not in st


def test_state_fade_shape_names_for_models_that_read_them_directionally():
    """fade_shape='names' (decision2 profile, fade_fields=false): the names
    STAY in triggers_fired and no side-aware fields are emitted — the B.44
    fields price −46.2% on decision2 (WATCHLIST B.44/B.45 re-run), so the
    shape is a per-model profile knob, not a global constant."""
    p = _perception(triggers=[
        {"name": "momentumBurst", "fired": True, "reason": "vol surge"},
        {"name": "mfiExtreme", "fired": True, "reason": "long — MFI 12 <= 20 oversold",
         "side": "long", "kind": "oversold", "mfi_value": 12, "threshold": 20},
        {"name": "rsiDivergence", "fired": True, "reason": "short — bearish",
         "side": "short", "flavour": "bearish", "extreme_pct": 1.8},
    ])
    st = dv.build_state("BTC", p, _tf(), _tf(), _tf(), "0.0100%/hr", "n", [],
                        win_rate=0.5, n_closes=10, fade_shape="names")
    assert st["triggers_fired"] == ["momentumBurst", "mfiExtreme", "rsiDivergence"]
    assert "mfi_extreme" not in st and "rsi_divergence" not in st
    json.dumps(st)


def test_state_held_pnl_signed_for_short():
    # short at mid ABOVE entry = losing: pnl_pct must be negative
    st = dv.build_state(
        "BTC", _perception(), _tf(), _tf(), _tf(), "N/A", "no news",
        [{"coin": "BTC", "side": "short", "size_usd": 33.0,
          "entry_px": 48_000.0, "unrealized_pnl_usd": -2.5}],
        win_rate=0.5, n_closes=3,
    )
    assert st["held_position"]["pnl_pct_vs_entry"] == -4.17
    assert st["held_position"]["unrealized_pnl_usd"] == -2.5


def test_state_held_without_entry_keys_still_works():
    # defensive: an open_positions row missing the new keys (older callers /
    # partial fetch) must not break the state build
    st = dv.build_state(
        "BTC", _perception(), _tf(), _tf(), _tf(), "N/A", "no news",
        [{"coin": "BTC", "side": "long", "size_usd": 33.0}],
        win_rate=0.5, n_closes=3,
    )
    assert st["held_position"]["side"] == "long"
    assert "entry_px" not in st["held_position"]
    assert "pnl_pct_vs_entry" not in st["held_position"]


def test_state_not_polluted_by_other_positions():
    st = dv.build_state(
        "BTC", _perception(), _tf(), _tf(), _tf(), "N/A", "no news",
        [{"coin": "ETH", "side": "short", "size_usd": 40.0}],
        win_rate=0.5, n_closes=3,
    )
    assert "held_position" not in st


def test_state_bounded_under_max_chars():
    st = dv.build_state(
        "BTC", _perception(), _tf(), _tf(), _tf(), "N/A", "x" * 5000,
        [], win_rate=0.5, n_closes=3, max_state_chars=2000,
    )
    assert len(json.dumps(st)) <= 2000 + 1600  # news clipped to budget/4


# ── answers -> verdict mapping ─────────────────────────────────────────────

def test_verdict_long_from_direction():
    p = dv.verdict_from_answers(
        _answers({"long": 0.6, "short": 0.1, "none": 0.3},
                 {0: 0.0, 1: 0.0, 2: 0.2, 3: 0.6, 4: 0.2}),
        "BTC", _perception(),
    )
    assert p["verdict"] == "LONG" and p["side"] == "long"
    assert p["confidence"] > 0.5


def test_verdict_short():
    p = dv.verdict_from_answers(
        _answers({"long": 0.1, "short": 0.7, "none": 0.2},
                 {0: 0.1, 1: 0.1, 2: 0.3, 3: 0.4, 4: 0.1}),
        "BTC", _perception(),
    )
    assert p["verdict"] == "SHORT" and p["side"] == "short"


def test_verdict_pass_vs_veto_on_trap():
    conv = {0: 0.5, 1: 0.3, 2: 0.2, 3: 0.0, 4: 0.0}
    none_probs = {"long": 0.1, "short": 0.1, "none": 0.8}
    p_pass = dv.verdict_from_answers(_answers(none_probs, conv, trap=0.2), "BTC", _perception())
    p_veto = dv.verdict_from_answers(_answers(none_probs, conv, trap=0.8), "BTC", _perception())
    assert p_pass["verdict"] == "PASS"
    assert p_veto["verdict"] == "VETO"


def test_verdict_close_only_via_close_question():
    # not asked -> never CLOSE, even with a huge trap
    p = dv.verdict_from_answers(
        _answers({"long": 0.1, "short": 0.1, "none": 0.8}, {0: 1.0}, trap=0.9),
        "BTC", _perception(),
    )
    assert p["verdict"] != "CLOSE"
    # asked (held) and true -> CLOSE
    p2 = dv.verdict_from_answers(
        _answers({"long": 0.1, "short": 0.1, "none": 0.8}, {0: 1.0}, close=0.7),
        "BTC", _perception(),
    )
    assert p2["verdict"] == "CLOSE"


def test_news_risk_negative_flag():
    p = dv.verdict_from_answers(
        _answers({"long": 0.6, "short": 0.2, "none": 0.2}, {2: 1.0}, news=0.8),
        "BTC", _perception(),
    )
    assert p["news_risk"] == "negative"


def test_confidence_clamped_and_reasoning_present():
    p = dv.verdict_from_answers(
        _answers({"long": 1.0, "short": 0.0, "none": 0.0}, {4: 1.0}),
        "BTC", _perception(),
    )
    assert 0.0 <= p["confidence"] <= 1.0
    assert "dir L" in p["reasoning"]


def test_malformed_answers_degrade_to_pass():
    p = dv.verdict_from_answers({"direction": {}}, "BTC", _perception())
    assert p["verdict"] == "PASS"
    assert p["confidence"] == 0.0
    assert not p["ai_down"]


# ── the call contract ──────────────────────────────────────────────────────

def test_call_never_raises_on_dead_endpoint(monkeypatch):
    answers, wall_ms, server_ms = dv.call_systemone(
        "http://127.0.0.1:1", "", "clef-flash", {"coin": "BTC"}, {}, timeout_s=1.0,
    )
    assert answers is None
    assert wall_ms >= 0


def test_record_dv_appends_jsonl(_isolated_dv_file):
    dv.record_dv({"coin": "BTC", "dv_verdict": "PASS"})
    dv.record_dv({"coin": "ETH", "dv_verdict": "LONG"})
    rows = [json.loads(l) for l in _isolated_dv_file.read_text().splitlines()]
    assert len(rows) == 2 and all("ts" in r for r in rows)


# ── research() integration ─────────────────────────────────────────────────

def _patch_research_deps(monkeypatch, verdict_json):
    """Standard research() dep patching (mirrors test_duel_store): candles,
    funding, news, account state, the LLM call."""
    monkeypatch.setattr(research, "fetch_hl_candles", lambda *a, **k: _candles())
    monkeypatch.setattr(research, "_fetch_funding_rate", lambda c: "0.01%/hr")
    monkeypatch.setattr(research, "_fetch_news", lambda c: "no news")
    monkeypatch.setattr(research, "resolve_user_address", lambda: None)
    monkeypatch.setattr(research, "_call_ai", lambda sp, um: (verdict_json, None))
    monkeypatch.setattr(research, "build_prior_call_block", lambda *a, **k: "")
    monkeypatch.setattr(research, "build_rvol_block", lambda c: "")


def test_research_dormant_writes_no_dv_row(_dv_on, monkeypatch):
    # endpoint env removed -> hook returns None, no row, primary unaffected
    monkeypatch.delenv("LLM_DV_BASE_URL")
    _patch_research_deps(monkeypatch, 'thinking\n{"verdict":"PASS","confidence":0.2}')
    a = research.research("BTC", _perception())
    assert a["verdict"] == "PASS"
    assert a["decision_voice_at_entry"] is None
    assert not dv.dv_file() or not __import__("os").path.exists(dv.dv_file())


def test_research_records_dv_row(_dv_on, monkeypatch, tmp_path):
    fake = _answers({"long": 0.65, "short": 0.05, "none": 0.30},
                    {0: 0.0, 1: 0.05, 2: 0.25, 3: 0.55, 4: 0.15})

    def fake_call(base_url, api_key, model, state, questions, timeout_s=30.0):
        assert base_url == "http://dv.test:8080"
        assert "close_now" not in questions  # BTC not held
        return fake, 42, 30

    monkeypatch.setattr(dv, "call_systemone", fake_call)
    _patch_research_deps(monkeypatch, 'clean trend\n{"verdict":"LONG","confidence":0.78,"side":"long"}')
    a = research.research("BTC", _perception())

    assert a["decision_voice_at_entry"] == {
        "model": "clef-flash", "verdict": "LONG",
        "confidence": a["decision_voice_at_entry"]["confidence"],
        "side": "long",
        # Change-1 scalars must ride the whitelist (2026-08 pitfall).
        "trap": 0.1, "close_now": None,
    }
    rows = [json.loads(l) for l in tmp_path.joinpath("dv.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    r = rows[0]
    assert r["dv_verdict"] == "LONG" and r["coin"] == "BTC"
    assert r["perception_id"] == "pid-dv-1"
    assert r["dv_answers"]["direction"]["probabilities"]["long"] == 0.65
    assert r["dv_ms"] == 42 and r["dv_server_ms"] == 30


def test_research_dv_failure_is_silent(_dv_on, monkeypatch):
    monkeypatch.setattr(dv, "call_systemone",
                        lambda *a, **k: (None, 10, None))
    _patch_research_deps(monkeypatch, '{"verdict":"PASS","confidence":0.3}')
    a = research.research("BTC", _perception())
    assert a["verdict"] == "PASS"
    assert a["decision_voice_at_entry"] is None


def test_research_held_coin_asks_close(_dv_on, monkeypatch):
    asked = {}

    def fake_call(base_url, api_key, model, state, questions, timeout_s=30.0):
        asked["held"] = "close_now" in questions
        asked["state_held"] = "held_position" in state
        return _answers({"long": 0.1, "short": 0.1, "none": 0.8},
                        {0: 1.0}, close=0.8), 20, 15

    monkeypatch.setattr(dv, "call_systemone", fake_call)
    monkeypatch.setattr(research, "fetch_hl_candles", lambda *a, **k: _candles())
    monkeypatch.setattr(research, "_fetch_funding_rate", lambda c: "0.01%/hr")
    monkeypatch.setattr(research, "_fetch_news", lambda c: "no news")
    monkeypatch.setattr(research, "resolve_user_address", lambda: "0xabc")
    monkeypatch.setattr(research, "fetch_account_state", lambda user, include_hip3=False: {
        "equity": "1000",
        "asset_positions": [{"position": {"coin": "BTC", "szi": "0.001",
                                          "positionValue": "50", "entryPx": "49000"}}],
    })
    monkeypatch.setattr(research, "_call_ai",
                        lambda sp, um: ('{"verdict":"PASS","confidence":0.4}', None))
    monkeypatch.setattr(research, "build_prior_call_block", lambda *a, **k: "")
    monkeypatch.setattr(research, "build_rvol_block", lambda c: "")

    a = research.research("BTC", _perception())
    assert asked["held"] is True and asked["state_held"] is True
    assert a["decision_voice_at_entry"]["verdict"] == "CLOSE"
