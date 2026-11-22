"""AI equities desk — HIP-3 tokenized equities on Hyperliquid's xyz dex, tied
to the AI capex/compute trade. Every instrument below was checked live
2026-10-17 against metaAndAssetCtxs(dex="xyz") and cleared the desk's >$1M
24h-volume bar (see services/strategies/desks/_verify.py):

    primary   xyz:NVDA   $143.2M
              xyz:MU     $95.2M    (HBM memory — the AI capex bottleneck story)
              xyz:AMD    $39.8M
              xyz:GOOGL  $35.6M
              xyz:META   $33.8M
              xyz:PLTR   $8.1M
              xyz:TSM    $4.1M
    secondary xyz:AVGO   $2.5M     (custom AI silicon / networking)
              xyz:ORCL   $3.8M     (AI cloud infra)
              xyz:ARM    $3.7M     (chip IP)

MSFT was in the brief's named candidate list and is real ($16.7M, verified)
but is folded into `secondary` here alongside the other infra names rather
than `primary`, since its AI exposure is one line of a much bigger business —
judgment call, revisit if Julien wants it promoted.
Regenerate the volume check with:
    PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m services.strategies.desks._verify ai
"""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument, TokenBudget

DESK_ID = "ai"


def build_spec() -> DeskSpec:
    return DeskSpec(
        id=DESK_ID,
        name="AI Equities",
        tagline="HIP-3 AI-chip and AI-infra equities on Hyperliquid. Capex cycle, earnings, export rules.",
        instruments=[
            Instrument(coin="xyz:NVDA", label="Nvidia", role="primary"),
            Instrument(coin="xyz:MU", label="Micron", role="primary"),
            Instrument(coin="xyz:AMD", label="AMD", role="primary"),
            Instrument(coin="xyz:GOOGL", label="Alphabet", role="primary"),
            Instrument(coin="xyz:META", label="Meta", role="primary"),
            Instrument(coin="xyz:PLTR", label="Palantir", role="primary"),
            Instrument(coin="xyz:TSM", label="TSMC", role="primary"),
            Instrument(coin="xyz:MSFT", label="Microsoft", role="secondary"),
            Instrument(coin="xyz:AVGO", label="Broadcom", role="secondary"),
            Instrument(coin="xyz:ORCL", label="Oracle", role="secondary"),
            Instrument(coin="xyz:ARM", label="Arm", role="secondary"),
        ],
        pairs=[],
        news_queries=[
            "Nvidia earnings", "AI chip export controls", "AI capex", "HBM memory shortage",
            "GPU supply", "AI datacenter buildout", "China chip export ban",
        ],
        policy_topics=[
            "China chip export controls", "CHIPS Act", "AI executive order",
            "semiconductor tariffs",
        ],
        prediction_market_queries=["Nvidia earnings beat", "AI bubble", "chip export ban"],
        catalyst_kinds=["earnings", "export_control_update", "fomc", "cpi_release"],
        leverage_ladder=[5, 10, 20],
        horizon_days=7,
        token_budget=TokenBudget(),
        lessons=["AI capex cycle", "export control risk", "HBM memory bottleneck"],
    )
