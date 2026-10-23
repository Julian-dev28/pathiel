"""Regenerate the oil fixtures: python -m contracts.strategies.fixtures._build
Numbers are the real xyz:CL / xyz:BRENTOIL read of 2026-10-17 02:10 UTC."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contracts.strategies.models import (
    Call, CandleBar, Catalyst, ConditionalStats, DeskAnalytics, DeskSpec, ForwardStats, Headline,
    InstrumentSnapshot, Instrument, InstrumentStructure, IntelPacket, LadderRung, Lesson,
    LeverageView, MarketOdds, PairSnapshot, PairSpec, PayloadRef, Point, PolicyItem,
    PositioningItem, PriceRange, Quantiles, Scenario, SourceRef, Thesis, TokenBudget, Usage,
    AccountSnapshot,
)

HERE = Path(__file__).parent
T0 = datetime(2026, 10, 17, 2, 10, tzinfo=timezone.utc)


def q(*v):
    return Quantiles(p10=v[0], p25=v[1], p50=v[2], p75=v[3], p90=v[4])


spec = DeskSpec(
    id="oil", name="Oil",
    tagline="WTI and Brent perps on Hyperliquid. Supply shocks, inventories, OPEC+, policy.",
    instruments=[Instrument(coin="xyz:CL", label="WTI", role="primary"),
                 Instrument(coin="xyz:BRENTOIL", label="Brent", role="secondary")],
    pairs=[PairSpec(label="Brent-WTI spread", long="xyz:BRENTOIL", short="xyz:CL")],
    news_queries=["crude oil price", "Strait of Hormuz", "OPEC+", "EIA crude inventories"],
    policy_topics=["Strategic Petroleum Reserve", "Iran sanctions", "Russia oil price cap"],
    prediction_market_queries=["Iran deal", "Strait of Hormuz", "oil price"],
    catalyst_kinds=["eia_weekly", "api_weekly", "opec_meeting", "cftc_cot", "baker_hughes", "fomc"],
    token_budget=TokenBudget(),
    lessons=["Brent-WTI spread", "contango and backwardation", "COT positioning"],
)

analytics = DeskAnalytics(
    desk_id="oil", as_of=T0,
    instruments=[
        InstrumentSnapshot(coin="xyz:CL", label="WTI", mid=99.339, ret_1d=0.005, ret_5d=0.0746,
                           ret_20d=0.0652, ret_60d=0.1752, high_20d=101.19, low_20d=86.84,
                           high_60d=106.62, low_60d=84.0, atr14_pct=0.0278, vol_daily_10d=0.0124,
                           vol_daily_20d=0.0183, vol_daily_60d=0.0214, funding_hourly=6.25e-06,
                           open_interest_usd=154_400_000, volume_24h_usd=229_640_437,
                           max_leverage=20),
        InstrumentSnapshot(coin="xyz:BRENTOIL", label="Brent", mid=107.245, ret_1d=0.043,
                           ret_5d=0.031, ret_20d=0.04, high_20d=110.0, low_20d=96.5,
                           atr14_pct=0.026, vol_daily_10d=0.012, vol_daily_20d=0.017,
                           funding_hourly=-2.5462e-06, open_interest_usd=185_300_000,
                           volume_24h_usd=218_368_080, max_leverage=20),
    ],
    pairs=[PairSnapshot(label="Brent-WTI spread", long="xyz:BRENTOIL", short="xyz:CL",
                        spread_now=7.90, spread_mean_20d=9.69, spread_mean_60d=6.30,
                        spread_min_60d=2.66, spread_max_60d=11.58, pair_vol_daily=0.0088,
                        spread_change_7d=q(-2.42, -1.0, 0.11, 1.2, 2.81))],
    forward=[ForwardStats(coin="xyz:CL", horizon_days=7, n_windows=278,
                          sample_start=datetime(2026, 1, 6, tzinfo=timezone.utc),
                          sample_end=datetime(2026, 10, 10, tzinfo=timezone.utc),
                          final_ret=q(-0.0919, -0.04, 0.0131, 0.06, 0.1059), final_ret_mean=0.0158,
                          long_mae=q(-0.139, -0.0855, -0.0415, -0.015, -0.005),
                          short_mae=q(0.01, 0.025, 0.0574, 0.1089, 0.1556),
                          conditional=[ConditionalStats(label="after +5% 5d run", n=75,
                                                        mean=0.022, p50=0.0302, hit_rate_up=0.56)])],
    ladder=[
        LadderRung(coin="xyz:CL", side="long", leverage=5, equity_usd=15.21, notional_usd=76.03,
                   entry_px=99.339, liq_px=81.51, liq_dist_pct=-0.1795, p_liq=0.032, p_win=0.55,
                   ev_pct=0.058, equity_ret=q(-0.479, -0.205, 0.049, 0.328, 0.52), p_double=0.02,
                   p_lose_half=0.09, fees_funding_pct=0.0097, stop_px=88.64, p_stop=0.183,
                   verdict="hold"),
        LadderRung(coin="xyz:CL", side="long", leverage=10, equity_usd=15.21, notional_usd=152.06,
                   entry_px=99.339, liq_px=91.70, liq_dist_pct=-0.0769, p_liq=0.284, p_win=0.51,
                   ev_pct=0.043, equity_ret=q(-1.0, -1.0, 0.04, 0.622, 1.039), p_double=0.11,
                   p_lose_half=0.29, fees_funding_pct=0.0195, stop_px=94.75, p_stop=0.317,
                   verdict="dip_only"),
        LadderRung(coin="xyz:CL", side="long", leverage=20, equity_usd=15.21, notional_usd=304.11,
                   entry_px=99.339, liq_px=96.79, liq_dist_pct=-0.0256, p_liq=0.647, p_win=0.32,
                   ev_pct=-0.149, equity_ret=q(-1.0, -1.0, -1.0, 0.785, 1.772), p_double=0.22,
                   p_lose_half=0.65, fees_funding_pct=0.039, stop_px=97.81, p_stop=0.248,
                   verdict="avoid"),
    ],
    structure=[
        # Deterministic candle-derived structure for xyz:CL (WTI), the
        # primary instrument of THIS fixture desk - hand-built to the same
        # 2026-10-17 02:10 UTC read the rest of the fixture uses. Supports a
        # stop at 98.5 (just beyond the 8h low 98.6) and a first target at
        # the grounded high_20d (101.19, below the 24h/48h high of 100.1
        # would be too close - 20d range is the next real level up).
        InstrumentStructure(
            coin="xyz:CL", mid=99.339,
            last_closed_1h=CandleBar(open=99.20, high=99.42, low=99.05, close=99.339,
                                     closed_at=datetime(2026, 10, 17, 2, 0, tzinfo=timezone.utc)),
            range_8h=PriceRange(high=99.60, low=98.60),
            range_24h=PriceRange(high=100.10, low=96.80),
            range_48h=PriceRange(high=100.10, low=95.50),
            atr14_1h_pct=0.0062,
            mid_pos_in_24h=0.769,
        ),
    ],
    account=AccountSnapshot(equity_usd=15.21, source="fixture"),
    notes=["sample includes the Mar/Apr 2026 war shocks (-20% and -16.6% days)"],
)

intel = IntelPacket(
    desk_id="oil", as_of=T0,
    headlines=[
        Headline(id="h1", title="U.S. crude oil tops $100 as Iran escalates Hormuz attacks",
                 url="https://www.cnbc.com/2026/10/16/oil-price-today-iran-strait-hormuz-ukraine-russia.html",
                 source="CNBC", published_at=T0 - timedelta(hours=14), stance="bull", relevance=0.95),
        Headline(id="h2", title="WTI gains 6.5% as US crude inventories drop 8.6 million barrels",
                 url="https://coinpaper.com/37418/oil-price-today-wti-gains-65-as-us-crude-inventories-drop-86-million-barrels-brent-tops-105",
                 source="Coinpaper", published_at=T0 - timedelta(hours=20), stance="bull", relevance=0.8),
        Headline(id="h3", title="IEA says members to accelerate release of 100 million barrels",
                 url="https://qna.org.qa/en/News-Area/News/2026-10/7/iea-says-members-to-accelerate-release-of-100-million-barrels",
                 source="QNA", published_at=T0 - timedelta(days=10), stance="bear", relevance=0.7),
    ],
    policy=[PolicyItem(id="p1", title="G7 agrees 100M bbl emergency stock release over four months",
                       url="https://www.omanobserver.om/article/1197153/business/energy/iea-325-million-barrels-of-oil-from-strategic-reserves-released",
                       body="G7/IEA", status="announced", stance="bear")],
    catalysts=[
        Catalyst(id="c1", kind="api_weekly", title="API crude inventories",
                 at=datetime(2026, 10, 20, 20, 30, tzinfo=timezone.utc), importance=2, source="API"),
        Catalyst(id="c2", kind="eia_weekly", title="EIA Weekly Petroleum Status Report",
                 at=datetime(2026, 10, 21, 14, 30, tzinfo=timezone.utc), importance=3, source="EIA"),
        Catalyst(id="c3", kind="opec_meeting", title="OPEC+ eight-country monthly meeting",
                 at=datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc), importance=3, source="OPEC"),
    ],
    odds=[MarketOdds(id="o1", venue="polymarket", question="FIXTURE ONLY: US-Iran deal by Oct 31?",
                     probability=0.12, volume_usd=1_200_000, url="https://polymarket.com/")],
    positioning=[PositioningItem(id="cot1", source="cftc_cot", label="WTI non-commercial net long",
                                 value=99060, change=-10403,
                                 as_of=datetime(2026, 10, 6, tzinfo=timezone.utc))],
    token_estimate=900,
)

thesis = Thesis(
    id="oil-20261017T0210-m", desk_id="oil", author="machine", created_at=T0,
    expires_at=T0 + timedelta(days=7), horizon_days=7,
    # Stop at 98.5 sits just beyond the 8h range low (98.6, STRUCTURE above),
    # well inside the 5x ladder rung's own stop (88.64 - the leverage's
    # LIMIT, not a target) - and the first target (101.19, high_20d, already
    # grounded in InstrumentSnapshot) pays 2.21R to stop's 0.84%: a real,
    # tradable call, not the <1R WTI-at-91.4 stop the live Brent desk
    # shipped with before this fix (2026-10-23).
    call=Call(side="long", instrument="xyz:CL",
              one_liner="Long WTI at 5x: Hormuz shut, talks dead, specs light. Out below 98.5.",
              entry_px=99.339, entry_zone=[96.5, 99.5], stop_px=98.5, targets=[101.19, 106.62],
              leverage=5, confidence=0.58,
              invalidation="US-Iran deal headline, or WTI daily close below 98.5"),
    scenarios=[
        Scenario(name="Hormuz shut, grind up", probability=0.45, move_lo_pct=0.02, move_hi_pct=0.5,
                 description="No deal before OPEC+ Nov 1. WTI 103-106.", triggers=["tanker strikes"]),
        Scenario(name="Chop", probability=0.20, move_lo_pct=-0.02, move_hi_pct=0.02,
                 description="Range 96-101."),
        Scenario(name="Gulf restart and SPR weigh", probability=0.17, move_lo_pct=-0.08,
                 move_hi_pct=-0.02, description="EIA build Wed, drift to 94."),
        Scenario(name="Deal headline gap", probability=0.18, move_lo_pct=-0.5, move_hi_pct=-0.08,
                 description="-10% to -15% day.", triggers=["Oman/Qatar mediation"]),
    ],
    bull=[Point(text="Hormuz effectively closed, record tanker attacks", source_ids=["h1"]),
          Point(text="8.6M bbl US crude draw", source_ids=["h2"])],
    bear=[Point(text="100M bbl emergency stock release accelerating", source_ids=["h3", "p1"])],
    catalysts=intel.catalysts,
    leverage_view=[
        LeverageView(leverage=5, verdict="hold", reason="liq 81.51 below 20d low; P(liq) 3.2%"),
        LeverageView(leverage=10, verdict="dip_only", reason="liq 91.70 inside this week's range; P(liq) 28.4%"),
        LeverageView(leverage=20, verdict="avoid", reason="liq 96.79 is one ATR away; P(liq) 64.7%"),
    ],
    lessons=[Lesson(term="Brent-WTI spread",
                    explanation="Brent prices seaborne crude, WTI prices US inland crude. Hormuz risk lifts Brent; US-only events move WTI.",
                    tied_to="Spread fell from 11.5 to 7.9 this week on the US hurricane draw.")],
    what_changes_my_mind=["Iran accepts a reopening framework", "EIA shows a crude build"],
    sources=[SourceRef(id="h1", title="CNBC", url="https://www.cnbc.com/2026/10/16/oil-price-today-iran-strait-hormuz-ukraine-russia.html")],
    analytics_ref=PayloadRef(as_of=T0, sha256="0" * 64),
    intel_ref=PayloadRef(as_of=T0, sha256="0" * 64),
    primary_coins=["xyz:CL"],   # this fixture desk is the 2026-10-17 one, priced off WTI
    usage=Usage(model="fixture", input_tokens=5200, output_tokens=1400),
)


# A second thesis fixture, Brent instead of WTI, with a window that brackets
# the real watch-wallet fill history services/strategies/tests/fixtures/
# watch_wallet_brent_fills.json replays (2026-09-10 16:55 to 2026-10-22
# 15:53 UTC) -- services/strategies/evals/trades_replay.py grades that
# fixture's fills against THIS thesis, so the replay has something real to
# match against instead of the WTI thesis above's zero-overlap coin.
oil_thesis_brent = Thesis(
    id="oil-20260910T0000-m-brent", desk_id="oil", author="machine",
    created_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    expires_at=datetime(2026, 10, 24, tzinfo=timezone.utc), horizon_days=44,
    call=Call(side="long", instrument="xyz:BRENTOIL",
             one_liner="Long Brent at 20x off the Hormuz escalation: out below 95.",
             entry_px=103.95, entry_zone=[100.0, 108.0], stop_px=95.0,
             targets=[112.0, 118.0], leverage=20, confidence=0.55,
             invalidation="Daily close below 95, or a confirmed Hormuz reopening"),
    scenarios=[
        Scenario(name="Hormuz stays shut", probability=0.55, move_lo_pct=0.03, move_hi_pct=0.4,
                 description="Grinds to 112-118."),
        Scenario(name="Chop", probability=0.25, move_lo_pct=-0.05, move_hi_pct=0.03,
                 description="Range 99-107."),
        Scenario(name="Deal / SPR release", probability=0.20, move_lo_pct=-0.4, move_hi_pct=-0.05,
                 description="Gap down on a de-escalation headline."),
    ],
    bull=[Point(text="Record tanker attacks keep the Strait effectively closed")],
    bear=[Point(text="G7/IEA 100M bbl emergency release accelerating")],
    what_changes_my_mind=["Iran accepts a reopening framework", "daily close below 95"],
    analytics_ref=PayloadRef(as_of=T0, sha256="0" * 64),
    primary_coins=["xyz:BRENTOIL"],
    usage=Usage(model="fixture", input_tokens=4800, output_tokens=1300),
)


def main() -> None:
    for name, obj in (("oil_spec", spec), ("oil_analytics", analytics),
                      ("oil_intel", intel), ("oil_thesis", thesis),
                      ("oil_thesis_brent", oil_thesis_brent)):
        (HERE / f"{name}.json").write_text(obj.model_dump_json(indent=2) + "\n")
    print("wrote fixtures to", HERE)


if __name__ == "__main__":
    main()
