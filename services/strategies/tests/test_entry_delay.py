"""Gate tests for the entry delay (2026-11-13): `next_trade.entry_delay`
(pure), `entry_delay_for` (the config switch), `thesis_age_minutes`, the
delay inside `next_trade`, and the live callers' plumbing (the API's next
line, the trigger watcher's evaluate, oil_levels --next).

The rule is built and OFF: `ENTRY_DELAY_MIN` is None and no config sets
`oil_entry_delay_min`, so every existing next_trade answer is unchanged.
TRADE_ATTRIBUTION_2026-11-13.md says why it stays off (one afternoon
carries the edge).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pathiel.agents import config_store
from services.strategies import next_trade as NT
from services.strategies.backtest.desk_tick_sim import Params, live_params
from services.strategies.next_trade import entry_delay, entry_delay_for, next_trade, thesis_age_minutes

KW = dict(leverage=20, coin_max_leverage=20, stop_buffer=0.75)


# ── off by default, everywhere ─────────────────────────────────────────────

def test_the_rule_is_off_everywhere_by_default():
    assert NT.ENTRY_DELAY_MIN is None
    assert config_store.DEFAULT_CONFIG["oil_entry_delay_min"] is None
    assert entry_delay_for("xyz:BRENTOIL", {}) is None
    assert entry_delay_for("xyz:BRENTOIL", {"oil_entry_delay_min": None}) is None
    assert Params().entry_delay_min is None and live_params().entry_delay_min is None


def test_a_default_next_trade_answer_is_unchanged_by_the_delay_plumbing():
    base = next_trade("long", 100.40, 100.00, 106.00, thesis_entry=101.0, **KW)
    with_age = next_trade("long", 100.40, 100.00, 106.00, thesis_entry=101.0, thesis_age_min=3.0, **KW)
    assert base["ok"] is True and with_age == base and "entry_delay" not in base


# ── the pure rule ──────────────────────────────────────────────────────────

def test_entry_delay_blocks_a_young_thesis_and_frees_an_old_one():
    young = entry_delay(37.0, 120.0)
    assert young["blocked"] is True and young["wait_min"] == 83.0
    assert young["reason"] == "thesis 37 min old: no entry in the first 120 min of a call (83 min to go)"
    assert entry_delay(120.0, 120.0)["blocked"] is False
    assert entry_delay(500.0, 120.0)["blocked"] is False and entry_delay(500.0, 120.0)["wait_min"] == 0.0


def test_entry_delay_off_or_unknown_age_blocks_nothing():
    assert entry_delay(1.0, None)["blocked"] is False
    assert entry_delay(1.0, 0.0)["blocked"] is False
    assert entry_delay(1.0, -5.0)["blocked"] is False
    unknown = entry_delay(None, 120.0)
    assert unknown["blocked"] is False and "unknown" in unknown["reason"]


def test_entry_delay_for_reads_the_config_for_oil_coins_only():
    cfg = {"oil_entry_delay_min": 120}
    assert entry_delay_for("xyz:BRENTOIL", cfg) == 120.0
    assert entry_delay_for("xyz:CL", cfg) == 120.0
    assert entry_delay_for("xyz:GOLD", cfg) is None
    assert entry_delay_for("xyz:BRENTOIL", {"oil_entry_delay_min": 0}) is None       # 0 = off
    assert entry_delay_for("xyz:BRENTOIL", {"oil_entry_delay_min": "120"}) == 120.0
    assert entry_delay_for("xyz:BRENTOIL", {"oil_entry_delay_min": "junk"}) is None


def test_entry_delay_for_reads_the_live_config_file_when_none_is_passed(monkeypatch, tmp_path):
    path = tmp_path / ".agent-config.json"
    path.write_text('{"oil_entry_delay_min": 90}')
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(path))
    assert entry_delay_for("xyz:BRENTOIL") == 90.0
    path.write_text("{}")
    assert entry_delay_for("xyz:BRENTOIL") is None


def test_thesis_age_minutes_reads_datetimes_and_iso_strings():
    now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    assert thesis_age_minutes(now - timedelta(minutes=37), now_ms) == 37.0
    assert thesis_age_minutes("2026-11-13T11:23:00Z", now_ms) == 37.0
    assert thesis_age_minutes("2026-11-13T11:23:00+00:00", now_ms) == 37.0
    assert thesis_age_minutes(datetime(2026, 11, 13, 11, 23), now_ms) == 37.0      # naive = UTC
    assert thesis_age_minutes(now + timedelta(minutes=5), now_ms) == 0.0          # never negative
    assert thesis_age_minutes(None) is None and thesis_age_minutes("not a date") is None


# ── inside next_trade ──────────────────────────────────────────────────────

def test_a_young_thesis_holds_a_trade_the_rule_would_otherwise_take():
    out = next_trade("long", 100.40, 100.00, 106.00, thesis_entry=101.0, thesis_age_min=30.0,
                     entry_delay_min=120.0, **KW)
    assert out["ok"] is False
    assert out["blockers"][0] == "thesis 30 min old: no entry in the first 120 min of a call (90 min to go)"
    assert out["verdict"] == "waits: thesis 30 min old: no entry in the first 120 min of a call (90 min to go)"
    assert out["entry_delay"]["blocked"] is True and out["entry_delay"]["wait_min"] == 90.0
    # the levels are still the ones the tick will use once the delay passes
    assert out["stop_px"] == pytest.approx(99.25) and out["entry_px"] == pytest.approx(100.40)


def test_the_delay_stacks_on_the_other_blockers_and_keeps_their_verdict():
    out = next_trade("long", 104.0, 100.00, 106.00, thesis_entry=101.0, thesis_age_min=30.0,
                     entry_delay_min=120.0, **KW)
    assert out["ok"] is False and len(out["blockers"]) >= 2
    assert out["blockers"][0].startswith("thesis 30 min old") and "never chase" in " ".join(out["blockers"][1:])
    assert out["verdict"].startswith("waits: thesis 30 min old") and "; then waits: buy at" in out["verdict"]


def test_an_old_thesis_trades_and_the_answer_carries_the_delay_record():
    out = next_trade("long", 100.40, 100.00, 106.00, thesis_entry=101.0, thesis_age_min=180.0,
                     entry_delay_min=120.0, **KW)
    assert out["ok"] is True and out["entry_delay"]["blocked"] is False
    assert out["verdict"] == "buys now"


def test_an_unknown_thesis_age_never_blocks_even_with_the_delay_on():
    out = next_trade("long", 100.40, 100.00, 106.00, thesis_entry=101.0, thesis_age_min=None,
                     entry_delay_min=120.0, **KW)
    assert out["ok"] is True


def test_the_sim_and_the_live_rule_agree_on_the_boundary():
    """desk_tick_sim.entry_filter refuses while t - call_start < delay; the
    live rule while age < delay. Equal age trades in both."""
    from services.strategies.backtest.desk_tick_sim import Direction, entry_filter
    p = Params(entry_delay_min=120.0)
    d = Direction(side="long", regime_id="0")
    assert entry_filter(d, 119 * 60_000, None, None, p) == "delay"
    assert entry_filter(d, 120 * 60_000, None, None, p) is None
    assert entry_delay(119.0, 120.0)["blocked"] is True and entry_delay(120.0, 120.0)["blocked"] is False


# ── the live callers' plumbing ─────────────────────────────────────────────

def _script(name: str):
    """Import scripts/<name>.py by path (the scripts dir is not a package)."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[3] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_attr_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_trigger_watch_evaluate_passes_the_delay_through():
    tw = _script("trigger_watch")
    lv = {"range_24h": {"low": 100.0, "high": 106.0}, "atr14_1h": 0.5, "mid": 100.4}
    free = tw.evaluate("long", 101.0, 100.4, lv, "xyz:BRENTOIL", 20.0, 20.0)
    assert free["ok"] is True
    held = tw.evaluate("long", 101.0, 100.4, lv, "xyz:BRENTOIL", 20.0, 20.0, thesis_age_min=10.0,
                       entry_delay_min=120.0)
    assert held["ok"] is False and held["blockers"][0].startswith("thesis 10 min old")


def test_oil_levels_next_order_passes_the_delay_through():
    ol = _script("oil_levels")
    lv = {"range_24h": {"low": 100.0, "high": 106.0}, "atr14_1h": 0.5, "mid": 100.4}
    free = ol.next_order("xyz:BRENTOIL", "long", lv, 101.0, 20.0, 20.0)
    assert free["ok"] is True and "place_bracket" in free
    held = ol.next_order("xyz:BRENTOIL", "long", lv, 101.0, 20.0, 20.0, thesis_age_min=10.0, entry_delay_min=120.0)
    assert held["ok"] is False and "place_bracket" not in held


def test_the_config_tool_reaches_the_key_through_its_generic_path(monkeypatch, tmp_path):
    """handle_config accepts any key already present in read_agent_config()
    (DEFAULT_CONFIG merged in), so the operator's MCP surface is unchanged
    (tests/test_mcp_operator_unchanged.py pins it) and the switch still works."""
    path = tmp_path / ".agent-config.json"
    path.write_text("{}")
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(path))
    cfg = config_store.read_agent_config()
    assert "oil_entry_delay_min" in cfg and cfg["oil_entry_delay_min"] is None
    saved = config_store.update_agent_config({"oil_entry_delay_min": 120})
    assert saved["oil_entry_delay_min"] == 120 and entry_delay_for("xyz:BRENTOIL") == 120.0
    assert '"oil_entry_delay_min": 120' in path.read_text()
