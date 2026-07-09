"""Shadow-ledger tests. The grader section pins the 2026-07-09 audit bugs:

  1. int(horizon_days) truncated sub-day horizons to 0 -> permanently ungradeable
     (neg_funding_fade traded live 10 days with "0 resolved").
  2. Short return was entry/last - 1 (convexity-inverted, optimistic for every
     winning short: entry 100 -> 90 graded +11.1% instead of +10%).
  3. No funding term -> a funding-gated short book graded price-only.
  4. Duplicate cluster records graded as independent evidence (one MANTA cluster
     was 5 of neg_funding_fade's 13 records).
  5. summary() and grade_records() disagreed on what "resolved" means
     ("13 res" in inventory vs "only 0 resolved" in the verdict).
"""
import pytest

from hermes_trader.agents import shadow_ledger as SL

HOUR = 3_600_000
DAY = 86_400_000
SIG_T = 1_750_000_000_000  # arbitrary epoch-ms bar time


@pytest.fixture
def ledger_dir(monkeypatch, tmp_path):
    d = tmp_path / "shadow_ledger"
    monkeypatch.setattr(SL, "_ledger_dir", lambda: str(d) if d.exists() or d.mkdir(parents=True) or True else str(d))
    return d


def _bars(prices, t0=SIG_T + HOUR, bar_ms=HOUR):
    """Flat OHLC bars at the given closes, strictly after the signal bar."""
    return [{"t": t0 + i * bar_ms, "o": p, "h": p, "l": p, "c": p, "v": 1_000.0}
            for i, p in enumerate(prices)]


def _rec(**ov):
    rec = {"coin": "ALT", "side": "short", "signal_bar_t": SIG_T,
           "entry_ref_px": 100.0, "horizon_days": 8 / 24.0, "stop_pct": 25.0,
           "ts": SIG_T, "meta": {}}
    rec.update(ov)
    return rec


# --------------------------------------------------------------------- record/load
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


# --------------------------------------------------------------------- intervals
def test_grade_interval_subday_uses_hourly_bars():
    assert SL.grade_interval(8 / 24.0) == ("1h", HOUR, 8)


def test_grade_interval_daily_ceils():
    assert SL.grade_interval(1.0) == ("1d", DAY, 1)
    assert SL.grade_interval(2.5) == ("1d", DAY, 3)


def test_fractional_horizon_not_ungradeable():
    """Regression: int(0.333) == 0 made 8h-horizon records permanently ungradeable."""
    seen = {}

    def fetch_fwd(coin, sig_t, n_bars, interval):
        seen["interval"], seen["n_bars"] = interval, n_bars
        return _bars([99.0] * 13)

    out = SL.grade_records([_rec()], fetch_fwd, now_ms=SIG_T + 2 * DAY)
    assert out["ungradeable"] == 0
    assert out["pending"] == 0
    assert out["n"] == 1
    assert seen["interval"] == "1h" and seen["n_bars"] >= 8


def test_subday_pending_until_bars_close():
    """8h horizon on hourly bars resolves ~10h after the signal, not 2+ days."""
    fetch = lambda c, t, n, i: _bars([99.0] * 13)
    assert SL.grade_records([_rec()], fetch, now_ms=SIG_T + 5 * HOUR)["pending"] == 1
    assert SL.grade_records([_rec()], fetch, now_ms=SIG_T + 11 * HOUR)["n"] == 1


# --------------------------------------------------------------------- returns
def test_short_return_is_fraction_of_notional():
    """Regression: entry 100 -> 90 must grade +10%, not entry/last-1 = +11.1%."""
    ret, held = SL.simulate_exit("short", 100.0, _bars([95.0, 90.0]), 50.0, 2)
    assert ret == pytest.approx(0.10)
    assert held == 2


def test_short_losing_return_symmetry():
    """entry 100 -> 110 (stop not touched) must grade -10%."""
    fwd = [{"t": 0, "o": 100, "h": 110, "l": 100, "c": 110, "v": 1}]
    ret, _ = SL.simulate_exit("short", 100.0, fwd, 25.0, 1)
    assert ret == pytest.approx(-0.10)


def test_simulate_exit_short_stop_and_horizon():
    # short from 100; a bar high reaching 108 (>= +8%) stops out at -8%
    fwd_stop = [{"t": 0, "o": 100, "h": 108, "l": 99, "c": 105, "v": 1}]
    ret, held = SL.simulate_exit("short", 100.0, fwd_stop, 8.0, 10)
    assert ret == pytest.approx(-0.08)
    assert held == 1
    # short from 100; price drifts to 90 by horizon close -> +10% of notional
    fwd_win = [{"t": 0, "o": 100, "h": 101, "l": 89, "c": 90, "v": 1}]
    ret, held = SL.simulate_exit("short", 100.0, fwd_win, 8.0, 10)
    assert ret == pytest.approx(0.10)
    assert held == 1


def test_simulate_exit_long_stop_and_horizon():
    fwd_stop = [{"t": 0, "o": 100, "h": 101, "l": 91, "c": 95, "v": 1}]
    ret, _ = SL.simulate_exit("long", 100.0, fwd_stop, 8.0, 10)
    assert ret == pytest.approx(-0.08)
    fwd_win = [{"t": 0, "o": 100, "h": 112, "l": 100, "c": 110, "v": 1}]
    ret, _ = SL.simulate_exit("long", 100.0, fwd_win, 8.0, 10)
    assert ret == pytest.approx(0.10)


def test_stops_report_bars_held():
    ret, held = SL.simulate_exit("short", 100.0, _bars([120.0, 126.0, 90.0]), 25.0, 3)
    assert ret == pytest.approx(-0.25)
    assert held == 2
    ret, held = SL.simulate_exit("long", 100.0, _bars([74.0, 120.0]), 25.0, 2)
    assert ret == pytest.approx(-0.25)
    assert held == 1


# --------------------------------------------------------------------- funding
def test_funding_sign_short_pays_negative_rate():
    rows = [{"time": SIG_T + (i + 1) * HOUR, "fundingRate": "-0.001"} for i in range(8)]
    assert SL.funding_return("short", rows, SIG_T, SIG_T + 8 * HOUR) == pytest.approx(-0.008)
    assert SL.funding_return("long", rows, SIG_T, SIG_T + 8 * HOUR) == pytest.approx(0.008)


def test_funding_window_filter():
    rows = [{"time": SIG_T + 1 * HOUR, "fundingRate": 0.001},
            {"time": SIG_T + 99 * HOUR, "fundingRate": 0.5}]   # outside the hold
    assert SL.funding_return("short", rows, SIG_T, SIG_T + 8 * HOUR) == pytest.approx(0.001)


def test_grade_records_nets_funding_over_holding_time():
    """A short held 8 hourly bars at -0.1%/h funding: price +2% -> net +1.2%."""
    fetch_fwd = lambda c, t, n, i: _bars([99.0] * 7 + [98.0] + [98.0] * 5)
    windows = {}

    def fetch_funding(coin, start_ms, end_ms):
        windows["span_h"] = (end_ms - start_ms) / HOUR
        return [{"time": start_ms + (j + 1) * HOUR, "fundingRate": -0.001} for j in range(8)]

    out = SL.grade_records([_rec()], fetch_fwd, now_ms=SIG_T + 2 * DAY,
                           fetch_funding=fetch_funding)
    assert out["funding_included"] is True
    assert windows["span_h"] == 8          # funding accrues over bars_held only
    d = out["detail"][0]
    assert d["price_pct"] == pytest.approx(2.0)
    assert d["funding_pct"] == pytest.approx(-0.8)
    assert d["ret_pct"] == pytest.approx(1.2)
    assert out["slip0"]["mean_pct"] == pytest.approx(1.2)


def test_grade_without_funding_is_price_only():
    fetch = lambda c, t, n, i: _bars([98.0] * 13)
    out = SL.grade_records([_rec()], fetch, now_ms=SIG_T + 2 * DAY)
    assert out["funding_included"] is False
    assert out["detail"][0]["funding_pct"] == 0.0


# --------------------------------------------------------------------- dedup
def test_dedup_collapses_same_coin_cluster():
    """5 re-signals of the same coin inside one horizon = ONE episode."""
    recs = [_rec(signal_bar_t=SIG_T + i * 5 * 60_000) for i in range(5)]
    kept, dropped = SL.dedup_episodes(recs)
    assert len(kept) == 1 and dropped == 4
    assert kept[0]["signal_bar_t"] == SIG_T  # first signal wins (PIT)


def test_dedup_keeps_independent_episodes():
    recs = [_rec(),
            _rec(signal_bar_t=SIG_T + 9 * HOUR),          # past the 8h horizon -> new episode
            _rec(coin="OTHER", signal_bar_t=SIG_T + 1)]    # different coin -> kept
    kept, dropped = SL.dedup_episodes(recs)
    assert len(kept) == 3 and dropped == 0


def test_grade_records_reports_dedup():
    recs = [_rec(), _rec(signal_bar_t=SIG_T + 5 * 60_000)]
    fetch = lambda c, t, n, i: _bars([99.0] * 13)
    out = SL.grade_records(recs, fetch, now_ms=SIG_T + 2 * DAY)
    assert out["n"] == 1 and out["deduped"] == 1
    off = SL.grade_records(recs, fetch, now_ms=SIG_T + 2 * DAY, dedup=False)
    assert off["n"] == 2 and off["deduped"] == 0


# --------------------------------------------------------------------- summary/grade agreement
def test_summary_and_grade_agree_on_resolved(ledger_dir):
    """Regression: inventory said '13 res' while the grade said 'only 0 resolved'."""
    now = SIG_T + 2 * DAY
    SL.record("agree_book", coin="ALT", side="short", signal_bar_t=SIG_T,
              entry_ref_px=100.0, horizon_days=8 / 24.0, stop_pct=25.0, ts=SIG_T)
    inv = {r["book"]: r for r in SL.summary(now_ms=now)}["agree_book"]
    assert inv["resolved"] == 1
    out = SL.grade_records(SL.load("agree_book"), lambda c, t, n, i: _bars([99.0] * 13),
                           now_ms=now)
    assert out["n"] == 1 and out["pending"] == 0


# --------------------------------------------------------------------- classify
def test_grade_and_classify_validated():
    now = 100 * DAY
    recs = [{"coin": f"C{i}", "side": "short", "signal_bar_t": now - 30 * DAY,
             "entry_ref_px": 100.0, "horizon_days": 5, "stop_pct": 8.0, "ts": now - 30 * DAY}
            for i in range(10)]

    def fetch_fwd(coin, sig_t, n, interval):
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

    def fetch_fwd(coin, sig_t, n, interval):
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
    g = SL.grade_records(recs, lambda c, t, n, i: [], now_ms=now)
    assert g["n"] == 0
    assert g["pending"] == 1
    assert g["ungradeable"] == 1
