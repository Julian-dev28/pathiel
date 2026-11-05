"""Drift alarm (operator 2026-11-05: "ADD DRIFT ALARM"): a steady 4h move the
15-minute shock misses, e.g. that evening's Brent climb after tanker attacks."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from services.intel import headline_watch as HW

M = HW.MIN_MS
ROOT = Path(__file__).resolve().parents[3]


def _climb(start: float, end: float, minutes: int):
    """One mid a minute, linear: no 15-minute leg is a shock."""
    return [(i * M, start + (end - start) * i / minutes) for i in range(minutes)]


def test_the_11_05_evening_climb_fires_drift_but_not_shock():
    # 1h closes that evening: 96.585 at 18:00Z to 98.361 by 21:00Z (+1.84% in 3h)
    h = _climb(96.585, 98.30, 180)
    now = 180 * M
    assert HW.price_shock("xyz:BRENTOIL", h, now, 98.361) is None
    d = HW.price_drift("xyz:BRENTOIL", h, now, 98.361)
    assert d and d["label"] == "price drift" and d["move"] > 0.0175 and d["group"] == "oil"


def test_a_normal_day_stays_quiet_and_both_directions_count():
    h = _climb(96.0, 97.2, 240)           # +1.25% in 4h: about Brent's p88, no alarm
    assert HW.price_drift("xyz:BRENTOIL", h, 240 * M, 97.2) is None
    down = _climb(100.0, 98.3, 240)
    assert HW.price_drift("xyz:BRENTOIL", down, 240 * M, 98.2)["move"] < 0


def test_needs_half_a_window_of_history():
    h = _climb(96.0, 98.0, 90)            # 90 min of history: too short to call a 4h drift
    assert HW.price_drift("xyz:BRENTOIL", h, 90 * M, 98.2) is None


def test_gold_has_its_own_threshold_and_unknown_coins_never_fire():
    h = _climb(4000.0, 4038.0, 200)       # +0.95%
    assert HW.price_drift("xyz:GOLD", h, 200 * M, 4038.0)
    assert HW.price_drift("xyz:NVDA", h, 200 * M, 4038.0) is None


def _watch():
    spec = importlib.util.spec_from_file_location("trigger_watch_drift", ROOT / "scripts" / "trigger_watch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_watcher_alerts_once_per_drift_window_and_keeps_4h_of_history():
    W = _watch()
    state = {"primed_sources": ["posts", "official", "news", "odds"], "last_news_s": 1e18, "last_posts_s": 1e18}
    hist = {}
    targets = [("oil", "xyz:BRENTOIL", "oil")]
    hits = []
    for i in range(300):
        px = 96.585 + 1.8 * min(i, 180) / 180
        hits += W.headline_pass(targets, i * 60.0, state, hist, {"xyz:BRENTOIL": px},
                                fetch_news=lambda g: [], fetch_odds_fn=lambda d: [],
                                fetch_posts_fn=lambda: [], fetch_official_fn=lambda: [])
    drifts = [a for _d, a in hits if a["label"] == "price drift"]
    assert len(drifts) == 1
    assert len(hist["xyz:BRENTOIL"]) == HW.HISTORY_KEEP_MIN


def test_eval_replay_counts_one_alert_per_drift_window():
    from services.intel.evals.eval_price_drift import replay
    H = 3_600_000
    closes = [(i * H, 100.0 + (2.0 if i >= 6 else 0.0)) for i in range(12)]   # a +2% step after 6h
    fired = replay("xyz:BRENTOIL", closes)
    assert len(fired) == 1
