"""Oil desk — the flagship. Julien wants to become a master oil trader, so
this desk carries the most depth: three real energy perps, the Brent-WTI
spread, and the widest news/policy/catalyst coverage.

Verified against Hyperliquid's live `/info` (metaAndAssetCtxs, dex="xyz") on
2026-10-17: xyz:CL (WTI) $230.3M 24h volume, xyz:BRENTOIL (Brent) $218.7M,
xyz:NATGAS $15.8M — all comfortably real markets, maxLeverage 20/20/10.
xyz:HO (heating oil, NYMEX ticker "HO") exists but trades $77k/24h — too thin
to list under the same >$1M bar every other desk in this registry uses, so it
is left out. No gasoline/RBOB perp exists on Hyperliquid as of this check
(searched the full universe dump for "RBOB"/"GASOLINE"/"USOIL" — zero hits).
Regenerate this check with:
    PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m services.strategies.desks._verify oil
"""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument, PairSpec, TokenBudget

DESK_ID = "oil"


def build_spec() -> DeskSpec:
    return DeskSpec(
        id=DESK_ID,
        name="Oil",
        tagline="WTI and Brent perps on Hyperliquid. Supply shocks, inventories, OPEC+, policy.",
        instruments=[
            Instrument(coin="xyz:CL", label="WTI", role="primary"),
            Instrument(coin="xyz:BRENTOIL", label="Brent", role="secondary"),
            Instrument(coin="xyz:NATGAS", label="Nat Gas", role="hedge"),
        ],
        pairs=[PairSpec(label="Brent-WTI spread", long="xyz:BRENTOIL", short="xyz:CL")],
        news_queries=[
            "crude oil price", "Strait of Hormuz", "OPEC+", "EIA crude inventories",
            "natural gas price", "Iran oil exports", "Russia oil exports",
        ],
        policy_topics=[
            "Strategic Petroleum Reserve", "Iran sanctions", "Russia oil price cap",
            "Venezuela sanctions", "US shale permitting",
        ],
        prediction_market_queries=["Iran deal", "Strait of Hormuz", "oil price", "OPEC+ cut"],
        catalyst_kinds=[
            "eia_weekly", "api_weekly", "opec_meeting", "cftc_cot", "baker_hughes", "fomc",
        ],
        leverage_ladder=[5, 10, 20],
        horizon_days=7,
        # Flagship desk: more room per refresh than the default budget, since
        # Julien wants the deepest coverage here.
        token_budget=TokenBudget(max_input_tokens=9000, max_output_tokens=2400,
                                 refresh_min_interval_min=60, material_move_pct=1.5,
                                 min_headline_relevance=0.7),
        lessons=[
            "Brent-WTI spread", "contango and backwardation", "COT positioning",
            "SPR releases", "OPEC+ quotas", "crack spreads",
        ],
    )
