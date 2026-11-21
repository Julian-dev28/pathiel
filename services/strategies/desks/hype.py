"""HYPE desk: one instrument, HYPE (Hyperliquid's own token, a main-dex
perp -- no xyz: prefix), long or short. Built 2026-11-20; operator: "Start
the research on HYPE and desk for hype, maybe." Ships INACTIVE: the desk
ticks `SKIP desk not active` until the operator adds it to `active_desks`
with a `desk_equity_share` (docs/strategy/HYPE_STRATEGY.md has the exact
config change).

Verified against Hyperliquid's live `/info` (meta/metaAndAssetCtxs,
dex=None -- the main perp dex) on 2026-11-20: HYPE maxLeverage 10,
marginTableId 52, mid ~$65, $636M 24h volume, $18.7M open interest, spread
about 2.5 bps, $20,000 of one-sided market buy pressure moves the book only
0.019%. It trades around the clock, weekends included, with no gap in 208
days of 1h bars; weekend hours range about 30% less than weekday ones but
are not meaningfully thinner in a way that matters at this desk's size.

Why its own desk: HYPE is Hyperliquid's own governance/fee token, the
clearest "the exchange's health is the thesis" instrument on the venue, and
belongs with none of the other desks (not an index, not a megacap, not a
commodity). One instrument on purpose, same reasoning as gas.py and
sp500.py: every coin a desk lists counts toward its one slot.

The rule and its test: services/strategies/next_trade.HYPE_RULE,
docs/strategy/HYPE_BACKTEST_2026-11-20.md. Verdict EDGE on the research
bar for a 120h-trend-gated version of the oil/sp500 24h-range rule (the
plain, ungated rule is a clear loser here -- oil fades structure, HYPE
trends). A second family (a Fibonacci pullback) also cleared the bar but is
not wired live; see the backtest doc.
"""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument, TokenBudget

DESK_ID = "hype"
COIN = "HYPE"


def build_spec() -> DeskSpec:
    return DeskSpec(
        id=DESK_ID,
        name="HYPE",
        tagline="Hyperliquid's own token perp, long or short. Protocol revenue and buybacks, "
                "token unlocks, exchange competition, BTC and the wider crypto tape.",
        instruments=[Instrument(coin=COIN, label="Hyperliquid", role="primary")],
        pairs=[],
        # Eight phrases: services/intel/sources/news.py sends one Google News
        # request per phrase (GNEWS_MAX_QUERIES = 8) plus one broad OR query
        # over the first eight, so every phrase here is searched. What moves
        # HYPE: the protocol itself (HIP governance, the assistance fund's
        # buybacks, HLP vault performance), security (an exploit or bridge
        # issue would be existential for a perp DEX token), competition
        # (rival DEXes, a CEX listing or delisting), token unlocks/vesting,
        # and the wider crypto tape it is beta-exposed to (BTC moves, crypto
        # regulation).
        news_queries=["Hyperliquid", "HYPE token", "Hyperliquid buyback", "Hyperliquid exploit OR hack",
                      "perp DEX volume", "crypto exchange regulation", "bitcoin price", "HYPE token unlock"],
        policy_topics=["crypto market structure legislation", "CFTC crypto derivatives",
                       "stablecoin regulation", "offshore exchange enforcement"],
        prediction_market_queries=["Hyperliquid", "HYPE price", "bitcoin price target", "crypto regulation"],
        # FOMC and CPI are always on (services/intel/sources/catalysts.py);
        # token_unlock turns on automatically for any crypto-ticker desk
        # (desk_rules.is_crypto_desk reads pathiel/agents/unlock_map.json,
        # which lists "Hyperliquid": "HYPE"). Nothing coin-specific to add
        # here beyond that.
        catalyst_kinds=["fomc", "cpi"],
        leverage_ladder=[5, 10],
        horizon_days=7,
        token_budget=TokenBudget(),
        lessons=["protocol revenue and buybacks", "HLP vault and open interest", "token unlock schedule",
                 "perp DEX competition", "BTC beta and the crypto tape"],
    )
