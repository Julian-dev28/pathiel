"""Natural gas desk — xyz:NATGAS only, no secondary, no pair. The edge here
is distinct from oil's: the Thursday 14:30 UTC (10:30 ET) EIA Weekly Natural
Gas Storage Report versus consensus, weather (heating degree days in winter,
cooling degree days in summer drive power-sector gas burn), and LNG export
feedgas flows (every incremental export terminal pulls supply off the
domestic balance). oil.py already carries xyz:NATGAS as a hedge instrument
for the oil desk's own Brent/WTI focus; this desk makes Nat Gas the primary
story with the deeper, gas-specific news/policy/catalyst coverage that
belongs to it rather than to oil.

Verified against Hyperliquid's live `/info` (metaAndAssetCtxs, dex="xyz") on
2026-10-23 06:00 UTC: xyz:NATGAS $16,084,503 24h volume, $3,289,754 open
interest, maxLeverage 10 — a real, comfortably-liquid market (matches the
build brief's "about $17M/day"). No other natural-gas-named perp exists on
Hyperliquid (same universe dump oil.py's docstring searched).
Regenerate this check with:
    PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m services.strategies.desks._verify gas

catalyst_kinds: "eia_gas_storage" (this desk's own weekly storage-report
generator, services/intel/calendar_us.py:eia_gas_storage_release) plus
"fomc" and "cftc_cot" per the build brief — "fomc" is always on for every
desk regardless (services/intel/sources/catalysts.py:_ALWAYS_ON_KINDS) but
is listed explicitly here anyway, same as oil.py does, so a reader of this
file sees the full intended catalyst set without having to know that rule.
"""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument, TokenBudget

DESK_ID = "gas"


def build_spec() -> DeskSpec:
    return DeskSpec(
        id=DESK_ID,
        name="Nat Gas",
        tagline="Henry Hub natural gas perp on Hyperliquid. Storage surprises, "
                "weather, LNG export flows.",
        instruments=[
            Instrument(coin="xyz:NATGAS", label="Nat Gas", role="primary"),
        ],
        pairs=[],
        news_queries=[
            "natural gas price", "Henry Hub", "LNG exports", "EIA natural gas storage",
            "heating degree days", "cooling degree days", "natural gas pipeline",
        ],
        policy_topics=[
            "LNG export permits", "FERC", "DOE export authorizations",
        ],
        prediction_market_queries=["natural gas price", "winter weather forecast", "LNG exports"],
        catalyst_kinds=["eia_gas_storage", "fomc", "cftc_cot"],
        leverage_ladder=[5, 10],
        horizon_days=7,
        token_budget=TokenBudget(),
        lessons=[
            "storage surprise", "degree days", "Henry Hub", "LNG feedgas", "shoulder season",
        ],
    )
