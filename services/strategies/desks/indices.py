"""Indices desk — HIP-3 index perps on Hyperliquid's xyz dex. Verified live
2026-10-17: xyz:XYZ100 $331.9M 24h volume (maxLeverage 30, the xyz dex's own
100-name composite index), xyz:SP500 $273.7M (maxLeverage 50, S&P 500
tracker) — both marked primary, no single anchor. xyz:JP225 $3.1M (Nikkei
225) and xyz:KR200 $1.3M (KOSPI 200) clear the $1M bar and ride along as
secondary for session-overlap context. xyz:IBOV and xyz:NIFTY exist in the
universe but read $0 24h volume — dead markets, excluded.
"""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument, TokenBudget

DESK_ID = "indices"


def build_spec() -> DeskSpec:
    return DeskSpec(
        id=DESK_ID,
        name="Indices",
        tagline="Broad index perps on Hyperliquid. Macro risk appetite, Fed path, earnings season.",
        instruments=[
            Instrument(coin="xyz:XYZ100", label="XYZ100", role="primary"),
            Instrument(coin="xyz:SP500", label="S&P 500", role="primary"),
            Instrument(coin="xyz:JP225", label="Nikkei 225", role="secondary"),
            Instrument(coin="xyz:KR200", label="KOSPI 200", role="secondary"),
        ],
        pairs=[],
        news_queries=["S&P 500 outlook", "stock market today", "Fed rate decision", "earnings season"],
        policy_topics=["FOMC rate decision", "US fiscal policy", "tariff policy"],
        prediction_market_queries=["Fed rate cut", "recession 2026", "S&P 500 target"],
        catalyst_kinds=["fomc", "cpi_release", "nfp_release", "earnings"],
        leverage_ladder=[5, 10, 20],
        horizon_days=7,
        token_budget=TokenBudget(),
        lessons=["index composition", "beta and correlation", "Fed path and risk appetite"],
    )
