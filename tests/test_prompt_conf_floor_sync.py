"""Prompt/gate confidence-floor sync (owner fix 2026-10-16).

The CONFIDENCE CALIBRATION block used to hard-code 0.70 while the runner
gate ran runner_entry_gate.min_confidence 0.65 — the model was told to
"meet 0.70" for a gate that actually admits at 0.65. The prompt now
hot-reads the configured floor per build_system_prompt call, so the two
can never drift again. Fail-open: a config-read error renders the
historical 0.70.

Config-file pattern per the ops skill (same as test_fade_doctrine_flag):
write config_store.CONFIG_PATH directly, back up/restore.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes_trader.agents import config_store  # noqa: E402
from hermes_trader.agents.system_prompt import build_system_prompt  # noqa: E402

_ARGS = ("LIVE", 0.55, 10)


@pytest.fixture
def cfg_file():
    """Isolate the config file the hot-read opens; restore after."""
    p = Path(config_store.CONFIG_PATH)
    existed = p.exists()
    backup = p.read_text() if existed else None
    yield p
    if existed:
        p.write_text(backup)
    elif p.exists():
        p.unlink()


def _write_cfg(payload: str):
    Path(config_store.CONFIG_PATH).write_text(payload)


def test_configured_floor_renders_in_prompt(cfg_file):
    """Live floor 0.65: every calibration site shows 0.65, no stale 0.70."""
    _write_cfg('{"runner_entry_gate": {"min_confidence": 0.65}}')
    p = build_system_prompt(*_ARGS)
    assert "requires confidence >= 0.65" in p
    assert "- 0.65–0.84:" in p
    assert "- 0.45–0.64:" in p
    assert "Take it with confidence 0.65+" in p
    assert "output PASS with confidence < 0.65" in p
    assert "0.70–0.84" not in p
    assert "requires 0.70" not in p


def test_absent_key_falls_back_to_historical_070(cfg_file):
    """Key absent = historical prompt numbers (byte-compatible default)."""
    _write_cfg("{}")
    p = build_system_prompt(*_ARGS)
    assert "requires confidence >= 0.70" in p
    assert "- 0.70–0.84:" in p
    assert "- 0.45–0.69:" in p


def test_malformed_config_fails_open(cfg_file):
    """Unreadable config must not crash prompt build — historical 0.70."""
    _write_cfg("{not json")
    p = build_system_prompt(*_ARGS)
    assert "requires confidence >= 0.70" in p
