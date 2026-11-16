"""The whole tick on fakes: the real rule, the real `pathiel.agents.bracket`,
a fake exchange that records every call. No network."""
from __future__ import annotations

import dataclasses
import json
import os

import pytest

from pathiel.agents import bracket
from pathiel.models.types import Candle
from services.fib_sleeve import rule, tick
from services.fib_sleeve.config import SleeveConfig, sleeve_dir
from services.fib_sleeve.store import ReadOnlyStore, Store
from services.fib_sleeve.tests import _scenario as S

OFF = SleeveConfig()
ON = dataclasses.replace(OFF, enabled=True)
LEVEL, STOP, TARGET = 1018.658, 1015.3223, 1049.658
SATURDAY = S.T0 + 5 * 24 * S.HOUR + 12 * S.HOUR


def make(ex=None, live=False, fills=(), eq=None, extra15=None, role=None, master=S.MASTER):
    ex = ex or S.FakeEx()
    hs = S.hours()
    b15 = S.quarters(hs) + (extra15 if extra15 is not None else [S.forming(1024.0)])
    deps = tick.Deps(
        ex=ex, bracket=bracket,
        fetch_candles=lambda coin, interval, count: list(hs if interval == "1h" else b15),
        fetch_fills=lambda user, start: [f for f in fills if f["time"] >= start],
        equity=eq or S.equity(), role=lambda user: role or {"role": "subAccount", "master": S.MASTER},
        master=lambda: master, store=Store(sleeve_dir(), read_only=not live))
    return ex, deps


def files():
    d = sleeve_dir()
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


# ── enabled = false ───────────────────────────────────────────────────────

def test_the_default_config_is_disabled_and_names_the_sub_account():
    assert OFF.enabled is False and OFF.account == S.SUB and OFF.coin == S.COIN
    assert OFF.variant_name == "zz2_with_L382_limit_atr1_T618_be75" and OFF.risk_frac == 0.06


def test_live_tick_with_enabled_false_reads_nothing_and_places_nothing():
    ex, deps = make(live=True)
    d = tick.run_tick(OFF, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "disabled"
    assert ex.calls == [] and ex.reads == [] and files() == []


# ── dry run ───────────────────────────────────────────────────────────────

def test_dry_run_prints_what_it_would_place_and_touches_nothing():
    ex, deps = make()
    d = tick.run_tick(OFF, live=False, now_ms=S.NOW, deps=deps)
    assert d["action"] == "would_place" and d["mode"] == "dry_run" and "enabled is false" in d["note"]
    b = d["bracket"]
    assert b["dry_run"] is True and b["placed"] is False and b["side"] == "long"
    assert (b["entry_px"], b["stop_px"], b["tp_px"]) == (LEVEL, STOP, TARGET)
    assert d["size"]["size"] == 0.31 and d["size"]["equity"] == 20.06
    assert ex.calls == [] and files() == []                     # nothing signed, nothing written


def test_dry_run_store_and_facade_refuse_writes():
    with pytest.raises(ReadOnlyStore):
        Store(sleeve_dir(), read_only=True).save({})
    with pytest.raises(ReadOnlyStore):
        Store(sleeve_dir(), read_only=True).append_log({})


def test_size_follows_the_sub_accounts_own_equity():
    _, deps = make(eq=S.equity(40.12))
    d = tick.run_tick(OFF, live=False, now_ms=S.NOW, deps=deps)
    assert d["size"]["size"] == 0.62 and d["size"]["risk_usd"] <= 0.06 * 40.12


# ── live, flat ────────────────────────────────────────────────────────────

def test_live_tick_rests_the_bracket_on_the_sub_account():
    ex, deps = make(live=True)
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "place"
    assert ("position", S.SUB, S.COIN) in ex.reads and ("orders", S.SUB, S.COIN) in ex.reads
    assert ("set_leverage", S.COIN, 20, False, True) in ex.calls
    placed = [c for c in ex.calls if c[0] == "bracket"]
    assert placed == [("bracket", S.COIN, True, 0.31, LEVEL, STOP, TARGET)]   # the target as tested: no 0.5% offset
    state = Store(sleeve_dir()).load()
    assert state == {"epoch_ms": S.NOW, "starting_equity_usd": 20.06, "kill": None}
    rows = [json.loads(x) for x in open(Store(sleeve_dir()).log_path)]
    assert len(rows) == 1 and rows[0]["action"] == "place" and rows[0]["account"] == S.SUB


def test_live_refuses_an_account_that_is_not_the_signers_sub_account():
    for role in ({"role": "user"}, {"role": "subAccount", "master": "0x" + "ab" * 20}, {"role": None}):
        ex, deps = make(live=True, role=role)
        d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
        assert d["action"] == "error" and "not a sub-account" in d["why"] and ex.calls == []


def test_first_live_tick_needs_a_flat_account():
    ex, deps = make(ex=S.FakeEx(position=0.31, orders=[S.stop_row(STOP, 0.31)]), live=True)
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "error" and "flat" in d["why"] and ex.calls == []


def test_failed_reads_place_nothing():
    ex = S.FakeEx()
    ex.position = None
    _, deps = make(ex=ex, live=True)
    assert tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)["why"].startswith("position read failed")
    ex2, deps2 = make(live=True, eq=S.equity(ok=False))
    assert "equity read failed" in tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps2)["why"]
    ex3, deps3 = make(live=True, extra15=[])
    deps3.fetch_candles = lambda coin, interval, count: []
    assert "candle read failed" in tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps3)["why"]
    assert ex.calls == ex2.calls == ex3.calls == []


def test_one_position_at_a_time_on_the_sub_account():
    other = [{"coin": "xyz:CL", "szi": 1.0, "entry_px": 60.0, "unrealized_pnl": 0.0}]
    ex, deps = make(live=True, eq=S.equity(positions=other))
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "blocked" and "xyz:CL" in d["why"] and not [c for c in ex.calls if c[0] == "bracket"]


def test_a_resting_entry_at_the_rules_level_is_left_alone():
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(ex=S.FakeEx(orders=[S.entry_row(LEVEL, 0.31)]), live=True)
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "resting" and ex.calls == []


def test_a_resting_entry_from_an_older_swing_is_cancelled_not_replaced_in_the_same_tick():
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(ex=S.FakeEx(orders=[S.entry_row(1005.0, 0.31)]), live=True)
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "cancel_entry" and d["why"] == "swing_changed"
    assert ex.calls == [("cancel", 10, S.COIN), ("cancel_stranded", S.COIN)]


def test_weekend_cancels_a_resting_entry_and_places_none():
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(ex=S.FakeEx(orders=[S.entry_row(LEVEL, 0.31)]), live=True)
    d = tick.run_tick(ON, live=True, now_ms=SATURDAY, deps=deps)
    assert d["action"] == "cancel_entry" and d["why"] == "weekend"
    ex2, deps2 = make(live=True)
    d2 = tick.run_tick(ON, live=True, now_ms=SATURDAY, deps=deps2)
    assert d2["action"] == "no_order" and d2["why"] == "weekend" and ex2.calls == []


def test_a_bracket_the_venue_half_refused_is_cancelled():
    class Half(S.FakeEx):
        def place_hl_bracket_order(self, *a, **k):
            self.calls.append(("bracket",) + a)
            return {"ok": False, "entry": {"ok": True}, "sl": {"ok": False, "error": "bad trigger"}, "tp": {"ok": True}}
    ex, deps = make(ex=Half(), live=True)
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "place_incomplete" and ex.calls[-1] == ("cancel_all", S.COIN)


# ── live, in a position ───────────────────────────────────────────────────

FILL_T = S.T0 + 31 * S.HOUR + 120_000       # filled two minutes into the 31:00 bar
POSITION = [{"coin": S.COIN, "szi": 0.31, "entry_px": 1018.66, "unrealized_pnl": 0.0}]


def in_position(extra15, now, orders=None, live=True):
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    orders = orders if orders is not None else [S.stop_row(1015.32, 0.31), S.tp_row(1049.66, 0.31)]
    ex = S.FakeEx(position=0.31, orders=orders, price=extra15[-1].c if extra15 else 1024.0)
    ex, deps = make(ex=ex, live=live, eq=S.equity(positions=POSITION), extra15=extra15,
                    fills=[S.fill(FILL_T, "B", 0.31, 1018.66, 0.0)])
    return ex, tick.run_tick(ON if live else OFF, live=live, now_ms=now, deps=deps)


def bar(k, h, c, l=None):
    t = S.T0 + 31 * S.HOUR + k * S.Q
    return Candle(t=t, o=c, h=h, l=l if l is not None else min(c, 1018.0), c=c, v=1)


def test_position_below_75_percent_holds():
    ex, d = in_position([bar(0, 1020, 1019), bar(1, 1041.8, 1040), bar(2, 1040, 1040)], S.T0 + 31 * S.HOUR + 2 * S.Q + 1)
    assert d["action"] == "hold" and ex.calls == []
    assert d["position"]["stop_px"] == 1015.32 and d["position"]["stop_covers"] == 0.31


def test_ratchet_moves_the_stop_to_the_entry_then_cancels_the_old_one():
    # 75% of the way from 1018.66 to 1049.66 is 1041.91
    ex, d = in_position([bar(0, 1020, 1019), bar(1, 1042.0, 1040), bar(2, 1040, 1040)], S.T0 + 31 * S.HOUR + 2 * S.Q + 1)
    assert d["action"] == "ratchet" and d["stop_px"] == 1018.66
    assert ex.calls == [("trigger", S.COIN, True, 0.31, 1018.66, "sl"), ("cancel", 11, S.COIN)]


def test_ratchet_ignores_the_fill_bar_and_the_forming_bar():
    now = S.T0 + 31 * S.HOUR + 2 * S.Q + 1
    ex, d = in_position([bar(0, 1045, 1019), bar(1, 1030, 1029), bar(2, 1046, 1045)], now)
    assert d["action"] == "hold" and ex.calls == []


def test_ratchet_dry_run_reports_and_signs_nothing():
    ex, d = in_position([bar(0, 1020, 1019), bar(1, 1042.0, 1040), bar(2, 1040, 1040)],
                        S.T0 + 31 * S.HOUR + 2 * S.Q + 1, live=False)
    assert d["action"] == "ratchet" and d["result"] == {"dry_run": True} and ex.calls == []


def test_weekend_closes_the_position_reduce_only_and_clears_its_orders():
    ex, d = in_position([S.forming(1024.0)], SATURDAY)
    assert d["action"] == "close" and d["why"] == "weekend"
    assert ex.calls == [("ioc", S.COIN, False, 0.31, True), ("cancel_all", S.COIN)]


def test_a_position_without_a_stop_is_closed():
    ex, d = in_position([S.forming(1024.0)], S.NOW, orders=[S.tp_row(1049.66, 0.31)])
    assert d["action"] == "close" and "no stop" in d["why"] and ex.calls[0] == ("ioc", S.COIN, False, 0.31, True)


# ── kill switch ───────────────────────────────────────────────────────────

def test_three_losses_latch_the_kill_switch_and_place_nothing():
    Store(sleeve_dir()).save({"epoch_ms": S.T0 - 10 * S.HOUR, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(live=True, fills=S.losing_trips(3, loss=0.1, start=S.T0))
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "killed" and d["kill_switch_tripped"] is True and "3 consecutive losses" in d["why"]
    assert not [c for c in ex.calls if c[0] in ("bracket", "set_leverage")]
    assert "3 consecutive losses" in Store(sleeve_dir()).load()["kill"]["reason"]      # logged and latched
    # the latch holds on the next tick even with no fills to read
    ex2, deps2 = make(live=True, fills=[])
    d2 = tick.run_tick(ON, live=True, now_ms=S.NOW + S.Q, deps=deps2)
    assert d2["action"] == "killed" and "kill_switch_tripped" not in d2 and ex2.calls == []


def test_twenty_percent_drawdown_of_the_starting_equity_kills():
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(live=True, eq=S.equity(16.04))            # 20.04% under the start
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "killed" and "starting equity" in d["why"] and ex.calls == []
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex2, deps2 = make(live=True, eq=S.equity(16.06))          # 19.9%: still trading
    assert tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps2)["action"] == "place"


def test_kill_switch_cancels_a_resting_entry_and_fills_before_the_start_do_not_count():
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06,
                              "kill": {"reason": "3 consecutive losses (limit 3)", "at_ms": S.T0}})
    ex, deps = make(ex=S.FakeEx(orders=[S.entry_row(LEVEL, 0.31)]), live=True)
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "cancel_entry" and d["why"].startswith("kill switch")
    Store(sleeve_dir()).save({"epoch_ms": S.T0 + 20 * S.HOUR, "starting_equity_usd": 20.06, "kill": None})
    ex2, deps2 = make(live=True, fills=S.losing_trips(3, loss=0.1, start=S.T0))   # before the epoch
    assert tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps2)["action"] == "place"


def test_dry_run_reports_a_tripped_switch_without_latching_it():
    Store(sleeve_dir()).save({"epoch_ms": S.T0 - 10 * S.HOUR, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(live=False, fills=S.losing_trips(3, loss=0.1, start=S.T0))
    d = tick.run_tick(OFF, live=False, now_ms=S.NOW, deps=deps)
    assert d["action"] == "killed" and Store(sleeve_dir()).load()["kill"] is None and ex.calls == []


def test_rule_constants_cover_the_history_a_tick_needs():
    assert rule.HOURS_WANTED >= 200 and rule.BARS_WANTED * 15 >= 10 * 24 * 60
