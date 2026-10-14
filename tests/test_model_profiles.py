"""Model-profile config split (2026-10-14): model-specific tuning lives in
config-models/<name>.json, referenced from the main JSON by
``"model_profile": "<name>"``. Profile = base, inline slot keys override;
missing/corrupt profile fails open to the inline keys with a loud log."""

import json

from hermes_trader.agents import config_store
from hermes_trader.agents.config_store import resolve_model_slot


def test_resolve_no_profile_is_untouched():
    slot = {"model": "x", "timeout_s": 5}
    assert resolve_model_slot(slot) is slot
    assert resolve_model_slot(None) is None
    assert resolve_model_slot("string") == "string"


def test_resolve_profile_is_base_inline_overrides(tmp_path, monkeypatch):
    (tmp_path / "m1.json").write_text(json.dumps(
        {"model": "m1", "trap_veto_threshold": 0.35, "fade_fields": False}))
    monkeypatch.setattr(config_store, "MODEL_CONFIG_DIR", str(tmp_path))
    out = resolve_model_slot({"model_profile": "m1", "trap_veto_threshold": 0.5})
    assert out["model"] == "m1"
    assert out["fade_fields"] is False
    assert out["trap_veto_threshold"] == 0.5  # inline key wins over profile
    assert "model_profile" not in out


def test_resolve_missing_file_fails_open(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "MODEL_CONFIG_DIR", str(tmp_path))
    out = resolve_model_slot({"model_profile": "gone", "engine": "systemone"})
    assert out == {"engine": "systemone"}  # inline keys only, no raise


def test_resolve_corrupt_file_fails_open(tmp_path, monkeypatch):
    (tmp_path / "bad.json").write_text("{not json")
    monkeypatch.setattr(config_store, "MODEL_CONFIG_DIR", str(tmp_path))
    assert resolve_model_slot({"model_profile": "bad", "a": 1}) == {"a": 1}


def test_resolve_rejects_path_traversal(tmp_path, monkeypatch):
    (tmp_path.parent / "secret.json").write_text(json.dumps({"evil": True}))
    monkeypatch.setattr(config_store, "MODEL_CONFIG_DIR", str(tmp_path))
    assert resolve_model_slot({"model_profile": "../secret", "a": 1}) == {"a": 1}


def test_primary_engine_config_resolves_profile(tmp_path, monkeypatch):
    cfg = tmp_path / ".agent-config.json"
    cfg.write_text(json.dumps({"llm": {"primary_engine": {
        "engine": "systemone", "model_profile": "d2"}}}))
    models = tmp_path / "config-models"
    models.mkdir()
    (models / "d2.json").write_text(json.dumps({
        "model": "decision2-lux-9b", "base_url": "http://decision2-serve:8081",
        "trap_veto_threshold": 0.35, "trap_veto_shadow_mode": False,
        "fade_fields": False}))
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(config_store, "MODEL_CONFIG_DIR", str(models))
    from hermes_trader.agents import decision_voice as dv
    e = dv.primary_engine_config()
    assert e["engine"] == "systemone"
    assert e["model"] == "decision2-lux-9b"
    assert e["base_url"] == "http://decision2-serve:8081"
    assert e["fade_fields"] is False
    assert e["trap_veto_threshold"] == 0.35
    assert e["trap_veto_shadow_mode"] is False


def test_slot_get_resolves_profile(tmp_path, monkeypatch):
    cfg = tmp_path / ".agent-config.json"
    cfg.write_text(json.dumps({"llm": {"primary": {"model_profile": "p"}}}))
    (tmp_path / "p.json").write_text(json.dumps(
        {"model": "big-model", "sampling": {"temperature": 0.2}}))
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(config_store, "MODEL_CONFIG_DIR", str(tmp_path))
    from hermes_trader.agents import duel_store, research
    assert duel_store.slot_get("primary", "model") == "big-model"
    assert research.effective_llm_sampling()["temperature"] == 0.2


def test_profile_base_url_beats_env(tmp_path, monkeypatch):
    """The env var names ONE fixed server; a profile must never be silently
    re-pointed at it (wrong model). Profile base_url > env > inline."""
    cfg = tmp_path / ".agent-config.json"
    cfg.write_text(json.dumps({"llm": {"primary_engine": {
        "engine": "systemone", "model_profile": "d2"}}}))
    models = tmp_path / "config-models"
    models.mkdir()
    (models / "d2.json").write_text(json.dumps(
        {"model": "d2", "base_url": "http://decision2-serve:8081"}))
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(config_store, "MODEL_CONFIG_DIR", str(models))
    monkeypatch.setenv("LLM_SYSTEMONE_BASE_URL", "http://env:8081")
    from hermes_trader.agents import decision_voice as dv
    assert dv.primary_engine_config()["base_url"] == "http://decision2-serve:8081"


def test_composite_score_knob_flows_from_profile(tmp_path, monkeypatch):
    """decision2's profile turns composite_score back ON for the seat
    (scoreadd +12.04%); slots/profiles without the key keep it off."""
    cfg = tmp_path / ".agent-config.json"
    cfg.write_text(json.dumps({"llm": {
        "primary_engine": {"engine": "systemone", "model_profile": "d2"},
        "decision_voice": {"model_profile": "nano"}}}))
    models = tmp_path / "config-models"
    models.mkdir()
    (models / "d2.json").write_text(json.dumps(
        {"model": "d2", "base_url": "http://x:1", "composite_score": True}))
    (models / "nano.json").write_text(json.dumps(
        {"model": "nano", "base_url": "http://y:2"}))
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(config_store, "MODEL_CONFIG_DIR", str(models))
    monkeypatch.setenv("LLM_DV_BASE_URL", "http://y:2")
    from hermes_trader.agents import decision_voice as dv
    assert dv.primary_engine_config()["composite_score"] is True
    assert dv._dv_config()["composite_score"] is False
