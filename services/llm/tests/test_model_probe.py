"""Unit tests for services/llm/model_probe.py.

No real subprocess ever runs here - run_cli is monkeypatched at the
model_probe module boundary so these tests are free and instant, same as
every other gate test (SWARM_RULES.md section 4: "Gate tests never call an
LLM; use fakes.").
"""
from __future__ import annotations

import json
import time

from services.llm import config, model_probe
from services.llm.errors import LlmError


def _ok_envelope(result="OK"):
    return {"result": result, "is_error": False}


def test_env_override_short_circuits_probing(monkeypatch):
    monkeypatch.setenv("PATHIEL_LLM_MODEL", "claude-opus-5-5")

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("probing must not run when PATHIEL_LLM_MODEL is set")

    monkeypatch.setattr(model_probe, "run_cli", _should_not_be_called)
    assert model_probe.probe_default_model() == "claude-opus-5-5"


def test_first_candidate_that_answers_wins(monkeypatch):
    calls = []

    def _fake_run_cli(args, user_text, timeout_s, env=None):
        model = args[args.index("--model") + 1]
        calls.append(model)
        return _ok_envelope()

    monkeypatch.setattr(model_probe, "run_cli", _fake_run_cli)
    result = model_probe.probe_default_model()
    assert result == config.MODEL_PROBE_CANDIDATES[0]
    assert calls == [config.MODEL_PROBE_CANDIDATES[0]]  # stopped after the first success


def test_falls_through_to_second_candidate_if_first_fails(monkeypatch):
    def _fake_run_cli(args, user_text, timeout_s, env=None):
        model = args[args.index("--model") + 1]
        if model == config.MODEL_PROBE_CANDIDATES[0]:
            raise LlmError("down for maintenance")
        return _ok_envelope()

    monkeypatch.setattr(model_probe, "run_cli", _fake_run_cli)
    result = model_probe.probe_default_model()
    assert result == config.MODEL_PROBE_CANDIDATES[1]


def test_empty_result_counts_as_failure(monkeypatch):
    def _fake_run_cli(args, user_text, timeout_s, env=None):
        model = args[args.index("--model") + 1]
        if model == config.MODEL_PROBE_CANDIDATES[0]:
            return _ok_envelope(result="")
        return _ok_envelope()

    monkeypatch.setattr(model_probe, "run_cli", _fake_run_cli)
    assert model_probe.probe_default_model() == config.MODEL_PROBE_CANDIDATES[1]


def test_all_candidates_failing_returns_empty_string_and_caches_it(monkeypatch):
    def _always_fails(args, user_text, timeout_s, env=None):
        raise LlmError("nothing is up")

    monkeypatch.setattr(model_probe, "run_cli", _always_fails)
    assert model_probe.probe_default_model() == ""

    cached = json.loads(open(config.model_probe_cache_path()).read())
    assert cached["model"] == ""


def test_result_is_cached_to_disk_and_reused_without_reprobing(monkeypatch):
    calls = {"n": 0}

    def _fake_run_cli(args, user_text, timeout_s, env=None):
        calls["n"] += 1
        return _ok_envelope()

    monkeypatch.setattr(model_probe, "run_cli", _fake_run_cli)
    first = model_probe.probe_default_model()
    assert calls["n"] == 1

    # Fresh in-process memo cleared, but the on-disk cache should still answer.
    model_probe._reset_memo_for_tests()
    second = model_probe.probe_default_model()
    assert second == first
    assert calls["n"] == 1  # no new ping fired


def test_stale_cache_triggers_a_fresh_probe(monkeypatch, tmp_path):
    cache_path = config.model_probe_cache_path()
    import os
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    stale_age = config.MODEL_PROBE_TTL_S + 3600
    with open(cache_path, "w") as fh:
        json.dump({"model": config.MODEL_PROBE_CANDIDATES[0], "probed_at": time.time() - stale_age}, fh)

    calls = {"n": 0}

    def _fake_run_cli(args, user_text, timeout_s, env=None):
        calls["n"] += 1
        return _ok_envelope()

    monkeypatch.setattr(model_probe, "run_cli", _fake_run_cli)
    model_probe.probe_default_model()
    assert calls["n"] == 1  # the stale cache was not trusted


def test_corrupt_cache_file_is_ignored_not_fatal(monkeypatch):
    cache_path = config.model_probe_cache_path()
    import os
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as fh:
        fh.write("{not json")

    monkeypatch.setattr(model_probe, "run_cli", lambda *a, **k: _ok_envelope())
    # Must not raise despite the unreadable cache.
    assert model_probe.probe_default_model() == config.MODEL_PROBE_CANDIDATES[0]


def test_force_reprobe_ignores_memo_and_cache(monkeypatch):
    calls = {"n": 0}

    def _fake_run_cli(args, user_text, timeout_s, env=None):
        calls["n"] += 1
        return _ok_envelope()

    monkeypatch.setattr(model_probe, "run_cli", _fake_run_cli)
    model_probe.probe_default_model()
    assert calls["n"] == 1
    model_probe.probe_default_model(force=True)
    assert calls["n"] == 2


def _write_cache(model, age_s):
    import os
    path = config.model_probe_cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"model": model, "probed_at": time.time() - age_s}, fh)


def test_degraded_pick_is_reprobed_after_the_short_ttl(monkeypatch):
    """A fallback model cached 20 minutes ago is stale (degraded TTL is 15
    min): the best model gets another chance instead of a 24h downgrade."""
    _write_cache(config.MODEL_PROBE_CANDIDATES[1], config.MODEL_PROBE_DEGRADED_TTL_S + 300)
    calls = {"n": 0}

    def _fake_run_cli(args, user_text, timeout_s, env=None):
        calls["n"] += 1
        return _ok_envelope()

    monkeypatch.setattr(model_probe, "run_cli", _fake_run_cli)
    assert model_probe.probe_default_model() == config.MODEL_PROBE_CANDIDATES[0]
    assert calls["n"] == 1


def test_best_pick_keeps_the_long_ttl(monkeypatch):
    _write_cache(config.MODEL_PROBE_CANDIDATES[0], config.MODEL_PROBE_DEGRADED_TTL_S + 300)

    def _no_ping(*a, **k):
        raise AssertionError("must not re-probe a fresh best-model pick")

    monkeypatch.setattr(model_probe, "run_cli", _no_ping)
    assert model_probe.probe_default_model() == config.MODEL_PROBE_CANDIDATES[0]


def test_in_process_memo_expires_for_a_degraded_pick(monkeypatch):
    model_probe._memo = {"model": config.MODEL_PROBE_CANDIDATES[1],
                         "probed_at": time.time() - config.MODEL_PROBE_DEGRADED_TTL_S - 60}
    monkeypatch.setattr(model_probe, "run_cli", lambda *a, **k: _ok_envelope())
    assert model_probe.probe_default_model() == config.MODEL_PROBE_CANDIDATES[0]
