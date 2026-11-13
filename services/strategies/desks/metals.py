"""Metals desk: gold and silver HIP-3 perps. Verified live 2026-10-17:
xyz:GOLD $51.2M 24h volume (maxLeverage 25), xyz:SILVER $130.7M (maxLeverage
25); re-checked 2026-11-05: xyz:GOLD $105.6M, xyz:SILVER $189.1M, both 25x.
Scoped to exactly these two per the build brief ("metals = gold/silver").

GOLD is the primary since 2026-11-05. Operator: "stop the silver tick, and
silver market, add gold tick and gold market, same formula, headlines,
thesis, price action, high lows, etc ... allocate 10% of our equity". Silver
(primary 2026-10-31 to 2026-11-05, docs/strategy/SILVER_DESK.md) went 0 for
5 live, every trade a short sold past the 24h edge and stopped (-$14.41).
Gold's own test before this switch: docs/strategy/GOLD_STRATEGY_TEST_2026-
11-05.md; the desk's rules: docs/strategy/GOLD_DESK.md. Silver stays listed
as the secondary for the gold-silver ratio and so an old silver thesis is
still recognised; the tick never trades it (a thesis whose instrument is
not the primary is "rewrite first": scripts/thesis_due.py says so, and
scripts/trigger_watch.py never wakes a desk on it). The desk id stays
"metals" so its journal carries over; a cached read or a thesis built for
the old silver primary is rebuilt, never served
(pipeline.analytics_matches_spec / primary_changed).

No PairSpec here on purpose: the contract's PairSnapshot models a spread as
`long_mid - short_mid` (correct for Brent-WTI, which trade in the same $/bbl
units and similar magnitude). Gold ($4100) and silver ($52) are ~80x apart,
so a raw price difference is dominated by gold's own moves and is not the
trade anyone actually watches: the real pair trade is the gold/silver RATIO.
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
        name="Gold",
        tagline="Gold perp on Hyperliquid, silver alongside for the gold-silver ratio. "
                "Real yields, the dollar, the Fed, central bank buying, safe-haven flows.",
        instruments=[
            Instrument(coin="xyz:GOLD", label="Gold", role="primary"),
            Instrument(coin="xyz:SILVER", label="Silver", role="secondary"),
        ],
        pairs=[],
        # Eight phrases: services/intel/sources/news.py sends one Google News
        # request per phrase (GNEWS_MAX_QUERIES = 8) plus one broad OR query
        # over the first eight, so every phrase here is searched. What moves
        # gold: the Fed and real yields, the dollar, central bank buying
        # (PBOC, the emerging-market banks), ETF flows, safe-haven demand
        # (Iran, the war), tariffs on metals, and China's own demand.
        news_queries=["gold price", "central bank gold buying", "gold ETF flows", "real yields gold",
                      "dollar index", "Fed rate cut", "Iran safe haven gold", "China gold imports"],
        policy_topics=["Fed rate decision", "real yields", "dollar index policy", "tariffs on metals"],
        # "Fed December" (2026-11-02): "Fed rate cut" only finds the 2026 cut
        # count markets (no cuts at 0.97); the December meeting market carries
        # the live hike-or-hold odds, the rate risk gold actually trades on.
        # "Iran ceasefire": the war-or-peace odds are gold's safe-haven bid.
        prediction_market_queries=["gold price", "Fed rate cut", "Fed December", "Iran ceasefire", "recession"],
        # Generator kinds (services/intel/sources/catalysts.py): FOMC and CPI
        # are always on; the COT report is the positioning read. Jobs (NFP),
        # PCE, Fed minutes, China data, the summits and the Iran talks come
        # from services/intel/data/scheduled_events.json through the "metals"
        # coin group, not from this list.
        catalyst_kinds=["fomc", "cpi", "cftc_cot"],
        leverage_ladder=[5, 10, 20],
        horizon_days=7,
        token_budget=TokenBudget(),
        lessons=["real yields vs gold", "dollar and gold", "central bank gold buying", "safe-haven flows",
                 "gold-silver ratio"],
    )
