"""drawdown_ladder: the live book for W-WH2, driven against a fake exchange.

Every test pins one way a no-stop ladder goes wrong in production rather than
in a backtest:

- the rule drifting from what was validated (constants, leverage)
- a ladder that closes but leaves rungs resting, so a later dip reopens a
  position with no target behind it
- a degraded account read taken as "flat" and acted on
- losing the state file and with it the only record of open ladders
- another safety system putting a stop on a ladder (flatten, DSL, slots)
- a grader reading 99% closed-trade wins as evidence
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

import pathiel.agents.drawdown_ladder_live as L
from pathiel.agents.rebalancer_owned import get_claims_registry

ROOT = Path(__file__).resolve().parents[1]
DAY = 86_400_000
DAY0 = 20_000 * DAY                     # a UTC midnight
NOW = DAY0 + 3_600_000                  # one hour into the entry window


# ── a fake exchange ──────────────────────────────────────────────────────────

class Venue:
    def __init__(self, bars=None, fill_px=100.0):
        self.bars = bars or {}
        self.fill_px = fill_px
        self.orders = {}            # oid -> dict
        self.next_oid = 1000
        self.calls = []
        self.leverage = {}
        self.closed = []
        self.realized = 7.5
        self.reject_limits = 0

    def daily_bars(self, coin, count):
        self.calls.append(("bars", coin))
        return self.bars.get(coin, [])

    def set_leverage(self, coin, leverage):
        self.leverage[coin] = leverage
        return True

    def market_buy(self, coin, notional):
        self.calls.append(("market_buy", coin, notional))
        return {"ok": True, "avg_px": self.fill_px, "total_sz": round(notional / self.fill_px, 6)}

    def limit(self, coin, is_buy, size, px, reduce_only=False):
        self.calls.append(("limit", coin, is_buy, size, px, reduce_only))
        if self.reject_limits:
            self.reject_limits -= 1
            return {"ok": False, "error": "Insufficient margin"}
        self.next_oid += 1
        self.orders[self.next_oid] = {"coin": coin, "is_buy": is_buy, "size": size, "px": px,
                                      "reduce_only": reduce_only, "status": "open"}
        return {"ok": True, "order_id": str(self.next_oid)}

    def size_for(self, coin, notional, px):
        return round(notional / px, 6)

    def cancel(self, coin, oid):
        self.calls.append(("cancel", coin, oid))
        if self.orders.get(oid, {}).get("status") == "open":
            self.orders[oid]["status"] = "canceled"
            return True
        return False

    def order_status(self, oid):
        return self.orders.get(oid, {}).get("status", "unknown")

    def close(self, coin):
        self.closed.append(coin)
        return True

    def realized_since(self, coin, start_ms):
        return self.realized

    # helpers for the tests
    def open_orders(self, coin, **match):
        return [o for o in self.orders.values() if o["coin"] == coin and o["status"] == "open"
                and all(o[k] == v for k, v in match.items())]


def bars_for(close_today, prior_high=100.0, n=40, day0=DAY0):
    """n closed daily bars ending yesterday, then today's partial bar."""
    rows = [[day0 - (n - i) * DAY, prior_high, prior_high, prior_high, prior_high]
            for i in range(n - 1)]
    rows.append([day0 - DAY, prior_high, prior_high, close_today, close_today])
    rows.append([day0, close_today, close_today, close_today, close_today])
    return rows


def pos(coin, szi, entry, upnl=0.0, funding_paid=0.0):
    return {"position": {"coin": coin, "szi": str(szi), "entryPx": str(entry),
                         "unrealizedPnl": str(upnl),
                         "cumFunding": {"sinceOpen": str(funding_paid)}}}


CFG = {"drawdown_ladder": {"enabled": True, "shadow_only": False, "sleeve_frac": 0.5}}


def run(venue, tmp_path, positions=(), equity=1000.0, available=1000.0, daily_pnl=0.0,
        limit=-121.0, now=NOW, config=CFG, allow_entries=True, events=None):
    return L.maybe_run(config, list(positions), equity, available, daily_pnl, limit,
                       allow_entries=allow_entries, now_ms=now, venue=venue,
                       log_event=(events.append if events is not None else lambda e: None),
                       state_path=str(tmp_path / "ladder.json"),
                       equity_log_path=str(tmp_path / "equity.jsonl"))


def state(tmp_path):
    return json.loads((tmp_path / "ladder.json").read_text())


def opened_btc(tmp_path, events=None):
    v = Venue(bars={"BTC": bars_for(78.0)})
    run(v, tmp_path, events=events)
    return v


# ── the rule is the validated rule ───────────────────────────────────────────

def test_the_constants_are_the_pre_registered_rule():
    spec = importlib.util.spec_from_file_location(
        "wh2", ROOT / "research" / "alpha_swarm" / "hypotheses" / "W-WH2_ladder_backtest.py")
    wh2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wh2)
    assert {"trigger_dd": L.TRIGGER_DD, "k": L.RUNGS, "gap": L.GAP, "tp": L.TP} == wh2.FROZEN
    assert L.LOOKBACK == wh2.LOOKBACK
    assert L.UNIVERSE == tuple(wh2.UNIVERSES["A_majors"])


def test_leverage_is_one_and_config_cannot_raise_it(tmp_path):
    assert L.LEVERAGE == 1
    v = Venue(bars={"BTC": bars_for(78.0)})
    cfg = {"drawdown_ladder": dict(CFG["drawdown_ladder"], leverage=3)}
    run(v, tmp_path, config=cfg)
    assert v.leverage == {"BTC": 1}


def test_the_exchange_minimum_sets_the_equity_floor():
    assert L.min_equity_to_trade(0.5) == pytest.approx(525.0)
    assert L.rung_notional(525.0, 0.5) == pytest.approx(10.5)


class TestTrigger:
    def test_fires_at_the_level(self):
        sig = L.trigger(bars_for(78.9), DAY0)
        assert sig["fire"] and sig["prior_high"] == 100.0 and sig["level"] == pytest.approx(78.9)

    def test_does_not_fire_above_it(self):
        assert L.trigger(bars_for(79.0), DAY0)["fire"] is False

    def test_todays_partial_bar_is_ignored(self):
        rows = bars_for(100.0)
        rows[-1] = [DAY0, 100.0, 100.0, 50.0, 50.0]      # today's intraday crash
        assert L.trigger(rows, DAY0)["fire"] is False

    def test_stale_history_cannot_answer(self):
        assert L.trigger(bars_for(78.0, day0=DAY0 - DAY)[:-1], DAY0) is None

    def test_short_history_cannot_answer(self):
        assert L.trigger(bars_for(78.0, n=30), DAY0) is None


# ── entry ────────────────────────────────────────────────────────────────────

def test_entry_places_rung_one_four_resting_rungs_and_the_target(tmp_path):
    events = []
    v = opened_btc(tmp_path, events)
    rung = 1000.0 * 0.5 / 25
    assert ("market_buy", "BTC", pytest.approx(rung)) in v.calls
    buys = sorted((o["px"] for o in v.open_orders("BTC", is_buy=True)), reverse=True)
    assert buys == pytest.approx([100 * (1 + L.GAP) ** i for i in range(1, 5)])
    (tp,) = v.open_orders("BTC", is_buy=False)
    assert tp["reduce_only"] and tp["px"] == pytest.approx(100 * (1 + L.TP))
    assert tp["size"] == pytest.approx(rung / 100)
    assert get_claims_registry().owner_of("BTC") == "drawdown_ladder"
    assert state(tmp_path)["ladders"]["BTC"]["status"] == "open"
    assert [e["action"] for e in events if e.get("coin") == "BTC"] == ["open"]


def test_rung_one_is_on_disk_before_any_resting_order_is_sent(tmp_path):
    """A crash between the fill and the rungs must leave a record, or the
    position is unmanaged and the next day's run skips the coin as held."""
    v = Venue(bars={"BTC": bars_for(78.0)})
    seen = {}

    def boom(*a, **k):
        seen["state"] = state(tmp_path)
        raise RuntimeError("network died")
    v.limit = boom
    with pytest.raises(RuntimeError):
        run(v, tmp_path)
    lad = seen["state"]["ladders"]["BTC"]
    assert lad["rungs"][0]["status"] == "filled" and lad["tp"] is None


def test_under_the_equity_floor_nothing_is_sent_and_it_says_why(tmp_path):
    events = []
    v = Venue(bars={"BTC": bars_for(78.0)})
    run(v, tmp_path, equity=500.0, events=events)
    assert not [c for c in v.calls if c[0] in ("market_buy", "limit")]
    assert "$525.00" in events[0]["reason"]


def test_without_main_dex_margin_for_the_whole_ladder_nothing_is_sent(tmp_path):
    events = []
    v = Venue(bars={"BTC": bars_for(78.0)})
    run(v, tmp_path, available=99.0, events=events)      # ladder needs 5 x $20
    assert not [c for c in v.calls if c[0] == "market_buy"]
    assert "main perp dex" in events[0]["reason"]


def test_the_daily_loss_gate_blocks_new_ladders(tmp_path):
    v = Venue(bars={"BTC": bars_for(78.0)})
    out = run(v, tmp_path, daily_pnl=-130.0, limit=-121.0)
    assert out["skipped"]["all"] == "daily loss gate" and not v.orders


@pytest.mark.parametrize("kw", [
    {"config": {"drawdown_ladder": dict(CFG["drawdown_ladder"], shadow_only=True)}},
    {"config": {"drawdown_ladder": dict(CFG["drawdown_ladder"], enabled=False)}},
    {"allow_entries": False},
    {"now": DAY0 + L.ENTRY_WINDOW_MS + 1},
])
def test_no_entry_when_demoted_disabled_off_or_late(tmp_path, kw):
    v = Venue(bars={"BTC": bars_for(78.0)})
    run(v, tmp_path, **kw)
    assert not v.orders


def test_each_coin_is_evaluated_once_per_day(tmp_path):
    v = Venue(bars={c: bars_for(100.0) for c in L.UNIVERSE})
    run(v, tmp_path)
    run(v, tmp_path, now=NOW + 60_000)
    assert sum(1 for c in v.calls if c[0] == "bars") == len(L.UNIVERSE)


def test_a_coin_another_book_holds_is_left_alone(tmp_path):
    reg = get_claims_registry()
    reg.claim("BTC", "xs_reversal")
    reg.save()
    v = Venue(bars={"BTC": bars_for(78.0)})
    out = run(v, tmp_path)
    assert out["skipped"]["BTC"] == "claimed by xs_reversal" and not v.orders


# ── management ───────────────────────────────────────────────────────────────

def test_a_rung_fill_reprices_the_target_to_the_new_average(tmp_path):
    events = []
    v = opened_btc(tmp_path)
    (old_tp,) = [oid for oid, o in v.orders.items() if not o["is_buy"]]
    rung2 = min((oid for oid, o in v.orders.items() if o["is_buy"]), key=lambda o: -v.orders[o]["px"])
    v.orders[rung2]["status"] = "filled"
    run(v, tmp_path, positions=[pos("BTC", 0.415446, 96.28)], now=NOW + 60_000, events=events)
    assert v.orders[old_tp]["status"] == "canceled"
    (tp,) = v.open_orders("BTC", is_buy=False)
    assert tp["size"] == pytest.approx(0.415446) and tp["px"] == pytest.approx(96.28 * (1 + L.TP))
    assert [e["action"] for e in events] == ["rung_filled"]
    assert state(tmp_path)["ladders"]["BTC"]["rungs"][1]["status"] == "filled"


def test_target_fill_cancels_every_rung_and_releases_the_coin(tmp_path):
    events = []
    v = opened_btc(tmp_path)
    for o in v.orders.values():
        if not o["is_buy"]:
            o["status"] = "filled"
    run(v, tmp_path, positions=[], now=NOW + 60_000, events=events)
    assert not v.open_orders("BTC")
    assert "BTC" not in state(tmp_path)["ladders"]
    assert get_claims_registry().owner_of("BTC") is None
    (close,) = events
    assert close["closed_by"] == "tp" and close["realized_usd"] == 7.5
    assert state(tmp_path)["realized_cum"] == 7.5


def test_a_size_read_from_before_the_target_filled_is_not_acted_on(tmp_path):
    """The position list is read at the top of the cycle, the order status later.
    A target that fills in between shows as 'filled' next to a size that is
    already gone. Market-closing on that read would be acting on a stale number."""
    v = opened_btc(tmp_path)
    for o in v.orders.values():
        if not o["is_buy"]:
            o["status"] = "filled"
    run(v, tmp_path, positions=[pos("BTC", 0.1, 100.0)], now=NOW + 60_000)
    assert v.closed == [] and not v.open_orders("BTC")
    assert state(tmp_path)["ladders"]["BTC"]["status"] == "closing"


def test_a_rung_that_filled_after_the_target_is_flattened_on_the_next_read(tmp_path):
    v = opened_btc(tmp_path)
    for o in v.orders.values():
        if not o["is_buy"]:
            o["status"] = "filled"
    run(v, tmp_path, positions=[pos("BTC", 0.2, 100.0)], now=NOW + 60_000)
    run(v, tmp_path, positions=[pos("BTC", 0.2, 92.83)], now=NOW + 120_000)
    assert v.closed == ["BTC"]
    run(v, tmp_path, positions=[], now=NOW + 180_000)
    assert "BTC" not in state(tmp_path)["ladders"]


def test_an_outside_close_cancels_the_rungs_and_the_target(tmp_path):
    events = []
    v = opened_btc(tmp_path)
    run(v, tmp_path, positions=[], now=NOW + 60_000, events=events)
    assert not v.open_orders("BTC")
    assert events[0]["closed_by"] == "external"


def test_a_degraded_account_read_touches_nothing(tmp_path):
    v = opened_btc(tmp_path)
    before = json.dumps(v.orders, sort_keys=True)
    out = run(v, tmp_path, positions=[], equity=0.0, now=NOW + 60_000)
    assert out["skipped"]["all"] == "degraded account read"
    assert json.dumps(v.orders, sort_keys=True) == before


def test_a_failed_realized_read_finishes_next_cycle_instead_of_recording_a_guess(tmp_path):
    v = opened_btc(tmp_path)
    v.realized = None
    run(v, tmp_path, positions=[], now=NOW + 60_000)
    assert state(tmp_path)["ladders"]["BTC"]["status"] == "closing"
    v.realized = 3.0
    run(v, tmp_path, positions=[], now=NOW + 120_000)
    assert "BTC" not in state(tmp_path)["ladders"]


def test_a_rejected_rung_is_retried_after_the_back_off(tmp_path):
    v = Venue(bars={"BTC": bars_for(78.0)})
    v.reject_limits = 1
    run(v, tmp_path)
    lad = state(tmp_path)["ladders"]["BTC"]
    assert lad["rungs"][1]["status"] == "pending"
    held = [pos("BTC", 0.2, 100.0)]
    run(v, tmp_path, positions=held, now=NOW + 60_000)
    assert state(tmp_path)["ladders"]["BTC"]["rungs"][1]["status"] == "pending"
    run(v, tmp_path, positions=held, now=NOW + L.RETRY_MS + 1)
    assert state(tmp_path)["ladders"]["BTC"]["rungs"][1]["status"] == "open"


def test_a_corrupt_state_file_is_quarantined_and_blocks_entries(tmp_path, caplog):
    (tmp_path / "ladder.json").write_text("{not json")
    v = Venue(bars={"BTC": bars_for(78.0)})
    out = run(v, tmp_path)
    assert out["skipped"]["all"] == "state unreadable" and not v.orders
    assert list(tmp_path.glob("ladder.json.corrupt-*"))
    assert "quarantined" in caplog.text


def test_one_mark_to_market_row_per_day_net_of_funding(tmp_path):
    v = opened_btc(tmp_path)
    held = [pos("BTC", 0.2, 100.0, upnl=-3.0, funding_paid=0.5)]
    run(v, tmp_path, positions=held, now=NOW + DAY)
    run(v, tmp_path, positions=held, now=NOW + DAY + 60_000)
    rows = L.load_equity_log(str(tmp_path / "equity.jsonl"))
    assert [r["day"] for r in rows] == sorted({r["day"] for r in rows})
    assert rows[-1]["unrealized"] == pytest.approx(-3.5) and rows[-1]["sleeve"] == 500.0


# ── grading ──────────────────────────────────────────────────────────────────

def test_drawdown_is_measured_against_the_sleeve_from_the_peak():
    rows = [{"pnl": 0, "sleeve": 100}, {"pnl": 30, "sleeve": 100}, {"pnl": -10, "sleeve": 100}]
    assert L.book_drawdown(rows) == pytest.approx(-0.40)


def test_the_grader_demotes_past_the_backtests_worst_drawdown():
    ok = [{"pnl": 0, "sleeve": 100}, {"pnl": -43, "sleeve": 100}]
    bad = ok + [{"pnl": -44, "sleeve": 100}]
    assert L.mtm_decision(ok, True)["action"] == "none"
    assert L.mtm_decision(bad, True)["action"] == "demote"
    assert L.mtm_decision([], True)["verdict"] == "PENDING"


def test_the_nightly_cycle_grades_this_book_on_mark_to_market_only():
    spec = importlib.util.spec_from_file_location("ac", ROOT / "scripts" / "autonomous_cycle.py")
    ac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ac)
    assert "drawdown_ladder" in ac._mtm_books()
    cfg = {"drawdown_ladder": {"enabled": True, "shadow_only": False, "sleeve_frac": 0.5}}
    assert ac.apply_action(cfg, "drawdown_ladder", "demote") is True
    assert cfg["drawdown_ladder"]["shadow_only"] is True


# ── the other safety systems leave ladders alone ─────────────────────────────

def test_book_positions_hides_ladders_from_everything_else():
    positions = [pos("BTC", 1, 1), pos("xyz:NVDA", -1, 1)]
    assert [p["position"]["coin"] for p in L.book_positions(positions, {"BTC"})] == ["xyz:NVDA"]


def test_owned_coins_reads_the_state_file(tmp_path):
    opened_btc(tmp_path)
    assert L.owned_coins(str(tmp_path / "ladder.json")) == {"BTC"}
    assert L.owned_coins(str(tmp_path / "missing.json")) == set()


LOOP = (ROOT / "scripts" / "trading_loop.py").read_text()
TREE = ast.parse(LOOP)


def _call(name):
    return [n for n in ast.walk(TREE) if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


def test_the_hard_flatten_iterates_book_positions_only():
    i = LOOP.index("HARD daily-loss floor breached")
    block = LOOP[LOOP.rindex("if equity > 0", 0, i):LOOP.index("hard_killswitch", i)]
    assert "_book_positions" in block and "for _p in _book_positions" in block
    assert "for _p in positions" not in block


def test_the_dsl_never_sees_a_ladder():
    (call,) = _call("rehydrate_from_exchange")
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "_book_positions"


def test_the_ladder_runs_before_off_mode_skips_the_cycle():
    (call,) = _call("_drawdown_ladder_maybe_run")
    off = LOOP.index('[mode] OFF')
    assert call.lineno < LOOP[:off].count("\n") + 1


# ── the live config carries the operator's decisions ─────────────────────────

CFG_LIVE = json.loads((ROOT / ".agent-config.json").read_text())


def test_the_capital_split_fits_in_margin():
    ladder = float(CFG_LIVE["drawdown_ladder"]["sleeve_frac"])
    xs = int(CFG_LIVE["max_concurrent"]) * float(CFG_LIVE["strategy_book_equity_frac"])
    free = float(CFG_LIVE["min_available_margin_pct"])
    assert ladder == 0.5
    assert ladder + xs + free <= 1.0, f"ladder {ladder:.0%} + xs {xs:.1%} + free {free:.0%}"


def test_the_crypto_exception_is_this_book_and_these_five_coins_only():
    assert CFG_LIVE["enable_crypto"] is False
    assert L.UNIVERSE == ("BTC", "ETH", "SOL", "BNB", "XRP")
    assert not any(":" in c for c in L.UNIVERSE)


def test_the_book_is_live_in_the_running_config():
    b = CFG_LIVE["drawdown_ladder"]
    assert b["enabled"] is True and b["shadow_only"] is False
    assert "leverage" not in b, "leverage is a code constant; a config key would be a lie"
