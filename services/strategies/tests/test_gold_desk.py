"""2026-11-05: gold (xyz:GOLD) replaces silver as the metals desk's primary
(operator: "stop the silver tick, and silver market, add gold tick and gold
market, same formula"). These pin the live rule's gold constants to the test
that chose them (docs/strategy/GOLD_STRATEGY_TEST_2026-11-05.md), the desk
spec, the "wrong instrument, rewrite first" gate, and the sim/live parity of
`live_params.live_gold_params`. Pure, no network."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from services.strategies import desks
from services.strategies.backtest.desk_tick_sim import Params
from services.strategies.evals import gold_strategy_test as G
from services.strategies.evals import live_params as LP
from services.strategies.next_trade import (GOLD, GOLD_STOP_BUFFER_FLOOR_FRAC, LEVERAGE_FIT,
                                            STOP_BUFFER_FLOOR_FRAC, leverage_fit_for, next_trade,
                                            stop_buffer_floor_frac_for, stop_buffer_for)

GOLD_MAX_LEVERAGE = 25.0


# ── the desk spec ────────────────────────────────────────────────────────────

def test_the_metals_desk_is_about_gold_now():
    spec = desks.require_spec("metals")
    assert spec.name == "Gold" and spec.instruments[0].coin == GOLD and spec.instruments[0].role == "primary"
    assert {i.coin for i in spec.instruments} == {"xyz:GOLD", "xyz:SILVER"}
    text = " ".join(spec.news_queries + spec.policy_topics + spec.prediction_market_queries + spec.lessons).lower()
    for word in ("gold", "central bank", "fed", "dollar", "real yields", "iran"):
        assert word in text, word
    assert "gold price" in spec.news_queries and "gold price" in spec.prediction_market_queries
    assert len(spec.news_queries) <= 8          # services/intel/sources/news.py searches at most eight phrases


def test_the_catalyst_kinds_are_real_generator_kinds():
    """`catalyst_kinds` is an allowlist of services/intel/sources/catalysts.py
    generators: the old "cpi_release"/"nfp_release" names matched none. Jobs
    and PCE reach the thesis through the scheduled-events "metals" group."""
    from services.intel.sources.catalysts import _CALENDAR_GENERATORS
    spec = desks.require_spec("metals")
    assert set(spec.catalyst_kinds) <= set(_CALENDAR_GENERATORS)
    assert {"fomc", "cpi", "cftc_cot"} <= set(spec.catalyst_kinds)


# ── the live rule's gold constants ───────────────────────────────────────────

def test_gold_keeps_the_0_8pct_floor_and_it_binds_on_a_normal_gold_tick():
    """Gold's 1.3 x ATR14 is about 0.34% of price (ATR about 12 at 4100), so
    the 0.8% floor (33) is the stop buffer on nearly every tick; a wide ATR
    still wins when it is wider."""
    assert GOLD_STOP_BUFFER_FLOOR_FRAC == STOP_BUFFER_FLOOR_FRAC == 0.008
    assert stop_buffer_floor_frac_for(GOLD) == 0.008 and stop_buffer_floor_frac_for("xyz:SILVER") == 0.008
    assert stop_buffer_for(GOLD, 12.0, 4100.0) == pytest.approx(32.8)        # floor: 0.8% of 4100
    assert stop_buffer_for(GOLD, 30.0, 4100.0) == pytest.approx(39.0)        # ATR wider: kept
    assert stop_buffer_for(GOLD, 12.0) == pytest.approx(15.6)                # no mid: bare ATR rule
    assert stop_buffer_for(GOLD, None, 4100.0) is None
    assert stop_buffer_for("xyz:BRENTOIL", 12.0, 4100.0) == 0.75             # Brent untouched


def test_gold_fits_its_leverage_20_then_15():
    assert leverage_fit_for(GOLD) == LEVERAGE_FIT == (20.0, 15.0)


def test_a_gold_entry_at_the_24h_low_trades_at_20x_and_a_wide_range_fits_15x():
    buf = stop_buffer_for(GOLD, 12.0, 4065.0)
    kw = dict(thesis_entry=4100.0, leverage=20.0, coin_max_leverage=GOLD_MAX_LEVERAGE, stop_buffer=buf,
              leverage_fit=leverage_fit_for(GOLD))
    tight = next_trade("long", 4065.0, 4060.0, 4180.0, **kw)
    assert tight["ok"] and tight["leverage"] == 20.0 and tight["stop_px"] == pytest.approx(4060.0 - buf)
    # a 24h range of 3960-4200 with the mid at 4010: the stop sits 2.1% away, too far for 20x at the
    # venue's 25x max (liquidation 3% out), so the tick trades at 15x instead of waiting
    wide = next_trade("long", 4010.0, 3960.0, 4200.0, **dict(kw, stop_buffer=stop_buffer_for(GOLD, 12.0, 4010.0)))
    assert wide["ok"] and wide["leverage"] == 15.0
    fixed = next_trade("long", 4010.0, 3960.0, 4200.0, **dict(kw, stop_buffer=stop_buffer_for(GOLD, 12.0, 4010.0),
                                                              leverage_fit=()))
    assert not fixed["ok"] and any("20x" in b for b in fixed["blockers"])


def test_no_gold_entry_after_a_break_and_never_chase():
    buf = stop_buffer_for(GOLD, 12.0, 4050.0)
    kw = dict(thesis_entry=4100.0, leverage=20.0, coin_max_leverage=GOLD_MAX_LEVERAGE, stop_buffer=buf,
              leverage_fit=leverage_fit_for(GOLD))
    broke = next_trade("long", 4050.0, 4060.0, 4180.0, **kw)          # mid under the 24h low
    assert not broke["ok"] and any("no entry after a break" in b for b in broke["blockers"])
    chase = next_trade("long", 4110.0, 4060.0, 4300.0, **kw)          # mid over the thesis entry
    assert not chase["ok"] and any("never chase" in b for b in chase["blockers"])


# ── live_gold_params: the eval baseline is the chosen variant ───────────────

def test_live_gold_params_is_the_test_winner_on_top_of_the_silver_shape():
    """GOLD_STRATEGY_TEST_2026-11-05.md picked `floor_0.8_fit` (silver's live
    rule as is): the floor plateau peaked at 0.8% with the lowest drawdowns,
    and the 20/15 fit never hurt. live_gold_params must equal that variant
    applied to the same base the test ran, so the eval and the live rule
    cannot drift apart."""
    base = LP.live_silver_params(GOLD_MAX_LEVERAGE, 12.0, starting_equity=G.START)
    chosen = dataclasses.replace(base, **G.variants()[G.BASELINE])
    assert LP.live_gold_params(GOLD_MAX_LEVERAGE, 12.0, starting_equity=G.START) == chosen
    p = LP.live_gold_params(GOLD_MAX_LEVERAGE, 12.0)
    assert (p.stop_buffer_atr, p.stop_buffer_floor_frac, p.entry_fit_leverage, p.risk_cap_frac) == (
        pytest.approx(1.3), pytest.approx(0.008), (20.0, 15.0), pytest.approx(0.15))
    assert (p.leverage, p.max_leverage, p.no_break_entry, p.min_r, p.entry_mode) == (20.0, 25.0, True, 2.0, "market")


def test_live_gold_params_follows_next_trade_not_a_copy(monkeypatch):
    """The floor and the fit are read from next_trade at call time: change
    the live rule there and the eval baseline moves with it."""
    import services.strategies.next_trade as NT
    monkeypatch.setitem(NT.STOP_BUFFER_FLOOR_FRAC_BY_COIN, GOLD, 0.005)
    monkeypatch.setattr(NT, "LEVERAGE_FIT_COINS", frozenset({NT.BRENT}))
    p = LP.live_gold_params(GOLD_MAX_LEVERAGE, 12.0)
    assert p.stop_buffer_floor_frac == pytest.approx(0.005) and p.entry_fit_leverage is None
    assert isinstance(p, Params)


# ── the sim and the live rule agree on gold ────────────────────────────────

def test_sim_setup_and_next_trade_agree_on_random_gold_markets():
    """Same question to desk_tick_sim._fit_leverage_setup (what the backtest
    traded) and next_trade (what the tick trades) on 3000 random gold-like
    markets: same trade or no trade, same stop, target and leverage."""
    import random

    from services.strategies.backtest.desk_tick_sim import Direction, _fit_leverage_setup, offset_target
    rng = random.Random(1105)
    p = LP.live_gold_params(GOLD_MAX_LEVERAGE, 12.0)
    agreed = traded = 0
    for _ in range(3000):
        mid = rng.uniform(3800.0, 4400.0)
        # the mid sits near one edge of a 24h range of 40 to 240 (1% to 6%); the
        # thesis entry is usually on the trade's side so chase refusals stay a minority
        side = rng.choice(["long", "short"])
        near, far = rng.uniform(-10.0, 40.0), rng.uniform(40.0, 240.0)
        low, high = (mid - near, mid + far) if side == "long" else (mid - far, mid + near)
        if high <= low:
            continue
        atr = rng.uniform(4.0, 30.0)
        entry_ceiling = mid + rng.uniform(-10.0, 40.0) * (1 if side == "long" else -1)
        lv = {"entry_low": low, "entry_high": high, "target_low": low, "target_high": high, "atr14": atr}
        setup, lev = _fit_leverage_setup(Direction(side=side, entry_ceiling=entry_ceiling), mid, lv, p)
        buf = stop_buffer_for(GOLD, atr, mid)
        nt = next_trade(side, mid, low, high, thesis_entry=entry_ceiling, leverage=20.0,
                        coin_max_leverage=GOLD_MAX_LEVERAGE, stop_buffer=buf, leverage_fit=leverage_fit_for(GOLD))
        sim_trades = setup is not None and "refused" not in setup
        assert nt["ok"] is sim_trades, (side, mid, low, high, atr, entry_ceiling, setup, nt.get("blockers"))
        if sim_trades:
            traded += 1
            assert nt["stop_px"] == pytest.approx(setup["stop_px"], abs=1e-6)
            assert nt["tp_px"] == pytest.approx(offset_target(setup["orig_target_px"], side, 0.005), abs=1e-6)
            assert nt["leverage"] == lev
        agreed += 1
    assert agreed > 2500 and traded > 100


# ── a silver thesis never trades on the gold desk ───────────────────────────

def test_a_silver_thesis_is_due_at_once_as_wrong_instrument():
    import importlib.util
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("thesis_due", root / "scripts" / "thesis_due.py")
    TD = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(TD)
    now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    silver = {"id": "metals-x", "created_at": (now - timedelta(minutes=5)).isoformat(),
              "call": {"side": "short", "instrument": "xyz:SILVER", "entry_px": 52.0, "stop_px": 52.6}}
    out = TD.refresh_due(silver, now, 52.1, primary_coin=GOLD)
    assert out["due"] is True and out["reasons"] == [
        "instrument xyz:SILVER is not the desk's primary xyz:GOLD: rewrite first"]      # past the 30-min gap
    gold = dict(silver, call=dict(silver["call"], instrument=GOLD, entry_px=4100.0, stop_px=4140.0))
    assert TD.refresh_due(gold, now, 4105.0, primary_coin=GOLD)["due"] is False       # inside the gap: held back
    flat = dict(silver, call={"side": "flat", "instrument": "xyz:SILVER"})
    assert TD.wrong_instrument(flat, GOLD) is None                                     # a flat call trades nothing
    assert TD.wrong_instrument(silver, None) is None                                   # no primary known: no reason


def test_the_tick_agent_and_the_watcher_say_gold():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    for rel in (".claude/agents/desk-tick.md", "agents/desk-tick.md"):
        text = (root / rel).read_text()
        assert "xyz:GOLD since 2026-11-05" in text and "a metals thesis on xyz:SILVER" in text, rel
        assert "gold's stop\n         buffer is the LARGER of 1.3 x the levels script's `atr14_1h` and" in text, rel
        assert "(gold: add `--max-lev 25`" in text, rel
    watcher = (root / "scripts" / "trigger_watch.py").read_text()
    assert 'parts.append(f"{desk}: thesis on {coin}, not {primary}")' in watcher   # a silver thesis never wakes the desk
