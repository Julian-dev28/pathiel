"""Order-size normalization that must happen before live risk gates."""

import math

from hermes_trader.client import exchange as ex


def test_min_entry_notional_honors_integer_size_precision(monkeypatch):
    monkeypatch.setattr(ex, "get_coin_index", lambda coin: (0, 0, 4))

    # $10.50 / $0.083 = 126.5 coins, but integer-size markets require 127.
    min_notional = ex.min_entry_notional_usd("MEGA", 0.083)

    assert math.isclose(min_notional, 127 * 0.083)


def test_entry_size_for_notional_matches_hl_minimum(monkeypatch):
    monkeypatch.setattr(ex, "get_coin_index", lambda coin: (0, 1, 4))

    size = ex.entry_size_for_notional("DEC", 12.0, 4.0)
    undersized = ex.entry_size_for_notional("DEC", 1.0, 4.0)

    assert size == 3.0
    assert undersized == 2.7  # ceil($10.50 / $4.00 to 0.1 coin)
