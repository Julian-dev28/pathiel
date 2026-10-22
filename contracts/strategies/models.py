"""Strategies (desks) contract, v1.

One desk = one siloed focus (oil, btc, top5 crypto, AI equities, ...). Every
service that touches a desk speaks these models and nothing else:

    services/strategies  owns DeskSpec registry, DeskAnalytics (deterministic),
                         the thesis journal + grader + trade grader, and the
                         HTTP routes.
    services/intel       owns IntelPacket (news, policy, catalysts, odds, COT).
    services/llm +
    services/thesis      own Thesis generation (the only latent step).
    pathiel/templates    render DeskCard / DeskBrief.

Rule: numbers are computed in DeskAnalytics/IntelPacket (deterministic space).
The Thesis may only quote numbers that exist in those payloads. The validator in
services/thesis enforces that. A thesis that invents a number is rejected.

Bump CONTRACT_VERSION on any breaking change and update every consumer in the
same change.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "1.0.0"

Side = Literal["long", "short"]
CallSide = Literal["long", "short", "flat", "pair"]
Stance = Literal["bull", "bear", "neutral", "unknown"]
Author = Literal["machine", "user"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── desk definition ──────────────────────────────────────────────────────────

class Instrument(_M):
    coin: str                                  # HL name, e.g. "xyz:CL", "BTC"
    label: str                                 # human name, e.g. "WTI"
    role: Literal["primary", "secondary", "hedge"] = "primary"


class PairSpec(_M):
    label: str                                 # "Brent-WTI spread"
    long: str                                  # coin bought
    short: str                                 # coin sold


class TokenBudget(_M):
    """Per-desk LLM spend policy. The pipeline skips the LLM entirely unless
    the market moved >= material_move_pct since the last thesis, a new
    high-relevance headline arrived, the thesis expired, or the user forced it."""
    max_input_tokens: int = 6000
    max_output_tokens: int = 1800
    refresh_min_interval_min: int = 60
    material_move_pct: float = 1.5             # abs % move of primary since last thesis
    min_headline_relevance: float = 0.7        # new headline at/above this forces rewrite


class DeskSpec(_M):
    id: str = Field(pattern=r"^[a-z0-9_]{2,24}$")
    name: str                                  # "Oil"
    tagline: str                               # one line, what this desk trades and why
    instruments: List[Instrument]
    pairs: List[PairSpec] = []
    news_queries: List[str] = []
    policy_topics: List[str] = []              # bills, regs, sanctions, SPR, tariffs...
    prediction_market_queries: List[str] = []  # Polymarket/Kalshi question search terms
    catalyst_kinds: List[str] = []             # e.g. "eia_weekly", "opec_meeting", "fomc"
    leverage_ladder: List[float] = [5, 10, 20]
    horizon_days: int = 7
    token_budget: TokenBudget = TokenBudget()
    lessons: List[str] = []                    # concept topics this desk teaches

    @model_validator(mode="after")
    def _one_primary(self) -> "DeskSpec":
        if not any(i.role == "primary" for i in self.instruments):
            raise ValueError("desk needs at least one primary instrument")
        return self


# ── deterministic analytics (services/strategies) ────────────────────────────

class Quantiles(_M):
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float


class InstrumentSnapshot(_M):
    coin: str
    label: str
    mid: float
    ret_1d: float                              # fractions, 0.05 = +5%
    ret_5d: float
    ret_20d: float
    ret_60d: Optional[float] = None
    high_20d: float
    low_20d: float
    high_60d: Optional[float] = None
    low_60d: Optional[float] = None
    atr14_pct: float
    vol_daily_10d: float
    vol_daily_20d: float
    vol_daily_60d: Optional[float] = None
    funding_hourly: Optional[float] = None     # HL rate per hour; + = longs pay
    open_interest_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    max_leverage: Optional[float] = None


class PairSnapshot(_M):
    label: str
    long: str
    short: str
    spread_now: float                          # long mid - short mid
    spread_mean_20d: float
    spread_mean_60d: Optional[float] = None
    spread_min_60d: Optional[float] = None
    spread_max_60d: Optional[float] = None
    pair_vol_daily: float                      # sd of daily log(long/short) change
    spread_change_7d: Optional[Quantiles] = None


class ConditionalStats(_M):
    label: str                                 # "after +5% 5d run"
    n: int
    mean: float
    p50: float
    hit_rate_up: float


class ForwardStats(_M):
    """Empirical horizon-day forward windows from daily bars."""
    coin: str
    horizon_days: int
    n_windows: int
    sample_start: datetime
    sample_end: datetime
    final_ret: Quantiles
    final_ret_mean: float
    long_mae: Quantiles                        # max adverse excursion for a long (negative)
    short_mae: Quantiles                       # max adverse excursion for a short (positive)
    conditional: List[ConditionalStats] = []


class LadderRung(_M):
    """One leverage choice, sized at full equity as margin unless noted.
    All *_pct / p_* fields are fractions of equity (returns) or probabilities."""
    coin: str
    side: Side
    leverage: float
    equity_usd: float
    notional_usd: float
    entry_px: float
    liq_px: float
    liq_dist_pct: float                        # signed price move to liquidation
    p_liq: float                               # P(liquidated within horizon), empirical
    p_win: float
    ev_pct: float                              # mean equity return after fees+funding
    equity_ret: Quantiles                      # equity return distribution
    p_double: float
    p_lose_half: float
    fees_funding_pct: float                    # round-trip taker + horizon funding, % of equity
    stop_px: Optional[float] = None            # suggested stop (60% of liq distance)
    p_stop: Optional[float] = None             # P(stop hit within horizon)
    verdict: Literal["hold", "dip_only", "intraday_only", "avoid"]


class AccountSnapshot(_M):
    equity_usd: Optional[float] = None
    source: Literal["venue", "fixture", "assumed"] = "assumed"


class DeskAnalytics(_M):
    contract_version: str = CONTRACT_VERSION
    desk_id: str
    as_of: datetime
    instruments: List[InstrumentSnapshot]
    pairs: List[PairSnapshot] = []
    forward: List[ForwardStats] = []
    ladder: List[LadderRung] = []
    account: AccountSnapshot = AccountSnapshot()
    notes: List[str] = []                      # data caveats, e.g. "sample includes war shock"


# ── intel (services/intel) ───────────────────────────────────────────────────

class Headline(_M):
    id: str                                    # stable hash of url|title
    title: str
    url: str
    source: str
    published_at: Optional[datetime] = None
    stance: Stance = "unknown"
    relevance: float = Field(ge=0, le=1)
    summary: Optional[str] = None


class PolicyItem(_M):
    id: str
    title: str
    url: str
    body: str                                  # "US Congress", "Treasury/OFAC", "OPEC+", "EU"...
    status: Literal["proposed", "in_committee", "passed_chamber", "enacted",
                    "executive_action", "regulation", "announced", "unknown"] = "unknown"
    date: Optional[datetime] = None
    summary: Optional[str] = None
    stance: Stance = "unknown"


class Catalyst(_M):
    id: str
    kind: str                                  # "eia_weekly", "opec_meeting", ...
    title: str
    at: datetime                               # UTC
    importance: int = Field(ge=1, le=3)        # 3 = market-moving
    source: str
    expected: Optional[str] = None             # consensus if known


class MarketOdds(_M):
    id: str
    venue: Literal["polymarket", "kalshi", "other"]
    question: str
    probability: float = Field(ge=0, le=1)
    volume_usd: Optional[float] = None
    url: str
    closes_at: Optional[datetime] = None


class PositioningItem(_M):
    id: str
    source: str                                # "cftc_cot", "hl_funding", ...
    label: str                                 # "WTI managed money net long"
    value: float
    change: Optional[float] = None
    as_of: datetime


class IntelPacket(_M):
    contract_version: str = CONTRACT_VERSION
    desk_id: str
    as_of: datetime
    headlines: List[Headline] = []
    policy: List[PolicyItem] = []
    catalysts: List[Catalyst] = []
    odds: List[MarketOdds] = []
    positioning: List[PositioningItem] = []
    token_estimate: int = 0                    # tokens of the compact render fed to the LLM
    sources_failed: List[str] = []             # feeds that errored; never silently empty


# ── thesis (services/thesis, stored by services/strategies) ─────────────────

class Call(_M):
    side: CallSide
    instrument: str                            # coin, or pair label when side == "pair"
    one_liner: str = Field(max_length=160)     # the whole trade in one sentence
    entry_px: Optional[float] = None
    entry_zone: Optional[List[float]] = None   # [lo, hi]
    stop_px: Optional[float] = None
    targets: List[float] = []
    leverage: Optional[float] = None
    confidence: float = Field(ge=0, le=1)
    invalidation: str                          # the observable that kills the idea


class Scenario(_M):
    name: str
    probability: float = Field(ge=0, le=1)
    # Horizon return bucket [lo, hi) as FRACTIONS despite the _pct name:
    # -0.05 means -5%. The description reaches the LLM through the JSON schema;
    # without it, live drafts wrote -12.37 for -12.37% (2026-10-17).
    move_lo_pct: float = Field(description="Horizon return bucket lower bound as a FRACTION, "
                                           "not percent: -0.05 means -5%.")
    move_hi_pct: float = Field(description="Horizon return bucket upper bound as a FRACTION, "
                                           "not percent: 0.03 means +3%.")
    description: str
    triggers: List[str] = []


class Point(_M):
    text: str
    source_ids: List[str] = []                 # Headline/PolicyItem/Catalyst/MarketOdds ids


class LeverageView(_M):
    leverage: float
    verdict: Literal["hold", "dip_only", "intraday_only", "avoid"]
    reason: str


class Lesson(_M):
    term: str
    explanation: str = Field(max_length=400)
    tied_to: str                               # what in today's market makes it matter


class SourceRef(_M):
    id: str
    title: str
    url: str


class PayloadRef(_M):
    as_of: datetime
    sha256: str


class Usage(_M):
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    duration_s: Optional[float] = None
    cost_usd: Optional[float] = None
    skipped_reason: Optional[str] = None       # set when the pipeline reused a thesis


class Grade(_M):
    graded_at: datetime
    outcome_px: float
    ret_pct: float
    direction_hit: Optional[bool] = None       # None for flat
    hit_target_first: Optional[bool] = None
    hit_stop_first: Optional[bool] = None
    brier: Optional[float] = None              # multiclass Brier over scenarios
    mae_pct: float
    mfe_pct: float
    notes: List[str] = []


class Thesis(_M):
    contract_version: str = CONTRACT_VERSION
    id: str
    desk_id: str
    author: Author = "machine"
    created_at: datetime
    expires_at: datetime
    horizon_days: int
    call: Call
    scenarios: List[Scenario] = []
    bull: List[Point] = []
    bear: List[Point] = []
    catalysts: List[Catalyst] = []
    leverage_view: List[LeverageView] = []
    lessons: List[Lesson] = []
    what_changes_my_mind: List[str] = []
    sources: List[SourceRef] = []
    analytics_ref: Optional[PayloadRef] = None
    intel_ref: Optional[PayloadRef] = None
    # The desk's primary coin(s) when this thesis was written, stamped by
    # services/strategies/pipeline. [] = written before 2026-10-19 (unknown).
    # The token gate rewrites a thesis whose desk has changed primary since.
    primary_coins: List[str] = []
    usage: Usage = Usage()
    grade: Optional[Grade] = None

    @model_validator(mode="after")
    def _scenarios_sum_to_one(self) -> "Thesis":
        if self.scenarios:
            total = sum(s.probability for s in self.scenarios)
            if abs(total - 1.0) > 0.02:
                raise ValueError(f"scenario probabilities sum to {total:.3f}, not 1")
        return self


# ── HTTP shapes (served by services/strategies/api.py) ──────────────────────
#   GET  /api/desks                     -> List[DeskCard]
#   GET  /api/desks/{desk_id}           -> DeskBrief
#   POST /api/desks/{desk_id}/refresh   -> RefreshJob      body: {"force": bool}
#   GET  /api/desks/jobs/{job_id}       -> RefreshJob
#   GET  /api/desks/{desk_id}/theses    -> List[Thesis]   (newest first)
#   POST /api/desks/{desk_id}/theses    -> Thesis         body: UserThesisIn
#   GET  /desks, /desks/{desk_id}       -> HTML pages

class TrackRecord(_M):
    n_graded: int = 0
    direction_hit_rate: Optional[float] = None
    mean_brier: Optional[float] = None
    mean_ret_pct: Optional[float] = None
    by_author: dict = {}                       # {"machine": {...}, "user": {...}} same keys
    # Trade-grading roll-up (services/strategies/trades.py), aggregated across
    # every registered account's fills on every graded thesis for this desk.
    # Optional/absent = no trades computed yet, never a 0 standing in for
    # "nothing to report" (0.0 realized P&L is a real, different fact).
    realized_pnl_usd: Optional[float] = None
    n_trades: int = 0
    follow_rate: Optional[float] = None


class AccountTradeGrade(_M):
    """One registered account's fills against one thesis's call, over the
    window [thesis.created_at, min(thesis.expires_at, next thesis's
    created_at)]. See services/strategies/trades.py."""
    account_id: str
    label: str
    main_address: str
    mode: str                                  # "trade" | "watch"
    source: str                                # "wallet_sign" | "api_key" | "watch" | "env"
    n_fills: int = 0
    n_opens: int = 0
    n_closes: int = 0
    realized_pnl_usd: float = 0.0              # sum(closedPnl) - sum(fee) over matched fills
    follow_rate: Optional[float] = None         # fraction of OPENING fills in the call's direction
    first_entry_px: Optional[float] = None      # px of the earliest opening fill in the window
    entered_in_zone: Optional[bool] = None      # first_entry_px inside call.entry_zone
    max_adverse_fill: Optional[float] = None    # worst single fill's closedPnl (most negative)


class ThesisTrades(_M):
    """Every registered account's AccountTradeGrade for one thesis. Stored as
    a sibling file to the thesis in the journal, not a Thesis field — see
    services/strategies/trades.py's module docstring for why."""
    thesis_id: str
    desk_id: str
    window_start: datetime
    window_end: datetime
    computed_at: datetime
    accounts: List[AccountTradeGrade] = []


class DeskCard(_M):
    id: str
    name: str
    tagline: str
    primary_coin: str
    primary_label: str
    mid: Optional[float] = None
    ret_1d: Optional[float] = None
    one_liner: Optional[str] = None            # latest thesis call
    call_side: Optional[CallSide] = None
    thesis_age_min: Optional[float] = None
    next_catalyst: Optional[Catalyst] = None
    track_record: TrackRecord = TrackRecord()


class DeskBrief(_M):
    spec: DeskSpec
    analytics: Optional[DeskAnalytics] = None
    intel: Optional[IntelPacket] = None
    thesis: Optional[Thesis] = None
    history: List[Thesis] = []
    track_record: TrackRecord = TrackRecord()
    stale: List[str] = []                      # which parts are older than their TTL
    refreshing: bool = False                   # analytics shown are cached; a rebuild is queued/running
    # The refresh (POST /api/desks/{id}/refresh) queued or running for this
    # desk right now, if any: a page opened or reloaded mid-refresh shows its
    # progress instead of a Refresh button that seems to do nothing. Set by
    # services/strategies/api.py; None everywhere else (CLI, MCP).
    refresh_job: Optional["RefreshJob"] = None


class RefreshJob(_M):
    id: str
    desk_id: str
    state: Literal["queued", "running", "done", "skipped", "error"]
    started_at: datetime
    finished_at: Optional[datetime] = None
    message: Optional[str] = None
    usage: Optional[Usage] = None


DeskBrief.model_rebuild()


class UserThesisIn(_M):
    call: Call
    scenarios: List[Scenario] = []
    notes: Optional[str] = None
