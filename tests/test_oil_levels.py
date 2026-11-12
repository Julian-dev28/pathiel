"""services/strategies/levels.py: the pure candle math, on synthetic candles
(no network), plus scripts/oil_levels.py's thin CLI wrapper around it."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from services.strategies import levels as L

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("oil_levels", ROOT / "scripts" / "oil_levels.py")
CLI = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CLI)

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
    assert CLI.main(["--check", "107.20", "106.70", "108.45"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["side"] == "long" and out["r_multiple"] == 2.5
    CLI.main(["--check", "107.61", "106.70", "108.45"])
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_check_with_leverage_prints_the_20x_line(capsys):
    CLI.main(["--check", "107.16", "106.70", "108.45", "--lev", "20"])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["verdict"] == "20x OK" and out["leverage"]["liq_px"] == pytest.approx(104.41, abs=0.03)
    CLI.main(["--check", "107", "101.65", "115", "--lev", "20"])
    out = json.loads(capsys.readouterr().out)
    assert not out["ok"] and out["verdict"] == "no 20x: max 8x for this stop"


# ── gold (the metals desk's primary since 2026-11-05) ──────────────────────

def _gold_levels(mid, low, high, atr=12.0):
    return {"mid": mid, "range_24h": {"low": low, "high": high}, "atr14_1h": atr}


def test_next_on_gold_uses_the_0_8pct_floor_and_trades_at_the_24h_low():
    from services.strategies.next_trade import stop_buffer_for
    out = CLI.next_order("xyz:GOLD", "long", _gold_levels(4065.0, 4060.0, 4180.0), 4100.0, 20.0, 25.0)
    buf = stop_buffer_for("xyz:GOLD", 12.0, 4065.0)
    assert buf == pytest.approx(32.52) and buf > 1.3 * 12.0           # the floor binds on a normal gold tick
    assert out["ok"] is True and out["coin"] == "xyz:GOLD" and out["leverage"] == 20.0
    assert out["stop_buffer"] == pytest.approx(buf) and out["stop_px"] == pytest.approx(4060.0 - buf)
    assert out["place_bracket"] == {"coin": "xyz:GOLD", "stop_px": out["stop_px"], "tp_px": 4180.0}
    assert out["tp_px"] == pytest.approx(4180.0 * 0.995)             # the offset target the R gate judged


def test_next_on_gold_fits_15x_on_a_wide_range_and_refuses_mid_range():
    wide = CLI.next_order("xyz:GOLD", "long", _gold_levels(4010.0, 3960.0, 4200.0), 4100.0, 20.0, 25.0)
    assert wide["ok"] is True and wide["leverage"] == 15.0
    mid_range = CLI.next_order("xyz:GOLD", "long", _gold_levels(4120.0, 4060.0, 4180.0), 4150.0, 20.0, 25.0)
    assert mid_range["ok"] is False and any("needs 2R" in b for b in mid_range["blockers"])
    assert "place_bracket" not in mid_range
    no_atr = CLI.next_order("xyz:GOLD", "long", _gold_levels(4065.0, 4060.0, 4180.0, atr=None), 4100.0, 20.0, 25.0)
    assert no_atr == {"ok": False, "reason": "no ATR for the stop buffer", "coin": "xyz:GOLD"}


# ── network (contracts/runtime, 2026-11-12) ────────────────────────────────

def _load_cli(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "oil_levels.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _posted_url(mod, monkeypatch) -> str:
    """The URL _post hands curl, with subprocess.run faked (no network)."""
    seen = []

    class _Done:
        stdout = "[]"

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return _Done()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod._post({"type": "allMids"}) == []
    assert len(seen) == 1 and seen[0][0] == "curl"
    return [a for a in seen[0] if a.startswith("https://")][0]


@pytest.mark.parametrize("env, want", [
    ({}, "https://api.hyperliquid.xyz/info"),
    ({"PATHIEL_NETWORK": "testnet"}, "https://api.hyperliquid-testnet.xyz/info"),
    ({"PATHIEL_INSTALL": "public"}, "https://api.hyperliquid-testnet.xyz/info"),
    ({"PATHIEL_INSTALL": "public", "PATHIEL_NETWORK": "mainnet"}, "https://api.hyperliquid.xyz/info"),
])
def test_info_url_follows_the_runtime_network(monkeypatch, env, want):
    for key in ("PATHIEL_INSTALL", "PATHIEL_NETWORK"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    mod = _load_cli("oil_levels_network_probe")
    assert mod.INFO_URL == want
    assert _posted_url(mod, monkeypatch) == want
