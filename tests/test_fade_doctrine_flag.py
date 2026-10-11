"""fade_counter_trend doctrine flag — coded-but-disabled promote knob.

`fade_counter_trend.enabled` in .agent-config.json (hot-read per
build_system_prompt call) arms a SECOND sanctioned counter-trend exception
(a fired fade signal is a counter-trend CANDIDATE) in rule 6 + the
anti-dip anti-pattern. Code default = key absent = FALSE = the disarmed
prompt contains NONE of the armed text, and the armed prompt differs from
the disarmed one by EXACTLY the two addenda (relative invariant — robust
to unrelated prompt edits and PR merge order, unlike a static golden).
Fail-open: a config-read error keeps the historical doctrine.

This is the promote-time half of WATCHLIST §B.41's PROMPT DOCTRINE
DECISION: when the MFI/rsiDivergence shadow cohort prices positive,
arming the doctrine is a same-inode config flip, not a rebuild.

Config-file pattern per the ops skill: write to config_store.CONFIG_PATH
directly (the env var is frozen at import), back up/restore contents.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes_trader.agents import config_store  # noqa: E402
from hermes_trader.agents.system_prompt import build_system_prompt  # noqa: E402

_ARGS = ("LIVE", 0.55, 10)

# The exact armed-only text (single source of truth for the invariant).
_ARMED_RULE6 = (
    "   (SECOND sanctioned exception: a FIRED fade signal — rsiDivergence, mfiExtreme,"
    "   bandSnapback, or a reversal candle — is a counter-trend CANDIDATE, not an automatic"
    "   override. When one fires, you MAY take the counter-trend side IF the exhaustion"
    "   evidence is strong (volume-confirmed washout/euphoria, divergence, structure"
    "   holding at the extreme). Judge it on merits like any setup; without a fired fade"
    "   signal the no-dip rule stands unchanged.)"
)
_ARMED_ANTIDIP = (
    "  The ONE exception is a fired fade signal with strong exhaustion evidence"
    "  (see rule 6) — an unconfirmed dip is still a SHORT."
)


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


def test_absent_key_renders_no_armed_text(cfg_file):
    """Block absent (the merge no-op): none of the armed addenda render."""
    _write_cfg("{}")
    p = build_system_prompt(*_ARGS)
    assert "SECOND sanctioned exception" not in p
    assert _ARMED_ANTIDIP not in p


def test_enabled_false_renders_no_armed_text(cfg_file):
    _write_cfg('{"fade_counter_trend": {"enabled": false}}')
    p = build_system_prompt(*_ARGS)
    assert "SECOND sanctioned exception" not in p
    assert _ARMED_ANTIDIP not in p


def test_enabled_arms_second_exception(cfg_file):
    _write_cfg('{"fade_counter_trend": {"enabled": true}}')
    p = build_system_prompt(*_ARGS)
    assert _ARMED_RULE6 in p
    assert _ARMED_ANTIDIP in p
    # the original whale-only sentence still renders (addendum, not replace)
    assert "the ONE sanctioned" in p
    # and the no-dip prohibition itself still renders
    assert "Never LONG when BOTH 4h AND 1d are bearish" in p


def test_armed_delta_is_exactly_the_two_addenda(cfg_file):
    """The flag's ENTIRE effect is the two addenda: removing them from the
    armed prompt reproduces the disarmed prompt byte-for-byte. (Relative
    invariant — no static golden to rot on unrelated prompt edits.)"""
    _write_cfg('{"fade_counter_trend": {"enabled": true}}')
    armed = build_system_prompt(*_ARGS)
    _write_cfg("{}")
    disarmed = build_system_prompt(*_ARGS)
    stripped = armed.replace("\n" + _ARMED_RULE6, "").replace("\n" + _ARMED_ANTIDIP, "")
    assert stripped == disarmed


def test_garbage_config_fails_open(cfg_file):
    """A malformed config file must keep the historical doctrine, never raise
    into the prompt-build path."""
    _write_cfg("{not json")
    p = build_system_prompt(*_ARGS)
    assert "SECOND sanctioned exception" not in p


def test_hot_read_per_call(cfg_file):
    """No module-level cache: rewriting the config between calls flips the
    rendered doctrine."""
    _write_cfg("{}")
    assert "SECOND sanctioned exception" not in build_system_prompt(*_ARGS)
    _write_cfg('{"fade_counter_trend": {"enabled": true}}')
    assert "SECOND sanctioned exception" in build_system_prompt(*_ARGS)
    _write_cfg("{}")
    assert "SECOND sanctioned exception" not in build_system_prompt(*_ARGS)
