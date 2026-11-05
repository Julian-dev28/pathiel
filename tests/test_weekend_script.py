"""scripts/weekend.py: the desk-tick agent's one deterministic answer on the
oil futures weekend (operator, 2026-11-05: "weekend flat")."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("weekend_script", ROOT / "scripts" / "weekend.py")
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


def test_inside_the_window_says_flatten_with_the_window_and_what_to_do():
    out = M.answer("xyz:BRENTOIL", ms(2026, 11, 7, 12))
    assert out["flatten"] is True and out["oil"] is True and out["next"] is None
    assert out["window"]["start_utc"] == "2026-11-06 21:00Z" and out["window"]["until_utc"] == "2026-11-08 23:00Z"
    assert out["window"]["reason"].startswith("Weekend flat: no new oil entries until 2026-11-08 23:00Z")
    assert out["what"].startswith("close an open position with close_position")
    assert out["utc"] == "2026-11-07 12:00Z"


def test_a_weekday_inside_24h_shows_the_next_window_and_never_flattens():
    out = M.answer("xyz:CL", ms(2026, 11, 6, 9))
    assert out["flatten"] is False and out["window"] is None
    assert out["next"]["start_utc"] == "2026-11-06 21:00Z" and out["next"]["in_hours"] == 12.0
    far = M.answer("xyz:BRENTOIL", ms(2026, 11, 4, 9))
    assert far["flatten"] is False and far["next"] is None            # more than 24h out
    assert M.answer("xyz:BRENTOIL", ms(2026, 11, 4, 9), warn_hours=72)["next"]["start_utc"] == "2026-11-06 21:00Z"


def test_gold_and_silver_never_flatten_even_on_saturday():
    for coin in ("xyz:GOLD", "xyz:SILVER", "BTC"):
        out = M.answer(coin, ms(2026, 11, 7, 12))
        assert out == {"coin": coin, "oil": False, "flatten": False, "window": None, "next": None,
                       "utc": "2026-11-07 12:00Z"}


def test_the_reopen_minute_is_open_and_the_close_minute_is_closed():
    assert M.answer("xyz:BRENTOIL", ms(2026, 11, 8, 23))["flatten"] is False
    assert M.answer("xyz:BRENTOIL", ms(2026, 11, 8, 22, 59))["flatten"] is True
    assert M.answer("xyz:BRENTOIL", ms(2026, 11, 6, 21))["flatten"] is True
    assert M.answer("xyz:BRENTOIL", ms(2026, 11, 6, 20, 59))["flatten"] is False


def test_main_prints_one_json_line_for_a_given_time(capsys):
    assert M.main(["--coin", "xyz:BRENTOIL", "--at", "2026-11-07T12:00Z"]) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["flatten"] is True and out["window"]["until_utc"] == "2026-11-08 23:00Z"
    assert M.main(["--coin", "xyz:GOLD", "--at", "2026-11-07T12:00Z"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["flatten"] is False


def test_main_defaults_to_now(monkeypatch, capsys):
    monkeypatch.setattr(M.time, "time", lambda: ms(2026, 11, 7, 12) / 1000)
    M.main(["--coin", "xyz:CL"])
    assert json.loads(capsys.readouterr().out.strip())["utc"] == "2026-11-07 12:00Z"
