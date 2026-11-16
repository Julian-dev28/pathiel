"""Round trips from fills, and the kill switch read off them. No network."""
from __future__ import annotations

import dataclasses

from services.fib_sleeve import ledger
from services.fib_sleeve.config import SleeveConfig
from services.fib_sleeve.tests import _scenario as S

CFG = SleeveConfig()


def test_round_trips_net_is_closed_pnl_less_every_fee():
    fills = [
        S.fill(1000, "B", 0.2, 100.0, 0.0, fee=0.01),
        S.fill(2000, "B", 0.1, 99.0, 0.2, fee=0.01),              # the entry filled in two pieces
        S.fill(3000, "A", 0.3, 103.0, 0.3, pnl=1.0, fee=0.02),    # target
        S.fill(4000, "A", 0.3, 103.0, 0.0, fee=0.01),             # a short opens
        {"coin": "xyz:CL", "time": 4500, "side": "B", "sz": "1", "px": "60", "startPosition": "0",
         "closedPnl": "-9", "fee": "1", "tid": 9},                # another coin: not the sleeve's
    ]
    closed, open_trade = ledger.round_trips(fills, S.COIN)
    assert len(closed) == 1
    t = closed[0]
    assert (t["side"], t["open_ms"], t["close_ms"], t["fills"]) == ("long", 1000, 3000, 3)
    assert t["net_usd"] == round(1.0 - 0.04, 6) and t["size"] == 0.3
    assert open_trade["side"] == "short" and open_trade["open_ms"] == 4000


def test_bare_symbol_rows_count_for_a_hip3_coin():
    fills = [dict(S.fill(1, "B", 1, 100, 0), coin="BRENTOIL"), dict(S.fill(2, "A", 1, 99, 1, pnl=-1), coin="BRENTOIL")]
    closed, open_trade = ledger.round_trips(fills, S.COIN)
    assert len(closed) == 1 and open_trade is None and closed[0]["net_usd"] < 0


def test_summary_counts_the_trailing_losses_only():
    fills = S.losing_trips(2) + [S.fill(S.T0 + 10 * S.HOUR, "B", 1, 100, 0),
                                 S.fill(S.T0 + 11 * S.HOUR, "A", 1, 102, 1, pnl=2.0)] \
        + S.losing_trips(2, start=S.T0 + 20 * S.HOUR)
    closed, _ = ledger.round_trips(fills, S.COIN)
    s = ledger.summary(closed)
    assert s == {"trades": 5, "wins": 1, "net_usd": round(4 * -0.52 + 1.98, 4), "consecutive_losses": 2}


def test_kill_after_three_consecutive_losses():
    two, _ = ledger.round_trips(S.losing_trips(2, loss=0.1), S.COIN)
    three, _ = ledger.round_trips(S.losing_trips(3, loss=0.1), S.COIN)
    assert ledger.kill_reason(CFG, two, 20.0, 19.7, True) is None
    assert "3 consecutive losses" in ledger.kill_reason(CFG, three, 20.0, 19.6, True)


def test_a_break_even_exit_counts_as_a_loss_because_it_pays_fees():
    fills = []
    for i in range(3):
        fills += [S.fill(S.T0 + i * S.HOUR, "B", 1, 100, 0), S.fill(S.T0 + i * S.HOUR + 1, "A", 1, 100, 1, pnl=0.0)]
    closed, _ = ledger.round_trips(fills, S.COIN)
    assert ledger.summary(closed)["consecutive_losses"] == 3


def test_kill_on_twenty_percent_realized_loss_of_the_starting_equity():
    cfg = dataclasses.replace(CFG, max_consecutive_losses=99)
    one, _ = ledger.round_trips(S.losing_trips(1, loss=3.97), S.COIN)      # -3.99 with fees: 19.95%
    assert ledger.kill_reason(cfg, one, 20.0, None, False) is None
    more, _ = ledger.round_trips(S.losing_trips(1, loss=3.98), S.COIN)     # -4.00: 20%
    assert "realized loss" in ledger.kill_reason(cfg, more, 20.0, None, False)


def test_kill_on_equity_drawdown_only_when_flat():
    assert "under the" in ledger.kill_reason(CFG, [], 20.0, 16.0, True)
    assert ledger.kill_reason(CFG, [], 20.0, 16.01, True) is None
    assert ledger.kill_reason(CFG, [], 20.0, 5.0, False) is None          # in a position: margin, not a loss
    assert ledger.kill_reason(CFG, [], None, 0.0, True) is None           # no starting equity known yet
