"""The desk-tick skill and agent carry the sp500 desk's rules (2026-11-19).

The tick is a prompt: what it does on xyz:SP500 is what these two files
say. This pins the lines that keep the operator's hand-placed S&P trade
untouched, the coin, the levels command and the 50x rule, in the project
files and in the generated plugin copies. Text only; no network."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / ".claude" / "agents" / "desk-tick.md"
SKILL = ROOT / ".claude" / "skills" / "desk-tick" / "SKILL.md"
PLUGIN_AGENT = ROOT / "agents" / "desk-tick.md"
PLUGIN_SKILL = ROOT / "skills" / "desk-tick" / "SKILL.md"
REASON = "operator position open on xyz:SP500"


def flat(path: Path) -> str:
    """The file as one line of single-spaced words (the text is hard-wrapped)."""
    return " ".join(path.read_text(encoding="utf-8").split())


@pytest.mark.parametrize("path", [AGENT, SKILL, PLUGIN_AGENT, PLUGIN_SKILL])
def test_the_operator_slot_stops_the_tick_with_a_hold(path):
    text = flat(path)
    assert "`slot` is `operator`" in text
    assert REASON in text
    assert "action=HOLD <reason>" in text and "STOP" in text
    assert "sp500" in text and "xyz:SP500" in text


@pytest.mark.parametrize("path", [AGENT, PLUGIN_AGENT])
def test_the_agent_never_touches_the_operators_trade(path):
    text = flat(path)
    for tool in ("close_position", "cancel_order", "sweep_brackets", "set_stop", "set_target", "set_leverage"):
        assert f"`{tool}`" in text
    assert "Nothing on that coin is the desk's to touch" in text
    assert "no entry or top-up" in text
    assert "Never pass `operator_override` from a tick" in text
    assert "`operator_position: true`" in text and "`operator_order: true`" in text


def test_the_skill_stops_before_it_launches_an_agent():
    text = flat(SKILL)
    hold = text.index("`slot` is `operator`")
    launch = text.index("**Otherwise launch the agent.**")
    assert hold < launch
    assert "No agent, no MCP calls" in text[hold:launch]
    assert "never closes, cancels, sweeps or moves a stop or target" in text[hold:launch]


def test_the_agent_knows_the_coin_the_levels_command_and_the_rule():
    text = flat(AGENT)
    assert "(DESK: oil, metals, sp500, ai or gas)" in text
    assert "sp500: xyz:SP500" in text
    assert "`--coin xyz:SP500`" in text and "`--lev 50`" in text
    assert "1.0 x `atr14_1h` and 0.4% of the mid" in text
    assert "The S&P has no offset" in text
    assert "a stop that does not fit 50x is no trade" in text
    assert "oil 90%, sp500 5%, metals 5%" in text
    assert "the monthly US jobs report blocks too" in text
    assert "the S&P tick does not run the weekend script" in text


def test_the_loops_skill_schedules_the_sp500_tick():
    text = flat(ROOT / ".claude" / "skills" / "desk-loops" / "SKILL.md")
    assert "prompt `/desk-tick sp500`" in text and "`6,21,36,51 * * * *`" in text


def test_the_slot_script_answers_what_the_docs_read():
    """`slot: operator` and the reason string come from code, not from the prompt."""
    from pathiel.agents import ownership
    assert ownership.OPERATOR_POSITION_REASON.format(coin="xyz:SP500") == REASON
    src = (ROOT / "scripts" / "desk_slot.py").read_text(encoding="utf-8")
    assert '"slot": "operator"' in src
