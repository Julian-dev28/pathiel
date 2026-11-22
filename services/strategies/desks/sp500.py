"""S&P 500 desk: one instrument, xyz:SP500, long or short. Built 2026-11-19;
operator: "ADD A S&P500 MARKET STRATEGY 50X 5% LONG OR SHORT". Ships
INACTIVE: the desk ticks `SKIP desk not active` until the operator adds it
to `active_desks` with a `desk_equity_share` (docs/strategy/SP500_STRATEGY.md
has the exact config change).

Verified against Hyperliquid's live `/info` (metaAndAssetCtxs, dex="xyz") on
2026-11-19: xyz:SP500 $682M 24h volume, maxLeverage 50, one margin tier
(maintenance margin 1%), szDecimals 3, spread 0.1 on 7,179.5 (0.14 bps),
growth mode on (the desk's fills on this dex pay about 0.9 bps a side). It
trades around the clock, weekends included, with no gap in 208 days of 1h
bars; the weekend book is about fifteen times thinner than the cash session.

Why its own desk and not the `indices` desk: `indices` lists four index
perps with two primaries and has never been active. A desk trades ONE
position on its primary and sizes off its own equity share, and the
operator asked for the S&P alone at its own leverage (50x; every other desk
caps at the risk profile's 20x: services/strategies/desk_leverage.py). One
instrument on purpose: every coin a desk lists counts toward its one slot,
so a second index here would let an unrelated hand-placed trade block it.

The rule and its test: services/strategies/next_trade.SP500_RULE,
docs/strategy/SP500_BACKTEST_2026-11-19.md. Verdict NOISE: the rule has no
measured edge without a thesis, and no S&P thesis has been graded yet.
"""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument, TokenBudget

DESK_ID = "sp500"
COIN = "xyz:SP500"


def build_spec() -> DeskSpec:
    return DeskSpec(
        id=DESK_ID,
        name="S&P 500",
        tagline="S&P 500 perp on Hyperliquid, long or short. The Fed and Treasury yields, "
                "megacap earnings, US macro data, tariffs, risk appetite.",
        instruments=[Instrument(coin=COIN, label="S&P 500", role="primary")],
        pairs=[],
        # Eight phrases: services/intel/sources/news.py sends one Google News
        # request per phrase (GNEWS_MAX_QUERIES = 8) plus one broad OR query
        # over the first eight, so every phrase here is searched. What moves
        # the index: the Fed path and yields, the megacaps that are a third
        # of its weight (Nvidia first), the monthly jobs and inflation
        # prints, tariffs, and the day's own tape.
        news_queries=["S&P 500", "stock market today", "Federal Reserve rate decision", "Treasury yields",
                      "Nvidia earnings", "megacap tech earnings", "US jobs report inflation", "tariffs stocks"],
        policy_topics=["FOMC rate decision", "Treasury yields and the dollar", "tariff policy",
                       "US fiscal policy and government funding", "AI chip export controls"],
        # "Fed December" carries the live hike-or-hold odds for the next
        # meeting ("Fed rate cut" alone finds the yearly cut-count markets).
        prediction_market_queries=["Fed rate cut", "Fed December", "recession", "S&P 500", "government shutdown"],
        # FOMC and CPI are always on (services/intel/sources/catalysts.py).
        # Jobs (NFP), PCE, PPI, retail sales, Fed minutes, holidays, the
        # megacap earnings rows and the funding deadlines come from
        # services/intel/data/scheduled_events.json through the "sp500" coin
        # group (services/intel/scheduled_events.py).
        catalyst_kinds=["fomc", "cpi"],
        leverage_ladder=[10, 25, 50],
        horizon_days=7,
        token_budget=TokenBudget(),
        lessons=["the Fed path and equity multiples", "yields versus stocks", "megacap weight in the index",
                 "macro data days", "overnight and cash sessions"],
    )
