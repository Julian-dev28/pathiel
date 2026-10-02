"""copycat: mirror up to 10 leaderboard wallets, one net position per coin.

Driven through `maybe_run` with a fake venue, same shape as test_copy_trade.py.
Claims go through the REAL rebalancer_owned registry and its real
_ACTIVE_CLAIM_BOOKS, so if "copycat" ever drops out of that set the open
tests here fail with "claim refused", exactly as production would.
"""
from __future__ import annotations

import pytest

from pathiel.agents import copycat_live as cc
from pathiel.agents import rebalancer_owned as ro
from pathiel.agents.atomic_io import read_json
from pathiel.agents.rebalancer_owned import get_claims_registry

LEADER1 = cc.DEFAULT_LEADERS[0]
LEADER2 = cc.DEFAULT_LEADERS[1]


@pytest.fixture(autouse=True)
def _fresh_claims_registry():
    ro._claims_registry = None
    yield
    ro._claims_registry = None


class FakeVenue:
    def __init__(self):
        self.leads = {}            # leader -> {"equity_perp_usdc","spot_usdc","positions"} or None
        self.extras = {}           # leader -> {"spot_other_usd","staked_hype_usd"} or None
        self.orders = []
        self.closes = []
        self.levs = {}             # coin -> {"leverage","is_cross"}
        self.fail_close = False
        self.mids = {}
        self.max_levs = {}
        self.force_isolated = set()

    def leader_state(self, user):
        return self.leads.get(user)

    def leader_extras(self, user):
        return self.extras.get(user)

    def mid(self, coin):
        return self.mids.get(coin, 100.0)

    def max_leverage(self, coin):
        return self.max_levs.get(coin, 50)

    def set_leverage(self, coin, leverage, is_cross):
        applied = False if coin in self.force_isolated else is_cross
        self.levs[coin] = {"leverage": leverage, "is_cross": applied}
        return {"ok": True, "is_cross": applied}

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

    def hold(self, leader, coin, szi, entry=100.0, lev=3.0,
             equity_perp=1_000_000.0, spot=0.0):
        lead = self.leads.setdefault(
            leader, {"equity_perp_usdc": equity_perp, "spot_usdc": spot, "positions": {}})
        lead["equity_perp_usdc"] = equity_perp
        lead["spot_usdc"] = spot
        if szi:
            lead["positions"][coin] = {"szi": szi, "entry_px": entry, "lev": lev}
        else:
            lead["positions"].pop(coin, None)


def cfg(**overrides):
    # sizing_mode pinned to "proportional" (the book's default is now
    # "fixed_margin" -- see the new sizing-mode tests below) so every test
    # in this file that was written against the proportional formula keeps
    # its original numbers.
    base = {"enabled": True, "shadow_only": False, "leaders": [LEADER1, LEADER2],
            "sleeve_frac": 0.5, "max_position_leverage": 50, "leverage_mode": "max",
            "margin_mode": "isolated", "include_spot_staked": False,
            "only_new_positions": False, "max_worse_entry_pct": 10.0,
            "min_order_bump": True, "sizing_mode": "proportional", "size_multiplier": 1.0}
    base.update(overrides)
    return {"copycat": base}


def ours(coin, szi, entry=100.0):
    return [{"position": {"coin": coin, "szi": str(szi), "entryPx": str(entry),
                          "unrealizedPnl": "0"}}]


@pytest.fixture
def run(tmp_path):
    paths = {"state_path": str(tmp_path / "s.json"), "equity_log_path": str(tmp_path / "e.jsonl")}

    def _run(venue, config=None, positions=None, **kw):
        args = {"equity": 1000.0, "dex_available": {"": 1000.0, "xyz": 0.0},
                "daily_pnl": 0.0, "daily_loss_limit": -100.0, **kw}
        return cc.maybe_run(config or cfg(), positions, args.pop("equity"),
                            args.pop("dex_available"), args.pop("daily_pnl"),
                            args.pop("daily_loss_limit"), venue=venue, now_ms=0,
                            **args, **paths)
    _run.state_path = paths["state_path"]
    _run.equity_log_path = paths["equity_log_path"]
    return _run


# ── settings() ────────────────────────────────────────────────────────────────

def test_settings_dedupes_caps_and_clamps_bad_enums_to_defaults():
    raw = {"leaders": ["0xAAA", "0xaaa", "0xbbb"] + [f"0x{i}" for i in range(20)],
           "sleeve_frac": 5.0, "max_position_leverage": 999, "leverage_mode": "yolo",
           "margin_mode": "???", "max_worse_entry_pct": -3}
    s = cc.settings({"copycat": raw})
    assert len(s["leaders"]) == cc.MAX_LEADERS
    assert len(set(s["leaders"])) == len(s["leaders"])            # deduped
    assert s["leaders"][:2] == ["0xaaa", "0xbbb"]                  # lowercased, first-seen order
    assert s["sleeve_frac"] == 1.0                                 # clamped to [0,1]
    assert s["max_position_leverage"] == 50                        # clamped to [1,50]
    assert s["leverage_mode"] == "max"                             # bad enum -> default
    assert s["margin_mode"] == "isolated"                          # bad enum -> default
    assert s["max_worse_entry_pct"] == 0.0                         # clamped to [0,100]


def test_settings_defaults_to_the_ten_leaderboard_wallets_when_missing():
    s = cc.settings({})
    assert s["leaders"] == cc.DEFAULT_LEADERS
    assert s["enabled"] is False
    assert s["shadow_only"] is False
    assert s["sleeve_frac"] == cc.DEFAULT_SLEEVE_FRAC
    assert s["max_position_leverage"] == cc.DEFAULT_MAX_POSITION_LEVERAGE
    assert s["leverage_mode"] == cc.DEFAULT_LEVERAGE_MODE
    assert s["margin_mode"] == cc.DEFAULT_MARGIN_MODE
    assert s["only_new_positions"] is False
    assert s["min_order_bump"] is True


# ── baseline ─────────────────────────────────────────────────────────────────

def test_baseline_only_new_positions_true_ignores_what_he_already_holds(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0)
    run(v, cfg(leaders=[LEADER1], only_new_positions=True))
    v.hold(LEADER1, "BTC", 2000.0)              # adds to the pre-existing position
    out = run(v, cfg(leaders=[LEADER1], only_new_positions=True))
    assert not v.orders and not out["opened"]


def test_baseline_only_new_positions_false_copies_his_existing_book_at_first_sight(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)      # $100k notional on $1M equity
    out = run(v, cfg(leaders=[LEADER1], only_new_positions=False))
    assert out["opened"] == ["BTC"]
    # allocation = 1000 equity * 0.5 sleeve / 1 leader = 500
    # his_added_ntl = 1000 * 100 = 100000; ntl = 100000/1e6*500 = 50; size = 50/100 mid = 0.5
    assert v.orders == [("BTC", True, 0.5, False)]


# ── max_worse_entry_pct ──────────────────────────────────────────────────────

def test_long_open_skipped_when_mid_is_worse_than_entry_and_retried_when_price_returns(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    v.mids["BTC"] = 115.0                             # >10% above his entry for a long
    out = run(v, cfg(leaders=[LEADER1]), equity=100_000.0)
    assert "worse than entry" in out["skipped"]["BTC"]
    assert not v.orders

    v.mids["BTC"] = 105.0                             # back within 10%
    out = run(v, cfg(leaders=[LEADER1]), equity=100_000.0)
    assert out["opened"] == ["BTC"]


def test_short_open_skipped_when_mid_is_worse_than_entry_and_retried_when_price_returns(run):
    v = FakeVenue()
    v.hold(LEADER1, "ETH", -10.0, entry=100.0)
    v.mids["ETH"] = 85.0                               # >10% below his entry for a short
    out = run(v, cfg(leaders=[LEADER1]), equity=100_000.0)
    assert "worse than entry" in out["skipped"]["ETH"]
    assert not v.orders

    v.mids["ETH"] = 95.0
    out = run(v, cfg(leaders=[LEADER1]), equity=100_000.0)
    assert out["opened"] == ["ETH"]


# ── leverage_mode ────────────────────────────────────────────────────────────

def test_leverage_mode_max_opens_at_the_coin_max_capped_by_max_position_leverage(run):
    v = FakeVenue()
    v.max_levs["BTC"] = 20
    v.hold(LEADER1, "BTC", 10.0, entry=100.0, lev=3.0)
    out = run(v, cfg(leaders=[LEADER1], leverage_mode="max", max_position_leverage=50),
             equity=100_000.0)
    assert out["opened"] == ["BTC"]
    assert v.levs["BTC"]["leverage"] == 20


def test_leverage_mode_match_uses_the_leaders_leverage_ceiled_and_capped(run):
    v = FakeVenue()
    v.max_levs["BTC"] = 20
    v.hold(LEADER1, "BTC", 10.0, entry=100.0, lev=7.2)
    out = run(v, cfg(leaders=[LEADER1], leverage_mode="match", max_position_leverage=50),
             equity=100_000.0)
    assert out["opened"] == ["BTC"]
    assert v.levs["BTC"]["leverage"] == 8               # ceil(7.2) under coin max 20 and cap 50


def test_max_position_leverage_caps_match_mode_below_the_leaders_own_leverage(run):
    v = FakeVenue()
    v.max_levs["BTC"] = 40
    v.hold(LEADER1, "BTC", 10.0, entry=100.0, lev=30.0)
    out = run(v, cfg(leaders=[LEADER1], leverage_mode="match", max_position_leverage=10),
             equity=100_000.0)
    assert out["opened"] == ["BTC"]
    assert v.levs["BTC"]["leverage"] == 10


# ── margin_mode ──────────────────────────────────────────────────────────────

def test_margin_mode_isolated_requests_is_cross_false(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1], margin_mode="isolated"), equity=100_000.0)
    assert v.levs["BTC"]["is_cross"] is False


def test_margin_mode_cross_requests_is_cross_true(run):
    v = FakeVenue()
    v.hold(LEADER1, "ETH", 10.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1], margin_mode="cross"), equity=100_000.0)
    assert v.levs["ETH"]["is_cross"] is True


def test_isolated_only_market_forces_is_cross_false_regardless_of_request(run):
    v = FakeVenue()
    v.force_isolated = {"XRP"}
    v.hold(LEADER1, "XRP", 10.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1], margin_mode="cross"), equity=100_000.0)
    assert v.levs["XRP"]["is_cross"] is False
    assert cc.owned_coins(run.state_path) == {"XRP"}


# ── include_spot_staked ──────────────────────────────────────────────────────

def test_include_spot_staked_false_sizes_off_perp_plus_spot_usdc_only(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 10.0, entry=100.0, equity_perp=900_000.0, spot=100_000.0)
    out = run(v, cfg(leaders=[LEADER1], include_spot_staked=False), equity=100_000.0)
    assert out["opened"] == ["BTC"]
    assert v.orders[0][2] == pytest.approx(0.5)


def test_include_spot_staked_true_adds_extras_to_his_equity_and_shrinks_the_size(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 10.0, entry=100.0, equity_perp=900_000.0, spot=100_000.0)
    v.extras[LEADER1] = {"spot_other_usd": 500_000.0, "staked_hype_usd": 500_000.0}
    out = run(v, cfg(leaders=[LEADER1], include_spot_staked=True), equity=100_000.0)
    assert out["opened"] == ["BTC"]
    assert v.orders[0][2] == pytest.approx(0.25)        # his equity doubled -> half the size


def test_include_spot_staked_true_skips_the_leader_when_extras_read_fails(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    v.extras[LEADER1] = None
    out = run(v, cfg(leaders=[LEADER1], include_spot_staked=True), equity=100_000.0)
    assert out["skipped"].get(f"leader:{LEADER1}") == "extras read failed"
    assert not v.orders


# ── multi-leader ownership ───────────────────────────────────────────────────

def test_two_leaders_on_the_same_coin_first_in_config_order_owns_it(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    v.hold(LEADER2, "BTC", 20.0, entry=100.0)
    out = run(v, cfg(leaders=[LEADER1, LEADER2]), equity=100_000.0)
    assert out["opened"] == ["BTC"]
    assert out["skipped"]["BTC"] == f"owned by leader {LEADER1}"
    assert len(v.orders) == 1

    v.hold(LEADER1, "BTC", 0.0)                         # leader1 goes flat -> our copy closes
    out2 = run(v, cfg(leaders=[LEADER1, LEADER2]), positions=ours("BTC", 0.5), equity=100_000.0)
    assert out2["closed"] == ["BTC"]
    assert cc.owned_coins(run.state_path) == set()
    assert "BTC" not in out2.get("opened", [])


def test_a_failed_leader_read_skips_only_that_leader(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    v.hold(LEADER2, "ETH", 10.0, entry=100.0)
    v.leads[LEADER1] = None                              # simulate a failed read for leader1
    out = run(v, cfg(leaders=[LEADER1, LEADER2]), equity=100_000.0)
    assert out["skipped"][f"leader:{LEADER1}"] == "leader read failed"
    assert out["opened"] == ["ETH"]


def test_a_leader_removed_from_config_still_mirrors_his_exits_but_opens_nothing_new(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1, LEADER2]), equity=1_000_000.0,
        dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert cc.owned_coins(run.state_path) == {"BTC"}

    v.hold(LEADER1, "BTC", 2000.0, entry=100.0)          # removed from config, adds -- not mirrored
    out = run(v, cfg(leaders=[LEADER2]), positions=ours("BTC", 5.0), equity=1_000_000.0,
             dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert out["skipped"]["BTC"] == "orphan leader: no new exposure"
    assert not out["added"]

    v.hold(LEADER1, "BTC", 0.0)                          # he goes flat -- must still close
    out2 = run(v, cfg(leaders=[LEADER2]), positions=ours("BTC", 5.0), equity=1_000_000.0,
              dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert out2["closed"] == ["BTC"]
    assert cc.owned_coins(run.state_path) == set()


# ── exit mirroring: reduce / close / flip / retry ────────────────────────────

def test_a_partial_close_takes_the_same_fraction_off_ours(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0, dex_available={"": 1_000_000.0, "xyz": 0.0})
    v.hold(LEADER1, "BTC", 250.0)                        # he takes 75% off
    out = run(v, cfg(leaders=[LEADER1]), positions=ours("BTC", 2.0), equity=1_000_000.0,
             dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert out["reduced"] == ["BTC"]
    assert v.orders[-1] == ("BTC", False, 1.5, True)


def test_his_flat_is_our_flat(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0, dex_available={"": 1_000_000.0, "xyz": 0.0})
    v.hold(LEADER1, "BTC", 0.0)
    out = run(v, cfg(leaders=[LEADER1]), positions=ours("BTC", 0.5), equity=1_000_000.0,
             dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert out["closed"] == ["BTC"] and v.closes == ["BTC"]
    assert cc.owned_coins(run.state_path) == set()


def test_a_flip_closes_then_opens_fresh(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0, dex_available={"": 1_000_000.0, "xyz": 0.0})
    v.hold(LEADER1, "BTC", -500.0, entry=100.0)          # flips long -> short
    out = run(v, cfg(leaders=[LEADER1]), positions=ours("BTC", 10.0), equity=1_000_000.0,
             dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert out["closed"] == ["BTC"]
    assert out["opened"] == ["BTC"]
    assert v.closes == ["BTC"]


def test_a_failed_close_is_retried_next_cycle(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0, dex_available={"": 1_000_000.0, "xyz": 0.0})
    v.hold(LEADER1, "BTC", 0.0)
    v.fail_close = True
    run(v, cfg(leaders=[LEADER1]), positions=ours("BTC", 0.5), equity=1_000_000.0,
        dex_available={"": 1_000_000.0, "xyz": 0.0})
    v.fail_close = False
    out = run(v, cfg(leaders=[LEADER1]), positions=ours("BTC", 0.5), equity=1_000_000.0,
             dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert out["closed"] == ["BTC"]


def test_our_copy_vanished_stops_mirroring_until_he_goes_flat_and_reopens(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0, dex_available={"": 1_000_000.0, "xyz": 0.0})

    v.hold(LEADER1, "BTC", 1500.0, entry=100.0)          # he adds; our copy is gone by hand
    out = run(v, cfg(leaders=[LEADER1]), positions=None, equity=1_000_000.0,
             dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert out["skipped"]["BTC"] == "our copy vanished"
    assert cc.owned_coins(run.state_path) == set()

    v.hold(LEADER1, "BTC", 2000.0, entry=100.0)          # still ignored while he holds it
    out_ignored = run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0,
                      dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert not out_ignored["opened"]

    v.hold(LEADER1, "BTC", 0.0)                          # he goes flat -- clears the ignore
    run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0, dex_available={"": 1_000_000.0, "xyz": 0.0})

    v.hold(LEADER1, "BTC", 500.0, entry=100.0)           # he reopens -- copyable again
    out2 = run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0, dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert out2["opened"] == ["BTC"]


# ── entries gating: daily loss / disabled / shadow_only ──────────────────────

def test_the_daily_loss_gate_blocks_the_open_and_the_whole_trip(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1]), daily_pnl=-200.0, equity=1_000_000.0,
        dex_available={"": 1_000_000.0, "xyz": 0.0})
    v.hold(LEADER1, "BTC", 2000.0, entry=100.0)          # gate clear now, trip already missed
    out = run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0,
             dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert not v.orders and not out["opened"]


def test_disabled_still_mirrors_exits_but_opens_nothing(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0, dex_available={"": 1_000_000.0, "xyz": 0.0})
    v.hold(LEADER1, "BTC", 0.0)
    out = run(v, cfg(leaders=[LEADER1], enabled=False), positions=ours("BTC", 0.5),
             equity=1_000_000.0, dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert out["closed"] == ["BTC"]


def test_shadow_only_demoted_still_mirrors_exits_but_opens_nothing(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0, dex_available={"": 1_000_000.0, "xyz": 0.0})
    v.hold(LEADER1, "BTC", 0.0)
    out = run(v, cfg(leaders=[LEADER1], shadow_only=True), positions=ours("BTC", 0.5),
             equity=1_000_000.0, dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert out["closed"] == ["BTC"]


# ── min_order_bump ───────────────────────────────────────────────────────────

def test_min_order_bump_rounds_a_small_notional_up_to_the_exchange_minimum(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 100.0, entry=100.0)           # ntl = 10000/1e6*500 = 5.0
    out = run(v, cfg(leaders=[LEADER1], min_order_bump=True))
    assert out["opened"] == ["BTC"]
    assert v.orders[0][2] * 100.0 == pytest.approx(cc._MIN_ORDER_USD)


def test_below_the_bump_floor_is_skipped_even_with_min_order_bump_on(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 40.0, entry=100.0)            # ntl = 4000/1e6*500 = 2.0, under 25% of $10.50
    out = run(v, cfg(leaders=[LEADER1], min_order_bump=True))
    assert "minimum" in out["skipped"]["BTC"]
    assert not v.orders


def test_min_order_bump_off_skips_the_same_small_notional(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 100.0, entry=100.0)
    out = run(v, cfg(leaders=[LEADER1], min_order_bump=False))
    assert "minimum" in out["skipped"]["BTC"]
    assert not v.orders


# ── cross-book claims ────────────────────────────────────────────────────────

def test_a_coin_claimed_by_another_active_book_is_skipped(run):
    v = FakeVenue()
    get_claims_registry().claim("BTC", "drawdown_ladder")
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)
    out = run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0,
             dex_available={"": 1_000_000.0, "xyz": 0.0})
    assert "claimed by drawdown_ladder" in out["skipped"]["BTC"]
    assert not v.orders


# ── grader + equity log ──────────────────────────────────────────────────────

def test_the_grader_demotes_past_half_the_sleeve():
    rows = [{"pnl": 0, "sleeve": 100}, {"pnl": -51, "sleeve": 100}]
    assert cc.mtm_decision(rows, True)["action"] == "demote"
    assert cc.mtm_decision(rows[:1], True)["action"] == "none"


def test_load_equity_log_reads_back_the_snapshot_written_by_maybe_run(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1]), equity=1_000_000.0, dex_available={"": 1_000_000.0, "xyz": 0.0})
    rows = cc.load_equity_log(run.equity_log_path)
    assert len(rows) == 1
    assert rows[0]["sleeve"] == pytest.approx(500_000.0)   # equity 1e6 * sleeve_frac 0.5


# ── settings()/leader_settings(): sizing_mode, leverage "fixed", overrides ──
# Operator decision 2026-10-02: proportional-only sizing rounds every copy to
# $0 on a tiny account (his real equity was $1.29). These fields let a small
# account pick a fixed dollar size instead. cfg()'s own default above pins
# sizing_mode to "proportional" so every test ABOVE this point keeps its
# original numbers; everything below tests the new default and the two
# fixed modes directly.

def test_settings_new_sizing_and_leverage_fields_default_correctly():
    s = cc.settings({})
    assert s["sizing_mode"] == cc.DEFAULT_SIZING_MODE == "fixed_margin"
    assert s["size_multiplier"] == 1.0
    assert s["fixed_margin_usd"] == 0.25
    assert s["fixed_notional_usd"] == 10.5
    assert s["leverage_mode"] == "max"
    assert s["leverage"] == 20
    assert s["leader_overrides"] == {}


def test_settings_clamps_new_sizing_and_leverage_fields_bad_enums_to_defaults():
    raw = {"sizing_mode": "yolo", "size_multiplier": 500.0, "fixed_margin_usd": -5.0,
           "fixed_notional_usd": 1.0, "leverage_mode": "fixed", "leverage": 999}
    s = cc.settings({"copycat": raw})
    assert s["sizing_mode"] == "fixed_margin"                     # bad enum -> default
    assert s["size_multiplier"] == 100.0                          # clamped to [0,100]
    assert s["fixed_margin_usd"] == cc._FIXED_MARGIN_USD_FLOOR     # clamped > 0
    assert s["fixed_notional_usd"] == 10.5                        # clamped >= 10.5
    assert s["leverage_mode"] == "fixed"                          # a valid enum now
    assert s["leverage"] == 50                                    # clamped to [1,50]


def test_leader_overrides_normalizes_address_case_and_filters_unknown_keys():
    raw = {
        f" {LEADER1.upper()} ": {"sizing_mode": "fixed_notional", "unknown_key": 1},
        "not-a-dict-value": "oops",
        LEADER2: "also not a dict",
    }
    s = cc.settings({"copycat": {"leader_overrides": raw}})
    assert set(s["leader_overrides"]) == {LEADER1}
    assert s["leader_overrides"][LEADER1] == {"sizing_mode": "fixed_notional"}


def test_leader_settings_merges_override_and_normalizes_through_the_same_pipeline():
    config = {"copycat": {"sizing_mode": "proportional", "leverage_mode": "max",
                          "max_worse_entry_pct": 10.0,
                          "leader_overrides": {LEADER1: {"sizing_mode": "fixed_notional",
                                                         "leverage_mode": "fixed",
                                                         "leverage": 7,
                                                         "max_worse_entry_pct": 2.0}}}}
    es1 = cc.leader_settings(config, LEADER1.upper())   # lookup is case/space-insensitive
    assert es1["sizing_mode"] == "fixed_notional"
    assert es1["leverage_mode"] == "fixed"
    assert es1["leverage"] == 7
    assert es1["max_worse_entry_pct"] == 2.0
    assert "leader_overrides" not in es1

    es2 = cc.leader_settings(config, LEADER2)           # no override -> same as settings()
    assert es2 == {k: v for k, v in cc.settings(config).items() if k != "leader_overrides"}


def test_leader_settings_ignores_non_overridable_fields_and_unknown_keys():
    config = {"copycat": {"sleeve_frac": 0.5, "only_new_positions": True,
                          "leader_overrides": {LEADER1: {"sleeve_frac": 0.9,
                                                         "only_new_positions": False,
                                                         "made_up": 123}}}}
    es = cc.leader_settings(config, LEADER1)
    assert es["sleeve_frac"] == 0.5             # not in _LEADER_OVERRIDE_KEYS -- unchanged
    assert es["only_new_positions"] is True     # not in _LEADER_OVERRIDE_KEYS -- unchanged


# ── sizing_mode behavior ─────────────────────────────────────────────────────

def test_sizing_mode_fixed_margin_opens_at_fixed_margin_times_leverage(run):
    v = FakeVenue()
    v.max_levs["BTC"] = 40
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    out = run(v, cfg(leaders=[LEADER1], sizing_mode="fixed_margin", fixed_margin_usd=1.0,
                     leverage_mode="max", max_position_leverage=50),
             dex_available={"": 1000.0, "xyz": 0.0})
    assert out["opened"] == ["BTC"]
    assert v.orders[0][2] * v.mid("BTC") == pytest.approx(40.0)    # $1.00 margin * 40x leverage
    assert v.levs["BTC"]["leverage"] == 40


def test_sizing_mode_fixed_margin_default_needs_the_bump_for_a_tiny_account(run):
    """The exact motivating scenario: a $1.29 account, defaults all the way
    (fixed_margin_usd=0.25, leverage_mode "max" -> 40x here -> raw notional
    $10.00, just under the exchange floor -- min_order_bump rescues it."""
    v = FakeVenue()
    v.max_levs["BTC"] = 40
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    out = run(v, cfg(leaders=[LEADER1], sizing_mode="fixed_margin",
                     leverage_mode="max", max_position_leverage=50),
             equity=1.29, dex_available={"": 1.29, "xyz": 0.0})
    assert out["opened"] == ["BTC"]
    assert v.orders[0][2] * v.mid("BTC") == pytest.approx(cc._MIN_ORDER_USD)


def test_sizing_mode_fixed_notional_opens_at_the_fixed_dollar_amount(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    out = run(v, cfg(leaders=[LEADER1], sizing_mode="fixed_notional", fixed_notional_usd=25.0),
             dex_available={"": 1000.0, "xyz": 0.0})
    assert out["opened"] == ["BTC"]
    assert v.orders[0][2] * v.mid("BTC") == pytest.approx(25.0)


def test_size_multiplier_scales_proportional_mode_only(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 1000.0, entry=100.0)
    out = run(v, cfg(leaders=[LEADER1], sizing_mode="proportional", size_multiplier=3.0))
    assert out["opened"] == ["BTC"]
    # baseline proportional notional at mult=1 is $50 (see the only_new_positions
    # false test above, same setup) -- x3 size_multiplier -> $150.
    assert v.orders[0][2] * v.mid("BTC") == pytest.approx(150.0)


def test_fixed_margin_add_scales_our_position_by_his_fractional_change(run):
    v = FakeVenue()
    v.max_levs["BTC"] = 10
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    cfg_fm = cfg(leaders=[LEADER1], sizing_mode="fixed_margin", fixed_margin_usd=5.0,
                leverage_mode="max", max_position_leverage=50)
    run(v, cfg_fm, dex_available={"": 1000.0, "xyz": 0.0})
    assert v.orders[-1][2] == pytest.approx(0.5)            # open: $5 margin * 10x / $100 mid

    v.hold(LEADER1, "BTC", 15.0, entry=100.0)                # he adds 50%
    out = run(v, cfg_fm, positions=ours("BTC", 0.5), dex_available={"": 1000.0, "xyz": 0.0})
    assert out["added"] == ["BTC"]
    assert v.orders[-1] == ("BTC", True, 0.25, False)        # our_add = 0.5 * 0.5 = 0.25 BTC


def test_fixed_notional_add_scales_our_position_by_his_fractional_change(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    cfg_fn = cfg(leaders=[LEADER1], sizing_mode="fixed_notional", fixed_notional_usd=20.0)
    run(v, cfg_fn, dex_available={"": 1000.0, "xyz": 0.0})
    assert v.orders[-1][2] == pytest.approx(0.2)             # open: $20 / $100 mid

    v.hold(LEADER1, "BTC", 20.0, entry=100.0)                 # he doubles his size
    out = run(v, cfg_fn, positions=ours("BTC", 0.2), dex_available={"": 1000.0, "xyz": 0.0})
    assert out["added"] == ["BTC"]
    assert v.orders[-1] == ("BTC", True, 0.2, False)          # our_add = 0.2 * 1.0 = 0.2 BTC


# ── leverage_mode "fixed" ────────────────────────────────────────────────────

def test_leverage_mode_fixed_uses_the_configured_leverage(run):
    v = FakeVenue()
    v.max_levs["BTC"] = 40
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    out = run(v, cfg(leaders=[LEADER1], leverage_mode="fixed", leverage=15,
                     max_position_leverage=50),
             equity=100_000.0)
    assert out["opened"] == ["BTC"]
    assert v.levs["BTC"]["leverage"] == 15


def test_leverage_mode_fixed_is_still_capped_by_the_coin_max(run):
    v = FakeVenue()
    v.max_levs["BTC"] = 10
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1], leverage_mode="fixed", leverage=45, max_position_leverage=50),
        equity=100_000.0)
    assert v.levs["BTC"]["leverage"] == 10


def test_leverage_mode_fixed_is_still_capped_by_max_position_leverage(run):
    v = FakeVenue()
    v.max_levs["BTC"] = 40
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    run(v, cfg(leaders=[LEADER1], leverage_mode="fixed", leverage=45, max_position_leverage=20),
        equity=100_000.0)
    assert v.levs["BTC"]["leverage"] == 20


# ── sizing_mode/leverage recorded in state + log_event ──────────────────────

def test_open_records_the_effective_sizing_mode_in_state_and_log_event(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    events = []
    out = run(v, cfg(leaders=[LEADER1], sizing_mode="fixed_notional", fixed_notional_usd=20.0),
             dex_available={"": 1000.0, "xyz": 0.0}, log_event=events.append)
    assert out["opened"] == ["BTC"]
    open_events = [e for e in events if e.get("action") == "open"]
    assert open_events and open_events[0]["sizing_mode"] == "fixed_notional"
    assert open_events[0]["leverage"] == v.levs["BTC"]["leverage"]

    state = read_json(run.state_path)
    assert state["copies"]["BTC"]["sizing_mode"] == "fixed_notional"


# ── per-leader overrides, end to end through maybe_run ──────────────────────

def test_leader_override_changes_effective_sizing_for_that_leader_only(run):
    v = FakeVenue()
    v.hold(LEADER1, "BTC", 10.0, entry=100.0)
    v.hold(LEADER2, "ETH", 10.0, entry=100.0)
    cfg_ov = cfg(leaders=[LEADER1, LEADER2], sizing_mode="fixed_notional",
                fixed_notional_usd=20.0,
                leader_overrides={LEADER2: {"fixed_notional_usd": 40.0}})
    out = run(v, cfg_ov, dex_available={"": 1000.0, "xyz": 0.0})
    assert sorted(out["opened"]) == ["BTC", "ETH"]
    btc_order = next(o for o in v.orders if o[0] == "BTC")
    eth_order = next(o for o in v.orders if o[0] == "ETH")
    assert btc_order[2] * v.mid("BTC") == pytest.approx(20.0)     # LEADER1: book-wide default
    assert eth_order[2] * v.mid("ETH") == pytest.approx(40.0)     # LEADER2: overridden
