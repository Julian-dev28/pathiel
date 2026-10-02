"""copy_trade: mirror the leader's new positions, never the ones he already held.

Driven through `maybe_run` with a fake venue: the diff logic is the whole book,
and a leader read that fails must never look like a leader who went flat.
"""
from __future__ import annotations

import pytest

from pathiel.agents import copy_trade_live as ct
from pathiel.agents.rebalancer_owned import get_claims_registry

LEADER = ct.DEFAULT_LEADER


class FakeVenue:
    def __init__(self):
        self.lead = {"equity": 1_000_000.0, "positions": {}}
        self.orders = []
        self.closes = []
        self.levs = {}
        self.fail_close = False

    def leader_state(self, user):
        return self.lead

    def mid(self, coin):
        return 100.0

    def max_leverage(self, coin):
        return 10

    def set_leverage(self, coin, leverage):
        self.levs[coin] = leverage
        return True

    def market(self, coin, is_buy, size, mid, reduce_only=False):
        self.orders.append((coin, is_buy, round(size, 6), reduce_only))
        return {"ok": True}

    def size_for(self, coin, notional, mid):
        return notional / mid

    def close(self, coin):
        if self.fail_close:
            return False
        self.closes.append(coin)
        return True

    def hold(self, coin, szi, lev=3.0):
        if szi:
            self.lead["positions"][coin] = {"szi": szi, "px": 100.0, "lev": lev}
        else:
            self.lead["positions"].pop(coin, None)


def cfg(mult=1.0, enabled=True):
    return {"copy_trade": {"enabled": enabled, "shadow_only": False, "leader": LEADER,
                           "leverage_mult": mult, "sleeve_frac": 0.5}}


def ours(coin, szi, entry=100.0):
    return [{"position": {"coin": coin, "szi": str(szi), "entryPx": str(entry),
                          "unrealizedPnl": "0"}}]


@pytest.fixture
def run(tmp_path):
    paths = {"state_path": str(tmp_path / "s.json"),
             "equity_log_path": str(tmp_path / "e.jsonl")}

    def _run(venue, config=None, positions=None, **kw):
        args = {"equity": 1000.0, "dex_available": {"": 1000.0, "xyz": 0.0},
                "daily_pnl": 0.0, "daily_loss_limit": -100.0, **kw}
        return ct.maybe_run(config or cfg(), positions, args.pop("equity"),
                            args.pop("dex_available"), args.pop("daily_pnl"),
                            args.pop("daily_loss_limit"), venue=venue, now_ms=0,
                            **args, **paths)
    _run.state_path = paths["state_path"]
    return _run


def test_positions_held_at_first_sight_are_never_copied(run):
    v = FakeVenue()
    v.hold("ETH", 1000.0)
    run(v)
    v.hold("ETH", 2000.0)                    # he adds to a pre-existing position
    out = run(v)
    assert not v.orders and not out["opened"]


def test_a_new_open_is_sized_at_mult_times_his_exposure_on_the_sleeve(run):
    v = FakeVenue()
    run(v)
    v.hold("BTC", 1000.0)                    # $100k on $1M equity = 0.1x
    out = run(v, cfg(mult=2.0))
    assert out["opened"] == ["BTC"]
    # 2 x 0.1 x $1000 equity x 0.5 sleeve = $100 = 1 BTC at $100
    assert v.orders == [("BTC", True, 1.0, False)]
    assert v.levs["BTC"] == 6                # his 3x setting x 2, under max 10


def test_the_multiplier_is_clamped_to_what_the_backtest_covered(run):
    v = FakeVenue()
    run(v)
    v.hold("BTC", 1000.0)
    run(v, cfg(mult=50.0))
    assert v.orders[0][2] == pytest.approx(ct.MAX_MULT * 0.5)


def test_a_partial_close_takes_the_same_fraction_off_ours(run):
    v = FakeVenue()
    run(v)
    v.hold("BTC", 1000.0)
    run(v)
    v.hold("BTC", 250.0)                     # he takes 75% off
    out = run(v, positions=ours("BTC", 2.0))
    assert out["reduced"] == ["BTC"]
    assert v.orders[-1] == ("BTC", False, 1.5, True)


def test_his_flat_is_our_flat(run):
    v = FakeVenue()
    run(v)
    v.hold("BTC", 1000.0)
    run(v)
    v.hold("BTC", 0)
    out = run(v, positions=ours("BTC", 0.5))
    assert out["closed"] == ["BTC"] and v.closes == ["BTC"]
    assert ct.owned_coins(run.state_path) == set()


def test_a_failed_leader_read_changes_nothing(run):
    v = FakeVenue()
    run(v)
    v.hold("BTC", 1000.0)
    run(v)
    v.lead = None
    out = run(v, positions=ours("BTC", 0.5))
    assert out["skipped"] == {"all": "leader read failed"}
    assert not v.closes


def test_a_failed_close_is_retried_next_cycle(run):
    v = FakeVenue()
    run(v)
    v.hold("BTC", 1000.0)
    run(v)
    v.hold("BTC", 0)
    v.fail_close = True
    run(v, positions=ours("BTC", 0.5))
    v.fail_close = False
    out = run(v, positions=ours("BTC", 0.5))
    assert out["closed"] == ["BTC"]


def test_the_daily_loss_gate_blocks_the_open_and_the_whole_trip(run):
    v = FakeVenue()
    run(v)
    v.hold("BTC", 1000.0)
    run(v, daily_pnl=-200.0)
    v.hold("BTC", 2000.0)                    # gate is clear now, trip already missed
    out = run(v)
    assert not v.orders and not out["opened"]


def test_disabled_still_mirrors_exits(run):
    v = FakeVenue()
    run(v)
    v.hold("BTC", 1000.0)
    run(v)
    v.hold("BTC", 0)
    out = run(v, cfg(enabled=False), positions=ours("BTC", 0.5))
    assert out["closed"] == ["BTC"]


def test_a_coin_another_book_holds_is_skipped(run):
    v = FakeVenue()
    run(v)
    get_claims_registry().claim("BTC", "drawdown_ladder")
    v.hold("BTC", 1000.0)
    out = run(v)
    assert "claimed by drawdown_ladder" in out["skipped"]["BTC"] and not v.orders


def test_a_copy_under_the_exchange_minimum_is_skipped(run):
    v = FakeVenue()
    run(v)
    v.hold("BTC", 10.0)                      # $1k on $1M -> $0.50 for us
    out = run(v)
    assert "minimum" in out["skipped"]["BTC"] and not v.orders


def test_the_grader_demotes_past_half_the_sleeve():
    rows = [{"pnl": 0, "sleeve": 100}, {"pnl": -51, "sleeve": 100}]
    assert ct.mtm_decision(rows, True)["action"] == "demote"
    assert ct.mtm_decision(rows[:1], True)["action"] == "none"
