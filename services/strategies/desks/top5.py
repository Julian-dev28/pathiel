"""Top5 desk — the five largest native crypto perps on Hyperliquid's main dex
by 24h notional volume. All five are marked `primary` (no single anchor asset
for a basket desk), so each gets its own forward-stats + ladder.

Verified live 2026-10-17 via pathiel.client.universe.get_universe(include_hip3=True),
ranked by dayNtlVlm, native dex only (dex is None): BTC $2.85B, ETH $991.9M,
SOL $323.7M, HYPE $318.0M, ZEC $304.2M. This ranking drifts — regenerate with:
    PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m services.strategies.desks._verify top5
and paste the new top 5 in here. Keeping it a static, reviewed list (rather
than re-ranking live on every refresh) keeps DeskSpec construction offline
and the ladder/forward-stats math reproducible run to run.
"""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument, TokenBudget

DESK_ID = "top5"


def build_spec() -> DeskSpec:
    return DeskSpec(
        id=DESK_ID,
        name="Top 5 Crypto",
        tagline="The five largest crypto perps on Hyperliquid's main dex by 24h volume.",
        instruments=[
            Instrument(coin="BTC", label="Bitcoin", role="primary"),
            Instrument(coin="ETH", label="Ethereum", role="primary"),
            Instrument(coin="SOL", label="Solana", role="primary"),
            Instrument(coin="HYPE", label="Hyperliquid", role="primary"),
            Instrument(coin="ZEC", label="Zcash", role="primary"),
        ],
        pairs=[],
        news_queries=[
            "bitcoin price", "ethereum price", "solana price", "hyperliquid HYPE",
            "zcash price", "altcoin rotation",
        ],
        policy_topics=["US crypto regulation", "SEC enforcement", "stablecoin bill"],
        prediction_market_queries=["bitcoin price target", "ethereum ETF", "altcoin season"],
        catalyst_kinds=["fomc", "cpi_release", "etf_flow_report", "token_unlock"],
        leverage_ladder=[5, 10, 20],
        horizon_days=7,
        token_budget=TokenBudget(),
        lessons=["market-cap rotation", "beta to BTC", "funding rate basics"],
    )
