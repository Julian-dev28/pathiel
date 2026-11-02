"""Gate tests for the 2026-11-02 loss audit (services/strategies/evals/loss_audit.py):
trades rebuilt from fills (fill order inside one order, top-ups, open
trades), the exit kind and opening stop from the order history, equity
before a trade with outside money in between, the price path, the thesis
flip, the four cause tags and the cause table.

No network. The fills and orders are the real rows of the pathiel wallet
(public userFills / historicalOrders) for the two Brent longs that frame
the loss: Sat 10-31 (three fills of one order, returned out of order by
trade id, plus a top-up, closed by hand) and Mon 11-02 (stopped at 109.23),
cut to the fields the audit reads.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.data import ThesisDirection
from services.strategies.evals import loss_audit as LA

HOUR_MS = 3_600_000
FIVE = 300_000


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


def fill(t, side, px, sz, start, pnl=0.0, fee=0.0, oid=1, tid=0, coin="xyz:BRENTOIL"):
    return {"coin": coin, "time": t, "side": side, "px": str(px), "sz": str(sz), "startPosition": str(start),
            "closedPnl": str(pnl), "fee": str(fee), "oid": oid, "tid": tid}


T1031 = 1793463511766      # 2026-10-31 16:18:31Z
T1101_ADD = 1793556261422  # 2026-11-01 18:04:21Z
T1101_OUT = 1793557130081  # 2026-11-01 18:18:50Z
T1102 = 1793591428254      # 2026-11-02 03:50:28Z
T1102_OUT = 1793608515729  # 2026-11-02 08:35:15Z

FILLS = [
    # the 10-31 long: tids come back 36.68, 31.1, 0.0 (not fill order)
    fill(T1031, "B", 111.48, 4.05, 36.68, fee=0.040634, oid=591501984153, tid=160496603983440),
    fill(T1031, "B", 111.48, 5.58, 31.1, fee=0.055985, oid=591501984153, tid=631487058718932),
    fill(T1031, "B", 111.48, 31.1, 0.0, fee=0.312032, oid=591501984153, tid=877438890163887),
    fill(T1101_ADD, "B", 111.8, 2.77, 40.73, fee=0.00929, oid=591934904031, tid=1),
    fill(T1101_OUT, "A", 111.7, 43.5, 43.5, pnl=8.68695, fee=0.437305, oid=592037182420, tid=2),
    # the 11-02 long: four fills of one market order, then the stop
    fill(T1102, "B", 110.01, 13.6, 28.65, fee=0.134652, oid=592267444527, tid=11),
    fill(T1102, "B", 110.01, 0.8, 0.0, fee=0.00792, oid=592267444527, tid=99),
    fill(T1102, "B", 110.01, 17.7, 10.95, fee=0.175245, oid=592267444527, tid=12),
    fill(T1102, "B", 110.01, 10.15, 0.8, fee=0.100494, oid=592267444527, tid=50),
    fill(1793600945884, "B", 110.0, 0.11, 42.25, fee=0.000362, oid=592330079912, tid=13),
    fill(T1102_OUT, "A", 109.23, 42.36, 42.36, pnl=-33.036564, fee=0.416428, oid=592335863711, tid=14),
    # a silver short still open
    fill(T1102 + 6_000_000, "A", 50.522, 9.41, 0.0, fee=0.04, oid=7, tid=15, coin="xyz:SILVER"),
]


def order(t, oid, side, typ, trigger, status="open", coin="xyz:BRENTOIL"):
    return {"order": {"coin": coin, "oid": oid, "timestamp": t, "side": side, "orderType": typ,
                      "triggerPx": str(trigger), "limitPx": str(trigger)}, "status": status, "statusTimestamp": t}


ORDERS = [
    order(T1031 + 1000, 591501990373, "A", "Stop Market", 111.02),
    order(T1031 + 2000, 591501994592, "A", "Take Profit Limit", 113.66),
    order(T1101_OUT, 592037182420, "A", "Limit", 0.0, "filled"),
    order(T1102 + 1000, 592267453528, "A", "Stop Market", 109.25),
    order(T1102 + 1000, 592267460727, "A", "Take Profit Limit", 111.83),
    order(T1102 + 9_788_000, 592335863711, "A", "Stop Market", 109.25),       # the folded stop (06:33Z)
    order(T1102_OUT, 592335863711, "A", "Stop Market", 0.0, "filled"),
]


def test_chain_fills_follows_the_position_not_the_trade_id():
    rows = LA.chain_fills([f for f in FILLS if f["time"] == T1031])
    assert [float(f["startPosition"]) for f in rows] == [0.0, 31.1, 36.68]
    rows = LA.chain_fills([f for f in FILLS if f["time"] == T1102])
    assert [float(f["startPosition"]) for f in rows] == [0.0, 0.8, 10.95, 28.65]


def test_reconstruct_trades():
    trs = LA.reconstruct_trades(FILLS, since_ms=ms(2026, 10, 20))
    a, b, s = trs
    assert (a["side"], a["exit_time"], a["max_size"], a["adds"]) == ("long", T1101_OUT, pytest.approx(43.5), 1)
    assert a["entry_px"] == pytest.approx(111.48) and a["exit_px"] == pytest.approx(111.7)
    assert a["avg_entry_px"] == pytest.approx((40.73 * 111.48 + 2.77 * 111.8) / 43.5)
    assert a["net_usd"] == pytest.approx(7.83, abs=0.005)               # the operator's +$7.83
    assert b["net_usd"] == pytest.approx(-33.036564 - 0.835101, abs=1e-6)  # both fees: -$33.87
    assert b["max_size"] == pytest.approx(42.36)
    assert s["coin"] == "xyz:SILVER" and s["exit_time"] is None
    assert [t["entry_time"] for t in LA.reconstruct_trades(FILLS, since_ms=ms(2026, 11, 2))] == [
        T1102, s["entry_time"]]


def test_a_position_opened_before_the_fills_is_skipped():
    tail = [fill(1000, "A", 100.0, 1.0, 1.0, pnl=1.0)]
    assert LA.reconstruct_trades(tail, since_ms=0) == []


def test_exit_kind_and_opening_levels():
    a, b, _ = LA.reconstruct_trades(FILLS, since_ms=0)
    assert LA.exit_kind(a, ORDERS) == "manual"
    assert LA.exit_kind(b, ORDERS) == "stop"
    assert LA.initial_levels(a, ORDERS) == {"stop_px": 111.02, "target_px": 113.66}
    assert LA.initial_levels(b, ORDERS) == {"stop_px": 109.25, "target_px": 111.83}


def test_equity_before_with_outside_money():
    portfolio = [["day", {"accountValueHistory": [[100, "50.0"], [300, "80.0"]]}],
                 ["perpDay", {"accountValueHistory": [[100, "49.0"]]}]]
    ledger = [{"time": 150, "delta": {"type": "send", "user": "0xabc", "destination": LA.USER.lower(),
                                      "usdcValue": "20.0"}},
              {"time": 160, "delta": {"type": "send", "user": LA.USER, "destination": LA.USER,
                                      "usdcValue": "5.0"}},          # between own dexes: not a flow
              {"time": 170, "delta": {"type": "send", "user": LA.USER, "destination": "0xdef",
                                      "usdcValue": "3.0", "fee": "1.0"}}]
    samples, flows = LA.equity_samples(portfolio), LA.external_flows(ledger)
    assert samples == [(100, 50.0), (300, 80.0)]
    assert flows == [(150, 20.0), (170, -4.0)]
    assert LA.equity_at(200, samples, flows) == pytest.approx(66.0)
    assert LA.equity_at(300, samples, flows) == pytest.approx(80.0)
    assert LA.equity_at(50, samples, flows) is None


def _bars(t0, rows):
    return [Candle(t=t0 + i * FIVE, o=o, h=h, l=lo, c=c, v=1.0) for i, (o, h, lo, c) in enumerate(rows)]


def test_excursion_and_after_exit():
    tr = {"side": "long", "entry_time": 1_000_000 + 60_000, "exit_time": 1_000_000 + 4 * FIVE + 60_000,
          "entry_px": 100.0, "avg_entry_px": 100.0, "target_px": 103.0}
    bars = _bars(1_000_000, [(100, 109, 99, 100),          # the fill bar: its high may predate the fill
                             (100, 101.5, 99.8, 101), (101, 102, 100.5, 101), (101, 101, 98.9, 99),
                             (99, 99.2, 98.5, 99),          # the exit bar
                             (99, 99.5, 98.8, 99.4), (99.4, 100.2, 99.3, 100.1), (100.1, 103.5, 100, 103)])
    ex = LA.excursion(tr, bars)
    assert ex == {"mfe_px": 102.0, "mae_px": 98.9}
    after = LA.after_exit(tr, bars, hours=24.0)
    assert after["back_to_entry_h"] == pytest.approx((6 * FIVE + FIVE - (4 * FIVE + 60_000)) / HOUR_MS, abs=0.01)
    assert after["back_to_tp_h"] == pytest.approx((8 * FIVE - (4 * FIVE + 60_000)) / HOUR_MS, abs=0.01)


def _thesis(parts, side):
    return ThesisDirection(created_at_ms=ms(*parts), side=side, entry_px=None, stop_px=None,
                           invalidation_px=None, targets=[], thesis_id="t")


def test_thesis_flip_after_and_thesis_at():
    theses = [_thesis((2026, 11, 1, 18, 19), "long"), _thesis((2026, 11, 2, 3, 48), "long"),
              _thesis((2026, 11, 2, 8, 40), "short")]
    tr = {"side": "long", "exit_time": T1102_OUT}
    assert LA.thesis_flip_after(tr, theses) == pytest.approx(0.08, abs=0.01)    # 5 minutes
    assert LA.thesis_flip_after({"side": "short", "exit_time": T1102_OUT}, theses) is None
    assert LA.thesis_flip_after({"side": "long", "exit_time": T1102_OUT - 13 * HOUR_MS}, theses) is None
    assert LA.thesis_at(T1102, theses).created_at_ms == ms(2026, 11, 2, 3, 48)


def _tagged(**kw):
    base = {"net_usd": 1.0, "exit_kind": "target", "back_to_entry_h": None, "flip_after_h": None,
            "mfe_r": 0.5, "exit_r": 1.0, "equity_at_size": 100.0}
    base.update(kw)
    return LA.tag_trade(base)


def test_tags():
    assert _tagged() == []
    assert _tagged(net_usd=-5, flip_after_h=3.0) == ["wrong_direction"]
    assert _tagged(net_usd=5, flip_after_h=3.0) == []                         # a win is never wrong-way
    assert _tagged(net_usd=-5, exit_kind="stop", back_to_entry_h=11.9) == ["stop_too_tight"]
    assert _tagged(net_usd=-5, exit_kind="stop", back_to_entry_h=12.5) == []
    assert _tagged(net_usd=-5, exit_kind="manual", back_to_entry_h=1.0) == []
    assert _tagged(mfe_r=1.0, exit_r=0.49) == ["round_trip"]
    assert _tagged(mfe_r=0.99, exit_r=-1.0) == []
    assert _tagged(net_usd=-10.01) == ["size"]
    assert _tagged(net_usd=-10.0) == []


def test_the_two_brent_longs_end_to_end():
    """11-02: wrong way (the short thesis came 5 minutes after the stop) and
    size (13% of the account). 10-31: a round trip (+2.2R, closed +0.48R)."""
    theses = {"oil": [_thesis((2026, 10, 31, 1, 53), "long"), _thesis((2026, 11, 2, 3, 48), "long"),
                      _thesis((2026, 11, 2, 8, 40), "short")]}
    portfolio = [["week", {"accountValueHistory": [[T1031 - 60_000, "276.0"], [T1102 - 60_000, "261.0"]]}]]
    bars = {"xyz:BRENTOIL": _bars(T1031 - T1031 % FIVE, [(111.5, 111.6, 111.4, 111.5)] + [
        (111.6, 112.5, 111.5, 112.0)] * 3 + [(111.8, 111.9, 111.6, 111.7)] * 4)
        + _bars(T1102 - T1102 % FIVE, [(110.0, 110.1, 109.9, 110.0)] + [(110.0, 110.5, 109.6, 110.0)] * 2)}
    rep = LA.run_audit({"fills": FILLS, "orders": ORDERS, "portfolio": portfolio, "ledger": [], "funding": []},
                       bars, theses, since_ms=ms(2026, 10, 20))
    a, b = [t for t in rep["trades"] if t["exit_time"] is not None]
    assert a["tags"] == ["round_trip"] and a["mfe_r"] == pytest.approx((112.5 - 111.48) / 0.46, abs=0.01)
    assert a["exit_r"] == pytest.approx(0.48, abs=0.01)
    assert b["tags"] == ["wrong_direction", "size"] and b["primary"] == "wrong_direction"
    assert b["pct_equity"] == pytest.approx(-13.0, abs=0.1) and b["r_unit"] == pytest.approx(0.76)
    ct = rep["causes"]["since_10_31"]
    assert ct["total"]["net_usd"] == pytest.approx(7.83 - 33.87, abs=0.01)
    assert sum(r["net_usd"] for r in ct["by_primary"]) == pytest.approx(ct["total"]["net_usd"], abs=0.02)
    assert ct["by_primary"][0]["cause"] == "wrong_direction"                   # ranked by $ lost
    assert {r["cause"] for r in ct["by_tag"]} == {"wrong_direction", "size", "round_trip"}
    size = next(r for r in ct["by_tag"] if r["cause"] == "size")
    assert size["at_stake_usd"] == pytest.approx(33.87 - 26.1, abs=0.01)       # beyond 10% of $261
    rt = next(r for r in ct["by_tag"] if r["cause"] == "round_trip")
    assert rt["at_stake_usd"] == pytest.approx((0.5 * 0.46 - 0.22) * 43.5, abs=0.01)
    assert rep["open"][0]["coin"] == "xyz:SILVER"
