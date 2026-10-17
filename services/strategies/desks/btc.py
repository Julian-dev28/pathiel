"""BTC desk — the single largest, most liquid perp on Hyperliquid's main dex.
Verified live 2026-10-17: $2.85B 24h volume, maxLeverage 40."""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument, TokenBudget

DESK_ID = "btc"


def build_spec() -> DeskSpec:
    return DeskSpec(
        id=DESK_ID,
        name="BTC",
        tagline="Bitcoin perp on Hyperliquid. Macro liquidity, ETF flows, halving cycle.",
        instruments=[Instrument(coin="BTC", label="Bitcoin", role="primary")],
        pairs=[],
        news_queries=["bitcoin price", "bitcoin ETF flows", "bitcoin halving", "bitcoin regulation"],
        policy_topics=["SEC bitcoin ETF", "US crypto regulation", "bitcoin reserve legislation"],
        prediction_market_queries=["bitcoin price target", "bitcoin ETF approval"],
        catalyst_kinds=["fomc", "cpi_release", "etf_flow_report", "halving"],
        leverage_ladder=[5, 10, 20],
        horizon_days=7,
        token_budget=TokenBudget(),
        lessons=["funding rate basics", "ETF flow as a demand proxy", "halving cycle"],
    )
