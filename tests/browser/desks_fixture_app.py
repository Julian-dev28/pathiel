"""Fixture-backed stand-in for the real services/strategies router.

Serves pathiel/templates/desk.html (the /desks grid list page, desks.html,
was removed 2026-10-23 — see docs/overhaul/OIL_DESK_UI_2026-10-23.md),
pathiel/static/*, and every route contracts/strategies/models.py defines at
the bottom of that file, against FOUR desks:

  oil   real fixtures (contracts/strategies/fixtures/oil_*.json) via the
        pydantic contract models — a fresh, fully-populated desk with a
        small 1d move, so clicking Refresh deterministically SKIPS
        ("no material move").
  btc   synthesized, fully-populated, with a large 1d move that exceeds its
        own token_budget.material_move_pct — clicking Refresh here
        deterministically returns a DONE job with a new thesis. Also the
        only desk with a second graded machine thesis AND a graded user
        thesis in its seed history, to exercise both track-record columns.
  ai    synthesized, has spec + analytics + intel but thesis=None and an
        empty history — the "no thesis yet" EMPTY state. Its ladder still
        has rows (defaults to the primary instrument's long side), proving
        the leverage switch works before a call exists.
  gas   synthesized, fully populated, but built 30 real days before "now"
        (computed from datetime.now() at import time, not a hardcoded date,
        so this stays deterministically stale no matter when the suite
        runs) — the STALE state. Its call is a SHORT, the only desk that
        exercises the leverage ladder's short-side branch. Carries one
        catalyst dated in the real future despite being otherwise stale, to
        prove catalysts render independent of the packet's own staleness.

Plus a desk id that is deliberately not registered anywhere, "err500": GET
/api/desks/err500 always raises, for the API-500 error-state test. It is
never listed by GET /api/desks.

This module owns no mutable module-level state. create_app() builds a fresh
DeskSpec/DeskAnalytics/IntelPacket registry, fresh per-desk history, and a
fresh job table on every call via closures, so parallel test modules (or
repeated calls within one module) never share state.

Run standalone for a manual look (SWARM_RULES: dev servers bind 127.0.0.1 on
a port in 8700-8799; this agent's assigned band is 8710-8719):

    PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m tests.browser.desks_fixture_app 8710
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from contracts.risk.models import RiskProfile, TradeSize, TradeSizeIn
from services.risk.derive import VenueRules
from services.risk.size import size_trade
from contracts.strategies.models import (
    AccountSnapshot, Call, Catalyst, ConditionalStats, DeskAnalytics, DeskBrief, DeskCard,
    DeskSpec, ForwardStats, Grade, Headline, Instrument, InstrumentSnapshot, IntelPacket,
    LadderRung, Lesson, LeverageView, MarketOdds, Point,
    PositioningItem, Quantiles, RefreshJob, Scenario, SourceRef, Thesis, TokenBudget,
    TrackRecord, Usage, UserThesisIn,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "contracts" / "strategies" / "fixtures"
TEMPLATES = REPO_ROOT / "pathiel" / "templates"
STATIC_DIR = REPO_ROOT / "pathiel" / "static"

NOW = datetime.now(timezone.utc)
FIXTURE_EQUITY_USD = 15.21


def q(p10, p25, p50, p75, p90) -> Quantiles:
    return Quantiles(p10=p10, p25=p25, p50=p50, p75=p75, p90=p90)


def ladder_rows(coin: str, side: str, entry: float, equity: float, rungs: list[dict]) -> List[LadderRung]:
    rows = []
    for r in rungs:
        rows.append(LadderRung(
            coin=coin, side=side, leverage=r["leverage"], equity_usd=equity,
            notional_usd=round(equity * r["leverage"], 2), entry_px=entry,
            liq_px=r["liq_px"], liq_dist_pct=r["liq_dist_pct"], p_liq=r["p_liq"], p_win=r["p_win"],
            ev_pct=r["ev_pct"], equity_ret=q(r["p10"], r["p25"], r["p50"], r["p75"], r["p90"]),
            p_double=r["p_double"], p_lose_half=r["p_lose_half"], fees_funding_pct=r["fees_funding_pct"],
            stop_px=r.get("stop_px"), p_stop=r.get("p_stop"), verdict=r["verdict"],
        ))
    return rows


# ── oil: the real fixtures ───────────────────────────────────────────────────

def _shift_dates(obj, delta: timedelta):
    """Every ISO-8601 datetime string in a raw fixture, moved by `delta`."""
    if isinstance(obj, dict):
        return {k: _shift_dates(v, delta) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shift_dates(v, delta) for v in obj]
    if isinstance(obj, str) and len(obj) >= 19 and obj[4] == "-" and obj[10] == "T":
        try:
            return (datetime.fromisoformat(obj.replace("Z", "+00:00")) + delta).isoformat()
        except ValueError:
            return obj
    return obj


def _load_oil() -> dict:
    """The real contract fixtures, re-anchored so their analytics were built
    at import time. Every date moves by the same delta, so the packet keeps
    its internal shape (thesis 30 min after the data, catalysts ahead of it)
    and stays FRESH whatever day the suite runs. Loaded as-is, the fixtures
    aged past the 24h staleness window a day after they were written and
    test_desk_brief_oil_matches_the_real_fixtures began failing on the
    calendar alone (2026-10-18)."""
    raw = {name: json.loads((FIXTURES / f"oil_{name}.json").read_text())
           for name in ("spec", "analytics", "intel", "thesis")}
    as_of = datetime.fromisoformat(raw["analytics"]["as_of"].replace("Z", "+00:00"))
    delta = NOW - as_of
    spec = DeskSpec.model_validate(raw["spec"])
    analytics = DeskAnalytics.model_validate(_shift_dates(raw["analytics"], delta))
    intel = IntelPacket.model_validate(_shift_dates(raw["intel"], delta))
    thesis = Thesis.model_validate(_shift_dates(raw["thesis"], delta))
    return {"spec": spec, "analytics": analytics, "intel": intel, "thesis": thesis}


# ── btc: synthesized, fully populated, large 1d move (forces a DONE refresh) ─

def _build_btc() -> dict:
    spec = DeskSpec(
        id="btc", name="BTC",
        tagline="Bitcoin perp on Hyperliquid. Macro liquidity, ETF flows, halving cycle.",
        instruments=[Instrument(coin="BTC", label="Bitcoin", role="primary")],
        news_queries=["bitcoin price", "bitcoin ETF flows"],
        policy_topics=["SEC bitcoin ETF", "banking regulation"],
        prediction_market_queries=["bitcoin price target"],
        catalyst_kinds=["fomc", "etf_flow_report"],
        leverage_ladder=[5.0, 10.0, 20.0], horizon_days=7,
        token_budget=TokenBudget(),
        lessons=["halving cycle", "funding rate basis"],
    )
    entry = 108_500.0
    analytics = DeskAnalytics(
        desk_id="btc", as_of=NOW,
        instruments=[InstrumentSnapshot(
            coin="BTC", label="Bitcoin", mid=entry, ret_1d=0.041, ret_5d=0.062, ret_20d=0.09,
            ret_60d=0.21, high_20d=112_000.0, low_20d=96_000.0, high_60d=114_000.0, low_60d=81_000.0,
            atr14_pct=0.031, vol_daily_10d=0.028, vol_daily_20d=0.03, vol_daily_60d=0.034,
            funding_hourly=1.1e-5, open_interest_usd=5_200_000_000.0, volume_24h_usd=18_000_000_000.0,
            max_leverage=40.0)],
        forward=[ForwardStats(
            coin="BTC", horizon_days=7, n_windows=410,
            sample_start=NOW - timedelta(days=300), sample_end=NOW - timedelta(days=7),
            final_ret=q(-0.11, -0.03, 0.02, 0.08, 0.15), final_ret_mean=0.021,
            long_mae=q(-0.16, -0.09, -0.04, -0.01, -0.003), short_mae=q(0.01, 0.03, 0.07, 0.13, 0.19),
            conditional=[ConditionalStats(label="after +4% 1d move", n=52, mean=0.03, p50=0.027, hit_rate_up=0.6)])],
        ladder=ladder_rows("BTC", "long", entry, 15.21, [
            {"leverage": 5, "liq_px": 88970.0, "liq_dist_pct": -0.18, "p_liq": 0.03, "p_win": 0.58,
             "ev_pct": 0.07, "p10": -0.5, "p25": -0.2, "p50": 0.06, "p75": 0.34, "p90": 0.55,
             "p_double": 0.03, "p_lose_half": 0.08, "fees_funding_pct": 0.009,
             "stop_px": 96862.0, "p_stop": 0.17, "verdict": "hold"},
            {"leverage": 10, "liq_px": 98735.0, "liq_dist_pct": -0.09, "p_liq": 0.25, "p_win": 0.53,
             "ev_pct": 0.05, "p10": -1.0, "p25": -0.95, "p50": 0.05, "p75": 0.6, "p90": 1.0,
             "p_double": 0.13, "p_lose_half": 0.28, "fees_funding_pct": 0.018,
             "stop_px": 102730.0, "p_stop": 0.3, "verdict": "dip_only"},
            {"leverage": 20, "liq_px": 103_678.0, "liq_dist_pct": -0.045, "p_liq": 0.6, "p_win": 0.3,
             "ev_pct": -0.12, "p10": -1.0, "p25": -1.0, "p50": -1.0, "p75": 0.7, "p90": 1.6,
             "p_double": 0.21, "p_lose_half": 0.62, "fees_funding_pct": 0.036,
             "stop_px": 106094.0, "p_stop": 0.24, "verdict": "avoid"},
        ]),
        account=AccountSnapshot(equity_usd=15.21, source="fixture"),
        notes=["sample excludes the 2026 ETF-approval gap week"],
    )
    intel = IntelPacket(
        desk_id="btc", as_of=NOW,
        headlines=[
            Headline(id="bh1", title="Bitcoin ETFs post first inflow week in a month",
                     url="https://example.com/btc-etf-inflows", source="Example Wire",
                     published_at=NOW - timedelta(hours=10), stance="bull", relevance=0.9),
            Headline(id="bh2", title="Fed officials signal caution ahead of next meeting",
                     url="https://example.com/fed-caution", source="Example Wire",
                     published_at=NOW - timedelta(hours=20), stance="bear", relevance=0.6),
        ],
        catalysts=[Catalyst(id="bc1", kind="fomc", title="FOMC rate decision",
                             at=NOW + timedelta(days=4), importance=3, source="Fed")],
        odds=[MarketOdds(id="bo1", venue="polymarket", question="FIXTURE ONLY: Fed cuts next meeting?",
                          probability=0.35, volume_usd=800_000.0, url="https://polymarket.com/")],
        positioning=[PositioningItem(id="bp1", source="hl_funding", label="BTC perp funding (annualized)",
                                     value=9.6, change=1.2, as_of=NOW)],
        token_estimate=750,
    )
    thesis = Thesis(
        id=f"btc-{NOW:%Y%m%dT%H%M}-m", desk_id="btc", author="machine", created_at=NOW,
        expires_at=NOW + timedelta(days=7), horizon_days=7,
        call=Call(side="long", instrument="BTC",
                  one_liner="Long BTC at 5x: ETF inflows resumed, liquidity eases into FOMC. Out below 97k.",
                  entry_px=entry, entry_zone=[106_000.0, 109_000.0], stop_px=96_862.0,
                  targets=[115_000.0, 122_000.0], leverage=5.0, confidence=0.6,
                  invalidation="Daily close below 97k, or a hawkish FOMC surprise"),
        scenarios=[
            Scenario(name="ETF grind higher", probability=0.4, move_lo_pct=0.03, move_hi_pct=0.15,
                     description="Inflows continue, liquidity stays easy.", triggers=["ETF flow report"]),
            Scenario(name="Chop", probability=0.25, move_lo_pct=-0.02, move_hi_pct=0.02, description="Range-bound into FOMC."),
            Scenario(name="FOMC hawkish fade", probability=0.2, move_lo_pct=-0.1, move_hi_pct=-0.03,
                     description="Fed pushes back on cuts.", triggers=["FOMC statement"]),
            Scenario(name="Liquidity shock", probability=0.15, move_lo_pct=-0.35, move_hi_pct=-0.1, description="Broad risk-off."),
        ],
        bull=[Point(text="Spot ETF inflows resumed after three weeks of outflows", source_ids=["bh1"])],
        bear=[Point(text="FOMC could surprise hawkish and drain risk liquidity", source_ids=["bh2"])],
        catalysts=intel.catalysts,
        leverage_view=[
            LeverageView(leverage=5, verdict="hold", reason="liq 88,970 below the 20d low; P(liq) 3%"),
            LeverageView(leverage=10, verdict="dip_only", reason="liq 98,735 inside this week's range; P(liq) 25%"),
            LeverageView(leverage=20, verdict="avoid", reason="liq 103,678 is about one ATR away; P(liq) 60%"),
        ],
        lessons=[Lesson(term="funding rate basis",
                        explanation="Perp funding is the cost of staying long when the market is crowded long. "
                                    "A rising annualized funding rate against a flat spot price is leverage, not conviction.",
                        tied_to="BTC funding is running +9.6% annualized, up 1.2pt this week.")],
        what_changes_my_mind=["FOMC surprises hawkish", "ETF flows reverse negative again"],
        sources=[SourceRef(id="bh1", title="Example Wire", url="https://example.com/btc-etf-inflows")],
        usage=Usage(model="fixture", input_tokens=4800, output_tokens=1200),
    )
    return {"spec": spec, "analytics": analytics, "intel": intel, "thesis": thesis}


# ── ai: synthesized, EMPTY (no thesis yet), but analytics/intel still live ──

def _build_ai() -> dict:
    spec = DeskSpec(
        id="ai", name="AI equities",
        tagline="Basket of AI-exposed equities via perps. Capex cycle, GPU supply, earnings.",
        instruments=[Instrument(coin="NVDA", label="Nvidia", role="primary"),
                     Instrument(coin="MSFT", label="Microsoft", role="secondary")],
        news_queries=["Nvidia earnings", "AI capex"],
        policy_topics=["export controls", "chip act funding"],
        catalyst_kinds=["earnings"],
        leverage_ladder=[5.0, 10.0, 20.0], horizon_days=7,
        token_budget=TokenBudget(),
        lessons=["capex supercycle", "forward P/E vs growth"],
    )
    entry = 184.0
    analytics = DeskAnalytics(
        desk_id="ai", as_of=NOW,
        instruments=[InstrumentSnapshot(
            coin="NVDA", label="Nvidia", mid=entry, ret_1d=0.008, ret_5d=0.021, ret_20d=0.04,
            high_20d=192.0, low_20d=168.0, atr14_pct=0.034, vol_daily_10d=0.026, vol_daily_20d=0.03,
            funding_hourly=2.0e-6, open_interest_usd=90_000_000.0, volume_24h_usd=310_000_000.0,
            max_leverage=10.0)],
        forward=[],
        ladder=ladder_rows("NVDA", "long", entry, 15.21, [
            {"leverage": 5, "liq_px": 150.9, "liq_dist_pct": -0.18, "p_liq": 0.04, "p_win": 0.54,
             "ev_pct": 0.03, "p10": -0.5, "p25": -0.22, "p50": 0.03, "p75": 0.28, "p90": 0.46,
             "p_double": 0.02, "p_lose_half": 0.1, "fees_funding_pct": 0.008, "verdict": "hold"},
            {"leverage": 10, "liq_px": 167.4, "liq_dist_pct": -0.09, "p_liq": 0.26, "p_win": 0.5,
             "ev_pct": 0.015, "p10": -1.0, "p25": -0.9, "p50": 0.02, "p75": 0.5, "p90": 0.85,
             "p_double": 0.09, "p_lose_half": 0.29, "fees_funding_pct": 0.016, "verdict": "dip_only"},
            {"leverage": 20, "liq_px": 175.7, "liq_dist_pct": -0.045, "p_liq": 0.58, "p_win": 0.33,
             "ev_pct": -0.08, "p10": -1.0, "p25": -1.0, "p50": -0.8, "p75": 0.6, "p90": 1.3,
             "p_double": 0.16, "p_lose_half": 0.58, "fees_funding_pct": 0.031, "verdict": "avoid"},
        ]),
        account=AccountSnapshot(equity_usd=15.21, source="fixture"),
    )
    intel = IntelPacket(
        desk_id="ai", as_of=NOW, headlines=[],
        catalysts=[Catalyst(id="ac1", kind="earnings", title="Nvidia quarterly earnings",
                             at=NOW + timedelta(days=6), importance=3, source="Nvidia IR")],
        odds=[], positioning=[], token_estimate=0,
    )
    return {"spec": spec, "analytics": analytics, "intel": intel, "thesis": None}


# ── gas: synthesized, fully populated, 30 REAL days stale, and SHORT ────────

def _build_gas() -> dict:
    t0 = NOW - timedelta(days=30)   # relative to wall-clock import time, never a fixed date
    spec = DeskSpec(
        id="gas", name="Nat Gas",
        tagline="Henry Hub natural gas perp. Weather, storage, LNG exports.",
        instruments=[Instrument(coin="xyz:NG", label="Nat Gas", role="primary")],
        news_queries=["natural gas storage", "LNG exports"],
        policy_topics=["LNG export permits"],
        catalyst_kinds=["eia_weekly"],
        leverage_ladder=[5.0, 10.0, 20.0], horizon_days=7,
        token_budget=TokenBudget(),
        lessons=["storage report cadence"],
    )
    entry = 3.20
    analytics = DeskAnalytics(
        desk_id="gas", as_of=t0,
        instruments=[InstrumentSnapshot(
            coin="xyz:NG", label="Nat Gas", mid=entry, ret_1d=-0.012, ret_5d=-0.04, ret_20d=-0.09,
            high_20d=3.65, low_20d=3.05, atr14_pct=0.04, vol_daily_10d=0.03, vol_daily_20d=0.035,
            funding_hourly=-1.5e-6, open_interest_usd=40_000_000.0, volume_24h_usd=60_000_000.0,
            max_leverage=20.0)],
        forward=[],
        ladder=ladder_rows("xyz:NG", "short", entry, 15.21, [
            {"leverage": 5, "liq_px": 3.776, "liq_dist_pct": 0.18, "p_liq": 0.03, "p_win": 0.57,
             "ev_pct": 0.06, "p10": -0.48, "p25": -0.19, "p50": 0.05, "p75": 0.3, "p90": 0.5,
             "p_double": 0.02, "p_lose_half": 0.09, "fees_funding_pct": 0.009,
             "stop_px": 3.546, "p_stop": 0.18, "verdict": "hold"},
            {"leverage": 10, "liq_px": 3.488, "liq_dist_pct": 0.09, "p_liq": 0.25, "p_win": 0.52,
             "ev_pct": 0.045, "p10": -1.0, "p25": -0.9, "p50": 0.04, "p75": 0.58, "p90": 0.95,
             "p_double": 0.12, "p_lose_half": 0.29, "fees_funding_pct": 0.017,
             "stop_px": 3.373, "p_stop": 0.3, "verdict": "dip_only"},
            {"leverage": 20, "liq_px": 3.344, "liq_dist_pct": 0.045, "p_liq": 0.6, "p_win": 0.31,
             "ev_pct": -0.13, "p10": -1.0, "p25": -1.0, "p50": -0.9, "p75": 0.65, "p90": 1.4,
             "p_double": 0.2, "p_lose_half": 0.6, "fees_funding_pct": 0.034,
             "stop_px": 3.286, "p_stop": 0.25, "verdict": "avoid"},
        ]),
        account=AccountSnapshot(equity_usd=15.21, source="fixture"),
        notes=["as_of is deliberately 30 days old — this desk exists to exercise the stale state"],
    )
    intel = IntelPacket(
        desk_id="gas", as_of=t0,
        headlines=[Headline(id="gh1", title="Mild winter outlook pressures gas prices",
                             url="https://example.com/gas-mild-winter", source="Example Wire",
                             published_at=t0 - timedelta(hours=6), stance="bear", relevance=0.8)],
        catalysts=[
            Catalyst(id="gc1", kind="eia_weekly", title="EIA natural gas storage report",
                     at=t0 + timedelta(days=2), importance=3, source="EIA"),
            # Dated in the real future despite the packet itself being stale,
            # so "Next catalysts" still has something to show on this desk.
            Catalyst(id="gc2", kind="eia_weekly", title="EIA natural gas storage report",
                     at=NOW + timedelta(days=3), importance=2, source="EIA"),
        ],
        odds=[], positioning=[], token_estimate=400,
    )
    thesis = Thesis(
        id=f"gas-{t0:%Y%m%dT%H%M}-m", desk_id="gas", author="machine", created_at=t0,
        expires_at=t0 + timedelta(days=7), horizon_days=7,
        call=Call(side="short", instrument="xyz:NG",
                  one_liner="Short nat gas at 5x: mild winter forecast, storage surplus. Out above 3.80.",
                  entry_px=entry, stop_px=3.80, targets=[2.70, 2.40], leverage=5.0, confidence=0.55,
                  invalidation="Arctic blast forecast revision, or a daily close above 3.80"),
        scenarios=[
            Scenario(name="Mild winter grinds lower", probability=0.5, move_lo_pct=-0.25, move_hi_pct=-0.05,
                     description="Storage surplus persists into shoulder season."),
            Scenario(name="Chop", probability=0.25, move_lo_pct=-0.03, move_hi_pct=0.03, description="Range-bound."),
            Scenario(name="Cold snap squeeze", probability=0.25, move_lo_pct=0.05, move_hi_pct=0.3,
                     description="An arctic blast spikes heating demand.", triggers=["polar vortex forecast"]),
        ],
        bull=[Point(text="Storage still above the 5-year average", source_ids=["gh1"])],
        bear=[Point(text="LNG export capacity additions are pulling supply forward", source_ids=[])],
        catalysts=intel.catalysts,
        leverage_view=[
            LeverageView(leverage=5, verdict="hold", reason="liq 3.78 above the 20d high; P(liq) 3%"),
            LeverageView(leverage=10, verdict="dip_only", reason="liq 3.49 inside this week's range; P(liq) 25%"),
            LeverageView(leverage=20, verdict="avoid", reason="liq 3.34 is about one ATR away; P(liq) 60%"),
        ],
        what_changes_my_mind=["A sustained arctic blast forecast", "Storage draw exceeds the 5-year average"],
        sources=[SourceRef(id="gh1", title="Example Wire", url="https://example.com/gas-mild-winter")],
        usage=Usage(model="fixture", input_tokens=3100, output_tokens=900),
    )
    return {"spec": spec, "analytics": analytics, "intel": intel, "thesis": thesis}


# ── graded history seeding ───────────────────────────────────────────────────

def _graded(desk_id: str, coin: str, days_ago: float, author: str, side: str, one_liner: str,
            entry: float, stop: float, lev: float, conf: float, ret_pct: float,
            direction_hit: Optional[bool], brier: Optional[float]) -> Thesis:
    created = NOW - timedelta(days=days_ago)
    return Thesis(
        id=f"{desk_id}-{created:%Y%m%dT%H%M%S}-{author[0]}", desk_id=desk_id, author=author,
        created_at=created, expires_at=created + timedelta(days=7), horizon_days=7,
        call=Call(side=side, instrument=coin, one_liner=one_liner, entry_px=entry, stop_px=stop,
                  leverage=lev, confidence=conf, invalidation="graded on the 7d mark"),
        grade=Grade(graded_at=created + timedelta(days=7), outcome_px=entry * (1 + ret_pct), ret_pct=ret_pct,
                    direction_hit=direction_hit, mae_pct=min(0.0, ret_pct) - 0.01,
                    mfe_pct=max(0.0, ret_pct) + 0.01, brier=brier),
        usage=Usage(model="fixture-seed"),
    )


def _seed_history(desks: dict) -> dict[str, list[Thesis]]:
    history: dict[str, list[Thesis]] = {}
    history["oil"] = [desks["oil"]["thesis"]] + [
        _graded("oil", "xyz:CL", 9, "machine", "long", "Long WTI at 5x on OPEC+ cut chatter.",
                92.1, 88.0, 5, 0.55, 0.041, True, 0.18),
        _graded("oil", "xyz:CL", 16, "user", "short", "Short WTI into the EIA build.",
                95.4, 97.0, 5, 0.6, 0.021, True, 0.09),
        _graded("oil", "xyz:CL", 23, "machine", "long", "Long WTI on a Hormuz tension spike.",
                90.0, 85.5, 10, 0.5, -0.062, False, 0.64),
    ]
    history["btc"] = [desks["btc"]["thesis"]] + [
        _graded("btc", "BTC", 11, "machine", "long", "Long BTC at 5x on ETF inflow resumption.",
                101_000.0, 95_000.0, 5, 0.58, 0.07, True, 0.14),
        _graded("btc", "BTC", 18, "machine", "short", "Short BTC into FOMC, crowded longs.",
                104_500.0, 108_000.0, 5, 0.52, -0.015, False, 0.48),
        _graded("btc", "BTC", 25, "user", "long", "Long BTC on a liquidity-easing bet.",
                98_000.0, 93_000.0, 5, 0.5, 0.09, True, 0.11),
    ]
    history["ai"] = []
    history["gas"] = [desks["gas"]["thesis"]] + [
        _graded("gas", "xyz:NG", 37, "machine", "short", "Short nat gas on a storage surplus.",
                3.4, 3.65, 5, 0.52, 0.03, True, 0.22),
    ]
    return history


def _compute_track_record(history: list[Thesis]) -> TrackRecord:
    def agg(rows: list[Thesis]) -> dict:
        graded = [t for t in rows if t.grade is not None]
        if not graded:
            return {"n_graded": 0, "direction_hit_rate": None, "mean_brier": None, "mean_ret_pct": None}
        hits = [t.grade.direction_hit for t in graded if t.grade.direction_hit is not None]
        briers = [t.grade.brier for t in graded if t.grade.brier is not None]
        return {
            "n_graded": len(graded),
            "direction_hit_rate": (sum(1 for h in hits if h) / len(hits)) if hits else None,
            "mean_brier": (sum(briers) / len(briers)) if briers else None,
            "mean_ret_pct": sum(t.grade.ret_pct for t in graded) / len(graded),
        }

    by_author = {"machine": agg([t for t in history if t.author == "machine"]),
                 "user": agg([t for t in history if t.author == "user"])}
    overall = agg(history)
    return TrackRecord(n_graded=overall["n_graded"], direction_hit_rate=overall["direction_hit_rate"],
                        mean_brier=overall["mean_brier"], mean_ret_pct=overall["mean_ret_pct"],
                        by_author=by_author)


DESK_ORDER = ["oil", "btc", "ai", "gas"]


class RefreshBody(BaseModel):
    force: bool = False


def create_app() -> FastAPI:
    """Fresh desk registry, history and job table per call — no shared
    mutable module state, so parallel test modules never collide."""
    desks = {"oil": _load_oil(), "btc": _build_btc(), "ai": _build_ai(), "gas": _build_gas()}
    history = _seed_history(desks)
    jobs: dict[str, RefreshJob] = {}

    # The /desks grid list page (desks.html) was removed 2026-10-23 (Oil
    # Desk UI overhaul) — only /desks/{desk_id} (desk.html) survives.
    desk_html = (TEMPLATES / "desk.html").read_text(encoding="utf-8")

    app = FastAPI(title="desks-fixture")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def next_catalyst(catalysts: list[Catalyst]) -> Optional[Catalyst]:
        upcoming = [c for c in catalysts if c.at > datetime.now(timezone.utc)]
        return min(upcoming, key=lambda c: c.at) if upcoming else None

    def card_for(desk_id: str) -> DeskCard:
        d = desks[desk_id]
        spec, analytics, intel, thesis = d["spec"], d["analytics"], d["intel"], d["thesis"]
        primary = next(i for i in spec.instruments if i.role == "primary")
        snap = next((i for i in analytics.instruments if i.coin == primary.coin), None)
        age_min = ((datetime.now(timezone.utc) - thesis.created_at).total_seconds() / 60.0) if thesis else None
        return DeskCard(
            id=desk_id, name=spec.name, tagline=spec.tagline,
            primary_coin=primary.coin, primary_label=primary.label,
            mid=snap.mid if snap else None, ret_1d=snap.ret_1d if snap else None,
            one_liner=thesis.call.one_liner if thesis else None,
            call_side=thesis.call.side if thesis else None,
            thesis_age_min=age_min, next_catalyst=next_catalyst(intel.catalysts),
            track_record=_compute_track_record(history[desk_id]),
        )

    def stale_parts(desk_id: str) -> list[str]:
        d = desks[desk_id]
        parts = []
        if datetime.now(timezone.utc) - d["analytics"].as_of > timedelta(hours=24):
            parts.append("analytics")
        if datetime.now(timezone.utc) - d["intel"].as_of > timedelta(hours=24):
            parts.append("intel")
        if d["thesis"] and datetime.now(timezone.utc) >= d["thesis"].expires_at:
            parts.append("thesis")
        return parts

    def brief_for(desk_id: str) -> DeskBrief:
        d = desks[desk_id]
        return DeskBrief(spec=d["spec"], analytics=d["analytics"], intel=d["intel"], thesis=d["thesis"],
                          history=history[desk_id], track_record=_compute_track_record(history[desk_id]),
                          stale=stale_parts(desk_id))

    def material_move(desk_id: str) -> bool:
        d = desks[desk_id]
        primary = next(i for i in d["spec"].instruments if i.role == "primary")
        snap = next(i for i in d["analytics"].instruments if i.coin == primary.coin)
        return abs(snap.ret_1d) * 100 >= d["spec"].token_budget.material_move_pct

    async def finish_job(job_id: str, desk_id: str) -> None:
        await asyncio.sleep(0.35)   # long enough that a poller observes "running" at least once
        job = jobs[job_id]
        d = desks[desk_id]
        base = d["thesis"]
        now = datetime.now(timezone.utc)
        if base is None:
            jobs[job_id] = job.model_copy(update={
                "state": "error", "finished_at": now,
                "message": "no thesis to refresh from — make a call first",
            })
            return
        new_thesis = base.model_copy(update={
            "id": f"{desk_id}-{now:%Y%m%dT%H%M%S}-m", "created_at": now,
            "expires_at": now + timedelta(days=d["spec"].horizon_days), "grade": None,
        })
        d["thesis"] = new_thesis
        history[desk_id].insert(0, new_thesis)
        tokens_k = (new_thesis.usage.input_tokens or 0) / 1000.0
        msg = f"new thesis, {tokens_k:.1f}k tokens".replace(".0k", "k")
        jobs[job_id] = job.model_copy(update={"state": "done", "finished_at": now, "message": msg})

    # pathiel.js's PathielSession (wave-2 onboarding, 2026-10-17 — replaced
    # the old PathielAuth.refresh()/`/auth/me` call this stub used to answer)
    # fetches /api/session unconditionally on every page's DOMContentLoaded
    # (it is in the shared masthead, not this desk's code), for the
    # "trading is off" banner that pathiel.js itself renders everywhere but
    # "/" and "/account". PathielSession.get() already treats a failed
    # fetch as "nothing gates this page" via its own try/catch — so this is
    # purely a console-noise fix (Chromium logs a "Failed to load resource:
    # 404" diagnostic for ANY non-2xx response, application code
    # notwithstanding), not a behavior change. accounts is out of scope for
    # the desks contract either way, and answering "fully onboarded,
    # nothing to say" here is actually MORE representative of a real
    # deployment — where the accounts router answers this route — than
    # leaving it 404.
    @app.get("/api/session")
    async def session() -> dict:
        return {"local_mode": True, "address": None, "active_account_id": None,
                "accounts_count": 1, "can_trade": True, "needs_onboarding": False}

    # The desk page sizes the call for the active account ("Your size") via
    # the risk service. Answered here with the REAL size_trade at a fixed
    # $15.21 starter account, so the line renders deterministically and the
    # page never logs a 404 for it.
    @app.post("/api/risk/size", response_model=TradeSize)
    async def risk_size(body: TradeSizeIn) -> TradeSize:
        return size_trade(FIXTURE_EQUITY_USD, RiskProfile(preset="starter"), [],
                          entry_px=body.entry_px, stop_px=body.stop_px, leverage=body.leverage,
                          coin_max_leverage=20.0, targets=body.targets,
                          venue=VenueRules(min_order_notional_usd=10.5), sz_decimals=3)

    @app.get("/desk/{desk_id}", response_class=HTMLResponse)
    async def desk_page(desk_id: str) -> HTMLResponse:
        return HTMLResponse(desk_html)

    # desks.js asks which desks are active (the tab row) and for the call's
    # leverage line on every switch. Declared BEFORE /api/desks/{desk_id} so
    # "active" is not read as a desk id. 2026-10-25: both were 404 here, and
    # Chromium logs every 404 as a console error, so all 24 zero-console-error
    # tests in this file were red for a fixture gap, not a page bug. An empty
    # active list keeps the single-desk layout every assertion was written
    # against; a not-ok line keeps the line box hidden.
    @app.get("/api/desks/active")
    async def active_desks() -> dict:
        return {"active_desks": [], "default_desk": "oil"}

    @app.get("/api/desks/{desk_id}/line")
    async def call_line(desk_id: str, leverage: float = 20.0) -> dict:
        return {"ok": False, "reason": "fixture app: the leverage line is not modelled here"}

    @app.get("/api/desks", response_model=List[DeskCard])
    async def list_desks() -> List[DeskCard]:
        return [card_for(did) for did in DESK_ORDER]

    @app.get("/api/desks/{desk_id}", response_model=DeskBrief)
    async def get_desk(desk_id: str) -> DeskBrief:
        if desk_id == "err500":
            raise RuntimeError("synthetic failure for the API-500 e2e test")
        if desk_id not in desks:
            raise HTTPException(status_code=404, detail="unknown desk")
        return brief_for(desk_id)

    @app.post("/api/desks/{desk_id}/refresh", response_model=RefreshJob)
    async def refresh_desk(desk_id: str, body: RefreshBody, background_tasks: BackgroundTasks) -> RefreshJob:
        if desk_id not in desks:
            raise HTTPException(status_code=404, detail="unknown desk")
        now = datetime.now(timezone.utc)
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        if not body.force and not material_move(desk_id):
            job = RefreshJob(id=job_id, desk_id=desk_id, state="skipped", started_at=now, finished_at=now,
                              message="skipped: no material move", usage=Usage(skipped_reason="no material move"))
            jobs[job_id] = job
            return job
        job = RefreshJob(id=job_id, desk_id=desk_id, state="running", started_at=now,
                          message="checking intel and analytics for a fresh read…")
        jobs[job_id] = job
        background_tasks.add_task(finish_job, job_id, desk_id)
        return job

    @app.get("/api/desks/jobs/{job_id}", response_model=RefreshJob)
    async def get_job(job_id: str) -> RefreshJob:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown or expired job")
        return job

    @app.get("/api/desks/{desk_id}/theses", response_model=List[Thesis])
    async def list_theses(desk_id: str) -> List[Thesis]:
        if desk_id not in desks:
            raise HTTPException(status_code=404, detail="unknown desk")
        return history[desk_id]

    @app.post("/api/desks/{desk_id}/theses", response_model=Thesis, status_code=201)
    async def create_thesis(desk_id: str, body: UserThesisIn) -> Thesis:
        if desk_id not in desks:
            raise HTTPException(status_code=404, detail="unknown desk")
        spec = desks[desk_id]["spec"]
        now = datetime.now(timezone.utc)
        # UserThesisIn deliberately carries no cross-field validator of its
        # own (contracts/strategies/README.md: Thesis is the one that enforces
        # scenario probabilities summing to 1) — so FastAPI's own request-body
        # parsing lets a bad scenario split through, and that validator only
        # fires here, once Thesis is actually constructed. Caught and
        # translated into the same 422 shape FastAPI itself would produce,
        # otherwise it is an uncaught ValidationError -> 500. The real
        # services/strategies router takes a UserThesisIn through this exact
        # same gap and needs this same translation.
        try:
            thesis = Thesis(
                id=f"{desk_id}-{now:%Y%m%dT%H%M%S}-u", desk_id=desk_id, author="user", created_at=now,
                expires_at=now + timedelta(days=spec.horizon_days), horizon_days=spec.horizon_days,
                call=body.call, scenarios=body.scenarios, usage=Usage(skipped_reason="user authored"),
            )
        except PydanticValidationError as exc:
            # exc.errors() includes an "input" key carrying the full raw
            # kwargs this call just tried to build Thesis from — live
            # datetime/Usage objects, not JSON-safe, which crashed the
            # response encoder (confirmed: TypeError, "datetime is not JSON
            # serializable") the first time this was wired straight through.
            # type/loc/msg are always plain str/int/tuple, so pulling just
            # those three keeps the response encodable without needing to
            # sanitize anything.
            detail = [{"type": e["type"], "loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]
            raise HTTPException(status_code=422, detail=detail) from exc
        history[desk_id].insert(0, thesis)
        return thesis

    return app


if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8710
    if not (8710 <= port <= 8719):
        raise SystemExit(f"port {port} is outside this agent's assigned 8710-8719 band")
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="info")
