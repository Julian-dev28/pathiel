"""The desk-tick skill and agent carry the hype desk's rules (2026-11-20).

The tick is a prompt: what it does on HYPE is what these two files say.
This pins the lines that keep the operator's hand-placed HYPE trade
untouched, the coin, the levels command, the 10x cap and the trend-gate
veto, in the project files and in the generated plugin copies. Text only;
no network."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / ".claude" / "agents" / "desk-tick.md"
SKILL = ROOT / ".claude" / "skills" / "desk-tick" / "SKILL.md"
PLUGIN_AGENT = ROOT / "agents" / "desk-tick.md"
PLUGIN_SKILL = ROOT / "skills" / "desk-tick" / "SKILL.md"
REASON = "operator position open on xyz:SP500"       # the literal the docs quote (HYPE shares the sentence)


def flat(path: Path) -> str:
    """The file as one line of single-spaced words (the text is hard-wrapped)."""
    return " ".join(path.read_text(encoding="utf-8").split())


@pytest.mark.parametrize("path", [AGENT, SKILL, PLUGIN_AGENT, PLUGIN_SKILL])
def test_the_operator_slot_stops_the_tick_for_hype_too(path):
    text = flat(path)
    assert "`slot` is `operator`" in text
    assert "sp500 and hype desks" in text
    assert "xyz:SP500" in text and "HYPE" in text
    assert "action=HOLD <reason>" in text and "STOP" in text


@pytest.mark.parametrize("path", [AGENT, PLUGIN_AGENT])
def test_the_agent_never_touches_the_operators_hype_trade(path):
    text = flat(path)
    assert "The operator's own HYPE trade (hype desk, since 2026-11-20)" in text
    assert "any position or order the desk did not place is the operator's" in text
    assert "Never pass `operator_override` from a tick" in text


def test_the_skill_stops_before_it_launches_an_agent_for_hype():
    text = flat(SKILL)
    hold = text.index("`slot` is `operator`")
    launch = text.index("**Otherwise launch the agent.**")
    assert hold < launch
    assert "No agent, no MCP calls" in text[hold:launch]
    assert "sp500 and hype desks" in text[hold:launch]


def test_the_agent_knows_the_hype_coin_the_levels_command_and_the_rule():
    text = flat(AGENT)
    assert "(DESK: oil, metals, sp500, hype, ai or gas)" in text
    assert "hype: HYPE" in text
    assert "`--coin HYPE`" in text and "`--lev 10`" in text
    assert "1.0 x `atr14_1h` with NO floor" in text
    assert "10x only, the venue's own max" in text
    assert "HYPE has no offset either" in text
    assert "a stop that does not fit is no trade" in text
    assert "HYPE also gates new entries on the 120h trend" in text
    assert "no new entry against the sign of the 120-hour return" in text


def test_the_agent_knows_hype_never_flattens_for_the_weekend_and_has_no_jobs_block():
    text = flat(AGENT)
    assert "HYPE trades 24/7 with" in text and "never ran" in text
    assert "HYPE has no macro-kind mapping of its own" in text
    assert "FOMC and CPI still block it like every other desk" in text


def test_the_loops_skill_schedules_the_hype_tick():
    text = flat(ROOT / ".claude" / "skills" / "desk-loops" / "SKILL.md")
    assert "prompt `/desk-tick hype`" in text


def test_the_slot_script_answers_what_the_docs_read_for_hype():
    """`slot: operator` and the HYPE reason string come from code, not from
    the prompt."""
    from pathiel.agents import ownership
    assert ownership.OPERATOR_POSITION_REASON.format(coin="HYPE") == "operator position open on HYPE"
    assert ownership.is_guarded("HYPE")
    src = (ROOT / "scripts" / "desk_slot.py").read_text(encoding="utf-8")
    assert '"slot": "operator"' in src


def test_the_plugin_copies_are_not_stale():
    """scripts/build_plugin_skill.py --check would pass: a committed
    drift here means someone edited the .claude/ source without
    rerunning the generator."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_plugin_skill_hype", ROOT / "scripts" / "build_plugin_skill.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    rendered = dict(gen.render_all())
    for out, text in rendered.items():
        assert out.read_text(encoding="utf-8") == text, out
