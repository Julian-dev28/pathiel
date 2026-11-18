"""scripts/resting_blackout.py: the desk-tick agent's one deterministic
answer on whether a resting entry must be cancelled for a blackout
(operator, 2026-11-18: rest the live rule's trigger instead of waiting for
a tick, so a resting limit must not sit through a window it would have been
refused entry into)."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("resting_blackout", ROOT / "scripts" / "resting_blackout.py")
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


# EIA window on 2026-10-28 (summer, no overlapping scheduled event):
# report 14:30Z, window [12:30Z, 16:30Z).
EIA_START = ms(2026, 10, 28, 12, 30)


def test_not_blocked_well_before_the_window():
    out = M.answer("xyz:BRENTOIL", "long", EIA_START - 20 * 60_000)
    assert out == {"coin": "xyz:BRENTOIL", "side": "long", "blocked": False, "reason": None,
                   "utc": out["utc"], "horizon_min": 15.0}


def test_blocked_inside_the_tick_cadence_before_the_window_opens():
    out = M.answer("xyz:BRENTOIL", "long", EIA_START - 10 * 60_000)
    assert out["blocked"] is True
    assert out["reason"].startswith("EIA blackout") and out["until_utc"] == "2026-10-28 16:30Z"
    assert out["event"] == "EIA weekly petroleum report"


def test_blocked_while_the_window_is_active():
    out = M.answer("xyz:BRENTOIL", "long", EIA_START + 60 * 60_000)
    assert out["blocked"] is True


def test_a_custom_horizon_is_honored():
    out = M.answer("xyz:BRENTOIL", "long", EIA_START - 20 * 60_000, horizon_ms=25 * 60_000)
    assert out["blocked"] is True and out["horizon_min"] == 25.0
    default = M.answer("xyz:BRENTOIL", "long", EIA_START - 20 * 60_000)
    assert default["blocked"] is False and default["horizon_min"] == 15.0


def test_non_oil_coin_with_no_event_is_never_blocked():
    out = M.answer("xyz:GOLD", "long", EIA_START - 5 * 60_000)
    assert out["blocked"] is False


def test_main_prints_one_json_line_for_a_given_time(capsys):
    assert M.main(["--coin", "xyz:BRENTOIL", "--side", "long", "--at", "2026-10-28T12:20Z"]) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["blocked"] is True and out["coin"] == "xyz:BRENTOIL" and out["side"] == "long"

    assert M.main(["--coin", "xyz:BRENTOIL", "--side", "long", "--at", "2026-10-28T09:00Z"]) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["blocked"] is False


def test_main_accepts_a_custom_horizon_in_minutes(capsys):
    assert M.main(["--coin", "xyz:BRENTOIL", "--side", "long", "--at", "2026-10-28T12:10Z",
                  "--horizon-min", "30"]) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["blocked"] is True and out["horizon_min"] == 30.0


def test_main_defaults_to_now(monkeypatch, capsys):
    monkeypatch.setattr(M.time, "time", lambda: EIA_START / 1000)
    M.main(["--coin", "xyz:BRENTOIL", "--side", "long"])
    out = json.loads(capsys.readouterr().out.strip())
    assert out["blocked"] is True and out["utc"] == "2026-10-28 12:30Z"


def test_side_is_optional_and_defaults_to_none():
    out = M.answer("xyz:BRENTOIL", None, EIA_START + 60 * 60_000)
    assert out["side"] is None and out["blocked"] is True
