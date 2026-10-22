"""scripts/oil_levels.py: the pure part, on synthetic candles. No network."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("oil_levels", ROOT / "scripts" / "oil_levels.py")
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)

H = L.HOUR_MS
NOW = 100 * H   # an exact hour boundary


def candle(i: int, o: float, h: float, lo: float, c: float, span: int = H) -> dict:
    t = i * span
    return {"t": t, "T": t + span, "o": o, "h": h, "l": lo, "c": c, "v": 1}


def hourly(n: int, base: float = 100.0) -> list:
    # n candles ending at NOW; each hour drifts up 0.1 with a 1.0 range
    start = 100 - n
    return [candle(start + i, base + i * 0.1, base + i * 0.1 + 0.5, base + i * 0.1 - 0.5, base + i * 0.1 + 0.1)
            for i in range(n)]


def test_open_candle_is_excluded():
    c = hourly(3) + [candle(100, 110, 150, 50, 120)]      # the open hour, wild wicks
    closed = L.closed_only(c, NOW)
    assert len(closed) == 3 and all(x["T"] <= NOW for x in closed)
    s = L.summarize(c, [], 100.0, NOW)
    assert s["range_8h"]["high"] < 150 and s["range_8h"]["low"] > 50


def test_ranges_cover_the_right_windows():
    c = hourly(48)
    s = L.summarize(c, [], 104.0, NOW)
    assert s["n_closed_1h"] == 48
    assert s["range_48h"]["low"] == pytest.approx(99.5)
    assert s["range_24h"]["low"] == pytest.approx(100 + 24 * 0.1 - 0.5)
    assert s["range_8h"]["high"] == pytest.approx(100 + 47 * 0.1 + 0.5)
    assert s["last_closed_1h"]["close"] == pytest.approx(100 + 47 * 0.1 + 0.1)
    assert 0 <= s["mid_pos_in_24h"] <= 1


def test_atr_needs_fifteen_candles_and_uses_true_range():
    assert L.atr(hourly(14)) is None
    flat = [candle(i, 100, 101, 99, 100) for i in range(15)]
    assert L.atr(flat) == pytest.approx(2.0)                 # plain high-low
    gap = flat[:-1] + [candle(14, 105, 106, 104, 105)]       # gaps up: TR = 106 - 100
    assert L.atr(gap) == pytest.approx((13 * 2.0 + 6.0) / 14)


def test_atr_pct_is_relative_to_mid():
    s = L.summarize(hourly(20), [], 100.0, NOW)
    assert s["atr14_pct"] == pytest.approx(s["atr14_1h"] / 100.0 * 100, rel=1e-6)


def test_fifteen_minute_micro_range_uses_last_four_closed():
    q = H // 4
    c15 = [candle(i, 100, 100 + i, 100 - i, 100, span=q) for i in range(396, 401)]  # last one open
    s = L.summarize([], c15, 100.0, NOW)
    assert s["range_15m_last4"] == {"high": 100 + 399, "low": 100 - 399}


def test_summarize_with_no_candles_does_not_crash():
    s = L.summarize([], [], 100.0, NOW)
    assert s["last_closed_1h"] is None and s["atr14_1h"] is None and "mid_pos_in_24h" not in s


def test_check_passes_through_the_bracket_r_gate(capsys):
    assert L.main(["--check", "107.20", "106.70", "108.45"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["side"] == "long" and out["r_multiple"] == 2.5
    L.main(["--check", "107.61", "106.70", "108.45"])
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_check_with_leverage_prints_the_20x_line(capsys):
    L.main(["--check", "107.16", "106.70", "108.45", "--lev", "20"])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["verdict"] == "20x OK" and out["leverage"]["liq_px"] == pytest.approx(104.41, abs=0.03)
    L.main(["--check", "107", "101.65", "115", "--lev", "20"])
    out = json.loads(capsys.readouterr().out)
    assert not out["ok"] and out["verdict"] == "no 20x: max 8x for this stop"
