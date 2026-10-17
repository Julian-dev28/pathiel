"""Metals desk — gold and silver HIP-3 perps. Verified live 2026-10-17:
xyz:GOLD $51.2M 24h volume (maxLeverage 25), xyz:SILVER $130.7M (maxLeverage
25). Scoped to exactly these two per the build brief ("metals = gold/silver").

No PairSpec here on purpose: the contract's PairSnapshot models a spread as
`long_mid - short_mid` (correct for Brent-WTI, which trade in the same $/bbl
units and similar magnitude). Gold ($4163) and silver ($56.9) are ~73x apart,
so a raw price difference is dominated by gold's own moves and is not the
trade anyone actually watches — the real pair trade is the gold/silver RATIO.
Bolting a ratio onto PairSnapshot would mean adding fields other agents'
fixtures don't have mid-swarm; left for a future CONTRACT_VERSION bump
instead of a unilateral change here. The ratio lives as a qualitative lesson
topic below until that happens.
"""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument, TokenBudget

DESK_ID = "metals"


def build_spec() -> DeskSpec:
    return DeskSpec(
        id=DESK_ID,
        name="Metals",
        tagline="Gold and silver perps on Hyperliquid. Real yields, dollar, safe-haven flows.",
        instruments=[
            Instrument(coin="xyz:GOLD", label="Gold", role="primary"),
            Instrument(coin="xyz:SILVER", label="Silver", role="secondary"),
        ],
        pairs=[],
        news_queries=["gold price", "silver price", "safe haven demand", "central bank gold buying"],
        policy_topics=["Fed rate decision", "real yields", "dollar index policy"],
        prediction_market_queries=["gold price target", "Fed rate cut"],
        catalyst_kinds=["fomc", "cpi_release", "nfp_release", "cftc_cot"],
        leverage_ladder=[5, 10, 20],
        horizon_days=7,
        token_budget=TokenBudget(),
        lessons=["gold-silver ratio", "real yields vs gold", "safe-haven flows"],
    )
