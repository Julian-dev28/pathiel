"""Tests for the free crypto whale-flow engine (pure functions; no network)."""

from hermes_trader.agents.crypto_whale import (
    binance_symbol, parse_aggtrades, compute_whale_flow, Print,
)


def test_binance_symbol():
    assert binance_symbol("BTC") == "BTCUSDT"
    assert binance_symbol("kPEPE") == "PEPEUSDT"
    assert binance_symbol("xyz:NVDA") is None      # equities have no Binance whale feed


def test_parse_aggtrades_side():
    payload = [
        {"p": "100", "q": "2", "T": 1, "m": False},  # taker BUY
        {"p": "100", "q": "1", "T": 2, "m": True},   # taker SELL
        {"bad": "row"},                              # skipped
    ]
    prints = parse_aggtrades(payload)
    assert len(prints) == 2
    assert prints[0].is_buy is True and prints[0].usd == 200
    assert prints[1].is_buy is False


def test_whale_flow_buying():
    prints = [
        Print(100, 2000, 1, True),    # $200k aggressive BUY
        Print(100, 50, 2, False),     # $5k SELL (below threshold, ignored)
        Print(100, 1500, 3, False),   # $150k aggressive SELL
    ]
    r = compute_whale_flow(prints, min_usd=100_000)
    assert r.whale_n == 2
    assert r.buy_usd == 200_000 and r.sell_usd == 150_000
    assert r.net_usd == 50_000
    assert r.bias == "balanced"       # 50k/350k = 14% < 20% imbalance


def test_whale_flow_strong_buy_bias():
    prints = [Print(100, 5000, 1, True), Print(100, 1000, 2, False)]  # 500k buy vs 100k sell
    r = compute_whale_flow(prints, min_usd=100_000)
    assert r.bias == "whale_buying" and r.net_usd == 400_000


def test_whale_flow_strong_sell_bias():
    prints = [Print(100, 1000, 1, True), Print(100, 5000, 2, False)]
    r = compute_whale_flow(prints, min_usd=100_000)
    assert r.bias == "whale_selling" and r.net_usd == -400_000


def test_whale_flow_empty():
    r = compute_whale_flow([], min_usd=100_000)
    assert r.whale_n == 0 and r.bias == "balanced" and r.net_usd == 0
