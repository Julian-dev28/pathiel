"""Core data types shared across the agent."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from pydantic import BaseModel


class Candle(BaseModel):
    """OHLCV candle."""
    t: int  # timestamp (ms)
    o: float  # open
    h: float  # high
    l: float  # low
    c: float  # close
    v: float  # volume

    def __getitem__(self, key: str) -> float:
        """Allow dict-style access: candle['c'], candle['t'], etc."""
        return getattr(self, key)


class TriggerHit(TypedDict, total=False):
    """Result of a single trigger check from `indicators.triggers`, consumed
    by the perception scan + composite scoring.

    `direction` ("up"/"down") is set by the IMPULSE triggers (pctMoveSpike,
    breakout, momentumBurst, shockDay) so the runner gate can tell a rally
    from a crash — before 2026-07-10 a +12% rally and a -12% crash produced
    the identical composite score and a down-impulse counted as LONG
    structure (the SMSN selloff-buy class)."""
    name: str
    score: float
    reason: str
    fired: bool
    direction: str


# ── DSL exit-policy override (book -> executor) ─────────────────────────────
#
# Shape of `analysis["dsl_exit_override"]`, built identically by all four live
# strategy books (news_surge_multi.py, news_surge_short_live.py,
# social_trending_recorder.py, unlock_short_live.py) and consumed by
# `executor.py` via `_merge_nested_config(config["dsl_exit"], override)` before
# being unpacked into `dsl_exit.ExitPolicy`. Stays a dict (not a dataclass) on
# purpose: it has to survive a dict-merge against the base `dsl_exit` config
# before `ExitPolicy` is constructed from the merged result
# (executor.py:748-1094) — converting it to a dataclass here would break that
# merge step. total=False throughout: every book supplies a different subset
# depending on which knobs its geometry actually uses.

class AtrStopOverride(TypedDict, total=False):
    enabled: bool
    atr_mult: float
    floor_pct: float
    ceiling_pct: float


class NoiseBandOverride(TypedDict, total=False):
    enabled: bool
    atr_mult: float


class DslExitOverride(TypedDict, total=False):
    max_loss_pct: float
    max_loss_roe_pct: float
    protect_pct: float
    retrace_threshold: float
    hard_timeout_minutes: float
    breakeven_trigger_pct: float
    breakeven_lock_pct: float
    stale_flat_timeout_minutes: float
    consecutive_breaches_required: int
    atr_stop: AtrStopOverride
    noise_band: NoiseBandOverride
    phase2_tiers: List[Dict[str, float]]  # [{pct_above_entry, retrace_threshold}, ...]


class WebSearchTelemetry(TypedDict):
    """Return shape of `research._web_search_telemetry()` — always all 4 keys
    (never partial), so `total=True`. Typed as its own TypedDict (rather than
    `Dict[str, Any]`) specifically so it can be spread with `**` into a
    `BookAnalysis` literal — mypy rejects `**` expansion of a plain
    `Dict[str, Any]` into a TypedDict literal, but allows it between two
    TypedDicts."""
    web_search_requested: bool
    web_search_used: bool
    web_search_request_count: int
    web_search_citations: List[str]


class _BookAnalysisRequired(TypedDict):
    """Keys read via `analysis["..."]` (bracket, no default) in
    `executor.maybe_execute` — a missing one is a live KeyError, so these are
    the only fields that are actually required."""
    id: str
    coin: str
    verdict: str          # "PASS" | "LONG" | "SHORT" | "CLOSE"
    confidence: float


class BookAnalysis(_BookAnalysisRequired, total=False):
    """The `analysis` dict handed from a book/research pipeline into
    `executor.maybe_execute`. Built independently at 6 sites:
    `research.py` (thin-4h-history early return + the main AI-verdict path),
    and each of the 4 live strategy books' `_analysis`/`_live_analysis`
    builder. `total=False` here mirrors the actual contract exactly: every key
    below is read via `.get()` (with a default) somewhere in `executor.py`,
    never via a bare subscript — see `_BookAnalysisRequired` for the 4 keys
    that ARE required.

    This type exists to let a type checker catch the failure mode the brief
    called out directly: a 5th book that forgets a key (or misspells one) an
    existing book relies on. It does not change any dict literal or access
    pattern — `TypedDict` is erased at runtime.
    """
    side: Optional[str]                      # "long" | "short" | None
    entry_px: float
    stop_px: float
    tp_px: float
    reasoning: str
    perception_id: str
    news_context: str
    news_risk: str                           # "none" | "positive" | "negative"
    ai_down: bool
    ai_brain_provider: str
    created_at: int                          # epoch ms
    composite_score: float
    # Trigger-derived flags (research.py's full path always sets these;
    # the thin-history early return sets only the first 5 — the rest default
    # via `bool(analysis.get(...))` at every read site, so the omission is
    # inert, see 2-type-consolidation.md).
    momentum_burst_fired: bool
    slow_burn_fired: bool
    slow_burn_count: int
    daily_mover_fired: bool
    up_impulse_fired: bool
    breakout_fired: bool
    shock_day_fired: bool
    volume_spike_fired: bool
    uptrend_momentum_fired: bool
    downtrend_momentum_fired: bool
    daily_move_pct: Optional[float]
    daily_volume_usd: Optional[float]
    # Web-search telemetry (research.py `_web_search_telemetry`, spread with **).
    web_search_requested: bool
    web_search_used: bool
    web_search_request_count: int
    web_search_citations: List[str]
    # Strategy-book identity + sizing/exit overrides.
    strategy_book: str
    strategy_book_notional: float
    strategy_book_equity_frac_override: float
    leverage_override: int
    backup_sl_pct_override: float
    sl_atr_mult_override: float
    tp_scale_fraction_override: float
    min_short_volume_usd_override: float
    dsl_exit_override: DslExitOverride
    # TA-sidestep override bookkeeping (executor.py mutates these in place on
    # a PASS->LONG upgrade).
    sidestep_override: bool


# ── shadow-ledger forward-grade result ──────────────────────────────────────
#
# Shape of `pathia.agents.shadow_ledger.grade_records()`'s return dict.
# Read differently by its 3 consumers (`scripts/autonomous_cycle.py`,
# `services/trend_engine/recorders.py`, `scripts/shadow_status.py`) — see
# 2-type-consolidation.md for the fee-tier discrepancy this shape surfaced
# between the promotion decision (`autonomous_cycle.py`, uses `slip6`) and the
# dashboard verdict (`classify()`/`recorders.py`, uses `slip12`).

class SlipTierGrade(TypedDict, total=False):
    mean_pct: float
    total_pct: float
    win: float


class OosHalves(TypedDict, total=False):
    first: Optional[float]
    second: Optional[float]
    n_first: int
    n_second: int


class GradeResult(TypedDict, total=False):
    n: int
    pending: int
    ungradeable: int
    errors: int
    deduped: int
    funding_included: bool
    # One member per `shadow_ledger.SLIP_TIERS_BPS` entry ([0, 6, 12, 25, 50]).
    slip0: SlipTierGrade
    slip6: SlipTierGrade
    slip12: SlipTierGrade
    slip25: SlipTierGrade
    slip50: SlipTierGrade
    mean_price_only_12bps_pct: float
    oos_12bps: OosHalves
    detail: List[Dict[str, Any]]
    verdict: Dict[str, str]  # {"label": ..., "why": ...} from classify()
