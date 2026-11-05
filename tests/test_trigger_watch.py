"""scripts/trigger_watch.py: wakes the desk tick the minute its entry rule
passes (operator, 2026-10-31, weekend 4-hour loops). No LLM calls."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("trigger_watch", ROOT / "scripts" / "trigger_watch.py")
W = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(W)

BRENT_LV = {"range_24h": {"low": 112.00, "high": 114.23}, "atr14_1h": 0.17}


def test_evaluate_matches_the_tick_rule():
    assert W.evaluate("long", 112.20, 112.165, BRENT_LV, "xyz:BRENTOIL", 20, 20)["ok"] is False   # 1.6R
    hit = W.evaluate("long", 112.20, 112.04, BRENT_LV, "xyz:BRENTOIL", 20, 20)
    assert hit["ok"] is True and hit["stop_px"] == 111.25
    assert W.evaluate("long", 111.90, 112.04, BRENT_LV, "xyz:BRENTOIL", 20, 20)["ok"] is False     # never chase
    assert W.evaluate("flat", None, 112.04, BRENT_LV, "xyz:BRENTOIL", 20, 20)["ok"] is False
    assert W.evaluate("long", 112.20, None, BRENT_LV, "xyz:BRENTOIL", 20, 20)["ok"] is False


# A silver breakdown (services/strategies/tests/test_silver_breakout.py's
# fixture): 24h range 47.85-50, the last 1h bar closed 47.9 under the prior 48.0 low.
SILVER_BO = {"prior_low": 48.0, "prior_high": 50.0, "last_close": 47.9, "recent_high": 48.05,
             "recent_low": 47.85, "lookback_h": 24, "stop_h": 4}
SILVER_LV = {"range_24h": {"low": 47.85, "high": 50.0}, "atr14_1h": 0.2, "breakout_24h": SILVER_BO}


def test_evaluate_never_wakes_a_breakout_now():
    out = W.evaluate("short", 49.0, 47.9, SILVER_LV, "xyz:SILVER", 25, 20)
    assert out["ok"] is False and out.get("source") != "breakout"


def test_evaluate_wakes_the_silver_breakout_and_never_a_brent_one(monkeypatch):
    from services.strategies import next_trade as NT
    monkeypatch.setattr(NT, "BREAKOUT_COINS", frozenset({"xyz:SILVER"}))   # the switch, if it comes back
    hit = W.evaluate("short", 49.0, 47.9, SILVER_LV, "xyz:SILVER", 25, 20)
    assert hit["ok"] is True and hit["source"] == "breakout"
    same_on_brent = W.evaluate("short", 49.0, 47.9, SILVER_LV, "xyz:BRENTOIL", 20, 20)
    assert same_on_brent["ok"] is False and "breakout" not in same_on_brent
    no_break = dict(SILVER_LV, breakout_24h=dict(SILVER_BO, last_close=48.1))
    assert W.evaluate("short", 49.0, 47.9, no_break, "xyz:SILVER", 25, 20)["ok"] is False


def test_cooldown():
    assert W.cooled_down({}, "oil", 1000.0, 1800)
    assert not W.cooled_down({"oil": 900.0}, "oil", 1000.0, 1800)
    assert W.cooled_down({"oil": 900.0}, "metals", 1000.0, 1800)


def _stub(monkeypatch, tmp_path, mids, held="", news=None):
    import _state_env
    import pathiel.agents.config_store as cs
    import pathiel.client.exchange as ex
    from services.intel import headline_watch as HW
    from services.strategies import journal, levels
    monkeypatch.setattr(W, "STATE_DIR", str(tmp_path))
    # the headline step: its state and alert files in tmp, its fetchers stubbed (no RSS, no Polymarket)
    monkeypatch.setattr(HW, "STATE_PATH", str(tmp_path / "headline_state.json"))
    monkeypatch.setattr(HW, "ALERTS_PATH", str(tmp_path / "headline_alerts.json"))
    monkeypatch.setattr(W, "fetch_headlines", lambda group: list(news or []))
    monkeypatch.setattr(W, "fetch_odds", lambda desk: [])
    monkeypatch.setattr(W, "fetch_posts", lambda: [])          # the Trump archive and the official
    monkeypatch.setattr(W, "fetch_official", lambda: [])       # feeds: services/intel/tests/test_sources_social.py
    monkeypatch.setattr(_state_env, "load_env_local", lambda root: None)
    monkeypatch.setattr(cs, "read_agent_config", lambda: {
        "active_desks": ["oil"], "desk_equity_share": {"oil": 1.0}, "risk_profile": {"max_leverage": 20}})
    call = SimpleNamespace(side="long", entry_px=112.20, instrument="xyz:BRENTOIL")
    monkeypatch.setattr(journal, "latest_thesis", lambda desk: SimpleNamespace(call=call))
    monkeypatch.setattr(levels, "summarize_coin", lambda coin: BRENT_LV)
    it = iter(mids)
    monkeypatch.setattr(levels, "fetch_mid", lambda coin: next(it))
    monkeypatch.setattr(ex, "get_max_leverage", lambda coin: 20)
    monkeypatch.setattr(W, "_slot", lambda mod, desk: held)


def test_watch_waits_then_fires_once_and_exits(monkeypatch, tmp_path, capsys):
    _stub(monkeypatch, tmp_path, [112.30, 112.20, 112.04])
    sleeps = []
    assert W.watch(once=False, cooldown_min=30, sleep=sleeps.append, clock=lambda: 1000.0) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["fire"] == "oil" and out["mid"] == 112.04 and len(sleeps) == 2
    assert json.loads((tmp_path / "fired.json").read_text())["oil"] == 1000.0
    assert "FIRE" in (tmp_path / "watch.log").read_text()


def test_a_desk_that_holds_something_or_just_fired_is_skipped(monkeypatch, tmp_path, capsys):
    _stub(monkeypatch, tmp_path, [112.04], held="holds position on xyz:BRENTOIL")
    W.watch(once=True, cooldown_min=30, clock=lambda: 1000.0)
    assert "tick handles it" in capsys.readouterr().out
    _stub(monkeypatch, tmp_path, [112.04])
    (tmp_path / "fired.json").write_text(json.dumps({"oil": 900.0}))
    W.watch(once=True, cooldown_min=30, clock=lambda: 1000.0)
    assert "cooling down" in capsys.readouterr().out


# ── the headline step (operator, 2026-11-05: "FIX THEM") ────────────────────

def _news(title, now_s):
    from datetime import datetime, timezone
    return {"title": title, "url": "u", "source": "Reuters",
            "published_at": datetime.fromtimestamp(now_s - 60, tz=timezone.utc)}


def test_first_run_primes_and_never_fires_on_the_headlines_already_out(monkeypatch, tmp_path, capsys):
    _stub(monkeypatch, tmp_path, [112.30], news=[_news("Iran, US reach deal to reopen Strait of Hormuz", 1000.0)])
    W.watch(once=True, cooldown_min=30, clock=lambda: 1000.0)
    out = capsys.readouterr().out
    assert "headlines: primed" in out and "FIRE" not in out
    state = json.loads((tmp_path / "headline_state.json").read_text())
    assert len(state["seen"]) == 1 and state["hist"]["xyz:BRENTOIL"] == [[1000000, 112.3]]
    assert not (tmp_path / "headline_alerts.json").exists()


def test_a_new_headline_wakes_the_desk_in_a_position_and_records_the_alert(monkeypatch, tmp_path, capsys):
    from services.intel import headline_watch as HW
    HW.save_state({"seen": [], "last_news_s": 0.0, "odds": {}}, str(tmp_path / "headline_state.json"))
    _stub(monkeypatch, tmp_path, [112.30], held="holds position on xyz:BRENTOIL",
          news=[_news("US strikes Iran's Qeshm Island", 1000.0)])
    assert W.watch(once=False, cooldown_min=30, clock=lambda: 1000.0) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line == {"fire": "oil", "also": [], "reason": "headline", "utc": line["utc"], "coin": "xyz:BRENTOIL",
                    "label": "iran/hormuz", "headline": "US strikes Iran's Qeshm Island"}
    assert json.loads((tmp_path / "fired.json").read_text()) == {"oil:headline": 1000.0}
    alerts = HW.read_alerts(str(tmp_path / "headline_alerts.json"))
    assert len(alerts) == 1 and alerts[0]["at_ms"] == 1_000_000 and alerts[0]["group"] == "oil"
    # the headline blackout now holds new entries on the oil coins for 30 min
    from services.strategies.event_blackout import event_blackout
    assert event_blackout("xyz:BRENTOIL", 1_000_000 + 29 * 60_000)["kind"] == "headline"
    assert event_blackout("xyz:BRENTOIL", 1_000_000 + 30 * 60_000) is None


def test_the_headline_cooldown_key_does_not_rewake_a_desk_that_just_woke(monkeypatch, tmp_path, capsys):
    from services.intel import headline_watch as HW
    HW.save_state({"seen": [], "last_news_s": 0.0, "odds": {}}, str(tmp_path / "headline_state.json"))
    (tmp_path / "fired.json").write_text(json.dumps({"oil:headline": 900.0}))
    _stub(monkeypatch, tmp_path, [112.30], news=[_news("OPEC+ agrees surprise output cut", 1000.0)])
    W.watch(once=True, cooldown_min=30, clock=lambda: 1000.0)
    out = capsys.readouterr().out
    assert "headlines: 1 hit(s) (capped or cooling down)" in out and '"fire"' not in out
    assert len(HW.read_alerts(str(tmp_path / "headline_alerts.json"))) == 1     # recorded all the same


def test_a_price_shock_wakes_the_desk_with_its_own_mid_history(monkeypatch, tmp_path, capsys):
    from services.intel import headline_watch as HW
    hist = [[1000_000 - 60_000 * (10 - i), 113.5] for i in range(10)]
    HW.save_state({"seen": [], "last_news_s": 1000.0, "odds": {}, "hist": {"xyz:BRENTOIL": hist}},
                  str(tmp_path / "headline_state.json"))
    _stub(monkeypatch, tmp_path, [112.30])        # -1.06% against the saved window
    assert W.watch(once=False, cooldown_min=30, clock=lambda: 1000.0) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line["reason"] == "headline" and line["label"] == "price shock" and line["coin"] == "xyz:BRENTOIL"
    assert "-1.06%" in line["headline"]
