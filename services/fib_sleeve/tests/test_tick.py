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

OFF = SleeveConfig()                                 # swing="range24", the live default
ON = dataclasses.replace(OFF, enabled=True)
LEVEL, STOP, TARGET = 1018.658, 1015.3223, 1038.932   # range24 and zigzag name the same swing on this fixture
SATURDAY = S.T0 + 5 * 24 * S.HOUR + 12 * S.HOUR


def make(ex=None, live=False, fills=(), eq=None, extra15=None, role=None, master=S.MASTER, hs=None):
    ex = ex or S.FakeEx()
    hs = hs if hs is not None else S.hours()
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
    assert OFF.swing == "range24"
    assert OFF.variant_name == "range24_with_L382_limit_atr1_T272_be50" and OFF.risk_frac == 0.06


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


def test_a_dry_run_cancel_signs_and_calls_nothing_and_shows_the_fresh_plan():
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(ex=S.FakeEx(orders=[S.entry_row(1005.0, 0.31)]), live=False)
    d = tick.run_tick(ON, live=False, now_ms=S.NOW, deps=deps)
    assert d["action"] == "cancel_entry" and d["why"] == "swing_changed"
    assert d["result"] == {"dry_run": True} and ex.calls == []
    assert d["plan"]["entry"] == LEVEL and round(d["plan"]["stop"], 4) == STOP and d["plan"]["target"] == TARGET


# ── a stale entry is cancelled AND replaced in the same tick ────────────────
# The bug (2026-11-17 07:51Z-08:59Z): a tick cancelled a stale entry and
# returned; the new one was only placed on the NEXT tick, 15+ minutes later
# (longer when ticks stalled), leaving a gap with nothing resting. The fix:
# cancel, confirm it took, place the new one - one tick, one log line.
#
# `S.FakeEx` never mutates its own `self.orders`/`self.position`, so a plain
# fake would make `place_bracket`'s OWN "an entry already rests, cancel
# first" guard refuse every replacement (it re-reads resting orders itself).
# `Mutating` makes `cancel_orders` and `place_hl_bracket_order` behave like
# the venue actually does: a cancel that succeeds really removes the order,
# a placed bracket really adds one - so the fresh read a REAL replacement
# depends on is real in the test too.

class Mutating(S.FakeEx):
    def cancel_orders(self, oid, coin=None, asset_idx=None):
        self.calls.append(("cancel", oid, coin))
        self.orders = [o for o in self.orders if int(o["oid"]) != int(oid)]
        return {"ok": True}

    def place_hl_bracket_order(self, coin, is_buy, size, entry_px, stop_px, tp_px):
        self.calls.append(("bracket", coin, is_buy, size, entry_px, stop_px, tp_px))
        self.orders.append(S.entry_row(entry_px, size, side="B" if is_buy else "A", oid=20))
        return {"ok": True, "entry": {"ok": True, "order_id": 20}, "sl": {"ok": True}, "tp": {"ok": True}}


def test_a_stale_resting_entry_is_cancelled_and_replaced_in_the_same_tick():
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(ex=Mutating(orders=[S.entry_row(1005.0, 0.31)]), live=True)
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "replace_entry" and d["why"].startswith("swing_changed")
    assert d["old"] == {"px": 1005.0, "side": "long"}
    assert d["new"] == {"px": LEVEL, "side": "long"}
    assert ("cancel", 10, S.COIN) in ex.calls and ("cancel_stranded", S.COIN) in ex.calls
    placed = [c for c in ex.calls if c[0] == "bracket"]
    assert placed == [("bracket", S.COIN, True, 0.31, LEVEL, STOP, TARGET)]   # the SAME tick places this
    # one log line for both actions, not two
    rows = [json.loads(x) for x in open(Store(sleeve_dir()).log_path)]
    assert len(rows) == 1 and rows[0]["action"] == "replace_entry"


class FillRace(S.FakeEx):
    """The old entry fills between the position read at the top of the
    tick and the cancel: the venue refuses the cancel (nothing left to
    cancel), and a second position read (the tick's own re-check) finds
    the fill."""

    def __init__(self, *a, filled_size: float = 0.31, **k):
        super().__init__(*a, **k)
        self._pos_reads = 0
        self._filled_size = filled_size

    def _position_size_on_coin(self, user, coin):
        self.reads.append(("position", user, coin))
        self._pos_reads += 1
        return self.position if self._pos_reads == 1 else self._filled_size

    def cancel_orders(self, oid, coin=None, asset_idx=None):
        self.calls.append(("cancel", oid, coin))
        return {"ok": False, "error": "Order was never placed, already canceled, or filled"}


def test_the_old_entry_filling_before_the_cancel_took_places_nothing_new():
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(ex=FillRace(orders=[S.entry_row(1005.0, 0.31)]), live=True)
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "entry_filled" and "nothing new placed" in d["why"]
    assert ("cancel", 10, S.COIN) in ex.calls
    assert not [c for c in ex.calls if c[0] == "bracket"]                    # never a second entry


class CancelRefused(S.FakeEx):
    """The venue refuses the cancel outright (not a fill): the order still
    rests, position stays flat."""

    def cancel_orders(self, oid, coin=None, asset_idx=None):
        self.calls.append(("cancel", oid, coin))
        return {"ok": False, "error": "HLRequestError: 503 Service Unavailable"}


def test_a_cancel_the_venue_refuses_places_nothing_and_reports_the_error_verbatim():
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(ex=CancelRefused(orders=[S.entry_row(1005.0, 0.31)]), live=True)
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "cancel_failed"
    assert d["why"] == "cancel refused, the old entry still rests: HLRequestError: 503 Service Unavailable"
    assert not [c for c in ex.calls if c[0] == "bracket"]


# ── idempotency: a rerun never double-places ─────────────────────────────

def test_a_rerun_after_a_successful_replace_is_a_no_op():
    """Not a replay of the SAME call (nothing here remembers having run) -
    a rerun is idempotent because every tick reads the venue fresh: once
    the new entry rests, the next tick (a retry, or just the next cron
    minute) sees it resting at the rule's own level and does nothing."""
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(ex=Mutating(orders=[S.entry_row(1005.0, 0.31)]), live=True)
    d1 = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d1["action"] == "replace_entry"
    ex2, deps2 = make(ex=Mutating(orders=list(ex.orders)), live=True)   # the venue's real post-tick state
    d2 = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps2)
    assert d2["action"] == "resting" and ex2.calls == []


def test_place_bracket_itself_refuses_a_double_entry_if_the_cancel_has_not_cleared_yet():
    """Defense in depth: even if cancel_orders reports ok, place_bracket
    re-reads resting orders itself before placing - if the stale entry is
    somehow still there (a slow cancel, eventual consistency on the venue
    side), it refuses rather than resting two entries side by side."""
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(ex=S.FakeEx(orders=[S.entry_row(1005.0, 0.31)]), live=True)   # never actually clears
    d = tick.run_tick(ON, live=True, now_ms=S.NOW, deps=deps)
    assert d["action"] == "cancel_entry" and "no replacement" in d["why"] and "already resting" in d["why"]
    assert not [c for c in ex.calls if c[0] == "bracket"]


# ── a resting entry from before a swing-mode change ─────────────────────────
# `_divergent_hours()`: the zigzag's original up-leg (origin 999.5, extreme
# 1030.5, as `_scenario.hours()`), then 13 more mildly-declining hours (no
# 2xATR reversal, so the zigzag's confirmed pivot never changes). By hour 44
# range24's last-24h window has slid onto a slightly different low (999.8),
# naming a slightly different 38.2% level - the entry's own (zigzag) level
# still rests, under the CURRENT default (range24).

def _divergent_hours(extra_hours=13):
    out = S.hours()[:31]
    px = out[-1].c
    for i in range(31, 31 + extra_hours):
        o = px
        px -= 0.25
        out.append(Candle(t=S.T0 + i * S.HOUR, o=o, h=o + 0.05, l=px - 0.05, c=px, v=1))
    return out


ZZ_LEVEL = 1018.658          # the resting entry's price: the zigzag swing's 38.2% level, unchanged since hour 31


def test_a_resting_entry_from_the_other_swing_mode_is_managed_not_cancelled():
    hs = _divergent_hours(13)                              # 44 hours: range24's level has drifted to 1018.7726
    now = S.T0 + 44 * S.HOUR + 60_000
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(ex=S.FakeEx(orders=[S.entry_row(ZZ_LEVEL, 0.31)]), live=True, hs=hs, extra15=[])
    d = tick.run_tick(ON, live=True, now_ms=now, deps=deps)
    assert d["action"] == "resting" and d["resting_entry_swing_mode"] == "zigzag" and ex.calls == []


def test_a_resting_entry_from_the_other_mode_is_cancelled_once_its_own_mode_says_so():
    """Keep ticking: once the ORIGINAL (zigzag) swing's own level is spent
    too, the entry is cancelled - the mode flip alone never cancels it, but
    its own mode eventually does."""
    hs = _divergent_hours(23)                              # 54 hours: the decline has now traded through 1018.658
    now = S.T0 + 54 * S.HOUR + 60_000
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    ex, deps = make(ex=S.FakeEx(orders=[S.entry_row(ZZ_LEVEL, 0.31)]), live=True, hs=hs, extra15=[])
    d = tick.run_tick(ON, live=True, now_ms=now, deps=deps)
    assert d["action"] == "cancel_entry"
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


def in_position(extra15, now, orders=None, live=True, cfg=None):
    Store(sleeve_dir()).save({"epoch_ms": S.T0, "starting_equity_usd": 20.06, "kill": None})
    orders = orders if orders is not None else [S.stop_row(1015.32, 0.31), S.tp_row(1049.66, 0.31)]
    ex = S.FakeEx(position=0.31, orders=orders, price=extra15[-1].c if extra15 else 1024.0)
    cfg = cfg if cfg is not None else (ON if live else OFF)
    ex, deps = make(ex=ex, live=live, eq=S.equity(positions=POSITION), extra15=extra15,
                    fills=[S.fill(FILL_T, "B", 0.31, 1018.66, 0.0)])
    return ex, tick.run_tick(cfg, live=live, now_ms=now, deps=deps)


def bar(k, h, c, l=None):
    t = S.T0 + 31 * S.HOUR + k * S.Q
    return Candle(t=t, o=c, h=h, l=l if l is not None else min(c, 1018.0), c=c, v=1)


# In a position, cfg.swing is never read (the stop/target it manages are
# whatever already rests on the venue - the tp_row fixture's 1049.66, not
# cfg.target), and the break-even ratchet never reads the swing either.
# Only cfg.ratchet's threshold matters here. The resting tp_row stays
# 1049.66 throughout: be75 pins the ORIGINAL zigzag row's 75% point
# (1041.91); the untagged tests below exercise CFG's own default, be50
# (its 50% point, 1034.16).
BE75 = dataclasses.replace(ON, ratchet="be75")


def test_position_below_75_percent_holds():
    ex, d = in_position([bar(0, 1020, 1019), bar(1, 1041.8, 1040), bar(2, 1040, 1040)],
                        S.T0 + 31 * S.HOUR + 2 * S.Q + 1, cfg=BE75)
    assert d["action"] == "hold" and ex.calls == []
    assert d["position"]["stop_px"] == 1015.32 and d["position"]["stop_covers"] == 0.31


def test_ratchet_moves_the_stop_to_the_entry_then_cancels_the_old_one():
    # 75% of the way from 1018.66 to 1049.66 is 1041.91: past be50's (default) and be75's trigger alike
    ex, d = in_position([bar(0, 1020, 1019), bar(1, 1042.0, 1040), bar(2, 1040, 1040)], S.T0 + 31 * S.HOUR + 2 * S.Q + 1)
    assert d["action"] == "ratchet" and d["stop_px"] == 1018.66
    assert ex.calls == [("trigger", S.COIN, True, 0.31, 1018.66, "sl"), ("cancel", 11, S.COIN)]


def test_position_below_50_percent_holds_under_the_default():
    # 50% of the way from 1018.66 to 1049.66 is 1034.16 (CFG's default, be50)
    ex, d = in_position([bar(0, 1020, 1019), bar(1, 1033.8, 1033), bar(2, 1033, 1033)], S.T0 + 31 * S.HOUR + 2 * S.Q + 1)
    assert d["action"] == "hold" and ex.calls == []


def test_default_ratchet_moves_the_stop_to_the_entry_at_50_percent():
    ex, d = in_position([bar(0, 1020, 1019), bar(1, 1034.2, 1033), bar(2, 1033, 1033)], S.T0 + 31 * S.HOUR + 2 * S.Q + 1)
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


def test_a_swing_mode_switch_never_touches_an_open_position():
    """cfg.swing is never read while a position is open (its stop/target
    are whatever already rests on the venue, and the break-even ratchet
    never reads the swing either): flipping ONLY `swing`, everything else
    equal, gives the exact same decision - for a bar that holds and one
    that ratchets."""
    now = S.T0 + 31 * S.HOUR + 2 * S.Q + 1
    other_swing = dataclasses.replace(ON, swing="zigzag")
    for bars in ([bar(0, 1020, 1019), bar(1, 1030.0, 1029), bar(2, 1029, 1029)],       # 36.6%: hold
                 [bar(0, 1020, 1019), bar(1, 1042.0, 1040), bar(2, 1040, 1040)]):      # 75.3%: ratchet
        _, d_default = in_position(bars, now)
        _, d_other = in_position(bars, now, cfg=other_swing)
        assert d_default["action"] == d_other["action"]
        assert d_default.get("stop_px") == d_other.get("stop_px")


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
