"""One-weekend hold override (operator, 2026-11-06: "hold the long for this
weekend if opened, close if up 65%")."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.strategies import weekend as WK

ROOT = Path(__file__).resolve().parent.parent
OV = {"weekend_override": {"window_start_utc": "2026-11-06 21:00Z", "hold": True, "take_profit_roe": 0.65}}


def ms(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


def _script():
    spec = importlib.util.spec_from_file_location("weekend_script_ov", ROOT / "scripts" / "weekend.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_override_only_matches_its_own_window():
    start = WK.oil_weekend("xyz:BRENTOIL", ms(2026, 11, 7, 12))["start_ms"]
    assert WK.hold_override(start, OV)["take_profit_roe"] == 0.65
    nxt = WK.oil_weekend("xyz:BRENTOIL", ms(2026, 11, 14, 12))["start_ms"]
    assert WK.hold_override(nxt, OV) is None                      # next weekend is flat again
    assert WK.hold_override(start, {}) is None
    assert WK.hold_override(start, {"weekend_override": {**OV["weekend_override"], "hold": False}}) is None


def test_roe_take_profit_price():
    assert WK.roe_take_profit(97.0, 15, 0.65) == pytest.approx(97.0 * (1 + 0.65 / 15))
    assert WK.roe_take_profit(97.0, 15, 0.65, "short") == pytest.approx(97.0 * (1 - 0.65 / 15))


def test_script_holds_instead_of_flattening_and_prices_the_target():
    S = _script()
    out = S.answer("xyz:BRENTOIL", ms(2026, 11, 7, 12), config=OV, entry_px=97.0, leverage=15)
    assert out["flatten"] is False and out["hold_override"]["hold"] is True
    assert out["take_profit_px"] == pytest.approx(101.203, abs=1e-3) and "set_target" in out["what"]
    plain = S.answer("xyz:BRENTOIL", ms(2026, 11, 7, 12), config={})
    assert plain["flatten"] is True and "hold_override" not in plain
    nxt = S.answer("xyz:BRENTOIL", ms(2026, 11, 14, 12), config=OV)
    assert nxt["flatten"] is True                                  # one weekend only


def test_watcher_does_not_fire_weekend_flat_under_the_override(monkeypatch):
    spec = importlib.util.spec_from_file_location("tw_ov", ROOT / "scripts" / "trigger_watch.py")
    W = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(W)
    import pathiel.agents.config_store as cs
    win = WK.oil_weekend("xyz:BRENTOIL", ms(2026, 11, 6, 21, 1))
    monkeypatch.setattr(cs, "read_agent_config", lambda: OV)
    assert W.weekend_fire("oil", "xyz:BRENTOIL", win, {}, 0.0, lambda: "holds long") is None
    monkeypatch.setattr(cs, "read_agent_config", lambda: {})
    assert W.weekend_fire("oil", "xyz:BRENTOIL", win, {}, 0.0, lambda: "holds long")["reason"] == "weekend_flat"


def test_agent_text_has_the_override_rule():
    for rel in (".claude/agents/desk-tick.md", "agents/desk-tick.md"):
        text = (ROOT / rel).read_text()
        assert "Operator hold override" in text and "set_target" in text, rel


def test_a_tick_older_than_20_minutes_is_presumed_dead():
    """2026-11-06 and 2026-11-09: a tick cut off by sleep or the network left
    every later tick skipping 'previous tick still running' for hours."""
    for rel in (".claude/skills/desk-tick/SKILL.md", "skills/desk-tick/SKILL.md"):
        text = (ROOT / rel).read_text()
        assert "Dead-tick rule" in text and "under 20 minutes" in text and "presumed dead" in text, rel
    loops = (ROOT / ".claude/skills/desk-loops/SKILL.md").read_text()
    assert "older than 20 minutes is\npresumed dead" in loops
