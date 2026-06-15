"""Tests for the free FINRA short-volume engine (pure functions; no network)."""

from hermes_trader.agents.short_volume import (
    parse_finra_shvol, classify_short_regime, build_report, ShortVolDay,
)

_SAMPLE = """Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20260615|HOOD|700000|0|1000000|B,Q,N
20260615|NVDA|300000|0|1000000|B,Q,N
Records: 2
"""


def test_parse_basic():
    rows = parse_finra_shvol(_SAMPLE)
    assert len(rows) == 2
    hood = next(r for r in rows if r.symbol == "HOOD")
    assert hood.short_vol == 700000 and hood.total_vol == 1000000
    assert abs(hood.ratio - 0.70) < 1e-9


def test_parse_filter_symbol_and_skips_footer():
    rows = parse_finra_shvol(_SAMPLE, want_symbol="nvda")
    assert len(rows) == 1 and rows[0].symbol == "NVDA"
    # header + "Records:" footer must not parse as rows
    assert all(r.date.isdigit() for r in rows)


def test_classify_regime():
    assert classify_short_regime(0.70) == "crowded_short_squeeze_fuel"
    assert classify_short_regime(0.45) == "neutral"
    assert classify_short_regime(0.20) == "light_short"


def test_build_report_trend_rising():
    days = [
        ShortVolDay("20260611", "HOOD", 400000, 0, 1000000),  # .40
        ShortVolDay("20260612", "HOOD", 500000, 0, 1000000),  # .50
        ShortVolDay("20260615", "HOOD", 700000, 0, 1000000),  # .70
    ]
    rep = build_report("HOOD", days)
    assert rep.ratio == 0.70
    assert rep.regime == "crowded_short_squeeze_fuel"
    assert rep.trend == "rising"
    assert rep.series == [0.4, 0.5, 0.7]
    assert rep.date == "20260615"          # newest


def test_build_report_empty():
    assert build_report("HOOD", []) is None
    assert build_report("ZZZZ", [ShortVolDay("20260615", "HOOD", 1, 0, 2)]) is None
