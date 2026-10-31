"""Gate tests for services/thesis/evals/replay.py: the no-lookahead
candle fetcher, the replay window, resumability, and replay_day's wiring
-- all offline, no network, no LLM call (write_thesis is always
injected as a stub here). The harness's own __main__ fetch/LLM path is a
periodic, paid run, not a commit gate."""
from __future__ import annotations

import json
import os

import pytest

from contracts.strategies.models import Call, DeskSpec, Instrument, Thesis, Usage
from pathiel.models.types import Candle
from services.strategies.backtest import data as D
from services.thesis.evals import replay as R

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


# ── _interval_ms ─────────────────────────────────────────────────────────────

def test_interval_ms_for_every_unit_replay_uses():
    assert R._interval_ms("1d") == DAY_MS
    assert R._interval_ms("1h") == HOUR_MS
    assert R._interval_ms("15m") == 900_000
    assert R._interval_ms("4h") == 4 * HOUR_MS


def test_interval_ms_raises_on_an_unknown_interval():
    with pytest.raises(ValueError):
        R._interval_ms("bogus")
    with pytest.raises(ValueError):
        R._interval_ms("")


# ── truncating_fetcher: the no-lookahead property ──────────────────────────

def _hourly(n: int, base_close: float = 100.0) -> list:
    return [Candle(t=i * HOUR_MS, o=base_close, h=base_close + 1, l=base_close - 1,
                   c=base_close, v=1.0) for i in range(n)]


def test_truncating_fetcher_returns_only_bars_already_closed_at_now():
    bars = _hourly(10)   # bars open at 0h..9h, close at 1h..10h
    now = _dt_from_ms(5 * HOUR_MS)   # exactly the close time of bar index 4
    fetch = R.truncating_fetcher({"X": {"1h": bars}}, now)
    got = fetch("X", "1h", 100)
    assert [c.t for c in got] == [i * HOUR_MS for i in range(5)]   # bars 0-4, not bar 5 (closes at 6h)


def test_truncating_fetcher_respects_count_by_taking_the_most_recent():
    bars = _hourly(10)
    now = _dt_from_ms(10 * HOUR_MS)
    fetch = R.truncating_fetcher({"X": {"1h": bars}}, now)
    got = fetch("X", "1h", 3)
    assert [c.t for c in got] == [7 * HOUR_MS, 8 * HOUR_MS, 9 * HOUR_MS]


def test_truncating_fetcher_is_empty_for_a_coin_or_interval_not_prefetched():
    fetch = R.truncating_fetcher({"X": {"1h": _hourly(5)}}, _dt_from_ms(5 * HOUR_MS))
    assert fetch("Y", "1h", 10) == []
    assert fetch("X", "1d", 10) == []


def test_truncating_fetcher_no_lookahead_mutating_a_future_bar_changes_nothing():
    """The defining property: a bar that closes AFTER `now` must never be
    able to change what a tick at `now` sees, however it's mutated."""
    bars = _hourly(10)
    now = _dt_from_ms(5 * HOUR_MS)
    all_candles = {"X": {"1h": list(bars)}}
    fetch = R.truncating_fetcher(all_candles, now)
    before = fetch("X", "1h", 100)

    # mutate a bar that closes strictly after `now` (bar index 6: closes at 7h)
    all_candles["X"]["1h"][6] = Candle(t=6 * HOUR_MS, o=99999.0, h=99999.0, l=1.0, c=50000.0, v=1.0)
    after = fetch("X", "1h", 100)
    assert before == after


def _dt_from_ms(ms: int):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


# ── replay_window ────────────────────────────────────────────────────────────

def test_replay_window_is_days_entries_oldest_first_never_including_today():
    from datetime import datetime, timezone
    end = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    days = R.replay_window(3, 16, end=end)
    assert len(days) == 3
    assert [d.date().isoformat() for d in days] == ["2026-10-27", "2026-10-28", "2026-10-29"]
    assert all(d.hour == 16 for d in days)
    assert days == sorted(days)   # oldest first


# ── assert_not_live_state_dir ───────────────────────────────────────────────

def test_assert_not_live_state_dir_raises_for_the_live_dir_and_its_children():
    with pytest.raises(ValueError):
        R.assert_not_live_state_dir(R.LIVE_STATE_DIR)
    with pytest.raises(ValueError):
        R.assert_not_live_state_dir(os.path.join(R.LIVE_STATE_DIR, "strategies", "oil"))


def test_assert_not_live_state_dir_passes_for_an_unrelated_path(tmp_path):
    R.assert_not_live_state_dir(str(tmp_path / "thesis-replay"))   # no raise


# ── existing_thesis_path / resumability ─────────────────────────────────────

def test_existing_thesis_path_is_none_until_the_file_is_written(tmp_path):
    from datetime import datetime, timezone
    day = datetime(2026, 10, 1, 16, tzinfo=timezone.utc)
    assert R.existing_thesis_path("oil", str(tmp_path), day) is None
    from services.thesis.assemble import make_thesis_id
    path = os.path.join(str(tmp_path), f"{make_thesis_id('oil', day)}.json")
    (tmp_path / os.path.basename(path)).write_text("{}")
    assert R.existing_thesis_path("oil", str(tmp_path), day) == path


# ── replay_day ───────────────────────────────────────────────────────────────

def _tiny_spec() -> DeskSpec:
    return DeskSpec(id="test1", name="Test", tagline="t",
                    instruments=[Instrument(coin="TEST", label="Test", role="primary")])


def _stub_thesis(spec_id: str, day, *, cost: float = 0.0123, confidence: float = 0.7) -> Thesis:
    from services.thesis.assemble import make_thesis_id
    call = Call(side="long", instrument="TEST", one_liner="x", entry_px=100.0, stop_px=95.0,
               targets=[110.0], confidence=confidence, invalidation="below 90")
    return Thesis(id=make_thesis_id(spec_id, day), desk_id=spec_id, created_at=day,
                 expires_at=day, horizon_days=7, call=call,
                 usage=Usage(model="opus", input_tokens=500, output_tokens=200, cost_usd=cost))


def test_replay_day_skips_already_written_without_calling_write_thesis_fn(tmp_path):
    from datetime import datetime, timezone
    from services.thesis.assemble import make_thesis_id
    spec = _tiny_spec()
    day = datetime(2026, 10, 1, 16, tzinfo=timezone.utc)
    path = os.path.join(str(tmp_path), f"{make_thesis_id(spec.id, day)}.json")
    with open(path, "w") as fh:
        json.dump({"already": "written"}, fh)

    def _boom(*a, **kw):
        raise AssertionError("write_thesis_fn must not be called for an already-written day")

    row = R.replay_day(spec, {}, lambda: {}, day, "opus", str(tmp_path), write_thesis_fn=_boom)
    assert row["skipped"] is True and row["reason"] == "already_written" and row["path"] == path


def test_replay_day_writes_journal_shaped_json_and_a_full_summary_row(tmp_path):
    from datetime import datetime, timezone
    spec = _tiny_spec()
    day = datetime(2026, 10, 1, 16, tzinfo=timezone.utc)
    all_candles = {"TEST": {"1d": _hourly(20), "1h": _hourly(20)}}
    captured = {}

    def stub_write_thesis(spec_, analytics, intel, prev, model=None, now=None):
        captured.update(spec=spec_, analytics=analytics, intel=intel, prev=prev, model=model, now=now)
        return _stub_thesis(spec_.id, now)

    out_dir = str(tmp_path / "theses")
    row = R.replay_day(spec, all_candles, lambda: {}, day, "opus", out_dir, write_thesis_fn=stub_write_thesis)

    # the real generation path's contract: no intel, no prior thesis, the
    # requested model, and the replay day itself as `now` (not wall clock)
    assert captured["intel"] is None and captured["prev"] is None
    assert captured["model"] == "opus" and captured["now"] == day
    assert captured["analytics"].as_of == day   # no lookahead: analytics was built AS OF day

    assert row["skipped"] is False
    assert row["side"] == "long" and row["instrument"] == "TEST"
    assert row["cost_usd"] == pytest.approx(0.0123)
    assert row["model"] == "opus" and row["validator_outcome"] == "ok"

    # written in the exact shape data.load_journaled_theses(out_dir) reads
    loaded = D.load_journaled_theses(out_dir)
    assert len(loaded) == 1
    assert (loaded[0].side, loaded[0].entry_px, loaded[0].stop_px, loaded[0].confidence) == \
        ("long", 100.0, 95.0, 0.7)


def test_replay_day_flags_a_degraded_validator_outcome(tmp_path):
    from datetime import datetime, timezone
    spec = _tiny_spec()
    day = datetime(2026, 10, 1, 16, tzinfo=timezone.utc)

    def stub_write_thesis(spec_, analytics, intel, prev, model=None, now=None):
        t = _stub_thesis(spec_.id, now)
        return t.model_copy(update={"usage": t.usage.model_copy(update={"skipped_reason": "LLM call failed: boom"})})

    row = R.replay_day(spec, {"TEST": {"1d": _hourly(20), "1h": _hourly(20)}}, lambda: {}, day, "opus",
                       str(tmp_path / "theses"), write_thesis_fn=stub_write_thesis)
    assert row["validator_outcome"] == "degraded" and row["degraded_reason"] == "LLM call failed: boom"


# ── fetch_all_candles ────────────────────────────────────────────────────────

def test_fetch_all_candles_fetches_daily_for_every_coin_and_hourly_only_for_primaries():
    spec = DeskSpec(id="test2", name="Test", tagline="t", instruments=[
        Instrument(coin="PRIMARY", label="P", role="primary"),
        Instrument(coin="SECONDARY", label="S", role="secondary"),
    ])
    calls = []

    def fake_fetch_cached(coin, interval, count, cache_dir):
        calls.append((coin, interval, count))
        return []

    R.fetch_all_candles(spec, "/tmp/doesnt-matter", 5, fetch_cached=fake_fetch_cached)
    by_coin_interval = {(c, i) for c, i, _ in calls}
    assert ("PRIMARY", "1d") in by_coin_interval and ("SECONDARY", "1d") in by_coin_interval
    assert ("PRIMARY", "1h") in by_coin_interval
    assert ("SECONDARY", "1h") not in by_coin_interval   # hourly structure is primaries-only


# ── main(): the guard rails, offline ─────────────────────────────────────────

def test_main_refuses_the_fable_model_without_touching_the_network():
    assert R.main(["--model", "fable"]) == 2


def test_main_refuses_to_write_under_the_live_state_dir():
    with pytest.raises(ValueError):
        R.main(["--out-dir", os.path.join(R.LIVE_STATE_DIR, "strategies", "oil", "theses")])


def test_with_primary_swaps_the_primary_without_touching_the_live_spec():
    from services.strategies.desks import get_spec
    from services.thesis.evals.replay import with_primary
    live = get_spec("metals")   # silver primary since 2026-10-31; swap gold in to prove the copy
    gold = with_primary(live, "xyz:GOLD")
    assert [(i.coin, i.role) for i in gold.instruments] == [("xyz:SILVER", "secondary"), ("xyz:GOLD", "primary")]
    assert [(i.coin, i.role) for i in get_spec("metals").instruments][0] == ("xyz:SILVER", "primary")
    assert gold.id == live.id


def test_with_primary_refuses_a_coin_the_desk_does_not_list():
    import pytest
    from services.strategies.desks import get_spec
    from services.thesis.evals.replay import with_primary
    with pytest.raises(ValueError, match="not an instrument"):
        with_primary(get_spec("metals"), "xyz:BRENTOIL")
