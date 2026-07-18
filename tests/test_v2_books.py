"""v2 gate tests — books: completed-bar contract, ported signal math, sizing floors.

Everything offline + deterministic (<2s lane). State files are monkeypatched to
tmp_path so tests never share dedup state.
"""
from __future__ import annotations

import json

import pytest

import hermes_trader.v2.books as books

DAY_MS = books.DAY_MS
NOW_MS = 1_752_800_000_000  # fixed epoch-ms so tests are deterministic


def _bar(t, c, h=None, l=None):
    return {"t": t, "o": c, "h": h if h is not None else c,
            "l": l if l is not None else c, "c": c, "v": 1.0}


def _daily_bars(closes, end_t=NOW_MS - (NOW_MS % DAY_MS)):
    """Chronological daily bars ending with a bar that STARTS at end_t."""
    n = len(closes)
    return [_bar(end_t - (n - 1 - i) * DAY_MS, c) for i, c in enumerate(closes)]


def _uni(coins, vol=1e8):
    return [{"coin": c, "dayNtlVlm": vol, "type": "perp", "midPx": 100.0} for c in coins]


@pytest.fixture
def _iso(tmp_path, monkeypatch):
    """Redirect every v2 book state file to this test's tmp dir."""
    monkeypatch.setattr(books, "_EF_STATE_FILE", str(tmp_path / "ef.json"))
    monkeypatch.setattr(books, "_FS_SEEN_FILE", str(tmp_path / "fs.json"))
    monkeypatch.setattr(books, "_XS_TS_FILE", str(tmp_path / "xs_ts"))
    monkeypatch.setattr(books, "_XS_OWNED_FILE", str(tmp_path / "xs_owned.json"))
    return tmp_path


# ── Completed-bar contract (v2 LAW #2 — this is a contract test) ──────────────

class TestCompletedBars:
    def test_forming_bar_is_dropped(self):
        bars = _daily_bars([100, 100, 88])          # last bar started < 24h ago
        now = bars[-1]["t"] + 1                     # 1ms into the forming bar
        out = books.completed_bars(bars, now)
        assert len(out) == 2 and out[-1]["c"] == 100

    def test_just_closed_bar_is_kept(self):
        bars = _daily_bars([100, 100, 88])
        now = bars[-1]["t"] + DAY_MS                # bar started exactly 24h ago
        out = books.completed_bars(bars, now)
        assert len(out) == 3 and out[-1]["c"] == 88

    def test_empty_and_none_are_safe(self):
        assert books.completed_bars([], NOW_MS) == []
        assert books.completed_bars(None, NOW_MS) == []


# ── extreme_fade ──────────────────────────────────────────────────────────────

_EF_CFG = {"enabled": True, "crash_pct": -0.12, "stop_pct": 20.0, "hold_days": 3.0,
           "equity_fraction": 0.40, "deep_tier": {"crash_pct": -0.20, "equity_fraction": 0.60},
           "entry_window_hours": 6.0, "max_new_per_cycle": 2, "min_volume_usd": 5e6,
           "max_scan_coins": 40, "leverage": 12,
           "skew_arm": {"enabled": False}}  # skew off by default in tests (see skew test)


def _fetch_crash(coin, interval, n, crash_close=87.0, forming_only=False):
    """5 flat completed bars then a crash bar; optionally the crash is only in the
    still-forming bar (must NOT signal — the live-reads-forming-bar audit bug)."""
    end_completed = NOW_MS - (NOW_MS % DAY_MS)               # started >= 24h ago? no:
    # Make the LAST bar the one starting at the current day boundary → forming.
    closes = [100, 100, 100, 100, 100, crash_close]
    bars = _daily_bars(closes, end_t=end_completed)
    if forming_only:
        return bars                                          # crash sits in forming bar
    # Shift one day back so the crash bar is completed (started exactly 24h+ ago).
    return _daily_bars(closes, end_t=end_completed - DAY_MS)


class TestExtremeFade:
    def test_completed_crash_signals_and_intends(self, _iso):
        now = NOW_MS - (NOW_MS % DAY_MS) + 3_600_000        # 1h after daily close
        r = books.extreme_fade_intents(_EF_CFG, _uni(["AAA"]), set(),
                                       lambda c, i, n: _fetch_crash(c, i, n),
                                       equity=150.0, now_ms=now)
        assert len(r.records) == 1 and r.records[0]["side"] == "long"
        assert r.records[0]["stop_pct"] == 20.0 and r.records[0]["horizon_days"] == 3.0
        assert len(r.intents) == 1
        it = r.intents[0]
        assert it.book == "extreme_fade" and it.side == "long"
        assert it.notional_usd == pytest.approx(0.40 * 150.0)   # $60 at $150 equity
        assert not it.meta["deep_tier"]

    def test_forming_bar_crash_does_not_signal(self, _iso):
        """THE contract: a -13% move on the still-forming bar is invisible."""
        now = NOW_MS - (NOW_MS % DAY_MS) + 3_600_000
        r = books.extreme_fade_intents(_EF_CFG, _uni(["AAA"]), set(),
                                       lambda c, i, n: _fetch_crash(c, i, n, forming_only=True),
                                       equity=150.0, now_ms=now)
        assert r.records == [] and r.intents == []

    def test_deep_tier_sizes_bigger(self, _iso):
        now = NOW_MS - (NOW_MS % DAY_MS) + 3_600_000
        r = books.extreme_fade_intents(_EF_CFG, _uni(["AAA"]), set(),
                                       lambda c, i, n: _fetch_crash(c, i, n, crash_close=78.0),
                                       equity=150.0, now_ms=now)
        assert r.intents[0].meta["deep_tier"] is True
        assert r.intents[0].notional_usd == pytest.approx(0.60 * 150.0)   # 1.5x size

    def test_stale_entry_window_skips_intent_but_still_records(self, _iso):
        now = NOW_MS - (NOW_MS % DAY_MS) + 7 * 3_600_000     # 7h > 6h window
        r = books.extreme_fade_intents(_EF_CFG, _uni(["AAA"]), set(),
                                       lambda c, i, n: _fetch_crash(c, i, n),
                                       equity=150.0, now_ms=now)
        assert len(r.records) == 1 and r.intents == []

    def test_crash_bar_dedup_after_open(self, _iso):
        now = NOW_MS - (NOW_MS % DAY_MS) + 3_600_000
        fetch = lambda c, i, n: _fetch_crash(c, i, n)
        r1 = books.extreme_fade_intents(_EF_CFG, _uni(["AAA"]), set(), fetch, 150.0, now)
        books.extreme_fade_mark_opened("AAA", r1.intents[0].signal_bar_t)
        r2 = books.extreme_fade_intents(_EF_CFG, _uni(["AAA"]), set(), fetch, 150.0, now)
        assert len(r2.records) == 1 and r2.intents == []     # grading never stops

    def test_held_coin_never_stacks(self, _iso):
        now = NOW_MS - (NOW_MS % DAY_MS) + 3_600_000
        r = books.extreme_fade_intents(_EF_CFG, _uni(["AAA"]), {"AAA"},
                                       lambda c, i, n: _fetch_crash(c, i, n),
                                       equity=150.0, now_ms=now)
        assert r.intents == []

    def test_skew_enforce_disarmed_records_but_never_intends(self, _iso, monkeypatch):
        cfg = dict(_EF_CFG)
        cfg["skew_arm"] = {"enabled": True, "window": 20, "threshold": 0.0, "enforce": True}
        monkeypatch.setattr(books, "_market_skew", lambda cbc, w: 0.5)   # positive = disarmed
        now = NOW_MS - (NOW_MS % DAY_MS) + 3_600_000
        r = books.extreme_fade_intents(cfg, _uni(["AAA"]), set(),
                                       lambda c, i, n: _fetch_crash(c, i, n),
                                       equity=150.0, now_ms=now)
        assert len(r.records) == 1 and r.records[0]["meta"]["armed"] is False
        assert r.intents == [] and r.info.get("disarmed") is True


# ── funding_spike_short ───────────────────────────────────────────────────────

_FS_CFG = {"enabled": True, "entry_z": 2.0, "exit_z": 1.0, "lookback_days": 30,
           "stop_pct": 15.0, "hold_days": 5.0, "equity_fraction": 0.25,
           "min_volume_usd": 20e6, "max_scan_coins": 40, "max_new_per_cycle": 1,
           "leverage": 12}


class TestFundingSpike:
    def test_funding_z_golden(self):
        """Hand-computed: 20 daily sums alternating 0.0/0.002 (mean .001, pstdev .001),
        trailing-24h sum 0.005 → z = 4.0 exactly (W-F2A math, imported not rewritten)."""
        rows = []
        for d in range(20):
            day_start = NOW_MS - (21 - d) * DAY_MS
            rows.append({"time": day_start + 3_600_000,
                         "fundingRate": 0.0 if d % 2 == 0 else 0.002})
        rows.append({"time": NOW_MS - 3_600_000, "fundingRate": 0.005})   # last 24h
        z = books.funding_z(rows, NOW_MS, lookback_days=20)
        assert z == pytest.approx(4.0)

    def test_episode_lifecycle(self, _iso):
        """z≥2 fires once; stays quiet until z decays below exit_z; then re-arms."""
        table = {"AAA": 2.5}
        zfn = lambda coin, now_ms, lb: table.get(coin)
        uni = _uni(["AAA"])
        r1 = books.funding_spike_intents(_FS_CFG, uni, set(), zfn, 150.0, NOW_MS)
        assert len(r1.intents) == 1 and r1.intents[0].side == "short"
        assert r1.intents[0].notional_usd == pytest.approx(0.25 * 150.0)
        books.funding_spike_mark_episode("AAA", r1.intents[0].signal_bar_t)

        table["AAA"] = 1.5    # still elevated but inside the episode → silent
        r2 = books.funding_spike_intents(_FS_CFG, uni, set(), zfn, 150.0, NOW_MS)
        assert r2.records == [] and r2.intents == []

        table["AAA"] = 0.5    # decayed below exit_z → episode over
        r3 = books.funding_spike_intents(_FS_CFG, uni, set(), zfn, 150.0, NOW_MS)
        assert r3.records == [] and r3.intents == []

        table["AAA"] = 2.2    # new spike → NEW episode
        r4 = books.funding_spike_intents(_FS_CFG, uni, set(), zfn, 150.0, NOW_MS)
        assert len(r4.intents) == 1

    def test_thin_coin_not_scanned(self, _iso):
        r = books.funding_spike_intents(_FS_CFG, _uni(["AAA"], vol=5e6), set(),
                                        lambda *a: 9.9, 150.0, NOW_MS)
        assert r.info["scanned"] == 0 and r.intents == []

    def test_held_coin_records_but_never_intends(self, _iso):
        r = books.funding_spike_intents(_FS_CFG, _uni(["AAA"]), {"AAA"},
                                        lambda *a: 3.0, 150.0, NOW_MS)
        assert len(r.records) == 1 and r.intents == []


# ── xs_momentum ───────────────────────────────────────────────────────────────

_XS_CFG = {"enabled": True, "lookback_days": 1, "k_per_leg": 2, "hold_days": 5.0,
           "equity_frac_per_leg": 0.10, "min_volume_usd": 1e6, "universe_top_n": 50,
           "residual": False, "ranking": "raw", "stop_pct": 25.0, "leverage": 12}

_RETS = {"A": 0.50, "B": 0.20, "C": 0.05, "D": -0.05, "E": -0.20, "F": -0.40}


def _xs_fetch(coin, interval, n):
    r = _RETS.get(coin, 0.0)
    return [_bar(0, 100.0), _bar(DAY_MS, 100.0 * (1 + r))]


class TestXsMomentum:
    def test_equity_gate_arming_floor(self):
        """Spec §3: arms when 0.10 × equity ≥ $10.50 per leg — around the $100 mark."""
        cfg = {"equity_frac_per_leg": 0.10, "k_per_leg": 4}
        assert books.xs_armed(19.0, cfg) is False       # $19 account: OFF
        assert books.xs_armed(100.0, cfg) is False      # $10/leg < $10.50: still OFF
        assert books.xs_armed(105.0, cfg) is True       # $10.50/leg: armed
        assert books.xs_armed(150.0, cfg) is True

    def test_below_floor_returns_empty(self, _iso):
        r = books.xs_intents(_XS_CFG, _uni(list(_RETS)), [], _xs_fetch, 19.0)
        assert r.intents == [] and r.records == [] and r.info["armed"] is False

    def test_armed_book_is_market_neutral(self, _iso):
        r = books.xs_intents(_XS_CFG, _uni(list(_RETS)), [], _xs_fetch, 150.0)
        longs = [i.coin for i in r.intents if i.side == "long"]
        shorts = [i.coin for i in r.intents if i.side == "short"]
        assert longs == ["A", "B"] and sorted(shorts) == ["E", "F"]
        for i in r.intents:
            assert i.notional_usd == pytest.approx(0.10 * 150.0)   # $15/leg at $150

    def test_rebalance_timer_gates_second_run(self, _iso):
        r1 = books.xs_intents(_XS_CFG, _uni(list(_RETS)), [], _xs_fetch, 150.0, now_s=1_000_000.0)
        assert r1.intents
        r2 = books.xs_intents(_XS_CFG, _uni(list(_RETS)), [], _xs_fetch, 150.0, now_s=1_001_000.0)
        assert r2.intents == [] and r2.info.get("waiting") is True
        r3 = books.xs_intents(_XS_CFG, _uni(list(_RETS)), [], _xs_fetch, 150.0,
                              now_s=1_000_000.0 + 5 * 86_400 + 1)
        assert r3.intents

    def test_blocked_coins_excluded_from_ranking(self, _iso):
        r = books.xs_intents(_XS_CFG, _uni(list(_RETS)), [], _xs_fetch, 150.0,
                             blocked_coins={"A"})
        longs = [i.coin for i in r.intents if i.side == "long"]
        assert "A" not in longs and "B" in longs        # next-best long instead


# ── Sizing floor arithmetic at $19 AND $150 (spec §3 table) ───────────────────

class TestSizingFloors:
    def test_at_19_everything_pins_to_exchange_minimum(self):
        assert books.intent_notional(0.40, 19.0) == pytest.approx(10.5)   # extreme_fade
        assert books.intent_notional(0.25, 19.0) == pytest.approx(10.5)   # funding_spike
        assert books.intent_notional(0.60, 19.0) == pytest.approx(11.4)   # deep tier clears

    def test_at_150_fracs_size_normally(self):
        assert books.intent_notional(0.40, 150.0) == pytest.approx(60.0)
        assert books.intent_notional(0.60, 150.0) == pytest.approx(90.0)
        assert books.intent_notional(0.25, 150.0) == pytest.approx(37.5)
        assert books.intent_notional(0.10, 150.0) == pytest.approx(15.0)

    def test_book_cap_clips_but_never_below_minimum(self):
        assert books.intent_notional(0.40, 150.0, book_cap_usd=20.0) == pytest.approx(20.0)
        assert books.intent_notional(0.40, 19.0, book_cap_usd=8.0) == pytest.approx(10.5)

    def test_min_order_parity_with_exchange_layer(self):
        from hermes_trader.client.exchange import MIN_ORDER_USD as REAL
        assert books.MIN_ORDER_USD == REAL
