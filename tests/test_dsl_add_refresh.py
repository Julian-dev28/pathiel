"""P0 bug (ALPHA-QUEUE, found 2026-07-13, fixed 2026-07-17): DSL tracker
entry_px went stale on position ADDS.

Evidence (SKHY, manual short + add): fills avg entry 158.73, close 158.90 =
-0.1% spot realized. The tracker kept the FIRST fill entry (159.83), read
+0.64% spot / +6.44% ROE, showed a green win, and ran its profit floor off
the wrong basis. Symmetric risk: an add above tracked entry delays the stop
beyond intended. Fix: rehydrate_from_exchange tracks last-seen size and, on
any material size increase, refreshes entry_px to the exchange's average
and clamps peak_px to the new basis.
"""
import time

import pytest

import hermes_trader.agents.dsl_exit as dsl


def _pos(coin, szi, entry):
    return {"position": {"coin": coin, "szi": str(szi), "entryPx": str(entry),
                         "leverage": {"value": 5}}}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(dsl, "load_state", lambda force=False: None)
    monkeypatch.setattr(dsl, "_save_state", lambda: None)
    dsl._active_positions.clear()
    yield
    dsl._active_positions.clear()


def _track(coin, side, entry, size, peak=None, lev=5):
    t = dsl.DSLTracker(coin, side, entry, time.time(), dsl.ExitPolicy(), leverage=lev)
    t.size = size
    if peak is not None:
        t.peak_px = peak
    dsl._active_positions[f"{coin}_{side}"] = t
    return t


def test_skhy_replay_short_add_refreshes_entry_basis():
    # SKHY 2026-07-13 replay: short first fill @159.83; a manual add moved
    # the true average to 158.73. Old code kept 159.83 and read a +0.64%
    # phantom spot profit at the 158.90 close; the truth was -0.1%.
    t = _track("xyz:SKHY", "short", 159.83, 100.0)
    dsl.rehydrate_from_exchange([_pos("xyz:SKHY", -200, 158.73)])
    assert t.entry_px == 158.73 and t.size == 200.0
    assert t.peak_px == 158.73          # short peak clamped to the new basis
    assert t._unrealized_pct(158.90) < 0   # the phantom win is gone


def test_long_add_above_entry_keeps_true_peak():
    t = _track("BTC", "long", 100.0, 10.0, peak=105.0)
    dsl.rehydrate_from_exchange([_pos("BTC", 20, 102.0)])
    assert t.entry_px == 102.0 and t.size == 20.0
    assert t.peak_px == 105.0           # real price extreme survives


def test_long_add_with_peak_below_new_entry_clamps_peak():
    t = _track("ETH", "long", 100.0, 10.0, peak=100.5)
    dsl.rehydrate_from_exchange([_pos("ETH", 20, 103.0)])
    assert t.peak_px == 103.0           # never a negative profit range


def test_partial_close_updates_size_not_entry():
    t = _track("SOL", "long", 50.0, 10.0, peak=60.0)
    dsl.rehydrate_from_exchange([_pos("SOL", 5, 50.0)])
    assert t.size == 5.0 and t.entry_px == 50.0 and t.peak_px == 60.0


def test_legacy_unknown_size_adopted_without_entry_refresh():
    t = _track("DOGE", "long", 0.1, 0.0)   # size 0 = pre-fix state file
    dsl.rehydrate_from_exchange([_pos("DOGE", 1000, 0.099)])
    assert t.size == 1000.0
    assert t.entry_px == 0.1               # no add proven — basis untouched


def test_unchanged_size_is_a_noop():
    t = _track("XRP", "short", 2.0, 100.0, peak=1.9)
    dsl.rehydrate_from_exchange([_pos("XRP", -100, 2.0)])
    assert t.entry_px == 2.0 and t.peak_px == 1.9 and t.size == 100.0


def test_synthesized_tracker_knows_its_size():
    dsl.rehydrate_from_exchange([_pos("NEW", 33, 1.5)])
    t = dsl._active_positions["NEW_long"]
    assert t.size == 33.0 and t.entry_px == 1.5


def test_size_survives_state_roundtrip():
    t = dsl.DSLTracker("BTC", "long", 100.0, 123.0, dsl.ExitPolicy(), leverage=3)
    t.size = 42.5
    assert dsl._tracker_from_dict(dsl._tracker_to_dict(t)).size == 42.5


def test_legacy_state_dict_defaults_size_zero():
    d = dsl._tracker_to_dict(dsl.DSLTracker("ETH", "short", 10.0, 1.0, dsl.ExitPolicy()))
    d.pop("size")
    assert dsl._tracker_from_dict(d).size == 0.0
