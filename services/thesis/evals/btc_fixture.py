"""A second, synthetic desk for the eval harness: BTC perps.

contracts/strategies/fixtures/oil_*.json is the real oil read of
2026-10-17 and is owned by the desk-core/contracts agents - not touched
here. This module builds an analogous (spec, analytics, intel) triple for a
desk that does not exist as a checked-in contract fixture, purely so
services/thesis/evals/run_eval.py can run write_thesis() against more than
one market shape. Numbers are plausible, hand-picked BTC-perp figures for
2026-10-17, not a real data pull - "synthetic" is not a euphemism here, it
is the point: the eval needs a second desk, not a second data feed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from contracts.strategies.models import (
    AccountSnapshot, CandleBar, Catalyst, ConditionalStats, DeskAnalytics, DeskSpec, ForwardStats,
    Headline, Instrument, InstrumentSnapshot, InstrumentStructure, IntelPacket, LadderRung,
    MarketOdds, PositioningItem, PriceRange, Quantiles, TokenBudget,
)

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def _q(p10, p25, p50, p75, p90) -> Quantiles:
    return Quantiles(p10=p10, p25=p25, p50=p50, p75=p75, p90=p90)


def build_btc_spec() -> DeskSpec:
    return DeskSpec(
        id="btc",
        name="BTC",
        tagline="Bitcoin perps on Hyperliquid. Macro liquidity, ETF flows, funding, basis.",
        instruments=[Instrument(coin="BTC", label="Bitcoin", role="primary")],
        news_queries=["bitcoin price", "bitcoin ETF flows", "Fed rate decision"],
        policy_topics=["SEC crypto rulemaking", "spot ETF approvals"],
        prediction_market_queries=["bitcoin price target", "Fed rate cut"],
        catalyst_kinds=["fomc", "cpi", "options_expiry", "cftc_cot"],
        leverage_ladder=[5.0, 10.0, 20.0],
        horizon_days=7,
        token_budget=TokenBudget(),
        lessons=["funding rate", "perp-spot basis", "open interest vs price"],
    )


def build_btc_analytics() -> DeskAnalytics:
    instrument = InstrumentSnapshot(
        coin="BTC", label="Bitcoin", mid=108_450.0, ret_1d=0.018, ret_5d=0.042, ret_20d=-0.015,
        ret_60d=0.21, high_20d=114_200.0, low_20d=97_800.0, high_60d=118_500.0, low_60d=82_000.0,
        atr14_pct=0.034, vol_daily_10d=0.027, vol_daily_20d=0.031, vol_daily_60d=0.038,
        funding_hourly=1.8e-5, open_interest_usd=8_200_000_000.0, volume_24h_usd=22_000_000_000.0,
        max_leverage=40.0,
    )
    forward = ForwardStats(
        coin="BTC", horizon_days=7, n_windows=312,
        sample_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sample_end=datetime(2026, 10, 10, tzinfo=timezone.utc),
        final_ret=_q(-0.14, -0.06, 0.018, 0.08, 0.16), final_ret_mean=0.021,
        long_mae=_q(-0.19, -0.11, -0.055, -0.02, -0.006),
        short_mae=_q(0.012, 0.03, 0.07, 0.13, 0.20),
        conditional=[ConditionalStats(label="after +4% 5d run", n=58, mean=0.015, p50=0.022, hit_rate_up=0.53)],
    )
    ladder = [
        LadderRung(
            coin="BTC", side="long", leverage=5.0, equity_usd=15.21, notional_usd=76.05,
            entry_px=108450.0, liq_px=88930.0, liq_dist_pct=-0.18, p_liq=0.03, p_win=0.54,
            ev_pct=0.05, equity_ret=_q(-0.45, -0.19, 0.045, 0.30, 0.49), p_double=0.02,
            p_lose_half=0.08, fees_funding_pct=0.0105, stop_px=97600.0, p_stop=0.17, verdict="hold",
        ),
        LadderRung(
            coin="BTC", side="long", leverage=10.0, equity_usd=15.21, notional_usd=152.06,
            entry_px=108450.0, liq_px=98690.0, liq_dist_pct=-0.09, p_liq=0.26, p_win=0.50,
            ev_pct=0.03, equity_ret=_q(-1.0, -0.95, 0.04, 0.58, 0.97), p_double=0.10,
            p_lose_half=0.27, fees_funding_pct=0.021, stop_px=101000.0, p_stop=0.30, verdict="dip_only",
        ),
        LadderRung(
            coin="BTC", side="long", leverage=20.0, equity_usd=15.21, notional_usd=304.11,
            entry_px=108450.0, liq_px=103570.0, liq_dist_pct=-0.045, p_liq=0.62, p_win=0.33,
            ev_pct=-0.12, equity_ret=_q(-1.0, -1.0, -1.0, 0.72, 1.65), p_double=0.20,
            p_lose_half=0.60, fees_funding_pct=0.042, stop_px=105800.0, p_stop=0.25, verdict="avoid",
        ),
    ]
    # Synthetic structure, same reasoning as the rest of this module: the
    # system prompt (2026-10-23) tells the model to place its stop/target
    # against STRUCTURE, not the ladder's liquidation-derived stop, so the
    # BTC eval case needs one too or the model has nothing but the ladder to
    # work from.
    structure = [InstrumentStructure(
        coin="BTC", mid=108_450.0,
        last_closed_1h=CandleBar(open=108_200.0, high=108_600.0, low=108_050.0, close=108_450.0,
                                 closed_at=T0),
        range_8h=PriceRange(high=109_800.0, low=107_200.0),
        range_24h=PriceRange(high=111_500.0, low=105_800.0),
        range_48h=PriceRange(high=114_200.0, low=103_000.0),
        atr14_1h_pct=0.0041,
        mid_pos_in_24h=0.465,
    )]
    return DeskAnalytics(
        desk_id="btc", as_of=T0, instruments=[instrument], pairs=[], forward=[forward], ladder=ladder,
        structure=structure,
        account=AccountSnapshot(equity_usd=15.21, source="fixture"),
        notes=["sample includes the 2026-03 leverage flush (-18% day)"],
    )


def build_btc_intel() -> IntelPacket:
    headlines = [
        Headline(
            id="bh1", title="Spot bitcoin ETFs post largest weekly inflow since launch",
            url="https://example.com/btc-etf-inflows", source="Bloomberg",
            published_at=T0 - timedelta(hours=10), stance="bull", relevance=0.9,
        ),
        Headline(
            id="bh2", title="Fed holds rates, signals one more cut this year",
            url="https://example.com/fed-holds", source="Reuters",
            published_at=T0 - timedelta(hours=20), stance="bull", relevance=0.75,
        ),
        Headline(
            id="bh3", title="Large miner wallets move 12,000 BTC to exchanges",
            url="https://example.com/miner-outflow", source="CoinDesk",
            published_at=T0 - timedelta(days=2), stance="bear", relevance=0.6,
        ),
    ]
    catalysts = [
        Catalyst(
            id="bc1", kind="fomc", title="FOMC rate decision", at=T0 + timedelta(days=3),
            importance=3, source="Federal Reserve",
        ),
        Catalyst(
            id="bc2", kind="options_expiry", title="Monthly BTC options expiry",
            at=T0 + timedelta(days=5), importance=2, source="Deribit",
        ),
    ]
    odds = [
        MarketOdds(
            id="bo1", venue="polymarket", question="FIXTURE ONLY: BTC above $120k by Nov 30?",
            probability=0.31, volume_usd=4_500_000.0, url="https://polymarket.com/",
        ),
    ]
    positioning = [
        PositioningItem(
            id="bcot1", source="cftc_cot", label="BTC leveraged funds net short",
            value=-4200.0, change=800.0, as_of=T0 - timedelta(days=11),
        ),
    ]
    return IntelPacket(
        desk_id="btc", as_of=T0, headlines=headlines, policy=[], catalysts=catalysts,
        odds=odds, positioning=positioning, token_estimate=700,
    )


def build_btc_packet():
    """(spec, analytics, intel) - the shape run_eval.py's case list wants."""
    return build_btc_spec(), build_btc_analytics(), build_btc_intel()
