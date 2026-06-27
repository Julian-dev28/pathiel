import os

import pytest

from hermes_trader.agents import shadow_ledger as SL


DAY = 86_400_000


@pytest.fixture
def ledger_dir(monkeypatch, tmp_path):
    d = tmp_path / "shadow_ledger"
    monkeypatch.setattr(SL, "_ledger_dir", lambda: str(d) if d.exists() or d.mkdir(parents=True) or True else str(d))
    return d


def test_record_and_load_roundtrip(ledger_dir):
    SL.record("bookX", coin="ALT", side="short", signal_bar_t=1000,
              entry_ref_px=10.0, horizon_days=10, stop_pct=8.0, ts=2000,
              meta={"move_pct": -9.0})
    recs = SL.load("bookX")
    assert len(recs) == 1
    r = recs[0]
    assert r["book"] == "bookX" and r["coin"] == "ALT" and r["side"] == "short"
    assert r["entry_ref_px"] == 10.0 and r["stop_pct"] == 8.0
    assert r["meta"]["move_pct"] == -9.0
    assert "bookX" in SL.list_books()


def test_record_many_and_summary(ledger_dir):
    now = 100 * DAY
    SL.record_many("bookY", [
        {"coin": "A", "side": "short", "signal_bar_t": now - 20 * DAY, "entry_ref_px": 5.0,
         "horizon_days": 10, "stop_pct": 8.0, "ts": now - 20 * DAY},   # resolved
        {"coin": "B", "side": "short", "signal_bar_t": now - 1 * DAY, "entry_ref_px": 6.0,
         "horizon_days": 10, "stop_pct": 8.0, "ts": now - 1 * DAY},    # pending
        {"coin": "C", "side": "short", "signal_bar_t": 0, "entry_ref_px": 0.0,
         "horizon_days": 10, "stop_pct": 8.0, "ts": now},              # ungradeable
    ])
    s = {r["book"]: r for r in SL.summary(now_ms=now)}["bookY"]
    assert s["n"] == 3
    assert s["gradeable"] == 2
    assert s["resolved"] == 1
    assert s["pending"] == 1
    assert s["ungradeable"] == 1


def test_simulate_exit_short_stop_and_horizon():
    # short from 100; a bar high reaching 108 (>= +8%) stops out at -8%
    fwd_stop = [{"t": 0, "o": 100, "h": 108, "l": 99, "c": 105, "v": 1}]
    assert SL.simulate_exit("short", 100.0, fwd_stop, 8.0, 10) == pytest.approx(-0.08)
    # short from 100; price drifts down to 90 by horizon close -> +10% for a short
    fwd_win = [{"t": 0, "o": 100, "h": 101, "l": 89, "c": 90, "v": 1}]
    assert SL.simulate_exit("short", 100.0, fwd_win, 8.0, 10) == pytest.approx(100 / 90 - 1)


def test_simulate_exit_long_stop_and_horizon():
    fwd_stop = [{"t": 0, "o": 100, "h": 101, "l": 91, "c": 95, "v": 1}]
    assert SL.simulate_exit("long", 100.0, fwd_stop, 8.0, 10) == pytest.approx(-0.08)
    fwd_win = [{"t": 0, "o": 100, "h": 112, "l": 100, "c": 110, "v": 1}]
    assert SL.simulate_exit("long", 100.0, fwd_win, 8.0, 10) == pytest.approx(0.10)


def test_grade_and_classify_validated():
    now = 100 * DAY
    # 10 resolved short signals that all win cleanly forward -> VALIDATED
    recs = [{"coin": f"C{i}", "side": "short", "signal_bar_t": now - 30 * DAY,
             "entry_ref_px": 100.0, "horizon_days": 5, "stop_pct": 8.0, "ts": now - 30 * DAY}
            for i in range(10)]

    def fetch_fwd(coin, sig_t, n):
        # price falls to 95 by horizon close -> +5% short, no stop touched
        return [{"t": sig_t + (j + 1) * DAY, "o": 100, "h": 100.5, "l": 95, "c": 95, "v": 1}
                for j in range(n)]

    g = SL.grade_records(recs, fetch_fwd, now_ms=now)
    assert g["n"] == 10
    assert g["slip12"]["mean_pct"] > 0
    assert g["verdict"]["label"] == "VALIDATED"


def test_grade_and_classify_refuted():
    now = 100 * DAY
    recs = [{"coin": f"C{i}", "side": "short", "signal_bar_t": now - 30 * DAY,
             "entry_ref_px": 100.0, "horizon_days": 5, "stop_pct": 8.0, "ts": now - 30 * DAY}
            for i in range(10)]

    def fetch_fwd(coin, sig_t, n):
        # price rises -> shorts lose / stop out
        return [{"t": sig_t + (j + 1) * DAY, "o": 100, "h": 110, "l": 100, "c": 109, "v": 1}
                for j in range(n)]

    g = SL.grade_records(recs, fetch_fwd, now_ms=now)
    assert g["slip12"]["mean_pct"] <= 0
    assert g["verdict"]["label"] == "REFUTED"


def test_classify_pending_below_min_n():
    g = {"n": 3, "slip12": {"mean_pct": 5.0}}
    assert SL.classify(g, min_n=8)["label"] == "PENDING"


def test_grade_skips_pending_and_ungradeable():
    now = 100 * DAY
    recs = [
        {"coin": "A", "side": "short", "signal_bar_t": now - 1 * DAY, "entry_ref_px": 10.0,
         "horizon_days": 10, "stop_pct": 8.0},   # pending (too recent)
        {"coin": "B", "side": "short", "signal_bar_t": 0, "entry_ref_px": 0.0,
         "horizon_days": 10, "stop_pct": 8.0},   # ungradeable
    ]
    g = SL.grade_records(recs, lambda c, t, n: [], now_ms=now)
    assert g["n"] == 0
    assert g["pending"] == 1
    assert g["ungradeable"] == 1
