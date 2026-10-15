"""Risk gates — every gate is a pure function returning {pass, reason?}.

All gates are evaluated; results are collected for telemetry (no short-circuit).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

GateResult = Dict[str, Any]  # {pass: bool, reason?: str}

logger = logging.getLogger(__name__)


class GateContext:
    """Context passed to all risk gates."""
    def __init__(
        self,
        confidence: float,
        current_positions: List[Dict[str, Any]],
        trade_notional_usd: float,
        daily_pnl: float,
        market_volume_24h_usd: float,
        coin: str,
        trade_side: str,  # 'long' or 'short'
        has_binary_news_risk: bool,
        equity: float,
        total_open_notional: float,
        composite_score: float = 0.0,
        momentum_burst_fired: bool = False,
        volume_spike_fired: bool = False,
        breakout_fired: bool = False,
        slow_burn_fired: bool = False,
        whale_signal_fired: bool = False,
        binary_news_match: str = "",
        peak_daily_pnl: float = 0.0,
        chronos_median_pct: Optional[float] = None,
        chronos_spread_pct: Optional[float] = None,
        chronos_q10_path_pct: Optional[List[float]] = None,
        chronos_q90_path_pct: Optional[List[float]] = None,
        timesfm_q10_path_pct: Optional[List[float]] = None,
        timesfm_q90_path_pct: Optional[List[float]] = None,
        timesfm_median_pct: Optional[float] = None,
        tirex_q10_path_pct: Optional[List[float]] = None,
        tirex_q90_path_pct: Optional[List[float]] = None,
        duelist_verdict: Optional[str] = None,
        # Cohort admission (B.46): with-the-signal vwap entries carry the
        # cohort tag + answering model so the classic confidence gate can
        # apply the same model+cohort admission as the runner gate.
        # Default None = byte-identical for every other construction site.
        cohort_tag: Optional[str] = None,
        primary_model: Optional[str] = None,
    ):
        self.confidence = confidence
        self.current_positions = current_positions
        self.trade_notional_usd = trade_notional_usd
        self.daily_pnl = daily_pnl
        self.peak_daily_pnl = peak_daily_pnl
        self.market_volume_24h_usd = market_volume_24h_usd
        self.coin = coin
        self.trade_side = trade_side
        self.has_binary_news_risk = has_binary_news_risk
        self.equity = equity
        self.total_open_notional = total_open_notional
        self.composite_score = composite_score
        self.momentum_burst_fired = momentum_burst_fired
        # Fresh-impulse trigger twins (volume_spike / breakout), carried for
        # the tail gates' impulse-class composite-escape denial — see
        # _tail_composite_escape_allowed. Default False = byte-identical
        # behaviour for every existing construction site.
        self.volume_spike_fired = volume_spike_fired
        self.breakout_fired = breakout_fired
        # True iff any 1h slow-burn trigger fired (volumeBuildup1h /
        # trendFlip1h / higherLows1h). Used as a counter-regime bypass: a
        # clean 1h accumulation pattern overrides the slow BTC proxy.
        self.slow_burn_fired = slow_burn_fired
        # True iff whale_index oi_funding_anomaly flagged this coin
        # (negative funding + flat price + high OI = whale accumulation).
        # Same gate-bypass role as slow_burn_fired; orthogonal signal.
        self.whale_signal_fired = whale_signal_fired
        # The headline + matched term that tripped the binary-news gate, for
        # log visibility ("which article blocked this?").
        self.binary_news_match = binary_news_match
        # Median move of this coin's Chronos-2 forecast, % vs last close.
        # Side-independent; fed by maybe_execute via get_chronos_signal_sync
        # (warm cache hit, or one bounded compute on a cold/expired cache —
        # never a cache-only peek, which raced the attach compute).
        # None = no usable forecast → chronos_mismatch_gate
        # has no opinion and passes.
        self.chronos_median_pct = chronos_median_pct
        # Chronos-2 p10-p90 spread, % vs last close (same warm sync read as
        # the median above). Fed to tail_spread_comparator (log-only, plan D4:
        # band-width veto vs quantile-path veto accrual). The ratio-aware
        # deadband counterfactual that also consumed it was removed 2026-09-18
        # (§C.1 die ruling). None = no spread (error signal) → consumers
        # have no opinion.
        self.chronos_spread_pct = chronos_spread_pct
        # Per-step Chronos-2 quantile paths, % vs last close (same sync read;
        # None on error signals / old shapes). Fed to
        # chronos_tail_trigger_gate — the shape-based counter-forecast veto
        # validated by the 2026-08-28 60-flag replay (adverse-quantile early
        # tail > path-mean > endpoint for loss avoidance).
        self.chronos_q10_path_pct = chronos_q10_path_pct
        self.chronos_q90_path_pct = chronos_q90_path_pct
        # TimesFM-3 per-step quantile paths, % vs last close (gate-side warm
        # sync read, same pattern as the chronos paths above; None on error
        # signals). Fed to forecast_agreement_veto_gate — the AND-agreement
        # tail veto from the 2026-09-02 two-model sweep.
        self.timesfm_q10_path_pct = timesfm_q10_path_pct
        self.timesfm_q90_path_pct = timesfm_q90_path_pct
        # Median move of this coin's TimesFM-3 forecast, % vs last close
        # (gate-side warm sync read, same pattern as chronos_median_pct and
        # the two q-path fields above). None = no usable forecast. Fed ONLY
        # to timesfm_mismatch_gate — the mirror of chronos_mismatch_gate on
        # the TimesFM signal. SHADOW-ONLY by construction: the gate always
        # passes; a data gap can never flag.
        self.timesfm_median_pct = timesfm_median_pct
        # TiRex 1.1 per-step quantile paths, % vs last close (gate-side warm
        # sync read from tirex_signal, same pattern as the chronos/timesfm
        # paths above; None on error/disabled). Fed to
        # tirex_tail_trigger_gate — chosen because in the 2026-09-16 MOE eval
        # (n=2,688 prod-regime anchors) tirex's adverse quantile beat every
        # other forecaster's at stop-out AUC on BOTH sides (long @3%: 0.898
        # vs chronos 0.863; short: 0.821 vs 0.797; paired bootstrap p<0.001).
        self.tirex_q10_path_pct = tirex_q10_path_pct
        self.tirex_q90_path_pct = tirex_q90_path_pct
        # The A/B duelist's verdict at entry (LONG / SHORT / PASS / VETO / None),
        # carried from research.py's `duelist_at_entry` snapshot via
        # maybe_execute. None = the duelist is disabled or failed — the
        # veto has no opinion and passes.
        self.duelist_verdict = duelist_verdict
        self.cohort_tag = cohort_tag
        self.primary_model = primary_model


def _tail_composite_escape_allowed(ctx: GateContext, cfg: Dict[str, Any]) -> bool:
    """Whether a tripped tail-trigger gate may be escaped via composite score.

    The impulse-class denial (2026-09-24, NIL knife cohort): on the fresh-
    impulse hype class — >= ``impulse_min_triggers`` (default 2) of the
    volume_spike / breakout / momentum_burst trio fired — the composite-score
    escape is CLOSED for tail gates. Conviction (conf >= min_conf) or nothing.

    Why: composite on this class is *earned by the blow-off itself* — one
    terminal re-acceleration wick fires all three triggers and mutes every
    forecaster gate at once. Executed-entry replay over the full log history
    (09-05..09-24, scratch/_tail_escape_replay.py): 4 entries where the
    chronos adverse tail tripped and composite escaped — MET 09-07 (comp 89.5,
    -$19.53), NIL 09-19 (-$1.10), ZEC 09-22 (conf+comp, -$0.28), NIL 09-24
    (comp 61.8, -$2.15) — 4/4 losers, net -$23.1, ALL with the full trio
    fired and every forecaster median negative. Same structural hole C.6
    closed on the regime gate (which also cites MET 09-07).

    Config: ``no_composite_escape_on_impulse`` per tail-gate block — code
    default False (byte-identical for any config without the key); armed via
    .agent-config.json. The conf escape path is never touched. Missing
    trigger flags default False => impulse count 0 => escape allowed
    (fail-open, old behaviour).
    """
    if not bool(cfg.get("no_composite_escape_on_impulse", False)):
        return True
    need = int(cfg.get("impulse_min_triggers", 2) or 2)
    fired = sum(1 for f in (ctx.volume_spike_fired, ctx.breakout_fired,
                            ctx.momentum_burst_fired) if f)
    return fired < need


def confidence_gate(ctx: GateContext, min_confidence: float) -> GateResult:
    if ctx.confidence >= min_confidence:
        return {"pass": True}
    return {"pass": False, "reason": f"confidence {ctx.confidence:.2f} < {min_confidence}"}


def _cohort_conf_admission(ctx: GateContext, cfg: Dict[str, Any],
                           res: GateResult, min_conf: float) -> GateResult:
    """Model+cohort admission for the CLASSIC confidence gate (B.46).

    Same key and floor as the runner gate's cohort_admissions (PR #84):
    the fade-short cohort's conf lives at p50 0.42 / max 0.58 (0/34 reach
    0.65) and the edge IS the low-conf band, so the general floor is a wall
    for it. Keyed "<answering model>/<cohort tag>" — welded to the
    gauntleted model; absent key / model mismatch / tagless / malformed =
    the original block result, byte-identical, fail-open. This exempts
    ONLY the confidence gate: the short-liquidity floor (squeeze risk on
    thin markets) and every other classic gate still apply — the cohort
    proves itself on liquid names first.
    """
    tag = str(getattr(ctx, "cohort_tag", "") or "").strip().lower()
    if not tag:
        return res
    adm = (cfg.get("runner_entry_gate") or {}).get("cohort_admissions") or {}
    key = f"{str(getattr(ctx, 'primary_model', '') or '').strip().lower()}/{tag}"
    if key not in adm:
        return res
    try:
        floor = float(adm[key])
    except (TypeError, ValueError):
        return res  # malformed -> original block (fail-open)
    if ctx.confidence >= floor:
        logger.info(
            f"[gate][COHORT] conf-gate ADMIT {ctx.coin} "
            f"{str(ctx.trade_side).upper()}: {key} conf {ctx.confidence:.2f} "
            f">= cohort floor {floor:.2f} (general {min_conf:.2f} exempted) — "
            f"all other classic gates incl. short liquidity still apply"
        )
        return {"pass": True, "via": f"cohort_admission:{key}",
                "cohort_floor": floor}
    return res


def max_concurrent_positions_gate(ctx: GateContext, max_concurrent: int) -> GateResult:
    if len(ctx.current_positions) < max_concurrent:
        return {"pass": True}
    return {"pass": False, "reason": f"max positions reached ({len(ctx.current_positions)}/{max_concurrent})"}


def per_trade_notional_cap_gate(ctx: GateContext, cap_usd: float) -> GateResult:
    cap = float(cap_usd or 0)
    if cap <= 0:
        return {"pass": True}
    # The executor normalizes the target notional into an exchange-valid coin
    # size before gates. Coin precision can create a few cents/dollars of cap
    # dust, e.g. target $650.00 -> valid size worth $650.05. Treat that as
    # still capped; larger overshoots remain blocked.
    precision_tolerance = max(0.25, cap * 0.005)
    if ctx.trade_notional_usd <= cap + precision_tolerance:
        return {"pass": True}
    return {"pass": False, "reason": f"trade notional ${ctx.trade_notional_usd:.2f} exceeds cap ${cap:.2f}"}


def effective_daily_kill_usd(config: Dict[str, Any], equity: float) -> float:
    """Daily kill threshold in USD (positive magnitude), equity-relative.

    thr = clamp(pct_of_equity * equity, min_usd, cap_usd). The pct form keeps
    the brake proportional as the account grows; the cap is the absolute
    ceiling ("never lose $X in one day no matter the equity"); the min stops
    a small account from halting on ordinary noise.
    Returns 0.0 when the kill switch is disabled (pct unset/0) — the entry
    gate, the halt timer AND the heartbeat's hard flatten then all stand
    down. This is the ENTRY-GATE / HALT threshold; the hard flatten fires
    at `flatten_daily_kill_usd` (a multiple of this) so the book has a
    grace band to recover in."""
    pct = float(_cfg(config, "daily_kill_pct_of_equity", 0) or 0)
    if pct <= 0:
        return 0.0
    thr = pct * max(0.0, float(equity or 0))
    cap = float(_cfg(config, "daily_kill_cap_usd", 0) or 0)
    if cap > 0:
        thr = min(thr, cap)
    floor = float(_cfg(config, "daily_kill_min_usd", 0) or 0)
    if floor > 0:
        thr = max(thr, floor)
    return thr


def flatten_daily_kill_usd(config: Dict[str, Any], equity: float) -> float:
    """Hard flatten threshold for the heartbeat kill-switch: the entry-gate /
    halt threshold times `daily_kill_flatten_mult` (default 1.25).

    The flatten is deliberately HIGHER than the halt so the book has a
    grace band (-T .. -mult*T): once the day breaches -T the halt blocks
    new entries, but the open positions are still alive and can claw the
    day back above the release band (clearing the halt early). A flat
    flatten at T would kill that recovery path — daily PnL is equity-based
    and stays pinned red once the book is empty, so the early release
    would be unreachable and the halt a hard 6h lock. 0 = disabled
    (kill switch off), mirroring effective_daily_kill_usd."""
    base = effective_daily_kill_usd(config, equity)
    if base <= 0:
        return 0.0
    mult = float(_cfg(config, "daily_kill_flatten_mult", 1.25) or 1.25)
    return base * mult


def hard_killswitch_should_fire(config: Dict[str, Any], equity: float,
                                open_positions: List[Dict[str, Any]],
                                daily_pnl: float,
                                clean_read: bool = True) -> tuple[bool, float]:
    """Pure decision for the heartbeat HARD daily-loss flatten: returns
    (should_fire, flatten_threshold_usd).

    Preserves the original guards (threshold>0, equity>0, positions open,
    pnl <= -threshold) and adds the 2026-10-03 plausibility gate: `clean_read`
    must be True — memory's equity plausibility filter rejected the last tick
    (suspected partial-dex degraded read) when it is False. A degraded
    aggregate can fabricate a catastrophic daily PnL: on 2026-10-03 a 429'd
    HIP-3 dex query dropped ~$948 of IDLE equity from the aggregate and the
    phantom -$948.16 fired the first-ever HARD fire, flattening the book on
    fiction (and double-booking the close via the rehydrate re-synth).
    A real crash with a clean feed still fires — the filter only trips on
    rejected reads, and a SUSTAINED real move is accepted after 180s."""
    thr = flatten_daily_kill_usd(config, equity)
    if thr <= 0 or equity <= 0 or not open_positions:
        return False, thr
    if not clean_read:
        logger.error(
            "[killswitch] daily-loss breach computed from an IMPLAUSIBLE "
            "equity read (memory rejected the last tick) — NOT flattening; "
            f"raw PnL ${daily_pnl:.2f}. If the loss is real it will "
            "re-assert on a clean read and fire then.")
        return False, thr
    return daily_pnl <= -thr, thr


def daily_loss_kill_switch(ctx: GateContext, max_daily_loss: float,
                           halt_remaining_min: float = 0.0) -> GateResult:
    """Block new ENTRIES when the day is deep in the red (or a halt timer is
    active). `max_daily_loss` is the effective USD threshold from
    effective_daily_kill_usd (0 = disabled → this gate passes). Block while
    daily_pnl <= -threshold OR a halt timer from a previous breach is still
    running. The halt is a TIMER (memory.arm_daily_halt) that expires or is
    cleared early on recovery — deliberately NOT a UTC-rollover lock, so a
    genuine regime change back can resume trading the same day."""
    if max_daily_loss <= 0:
        return {"pass": True}
    if halt_remaining_min > 0:
        return {"pass": False,
                "reason": (f"daily loss halt active ({halt_remaining_min:.0f}min "
                           f"remaining; lifts early on PnL recovery)")}
    if ctx.daily_pnl > -max_daily_loss:
        return {"pass": True}
    return {"pass": False,
            "reason": (f"daily loss killswitch triggered (PnL ${ctx.daily_pnl:.2f} "
                       f"<= -${max_daily_loss:.2f}, equity-relative)")}


def daily_giveback_gate(ctx: GateContext, halt_pct: float, min_peak_usd: float) -> GateResult:
    """Lock in a green day: once daily PnL has peaked at >= `min_peak_usd`, block
    NEW positions if it then retraces more than `halt_pct` from that peak. Existing
    positions keep riding their own stops; this only stops opening fresh risk so a
    won day can't fully round-trip. Disabled when halt_pct<=0. Resets at the UTC
    day roll (peak_daily_pnl resets in memory.track_daily_pnl)."""
    if halt_pct <= 0 or ctx.peak_daily_pnl < min_peak_usd:
        return {"pass": True}
    floor = ctx.peak_daily_pnl * (1.0 - halt_pct)
    if ctx.daily_pnl <= floor:
        return {"pass": False,
                "reason": (f"daily give-back halt: PnL ${ctx.daily_pnl:.0f} retraced "
                           f">{halt_pct*100:.0f}% from peak ${ctx.peak_daily_pnl:.0f} "
                           f"(floor ${floor:.0f}) — no new entries until UTC roll")}
    return {"pass": True}


def market_liquidity_floor(
    ctx: GateContext,
    min_volume: float,
    min_volume_hip3: Optional[float] = None,
    gate_config: Optional[dict] = None,
) -> GateResult:
    """Block trades on markets with insufficient 24h notional volume.

    HIP-3 tokenized-equity / commodity perps live on separate dexs and
    naturally carry less volume than BTC/ETH-style native markets (most
    `xyz:*` markets sit in the $1M–$50M range vs $1B+ for BTC). Applying
    the same 5M crypto floor incorrectly blocks adequately-liquid HIP-3
    markets like xyz:CRCL ($4.7M) and km:USTECH ($1.06M). When the coin
    is HIP-3 (colon-namespaced) and a separate `min_volume_hip3` is set,
    use that floor instead.

    High-confidence overrides: when `gate_config.bypass_low_volume=True`
    and confidence >= `gate_config.bypass_low_volume_min_conf`, allow the
    trade despite thin volume.
    """
    is_hip3 = ":" in (ctx.coin or "")
    floor = (min_volume_hip3 if (is_hip3 and min_volume_hip3 is not None) else min_volume)
    if ctx.market_volume_24h_usd >= floor:
        return {"pass": True}

    # High-confidence bypass
    if gate_config:
        if gate_config.get("bypass_low_volume"):
            min_conf = gate_config.get("bypass_low_volume_min_conf", 0.85)
            if ctx.confidence >= min_conf:
                return {
                    "pass": True,
                    "via": "bypass_low_volume",
                    "reason": (
                        f"low-liquidity bypass on {ctx.coin} (conf {ctx.confidence:.2f} >= {min_conf:.2f})"
                    ),
                }

    return {"pass": False, "reason": f"market 24h volume ${ctx.market_volume_24h_usd/1e6:.2f}M below floor ${floor/1e6:.2f}M"}


def short_liquidity_floor(ctx: GateContext, min_short_volume: float) -> GateResult:
    """SHORTS need materially more liquidity than longs — thin markets squeeze.

    Data (72h short segmentation): short BLEEDERS had a median 24h volume of
    ~$13M (XPL 0%/5 win, xyz:LITE -6.7%/10, PUMP, xyz:EWZ) while short WINNERS
    (XMR/TON/DOGE/BTC/ETH + commodities) had ~$223M — a 17x gap. Low-liquidity
    shorts ran to max_loss (the entire short bleed was 14 stopped shorts). Longs
    can tolerate a thin pump; a thin short gets squeezed. Applies ONLY to shorts;
    0/None disables (opt-in, reversible)."""
    if ctx.trade_side != "short" or not min_short_volume:
        return {"pass": True}
    if ctx.market_volume_24h_usd >= min_short_volume:
        return {"pass": True}
    return {"pass": False,
            "reason": (f"short on thin market: 24h vol ${ctx.market_volume_24h_usd/1e6:.1f}M "
                       f"< short floor ${min_short_volume/1e6:.0f}M (squeeze risk)")}


def history_floor_reason(coin: str, min_history_bars: int, fetch_daily) -> str:
    """Preflight gate: block coins younger than `min_history_bars` completed DAILY bars.

    Separate from the volume floors because a brand-new but high-volume listing sails
    through `min_market_volume_usd`/`min_hip3_volume_usd` with only a handful of daily
    bars. The perception scan's <50 check is on 5m bars (~4h), so it misses a coin that
    has barely EXISTED. Trading a coin with no track record is manipulation-prone and
    impossible to assess — gate on history AGE. (Upstream cd6eaeec677f.)

    `fetch_daily(coin, n)` returns daily candles (or None/[]). Returns a reason string
    to block, or "" to pass. Fail-OPEN on a transient fetch failure or empty read (don't
    punish a 429); only block on a short-but-NON-EMPTY history = a clear young coin.
    Disabled when min_history_bars <= 0. Pre-research preflight (needs a fetch), not a
    GateContext execute-stage gate.
    """
    try:
        min_hist = int(min_history_bars or 0)
    except (TypeError, ValueError):
        min_hist = 0
    if min_hist <= 0:
        return ""
    try:
        daily = fetch_daily(coin, min_hist + 5)
    except Exception:
        return ""  # transient fetch failure -> don't block
    if daily is not None and 0 < len(daily) < min_hist:
        return f"history_floor_preflight ({len(daily)}d < {min_hist}d history)"
    return ""


def reentry_cap_reason(coin: str, recent_entry_count: int, cap: int) -> str:
    """Block the (cap+1)-th entry on the same coin within the rolling window.

    The PnL audit found the book is fee-dominated by over-churned longs (BTC re-entered
    44x, ZEC/SOL 43x over ~8wk; $115 of the $165 fees). A per-coin re-entry cap recovers
    those fees — validated cost-swept: N=3 / rolling-24h ≈ +$24/56d with near-zero
    continuation risk (it only cuts the 3rd+ re-entry/coin/day, leaving legit rides intact).
    Risk-REDUCING. `recent_entry_count` = this coin's executed entries in the window
    (caller supplies it, e.g. memory.count_entries_since). cap <= 0 disables.
    """
    try:
        cap = int(cap or 0)
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0:
        return ""
    n = int(recent_entry_count or 0)
    if n >= cap:
        return f"reentry_cap ({n} entries in window >= cap {cap})"
    return ""


def coin_allowlist_gate(ctx: GateContext, allowlist: List[str], blocklist: List[str]) -> GateResult:
    if blocklist and ctx.coin in blocklist:
        return {"pass": False, "reason": f"{ctx.coin} is on the coin blocklist"}
    if allowlist and ctx.coin not in allowlist:
        return {"pass": False, "reason": f"{ctx.coin} not on the allowlist"}
    return {"pass": True}


def cooldown_gate(ctx: GateContext, last_trade_time: Optional[int], cooldown_min: float) -> GateResult:
    if last_trade_time is None:
        return {"pass": True}
    elapsed = (int(time.time() * 1000) - last_trade_time) / 60_000
    if elapsed >= cooldown_min:
        return {"pass": True}
    return {"pass": False, "reason": f"cooldown active ({int(cooldown_min - elapsed)}min remaining)"}


def opposite_direction_guard(ctx: GateContext) -> GateResult:
    """Block ANY re-entry on a coin we already hold. A held position is managed
    solely by the DSL engine + the periodic AI close-check (CLOSE / HOLD); it is
    never flipped (opposite side = no auto-flip) NOR added to (same side =
    uncontrolled pyramid). The held-coin close-check sometimes returns a fresh
    LONG/SHORT on a strong held name; without this it would try to pyramid in
    (previously only the exchange margin check stopped it)."""
    existing = next((p for p in ctx.current_positions if p["coin"] == ctx.coin), None)
    if not existing:
        return {"pass": True}
    if existing["side"] != ctx.trade_side:
        return {"pass": False, "reason": f"opposite position exists ({ctx.coin} {existing['side']}) — no auto-flip"}
    return {"pass": False, "reason": f"already holding {ctx.coin} {existing['side']} — no pyramid/re-entry"}


# Major crypto coins for correlation cap
_CRYPTO_COINS = frozenset([
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "MATIC", "LINK",
    "DOT", "UNI", "ATOM", "NEAR", "FTM", "APT", "ARB", "OP", "INJ", "TIA",
    "SUI", "SEI", "WIF", "PEPE", "BONK", "FLOKI", "TRX", "LTC", "BCH", "ETC",
    "XLM", "ALGO", "AAVE", "MKR", "SNX", "CRV", "COMP", "YFI", "SUSHI", "1INCH",
])


def correlation_cap(ctx: GateContext, max_crypto_correlated: int) -> GateResult:
    """Cap how many MAJOR-crypto longs we hold concurrently (majors are one
    macro trade). Scoped 2026-09-04: the cap constrains adding ANOTHER major —
    a non-major candidate (ZEC, LIT, CASHCAT, ...) is not correlated with the
    majors basket in _CRYPTO_COINS and must not be blocked by it. Previously a
    full majors book rejected every new long regardless of the candidate's own
    membership, which cost hours of trend entry on uncorrelated names (replay:
    12 solo-cap blocks in one evening session, nearly all non-majors)."""
    # Only cap long correlation
    if ctx.trade_side != "long":
        return {"pass": True}
    existing_crypto_long = sum(
        1 for p in ctx.current_positions
        if p["coin"] in _CRYPTO_COINS and p["side"] == "long"
    )
    if existing_crypto_long < max_crypto_correlated:
        return {"pass": True}
    # Cap is reached; it only binds candidates that are themselves majors.
    if ctx.coin not in _CRYPTO_COINS:
        return {"pass": True}
    return {"pass": False, "reason": f"crypto long correlation cap reached ({existing_crypto_long}/{max_crypto_correlated})"}


def equity_risk_cap(ctx: GateContext, max_total_notional_pct: float) -> GateResult:
    max_notional = ctx.equity * max_total_notional_pct
    projected_notional = ctx.total_open_notional + ctx.trade_notional_usd
    if projected_notional <= max_notional:
        return {"pass": True}
    return {
        "pass": False,
        "reason": f"total notional ${projected_notional:.0f} would exceed {max_total_notional_pct*100:.0f}% of equity (${max_notional:.0f})",
    }


def market_regime_gate(ctx: GateContext, counter_regime_min_conf: float = 0.7,
                       block_counter_trend_bypass: bool = False,
                       crowded_with_min_conf: float = 0.0,
                       no_composite_escape: bool = False) -> GateResult:
    """Block counter-regime trades unless conviction OR own-coin signal clears the bar.

      - aligned with regime → pass
      - regime neutral      → pass (subject to funding-regime override below)
      - counter-trend trade → pass if any of:
          * confidence >= counter_regime_min_conf
          * composite_score >= 50
          * momentumBurst fired (large fast move on 5m)
          * slow_burn_fired (1h vol surge or EMA cross — accumulation breakout)
        else block.

    The own-signal bypasses exist because the regime proxy (BTC for crypto,
    SP500 for equity) is slow; a strong individual signal should override
    a stale macro call.

    Funding-regime overlay (added 2026): SYMMETRIC enforcement — when the
    market-wide funding regime is crowded, any trade going AGAINST the crowd
    direction must clear a higher bar. This is direction-agnostic and will
    apply the same way when the regime flips:

      * SHORT_CROWDED + long  → counter-regime, elevated bar
      * LONG_CROWDED  + short → counter-regime, elevated bar
      * SHORT_CROWDED + short → aligned, normal bar (no bias added)
      * LONG_CROWDED  + long  → aligned, normal bar (no bias added)

    Elevated bar = confidence >= max(counter_regime_min_conf, 0.85)
                   OR composite_score >= 60
                   OR any binary trigger (momentumBurst / slow_burn / whale_signal)

    The bypass triggers are preserved on both sides — those are explicit
    "the regime proxy is stale" signals and we never want to hard-block on
    a clear individual setup, just enforce regime discipline by default.

    `no_composite_escape` (config `counter_regime_no_composite_escape`, C.6
    hard bar, live ON since 2026-09-21): for genuinely counter-TREND trades the
    composite-score escape is CLOSED — only confidence clears the gate. The
    cohort it was built from (n=14, net −$54.83) entered through the score hatch
    on fresh-breakout hype scans; every block logs a loud
    `[gate][COUNTER-TREND-BLOCK]` WARNING for the forward counterfactual review.
    """
    from hermes_trader.agents.market_regime import detect_regime
    regime = detect_regime(ctx.coin)
    # Regime-divergence shadow annotation (2026-09-27, WATCHLIST §C.16):
    # btc / alt-basket / own-coin trend reads attached to EVERY market_regime
    # gate result — pass and block alike — so they ride the `Trade result:`
    # line for accrual. Pure annotation: computed after the verdict inputs,
    # never read by any branch below, and failures collapse to gap-nulls.
    from hermes_trader.agents.market_regime import regime_divergence
    divergence = regime_divergence(ctx.coin)

    # Pull funding regime (cached) — used as a symmetric overlay on the
    # trend-regime gate. Both directions are treated identically: anything
    # going against the crowded side faces the elevated bar.
    #
    # PER-CLASS LOOKUP: the gate uses the funding regime of THIS coin's
    # asset class (crypto / equity / commodity), not a global crypto-only
    # signal. Without this, a SHORT_CROWDED crypto regime would gate longs
    # on oil (xyz:CL) and semis (xyz:ARM) — those have their own funding
    # markets and shouldn't be evaluated by the crypto crowd.
    try:
        from hermes_trader.agents.hyperfeed import market_get_funding_regime
        from hermes_trader.agents.market_regime import classify_asset
        funding_data = market_get_funding_regime()
        coin_class = classify_asset(ctx.coin)
        by_class = funding_data.get("regimes_by_class") or {}
        funding_regime = by_class.get(coin_class) or funding_data.get("regime", "NEUTRAL")
    except Exception:
        funding_regime = "NEUTRAL"

    # Symmetric counter-funding-regime detection.
    against_funding = (
        (funding_regime == "SHORT_CROWDED" and ctx.trade_side == "long") or
        (funding_regime == "LONG_CROWDED"  and ctx.trade_side == "short")
    )
    # WITH-crowd (squeeze-prone): trading the SAME side the crowd is already on
    # (short into SHORT_CROWDED / long into LONG_CROWDED). These are trend-aligned
    # but are exactly what gets squeezed on a reversal — they round-tripped the
    # 2026-06-06 day. Require elevated conviction so only strong setups join a
    # crowded book. Gated by crowded_with_min_conf (0 = off).
    with_crowd = (
        (funding_regime == "SHORT_CROWDED" and ctx.trade_side == "short") or
        (funding_regime == "LONG_CROWDED"  and ctx.trade_side == "long")
    )

    # Effective thresholds: only elevated when against the funding regime.
    # When aligned with funding regime, use the normal counter_regime_min_conf
    # so we never *raise* the bar for regime-aligned trades.
    effective_min_conf = counter_regime_min_conf
    effective_min_score = 50.0
    if against_funding:
        effective_min_conf = max(counter_regime_min_conf, 0.85)
        effective_min_score = 60.0

    # Context attached to every result so the log reads "why" without
    # re-deriving regime state after the fact. The divergence dict spreads
    # into every return path (§C.16 shadow annotation — display-only).
    base = {"regime": regime, "funding": funding_regime,
            "against_funding": against_funding, "counter_trend": False,
            "divergence": divergence}

    # Aligned with trend regime AND not against funding regime → easy pass,
    # UNLESS it's a with-crowd (squeeze-prone) entry that fails the elevated
    # conviction bar — those are the crowded shorts/longs that round-trip on a
    # squeeze, so a weak one is blocked here.
    aligned = (regime == "up" and ctx.trade_side == "long") or \
              (regime == "down" and ctx.trade_side == "short")
    if aligned and not against_funding:
        if with_crowd and crowded_with_min_conf > 0 and ctx.confidence < crowded_with_min_conf:
            return {"pass": False, "via": "crowded_squeeze",
                    **{**base, "with_crowd": True},
                    "reason": (f"with-crowd {ctx.trade_side} into {funding_regime} "
                               f"(squeeze risk) — need conf >= {crowded_with_min_conf:.2f}, "
                               f"have {ctx.confidence:.2f}")}
        return {"pass": True, "via": "aligned", **{**base, "with_crowd": with_crowd}}

    # Trend-regime neutral and not against funding regime → pass.
    if regime == "neutral" and not against_funding:
        return {"pass": True, "via": "neutral", **base}

    # Past here the trade is counter-trend and/or against the funding crowd —
    # it must clear the (possibly elevated) bar via conviction or own-signal.
    base["counter_trend"] = not aligned
    if ctx.confidence >= effective_min_conf:
        return {"pass": True, "via": "confidence", **base}
    # C.6 hard bar (config `counter_regime_no_composite_escape`, default False):
    # for trades genuinely fighting the TREND regime (up/down and not aligned)
    # the composite-score escape is closed — conviction (conf vs
    # effective_min_conf) or nothing. Rationale (.hermes/WATCHLIST.md §C.6 cohort
    # 2026-09-21, n=14 net −$54.83): the fresh breakout/burst trio auto-clears
    # composite ≥ 50, so hype scans walked the door the regime gate was built to
    # guard (MET 09-07 comp 89.5 → −$19.53). Scoped to trend disagreement only —
    # a neutral-regime against-FUNDING trade keeps its score hatch (the cohort
    # is all regime up/down counter-trend). Aligned/neutral trades returned
    # earlier and are UNAFFECTED; the flag never touches the confidence path.
    # Loud WARNING below so the blocked cohort is greppable for the forward
    # counterfactual check.
    trend_counter = base["counter_trend"] and regime in ("up", "down")
    hard_bar = no_composite_escape and trend_counter
    if not hard_bar:
        if ctx.composite_score >= effective_min_score:
            return {"pass": True, "via": "composite", **base}
    # Binary-trigger bypass: a strong own-coin signal (momentum_burst / slow_burn
    # / whale) normally overrides the slow macro-regime call. `block_counter_trend_bypass`
    # (config, default False, reversible) DISABLES this bypass here — i.e. for trades
    # that are already counter-trend and/or against the funding crowd. Data (journal
    # P166-P177, ~-7% drawdown) showed low-conviction LONGS forced through via
    # `trigger:slow_burn` against a DOWN tape (SP500/MU/ORCL longs) and bleeding. With
    # the flag on, a counter-regime trade must clear REAL conviction (conf/score); a
    # lone momentum trigger no longer pushes it through against the regime. Aligned and
    # neutral-regime trades returned earlier (lines above) and are UNAFFECTED, so this
    # does NOT blanket-weaken the bypass — only where it fights a strong directional regime.
    if (ctx.momentum_burst_fired or ctx.slow_burn_fired or ctx.whale_signal_fired) \
            and not block_counter_trend_bypass:
        trig = ("momentum_burst" if ctx.momentum_burst_fired
                else "slow_burn" if ctx.slow_burn_fired else "whale")
        return {"pass": True, "via": f"trigger:{trig}", **base}

    blocked_via = "blocked_bypass" if block_counter_trend_bypass else "blocked"
    escapes = f"conf >= {effective_min_conf:.2f}" + (
        "" if hard_bar else f" or score >= {effective_min_score:.0f}")
    result = {
        "pass": False,
        "via": blocked_via,
        **base,
        "reason": (f"counter-regime {ctx.trade_side} vs {regime} trend "
                   f"(funding={funding_regime}) — need {escapes}"
                   f"{'' if (block_counter_trend_bypass or hard_bar) else ' or own-coin signal'}, "
                   f"have conf {ctx.confidence:.2f}, score {ctx.composite_score:.0f}"),
    }
    # LOUD, greppable line for the C.6 forward counterfactual: every trade this
    # gate blocks is one that WOULD have traded pre-hard-bar. The review recipe
    # (.hermes/WATCHLIST.md §C.6) prices these from the log — keep the tag and
    # field order stable: [gate][COUNTER-TREND-BLOCK] COIN side conf X score Y
    # regime R funding F bar B composite_escape ON|OFF.
    logger.warning(
        f"[gate][COUNTER-TREND-BLOCK] {ctx.coin} {ctx.trade_side} "
        f"conf {ctx.confidence:.2f} score {ctx.composite_score:.1f} "
        f"regime {regime} funding {funding_regime} bar {effective_min_conf:.2f} "
        f"composite_escape {'OFF' if hard_bar else 'ON'}")
    return result


def news_blackout_gate(ctx: GateContext) -> GateResult:
    if not ctx.has_binary_news_risk:
        return {"pass": True}
    detail = f" — {ctx.binary_news_match}" if ctx.binary_news_match else ""
    return {"pass": False,
            "reason": f"binary news risk (Fed/earnings/hack in recent news){detail} — standing down"}


def _cfg(config: Dict[str, Any], key: str, default: Any) -> Any:
    """Read a config value tolerating snake_case or camelCase keys."""
    if key in config:
        return config[key]
    parts = key.split("_")
    camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
    return config[camel] if camel in config else default


def chronos_mismatch_gate(ctx: GateContext, gate_cfg: Dict[str, Any]) -> GateResult:
    """Chronos direction-mismatch conviction gate (SHADOW by default).

    When the entry side contradicts the cached Chronos-2 forecast (long with
    median_pct < 0, short with median_pct > 0, beyond `min_abs_median_pct`),
    the trade must carry elevated conviction: conf >= `min_conf` (0.90 — the
    same bar as the late-trend-chase bypass) OR composite >= `min_composite`
    (60). A counter-forecast scalp at 0.82 confidence is exactly the PURR
    failure mode this gate is meant to surface.

    SHADOW MODE: with `shadow_mode` true (default) the gate STRUCTURALLY
    returns pass=True and only carries a `shadow_would_block` marker, which
    the executor logs loudly. It cannot alter live execution until the
    operator flips shadow_mode to false in .agent-config.json — one knob.

    Fail-safe: no forecast (cold cache, model not loaded, in_prompt off) or a
    directionally-neutral forecast (within the deadband) = no opinion = pass.

    The ratio-aware deadband counterfactual that once accrued here was REMOVED
    2026-09-18 (WATCHLIST §C.1 die ruling: 84 accruals joined at 53.2% win,
    net −$74.76 — the fixed deadband was doing real work). The live rule is
    unchanged: fixed `min_abs_median_pct` only.
    """
    cfg = gate_cfg or {}
    if not bool(cfg.get("enabled", True)):
        return {"pass": True}
    med = ctx.chronos_median_pct
    if med is None:
        return {"pass": True}
    deadband = float(cfg.get("min_abs_median_pct", 0.5) or 0.5)
    if abs(med) < deadband:
        return {"pass": True}
    mismatch = ((ctx.trade_side == "long" and med < 0)
                or (ctx.trade_side == "short" and med > 0))
    if not mismatch:
        return {"pass": True}
    min_conf = float(cfg.get("min_conf", 0.90) or 0.90)
    min_composite = float(cfg.get("min_composite", 60.0) or 60.0)
    if ctx.confidence >= min_conf or ctx.composite_score >= min_composite:
        return {"pass": True}
    reason = (f"chronos_mismatch ({ctx.trade_side} vs forecast {med:+.2f}%; "
              f"conf {ctx.confidence:.2f} < {min_conf:.2f}, "
              f"composite {ctx.composite_score:.1f} < {min_composite:.0f})")
    result: GateResult = {"reason": reason}
    if bool(cfg.get("shadow_mode", True)):
        result["pass"] = True
        result["shadow_would_block"] = True
    else:
        result["pass"] = False
    return result


def chronos_tail_trigger_gate(ctx: GateContext, gate_cfg: Dict[str, Any]) -> GateResult:
    """Chronos tail-trigger conviction gate (SHADOW by default).

    Shape-based counter-forecast veto, validated by the 2026-08-28 60-flag
    replay: when the ADVERSE quantile of the cached Chronos-2 forecast path
    (q10 for longs, q90 for shorts) breaches `-min_adv_path_pct` at ANY of
    the first `window_steps` steps (5m candles each — 6 = 30m ahead), the
    entry is vetoed unless it clears the same elevated-conviction bar as
    `chronos_mismatch` (conf >= min_conf OR composite >= min_composite).

    Why the tail instead of the path mean (the reduction the mismatch gate
    uses): the replay showed the mean/endpoint scalars save ≈$0.96 across 14
    executed flags while the K=6/X=3.0 tail trigger saved ≈$3.21 — it blocks
    the same 4 stop-outs (MOVE, TRUMP, FARTCOIN x2, all within ~25m of entry)
    but RELEASES the small mean-reverting winners the path-mean gate kills.
    The q10 path also overstates realized magnitude ~2x, so the threshold is
    a boolean trip-wire near half the 5% spot stop — not a magnitude
    forecast — and deliberately sits well inside the stop.

    SHADOW MODE: with `shadow_mode` true (default, and how the operator armed
    it 2026-08-28) the gate STRUCTURALLY returns pass=True and only carries a
    `shadow_would_block` marker, which the executor logs loudly next to the
    existing chronos_mismatch shadow line. It cannot alter live execution
    until the operator flips shadow_mode to false in .agent-config.json.

    Fail-safes (no-opinion pass): disabled; no warm cached signal (cold cache
    / model down / disabled); paths missing (pre-change cache entries); path
    shorter than window_steps; tail not breached.
    """
    cfg = gate_cfg or {}
    if not bool(cfg.get("enabled", False)):
        return {"pass": True}
    k = int(cfg.get("window_steps", 6) or 6)
    x = float(cfg.get("min_adv_path_pct", 3.0) or 3.0)
    if k <= 0 or x <= 0:
        return {"pass": True}
    path = (ctx.chronos_q10_path_pct if ctx.trade_side == "long"
            else ctx.chronos_q90_path_pct)
    if not path or len(path) < k:
        return {"pass": True}
    window = path[:k]
    tail = min(window) if ctx.trade_side == "long" else max(window)
    breached = tail <= -x if ctx.trade_side == "long" else tail >= x
    if not breached:
        return {"pass": True}
    min_conf = float(cfg.get("min_conf", 0.90) or 0.90)
    min_composite = float(cfg.get("min_composite", 60.0) or 60.0)
    comp_escape_open = _tail_composite_escape_allowed(ctx, cfg)
    if ctx.confidence >= min_conf or (
            comp_escape_open and ctx.composite_score >= min_composite):
        return {"pass": True}
    reason = (f"chronos_tail_trigger ({ctx.trade_side} entry, adverse q-path "
              f"{'min' if ctx.trade_side == 'long' else 'max'} of first {k} steps "
              f"= {tail:+.2f}% beyond {x:.1f}%; conf {ctx.confidence:.2f} < "
              f"{min_conf:.2f}, composite {ctx.composite_score:.1f} < "
              f"{min_composite:.0f})")
    if not comp_escape_open:
        reason += "; composite escape closed on fresh-impulse class"
    if bool(cfg.get("shadow_mode", True)):
        return {"pass": True, "reason": reason, "shadow_would_block": True}
    return {"pass": False, "reason": reason}


def tirex_tail_trigger_gate(ctx: GateContext, gate_cfg: Dict[str, Any]) -> GateResult:
    """TiRex tail-trigger conviction gate (shadow-capable; same shape as
    ``chronos_tail_trigger_gate``).

    Why a tirex mirror: the 2026-09-16 MOE evaluation (n=2,688 prod-regime
    anchors, scratch/eval/moirai_eval/) found TiRex's adverse quantile path to
    be the best stop-out judge of all four forecasters on BOTH sides — long
    tail-AUC 0.898 @3% vs chronos 0.863 / timesfm 0.840 / moirai2 0.860, short
    0.821 vs 0.797 / 0.775 / 0.721 — with the tirex−chronos gap significant
    under paired bootstrap (ΔAUC +0.030 @2%, +0.034 @3%, p<0.001).

    But at the gate's OPERATING POINT (trip threshold X=2.5) the two are
    exactly tied on capture (both 40.7% of @2.5% stop-outs; McNemar-discordant
    pairs 8 vs 8 — each catches what the other misses). AUC ranks distributions;
    a gate trips once. So this gate ships as an ACCRUAL MIRROR first: same
    rule, same escape bar, its own would-block marker, so live logs can answer
    "either/or or both" with executed-cohort P/L instead of offline ranking.

    Rule (identical semantics to chronos_tail_trigger_gate): when the ADVERSE
    quantile of the cached tirex path (q10 for longs, q90 for shorts) breaches
    ``-min_adv_path_pct`` at ANY of the first ``window_steps`` steps, veto
    unless the entry clears the elevated-conviction bar (conf >= min_conf OR
    composite >= min_composite).

    SHADOW MODE: with ``shadow_mode`` true (default) the gate structurally
    returns pass=True and carries a ``shadow_would_block`` marker + join vars;
    it cannot alter execution until the operator flips shadow_mode in
    .agent-config.json.

    Fail-safes (no-opinion pass): disabled; paths missing/short (cold cache,
    model down, error signal); tail not breached. A data gap can never block.
    """
    cfg = gate_cfg or {}
    if not bool(cfg.get("enabled", False)):
        return {"pass": True}
    k = int(cfg.get("window_steps", 6) or 6)
    x = float(cfg.get("min_adv_path_pct", 2.5) or 2.5)
    if k <= 0 or x <= 0:
        return {"pass": True}
    path = (ctx.tirex_q10_path_pct if ctx.trade_side == "long"
            else ctx.tirex_q90_path_pct)
    if not path or len(path) < k:
        return {"pass": True}
    window = path[:k]
    tail = min(window) if ctx.trade_side == "long" else max(window)
    breached = tail <= -x if ctx.trade_side == "long" else tail >= x
    if not breached:
        return {"pass": True}
    min_conf = float(cfg.get("min_conf", 0.90) or 0.90)
    min_composite = float(cfg.get("min_composite", 60.0) or 60.0)
    comp_escape_open = _tail_composite_escape_allowed(ctx, cfg)
    if ctx.confidence >= min_conf or (
            comp_escape_open and ctx.composite_score >= min_composite):
        return {"pass": True}
    reason = (f"tirex_tail_trigger ({ctx.trade_side} entry, adverse q-path "
              f"{'min' if ctx.trade_side == 'long' else 'max'} of first {k} steps "
              f"= {tail:+.2f}% beyond {x:.1f}%; conf {ctx.confidence:.2f} < "
              f"{min_conf:.2f}, composite {ctx.composite_score:.1f} < "
              f"{min_composite:.0f})")
    if not comp_escape_open:
        reason += "; composite escape closed on fresh-impulse class"
    if bool(cfg.get("shadow_mode", True)):
        return {"pass": True, "reason": reason, "shadow_would_block": True,
                "tail_pct": tail, "window_steps": k}
    return {"pass": False, "reason": reason}


def tail_spread_comparator(ctx: GateContext, gate_cfg: Dict[str, Any]) -> GateResult:
    """Log-only comparator: path-tail veto vs band-width veto (plan D4, 2026-09-15).

    The armed chronos_tail_trigger_gate keys off the adverse QUANTILE PATH
    (min q10 / max q90 over the first `window_steps` steps). The 2026-09 model
    sweep (scratch/e2c_tail_vs_mean.py, 156k forecast-outcome pairs) found its
    stop-out AUC is nearly matched by the forecast BAND WIDTH (spread_pct) — a
    scalar requiring no path. This comparator records what each rule WOULD have
    done per evaluated entry so accrual can decide whether the path-based veto
    earns its complexity over a width-only veto.

    CONTRACT: never blocks. Always returns pass=True; on enabled, carries a
    `cmp` dict (tail_pct, spread_pct, tail_trip, spread_trip) for the executor
    to log. Fail-safe: any missing input -> trips False; disabled (default) ->
    plain {"pass": True}. Trip thresholds mirror the live tail gate by default
    (window_steps/min_adv_path_pct); spread threshold from spread_pct_threshold.
    """
    cfg = gate_cfg or {}
    if not bool(cfg.get("enabled", False)):
        return {"pass": True}
    k = int(cfg.get("window_steps", 6) or 6)
    x = float(cfg.get("min_adv_path_pct", 2.5) or 2.5)
    s_thr = float(cfg.get("spread_pct_threshold", 4.0) or 4.0)
    tail_pct = None
    tail_trip = False
    spread_trip = False
    try:
        path = (ctx.chronos_q10_path_pct if ctx.trade_side == "long"
                else ctx.chronos_q90_path_pct)
        if path and len(path) >= k > 0 and x > 0:
            window = path[:k]
            tail_pct = min(window) if ctx.trade_side == "long" else max(window)
            tail_trip = tail_pct <= -x if ctx.trade_side == "long" else tail_pct >= x
        spread = getattr(ctx, "chronos_spread_pct", None)
        if spread is not None and s_thr > 0:
            spread_trip = float(spread) >= s_thr
    except Exception:
        # A comparator bug must never touch execution.
        return {"pass": True}
    return {"pass": True, "cmp": {
        "tail_pct": tail_pct,
        "spread_pct": getattr(ctx, "chronos_spread_pct", None),
        "tail_trip": tail_trip,
        "spread_trip": spread_trip,
    }}


def timesfm_mismatch_gate(ctx: GateContext, gate_cfg: Dict[str, Any]) -> GateResult:
    """TimesFM direction-mismatch conviction gate (SHADOW-ONLY by construction).

    The per-forecaster mirror of ``chronos_mismatch_gate`` on the TimesFM-3
    signal: when the entry side contradicts the cached TimesFM forecast
    (long with median_pct < 0, short with median_pct > 0, beyond
    ``min_abs_median_pct``), the entry is ACCRUED as a would-block unless it
    carries elevated conviction (conf >= ``min_conf`` (0.90) OR composite >=
    ``min_composite`` (60)) — the same escape bar as the chronos pair.

    SHADOW-ONLY BY CONSTRUCTION: unlike the chronos pair there is NO
    ``shadow_mode`` config key and NO code path that returns pass=False —
    the gate is a pure GateContext function (no log calls in-gate) that
    ALWAYS returns pass: True. When its blocking condition would hold it
    carries ``shadow_would_block`` plus the join variables, and the
    EXECUTOR logs the loud accrual line next to the chronos_mismatch
    shadow line. The only config knob is ``enabled`` (default True): it
    controls whether the gate runs/accrues, never whether it can block.
    The pass: False branch is simply never written — the shadow-only
    posture is structural, not config-dependent.

    Simpler than the chronos mirror on purpose: no ratio-aware deadband
    counterfactual (that is chronos-specific, and this leg's data comes
    from a different model family).

    Fail-safes (pass, no marker, no raise): disabled; median None (cold
    cache / model down / disabled / error signal) — a data gap can never
    flag.
    """
    cfg = gate_cfg or {}
    if not bool(cfg.get("enabled", True)):
        return {"pass": True}
    med = ctx.timesfm_median_pct
    if med is None:
        return {"pass": True}
    deadband = float(cfg.get("min_abs_median_pct", 0.5) or 0.5)
    if abs(med) < deadband:
        return {"pass": True}
    mismatch = ((ctx.trade_side == "long" and med < 0)
                or (ctx.trade_side == "short" and med > 0))
    if not mismatch:
        return {"pass": True}
    min_conf = float(cfg.get("min_conf", 0.90) or 0.90)
    min_composite = float(cfg.get("min_composite", 60.0) or 60.0)
    if ctx.confidence >= min_conf or ctx.composite_score >= min_composite:
        return {"pass": True}
    reason = (f"timesfm_mismatch ({ctx.trade_side} vs forecast {med:+.2f}%; "
              f"conf {ctx.confidence:.2f} < {min_conf:.2f}, "
              f"composite {ctx.composite_score:.1f} < {min_composite:.0f})")
    return {
        "pass": True,
        "reason": reason,
        "shadow_would_block": True,
        "median_pct": med,
        "confidence": ctx.confidence,
        "composite_score": ctx.composite_score,
    }


def timesfm_tail_trigger_gate(ctx: GateContext, gate_cfg: Dict[str, Any]) -> GateResult:
    """TimesFM tail-trigger conviction gate (shadow-capable; promoted from
    shadow-only accrual 2026-09-17 per WATCHLIST §C.9).

    The per-forecaster mirror of ``chronos_tail_trigger_gate`` on the
    TimesFM-3 signal: when the ADVERSE quantile of the cached TimesFM path
    (q10 for longs, q90 for shorts — read via the same getattr pattern as
    ``forecast_agreement_veto_gate``) breaches the threshold at any of the
    first ``window_steps`` steps, the entry is vetoed unless it clears the
    elevated-conviction escape bar (conf >= min_conf (0.90) OR composite >=
    min_composite (60)).

    SHADOW-CAPABLE: with ``shadow_mode`` true (DEFAULT — accrual posture)
    the gate structurally returns pass=True and carries a
    ``shadow_would_block`` marker the executor logs; with ``shadow_mode``
    false it returns pass=False on its shape, exactly like the armed chronos
    gate. Promotion evidence (C.9 replay + re-time counterfactual, 2026-09-17):
    19 executed would-blocks net −$48.98, both split-halves negative, no
    single trade >50% of |net|; ~75% of savings survive re-timing
    (scratch/_timesfm_tail_retime_cf.py).

    Code defaults follow the timesfm sub-key of forecast_agreement_veto
    (the sweep-selected timesfm window from the 2026-09-02 two-model
    sweep): ``window_steps`` 12 (NOT 6) and ``min_adv_path_pct`` 2.0
    (NOT 3.0). Breach: tail <= -x for longs, tail >= x for shorts.

    Fail-safes (pass, no marker, no raise): disabled; path missing or
    shorter than the window (cold cache / disabled / error / pre-change
    signal); tail not breached. A data gap can never flag or block.
    """
    cfg = gate_cfg or {}
    if not bool(cfg.get("enabled", True)):
        return {"pass": True}
    k = int(cfg.get("window_steps", 12) or 12)
    x = float(cfg.get("min_adv_path_pct", 2.0) or 2.0)
    if k <= 0 or x <= 0:
        return {"pass": True}
    path = (getattr(ctx, "timesfm_q10_path_pct", None) if ctx.trade_side == "long"
            else getattr(ctx, "timesfm_q90_path_pct", None))
    if not path or len(path) < k:
        return {"pass": True}
    window = path[:k]
    tail = min(window) if ctx.trade_side == "long" else max(window)
    breached = tail <= -x if ctx.trade_side == "long" else tail >= x
    if not breached:
        return {"pass": True}
    min_conf = float(cfg.get("min_conf", 0.90) or 0.90)
    min_composite = float(cfg.get("min_composite", 60.0) or 60.0)
    comp_escape_open = _tail_composite_escape_allowed(ctx, cfg)
    if ctx.confidence >= min_conf or (
            comp_escape_open and ctx.composite_score >= min_composite):
        return {"pass": True}
    reason = (f"timesfm_tail_trigger ({ctx.trade_side} entry, adverse q-path "
              f"{'min' if ctx.trade_side == 'long' else 'max'} of first {k} steps "
              f"= {tail:+.2f}% beyond {x:.1f}%; conf {ctx.confidence:.2f} < "
              f"{min_conf:.2f}, composite {ctx.composite_score:.1f} < "
              f"{min_composite:.0f})")
    if not comp_escape_open:
        reason += "; composite escape closed on fresh-impulse class"
    if bool(cfg.get("shadow_mode", True)):
        return {
            "pass": True,
            "reason": reason,
            "shadow_would_block": True,
            "tail_pct": tail,
            "window_steps": k,
        }
    return {"pass": False, "reason": reason}


def forecast_agreement_veto_gate(ctx: GateContext, gate_cfg: Dict[str, Any]) -> GateResult:
    """Chronos ∧ TimesFM agreement tail-veto (SHADOW by default).

    Fires only when BOTH zero-shot forecasters see an adverse tail: the
    chronos q-path AND the timesfm q-path must breach their own threshold
    within their own window before the entry needs the elevated-conviction
    escape (conf >= min_conf OR composite >= min_composite). The 2026-09-02
    2-model parameter sweep (253 decision events, DSL-simmed P/L) found the
    AND shape to be the most split-half-robust veto cell: saved ~$24 vs ~$23
    for chronos-alone at K6/X2.5, while vetoing FEWER entries (44 vs 47 of 96)
    — timesfm alone vetoed with sign-flipping halves, so it must not veto by
    itself; agreement is its contribution.

    Per-model thresholds live under `chronos` / `timesfm` sub-keys (same
    window_steps / min_adv_path_pct semantics as chronos_tail_trigger_gate;
    sweep-selected defaults 6/2.5 and 12/2.0). Fail-safe: either model's
    paths missing (cold cache / disabled / inference error) = no agreement =
    pass. A data gap can never block a trade.
    """
    cfg = gate_cfg or {}
    if not bool(cfg.get("enabled", False)):
        return {"pass": True}

    def _breached(paths, side, k, x):
        if not paths or len(paths) < k:
            return False
        window = paths[:k]
        tail = min(window) if side == "long" else max(window)
        return tail <= -x if side == "long" else tail >= x

    ccfg = cfg.get("chronos") or {}
    tcfg = cfg.get("timesfm") or {}
    ck = int(ccfg.get("window_steps", 6) or 6)
    cx = float(ccfg.get("min_adv_path_pct", 2.5) or 2.5)
    tk = int(tcfg.get("window_steps", 12) or 12)
    tx = float(tcfg.get("min_adv_path_pct", 2.0) or 2.0)
    cpath = (ctx.chronos_q10_path_pct if ctx.trade_side == "long"
             else ctx.chronos_q90_path_pct)
    tpath = (getattr(ctx, "timesfm_q10_path_pct", None) if ctx.trade_side == "long"
             else getattr(ctx, "timesfm_q90_path_pct", None))
    if not _breached(cpath, ctx.trade_side, ck, cx):
        return {"pass": True}
    if not _breached(tpath, ctx.trade_side, tk, tx):
        return {"pass": True}
    min_conf = float(cfg.get("min_conf", 0.90) or 0.90)
    min_composite = float(cfg.get("min_composite", 60.0) or 60.0)
    if ctx.confidence >= min_conf or ctx.composite_score >= min_composite:
        return {"pass": True}
    reason = (f"forecast_agreement_veto (both models adverse: chronos first {ck} "
              f"steps vs {cx}%, timesfm first {tk} steps vs {tx}%; conf "
              f"{ctx.confidence:.2f} < {min_conf:.2f}, composite "
              f"{ctx.composite_score:.1f} < {min_composite:.0f})")
    if bool(cfg.get("shadow_mode", True)):
        return {"pass": True, "reason": reason, "shadow_would_block": True}
    return {"pass": False, "reason": reason}


def _fresh_5m_extreme(
    coin: str, side: str, lookback: int, px: float
) -> Optional[bool]:
    """Does the current price sit AT a fresh 5m extreme? The pop guard.

    True  = the still-forming 5m bar's open (the live price at the scan
            tick) is beyond the high/low of the last `lookback` CLOSED 5m
            bars -> a short-term pop is in flight.
    False = no fresh extreme -> the price is EXTENDING an existing range,
            not starting a new one.
    None  = no opinion (fetch failed / empty / not enough history) — the
            caller must fail closed.

    Deliberately measured on CLOSED bars with the forming bar's OPEN as the
    candidate price: the band_state verdict uses the forming bar's CLOSE
    (= live mid), but a Donchian extreme computed on the forming bar itself
    would compare the decision price against a range that includes it —
    self-referential. Closed prior bars + forming open is the honest read
    of "did the price just break out, or is it grinding an established
    range?" (the 5m guard of the 2026-09-06 band-release validation).
    """
    from hermes_trader.client.hl_client import fetch_hl_candles
    from hermes_trader.indicators.math import candle_val

    try:
        cs = fetch_hl_candles(coin, interval="5m", count=lookback + 2)
    except Exception:
        return None
    if not cs:
        return None
    closed = cs[:-1]
    if len(closed) < lookback:
        return None
    prior = closed[-lookback:]
    try:
        if side == "long":
            return px >= max(candle_val(c, "h") for c in prior)
        return px <= min(candle_val(c, "l") for c in prior)
    except Exception:
        return None


def band_counter_breach_gate(ctx: GateContext, gate_cfg: Dict[str, Any]) -> GateResult:
    """Band counter-trend breach conviction gate (SHADOW by default).

    Deterministic encoding of the shape the research prompt tells the LLM is
    a reversion, not a continuation: the band (same interval/params as the
    band_snapback trigger config) is TRENDING — drift > max_drift_pct — and
    price is >= `min_breach_pct` beyond the OPPOSITE-side edge:

      band drifts DOWN, price above the upper edge  + LONG entry
          -> top of a relief rally = start of the next down-swing
      band drifts UP,   price below the lower edge  + SHORT entry
          -> bottom of a pullback = start of the next up-swing

    The bounce/dip entry fights the drift and the nearest short-term
    reversion is back toward the band — AGAINST the entry. The LLM has been
    rationalizing exactly this as "the drift is already priced in ->
    continuation" (GRASS long 2026-08-26 19:07: 1h band DOWN 6.4% drift,
    px +6.6% vs upper edge, entered long at 0.82 conf -> -9.6% ROE). When
    the breach shape matches the entry side, the trade must clear the
    elevated bar: confidence >= `min_conf` (0.90). A genuinely
    high-conviction continuation still gets through; the 0.82
    rationalization does not. There is deliberately NO composite-score
    bypass on this gate — the composite is a multi-trigger tally and the
    shape it would excuse here is precisely the one this gate encodes.

    SHADOW MODE: with `shadow_mode` true the gate STRUCTURALLY returns
    pass=True and only carries a `shadow_would_block` marker, which the
    executor logs loudly — same pattern as chronos_mismatch_gate. It cannot
    alter live execution until the operator flips shadow_mode to false in
    .agent-config.json. `enabled` false (code default) disables it
    entirely.

    Candles are fetched inside the gate on the band interval (same
    interval/width as the band_snapback trigger config plus the drift
    reference: band_span + drift_ref_span + a few extra bars), so the
    read-side TTL cache in hl_client makes this a hit when perception's band
    trigger already pulled the same series seconds earlier — no extra network
    cost in the common path, and the gate stays self-contained (no
    GateContext plumbing).

    DRIFT REFERENCE (`drift_ref_span`, this gate's config block): the band
    edges stay the trigger's `band_span` MA, but the drift/direction verdict
    samples the SAME edge `drift_ref_span` bars back — the lag knob the
    trigger rework collapsed onto band_span, re-opened here for the gate
    alone. A late-chase entry is late against the LONGER trend, and a 16-bar
    EMA hugs price so tightly its own-window drift is structurally ~4-5x
    smaller (the GRASS 2026-08-26 tick read 6.4% over 48 bars but only 1.1%
    over 16 — the gate slept). Live value 32; absent = band_span = pre-key
    behaviour byte-identical. The drift gate's max_drift_pct stays the same
    1.5% bar.

    Fail-safes: disabled / no band config / candle fetch failure / short
    history / band not trending / breach below `min_breach_pct` / entry on
    the WITH-drift side (chasing the drift, not the bounce) = no opinion = pass.
    """
    cfg = gate_cfg or {}
    if not bool(cfg.get("enabled", False)):
        return {"pass": True}
    from hermes_trader.agents.config_store import read_agent_config
    from hermes_trader.client.hl_client import fetch_hl_candles
    from hermes_trader.indicators.math import candle_val
    from hermes_trader.indicators.triggers import band_state

    try:
        agent_cfg = read_agent_config() or {}
    except Exception as e:
        logger.debug(f"[gate] band_counter_breach: agent-config read failed: {e}")
        return {"pass": True}
    bs = agent_cfg.get("band_snapback") or {}
    if not bool(bs.get("enabled")):
        return {"pass": True}
    ov = (bs.get("overrides") or {}).get(ctx.coin) or {}
    bs = {**bs, **{k: v for k, v in ov.items() if v is not None}}
    # P3 umbrella: band_snapback.shadow_mode drives this gate to would-block-only
    # even when the gate's own shadow_mode is left at its default (true) — the
    # single flag the operator flips to shadow the whole band-snapback feature.
    # The gate's own band_counter_breach_gate.shadow_mode stays as a secondary
    # (belt-and-braces): shadow = own OR umbrella.
    umbrella_shadow = bool(bs.get("shadow_mode", False))
    interval = str(bs.get("interval", "1h"))
    span = max(2, int(bs.get("band_span", 16)))
    # Drift-reference lag: absent -> band_span (the trigger's own-window
    # semantics); explicit value -> the longer late-chase reference.
    try:
        drift_ref = int(cfg.get("drift_ref_span", span) or span)
    except (TypeError, ValueError):
        drift_ref = span
    drift_ref = max(1, drift_ref)

    try:
        candles = fetch_hl_candles(ctx.coin, interval=interval,
                                   count=span + drift_ref + 4)
    except Exception as e:
        logger.debug(f"[gate] band_counter_breach: candle fetch failed for {ctx.coin}: {e}")
        return {"pass": True}
    if not candles:
        return {"pass": True}
    try:
        st = band_state(
            candles,
            band_span=span,
            max_drift_pct=float(bs.get("max_drift_pct", 1.5)),
            ma_type=str(bs.get("ma_type", "ema")),
            drift_ref=drift_ref,
        )
    except Exception as e:
        logger.debug(f"[gate] band_counter_breach: band_state failed for {ctx.coin}: {e}")
        return {"pass": True}
    if st is None or not st["trending"]:
        return {"pass": True}

    # Breach = price beyond the OPPOSITE-side edge of the drift: drift DOWN
    # + above the upper edge (bounce) / drift UP + below the lower edge (dip).
    # The entry side must be the bounce/dip side — the counter-trend one.
    min_breach = float(cfg.get("min_breach_pct", 1.0) or 1.0)
    if st["direction"] == "DOWN" and ctx.trade_side == "long":
        breach = st["breach_opposite_pct"]
        shape = f"bounce above the upper edge of a DOWN-drifting {interval} band (drift {st['drift_pct']:.1f}%, breach {breach:.1f}%)"
    elif st["direction"] == "UP" and ctx.trade_side == "short":
        breach = st["breach_opposite_pct"]
        shape = f"dip below the lower edge of an UP-drifting {interval} band (drift {st['drift_pct']:.1f}%, breach {breach:.1f}%)"
    else:
        return {"pass": True}  # WITH the drift (or no breach) — not this shape
    if breach < min_breach:
        return {"pass": True}

    # ── Drift-confirmed release (2026-09-06, 28-block cohort counterfactual) ──
    # A shape the gate got WRONG: a genuine trend leg where the band is
    # confirming the drift (drift >= `min_drift_pct`) AND the price is NOT
    # popping — it's extending an established range (no fresh 5m extreme).
    # The 28-block replay: the conf>=0.90 escape never fired (max conf 0.85),
    # the block re-timed every entry deeper into the rip (ARB 09-05: all 7
    # band blocks lost to re-timing; the same shape with drift>=2.5 + no
    # fresh 5m extreme = +$25.90 executable, 7/8W), while the fresh-5m-
    # extreme rows were the pops the gate exists to kill (LIT max-loss,
    # DOGE stale-flat — net -$10.15 if released). So: only the drift-
    # CONFIRMED, non-pop quadrant escapes. Config-gated; fail-closed on
    # missing 5m data (the release NEVER fires on a guard the gate cannot
    # compute — the pop side is the gate's whole reason to exist).
    rel = cfg.get("drift_confirmed_release") or {}
    # Shadow-mode contract: while shadow_mode is ON the gate MUST log the
    # would-block and structurally pass — the release is a LIVE-execution
    # escape and must not fire (and must not suppress the would-block log)
    # in shadow.
    if bool(rel.get("enabled", False)) and not (
            bool(cfg.get("shadow_mode", True)) or umbrella_shadow):
        try:
            min_drift = float(rel.get("min_drift_pct", 2.5))
        except (TypeError, ValueError):
            min_drift = 2.5
        if st["drift_pct"] >= min_drift:
            try:
                pop_lookback = int(rel.get("pop_lookback_5m", 48))
            except (TypeError, ValueError):
                pop_lookback = 48
            px = candle_val(candles[-1], "c") if candles else 0.0
            pop = _fresh_5m_extreme(ctx.coin, ctx.trade_side, pop_lookback, px)
            if pop is False:  # None (no opinion) / True (pop) -> keep blocking
                return {
                    "pass": True,
                    "via": "drift_confirmed",
                    "reason": (
                        f"[gate:band_counter_breach] {ctx.coin} {ctx.trade_side} "
                        f"RELEASING (drift-confirmed): {shape} but band drift "
                        f"{st['drift_pct']:.1f}% >= {min_drift:.1f}% AND no fresh "
                        f"5m extreme (pop_lookback {pop_lookback}) — a confirmed "
                        f"trend leg, not the counter-trend pop. conf "
                        f"{ctx.confidence:.2f}."
                    ),
                }

    min_conf = float(cfg.get("min_conf", 0.90) or 0.90)
    if ctx.confidence >= min_conf:
        return {"pass": True, "via": "confidence"}
    reason = (
        f"[gate:band_counter_breach] {ctx.coin} {ctx.trade_side} at conf "
        f"{ctx.confidence:.2f} < {min_conf:.2f}: {shape} — the counter-trend "
        f"chase at the top/bottom of a drift needs >= {min_conf:.2f} conviction"
    )
    if bool(cfg.get("shadow_mode", True)) or umbrella_shadow:
        logger.warning(
            f"[gate] band_counter_breach would-block {ctx.coin} {ctx.trade_side} "
            f"(conf {ctx.confidence:.2f} < {min_conf:.2f}): {shape} — shadow_mode "
            f"ON, logging only, not blocking. Set "
            f"band_counter_breach_gate.shadow_mode=false to enable."
        )
        return {"pass": True, "reason": reason, "shadow_would_block": True}
    return {"pass": False, "reason": reason}


def duelist_veto_gate(ctx: GateContext, gate_cfg: Dict[str, Any]) -> GateResult:
    """Duelist-veto conviction gate (SHADOW by default).

    The fifth member of the shadow-gate house pattern. When the primary
    issues a DIRECTIONAL call (LONG/SHORT) and the A/B duelist — a second
    model answering the same research prompt — EXPLICITLY rejects the setup
    (verdict VETO) or takes the OPPOSITE side, the entry is vetoed unless it
    clears the elevated-conviction bar (conf >= `min_conf` OR composite >=
    `min_composite`). History: the gate shipped keyed on the duelist's PASS
    as abstention, validated by the 2026-08-31 48h replay (removed 12 of 41
    entries, net +$1.83 vs the −$2.72 baseline). The 2026-09-03 re-key moved
    the trigger from PASS to explicit VETO — see RE-KEYED below; the replay
    numbers above belong to the old key and DO NOT transfer.

    The veto is deliberately STRICT by default (no conviction-escape): the
    48h replay's +$1.83 result was computed with the strict rule, and an
    escape bar (conf >= 0.90) would have changed the count because several
    vetoed entries ran at 0.82. `min_conf` / `min_composite` therefore
    implement the "deliberately conservative" escape — set them high to let
    strong-conviction entries override a duelist objection (note: under the
    new VETO key, an explicit rejection is stronger evidence than under the
    old PASS key, so a tighter escape may be warranted once a VETO sample
    exists).

    SHADOW MODE: with `shadow_mode` true (default, and how it ships) the
    gate STRUCTURALLY returns pass=True and only carries a
    `shadow_would_block` marker, which the executor logs loudly next to the
    other shadow gates (`[gate][SHADOW] duelist_veto WOULD HAVE BLOCKED …`).
    The executed-trade result dict carries the full `gate_results`, so the
    marker lands in the session log's `execute` event and the
    `Trade result:` log line for free — joinable to the ledger (by
    analysis_id/coin/side) and to the duel JSONL (by ts/coin).

    Fail-safes (no-opinion pass): disabled; duelist verdict absent (the
    duelist is disabled or the second LLM call failed); the duelist AGREES
    (its verdict matches the primary's side) or ABSTAINS NEUTRALLY with PASS.
    A data gap can never block a trade.

    RE-KEYED 2026-09-03 (operator decision): the veto used to fire on the
    duelist's PASS — treating every abstention as an objection. With VETO now
    a first-class verdict ("this setup is a trap — avoid like the plague",
    see system prompt), PASS was redefined in the prompt as *neutral*
    abstention, and the gate fires on EXPLICIT VETO (or an opposite-side call)
    instead. Motivation: the 2026-09-03 headroom replay (32 trades since the
    Qwen2.5-Coder-7B anchor) found the PASS-keyed veto blocking 27/32 entries
    — a flow-reducer whose blocked cohort was net +$0.57, i.e. flipping it
    live would have LOST money, because bare PASS carried no quality
    information (the duelist PASSed ~84% of everything). VETO is the duelist
    actively saying NO; PASS just means "nothing excited me".
    """
    cfg = gate_cfg or {}
    if not bool(cfg.get("enabled", False)):
        return {"pass": True}
    dl = ctx.duelist_verdict
    if not dl:
        return {"pass": True}
    dl_upper = str(dl).upper()
    # The veto only fires on an EXPLICIT rejection of the primary's entry.
    # A PASS primary never reaches this gate (route_verdict routes PASS to
    # "none" unless a force-execute hint fires, in which case the force path
    # re-evaluates every gate — including this one — on the upgraded side).
    if dl_upper == "VETO":
        pass  # explicit active rejection of this setup — THE veto shape
    elif (ctx.trade_side == "long" and dl_upper == "SHORT") or \
         (ctx.trade_side == "short" and dl_upper == "LONG"):
        # The duelist took the OPPOSITE side — it doesn't just object to this
        # entry, it would trade against it. Same veto family (kept from the
        # pre-2026-09-03 rule: an explicit directional disagreement).
        pass
    else:
        # The duelist AGREES with the primary (same side), ABSTAINS neutrally
        # (PASS — no longer a veto: "not excited" != "avoid"), or the verdict
        # is unrecognised — no objection, pass.
        return {"pass": True}
    min_conf = float(cfg.get("min_conf", 0.90) or 0.90)
    min_composite = float(cfg.get("min_composite", 60.0) or 60.0)
    if ctx.confidence >= min_conf or ctx.composite_score >= min_composite:
        return {"pass": True}
    reason = (f"duelist_veto ({ctx.trade_side} entry, duelist said "
              f"{dl_upper}; conf {ctx.confidence:.2f} < {min_conf:.2f}, "
              f"composite {ctx.composite_score:.1f} < {min_composite:.0f})")
    if bool(cfg.get("shadow_mode", True)):
        return {"pass": True, "reason": reason, "shadow_would_block": True}
    return {"pass": False, "reason": reason}


def _quiet_tape_variant(
    name: str,
    label: str,
    vcfg: Dict[str, Any],
    fetch_tape,
) -> Optional[Dict[str, Any]]:
    """Evaluate ONE quiet_tape variant. Returns None when the variant is
    disabled or has no opinion (data gap / non-positive thresholds), else a
    dict with `would_block` (bool — the raw verdict, independent of mode),
    `live` (bool — shadow_mode false), and `reads` {vol, drift}.

    2026-09-11 change: a record is ALWAYS returned when the tape read is
    available — quiet OR loud (previously only on a fire) — so the gate's
    `variants` map carries the individual gate variables for every decision
    in the `Trade result:` JSON. When `would_block` is set, `reason` carries
    the `quiet_tape[<name>]` prefix (the per-variant join/audit key, with
    the live read values + thresholds). A data gap returns None (no opinion;
    a gap can never block a trade)."""
    if not bool(vcfg.get("enabled", False)):
        return None
    tape = fetch_tape()
    if tape is None:
        return None
    vol_pct = float(vcfg.get("vol_pct", 2.5) or 0.0)
    drift_pct = float(vcfg.get("drift_pct", 2.0) or 0.0)
    if vol_pct <= 0 or drift_pct <= 0:
        return None
    would_block = tape["vol"] < vol_pct and abs(tape["drift"]) < drift_pct
    out = {
        "would_block": would_block,
        "live": not bool(vcfg.get("shadow_mode", True)),
        "reads": {"vol": round(float(tape["vol"]), 4),
                  "drift": round(float(tape["drift"]), 4)},
    }
    if would_block:
        out["reason"] = (f"quiet_tape[{name}] (broad tape inactive: {label} trail24h vol "
                         f"{tape['vol']:.2f}% < {vol_pct:.2f}% AND |drift| "
                         f"{abs(tape['drift']):.2f}% < {drift_pct:.2f}%)")
    return out


def quiet_tape_gate(
    ctx: GateContext,
    gate_cfg: Dict[str, Any],
    coin_blocklist: Optional[List[str]] = None,
) -> GateResult:
    """Quiet broad-tape entry gate — multi-variant (2026-09-10/11).

    Each VARIANT blocks a NEW entry when its broad-tape read is quiet:
    trailing-24h realized vol < `vol_pct` AND |trailing-24h drift| <
    `drift_pct`. Two variants ship:

      `btc` — the BTC proxy (the original gate, 2026-09-10). Motivation
        (scratch/_regime_bleed.py + _coin_vs_btc.py): the 09-05/06/07 bleed
        (−$106.81 = 99% of the 30d loss) hit on the quietest broad tape of
        the month — BTC trail24h vol 0.76–1.37% vs a ~2.0% median — where a
        100%-long 5x book became a coin flip. Thresholds from
        scratch/_quiet_tape_sweep.py (n=516); the corrected-criterion replay
        (scratch/_quiet_tape_replay_merge.py, 2026-09-11) picked 1.5/2.0 as
        the best ELIGIBLE cell (Δ +74.62, blocked cohort −$74.62, all three
        sub-windows ≤ 0) over the max-Δ live cell 2.5/2.0 (Δ +130.50 but
        mid-window +$3.62 → not robust).

      `alt` — the alt-basket proxy (2026-09-11): an equal-weight index of
        the top-N main-dex alts by 24h volume (BTC excluded, vol-floored) —
        the tape the bot actually trades, which on a quiet-BTC day can be
        loud (2026-09-11: BTC vol 1.74% / drift −1.9% = quiet, while the
        basket read 4.2% / −6.6% = active). Thresholds 4.5/2.0 from
        scratch/_quiet_tape_alt_sweep_corrected.py (same 516 pairs / 3
        sub-windows as the BTC sweep, corrected sub-window criterion):
        vol<4.5 is the conservative vol row with a net-negative blocked
        cohort; 4.5/2.0 is the tightest drift cell on that row (n=50,
        blocked cohort −$52.20, avg win +$1.04 vs avg loss −$3.48).

    Config shapes (both accepted, hot-reloaded):
      flat (legacy, BTC-only):  {"enabled":…, "shadow_mode":…, "vol_pct":…, "drift_pct":…}
      multi-variant:           {"btc": {…}, "alt": {…}, "merge": "and"|"or"}
                                — per-variant enabled/shadow_mode/thresholds

    Merge semantics (`merge`, default "or"):
      `or`  — BLOCK iff any LIVE (shadow_mode false) variant reads quiet.
              The original (2026-09-10/11) merge.
      `and` — BLOCK iff ALL AVAILABLE reads are quiet (a data gap = that
              variant has no opinion: it can neither block nor release the
              other variant's quiet verdict — BTC quiet + ALT gap still
              blocks, so a broken alt feed cannot silently revert to
              BTC-alone protection). Owner call 2026-09-11: AND ends the
              tape-wide stalemate (any single quiet read used to veto the
              whole tape) at the cost of releasing the BTC-quiet/ALT-loud
              cohort; the released-cohort P/L accrues out-of-sample via the
              `[gate][ACC]` line before the merge choice is confirmed.
    Shadow variants never block under either merge — they carry
    `shadow_would_block` + `shadow_reasons` (one `quiet_tape[<name>]`
    reason per firing shadow variant), which the executor logs loudly.
    A data gap on ANY variant is a no-opinion pass for that variant; a gap
    can never block a trade.

    Accrual instrumentation (2026-09-11): the result ALWAYS carries
    `variants` — per enabled variant: `would_block` (raw verdict), `live`,
    `reads` {vol, drift} (null on data gap), plus `merge` and
    `released_by_merge` (AND merge passed while ≥1 LIVE variant would have
    blocked alone — the OR-vs-AND counterfactual join key, logged by the
    executor as `[gate][ACC]`). These ride in `gate_results` → every
    `Trade result:` line (blocked AND executed), so the ledger join gets
    both the verdict and the raw readings per decision.
    """
    cfg = gate_cfg or {}
    if "btc" in cfg or "alt" in cfg:
        variants = {"btc": cfg.get("btc") or {}, "alt": cfg.get("alt") or {}}
        merge = str(cfg.get("merge", "or") or "or").lower()
    else:
        # Legacy flat shape → the BTC variant only (OR merge is vacuous
        # with one variant).
        variants = {"btc": cfg, "alt": {}}
        merge = "or"
    if merge not in ("and", "or"):
        merge = "or"
    if not any(bool(v.get("enabled", False)) for v in variants.values()):
        return {"pass": True}
    from hermes_trader.agents.market_regime import (
        alt_basket_tape_activity,
        btc_tape_activity,
    )
    # The alt basket respects the operator's coin_blocklist (same list the
    # coin_filter gate applies) so a blocked name can't skew the tape read.
    try:
        _blocklist = tuple(coin_blocklist or ())
    except Exception:
        _blocklist = ()
    fetchers = {
        "btc": (lambda: btc_tape_activity(), "BTC"),
        "alt": (lambda: alt_basket_tape_activity(blocklist=_blocklist), "alt-basket"),
    }
    evaluated = {}
    gaps = []
    for name in ("btc", "alt"):
        vcfg = variants.get(name) or {}
        fetch, label = fetchers[name]
        r = _quiet_tape_variant(name, label, vcfg, fetch)
        if r is not None:
            evaluated[name] = r
        elif (bool(vcfg.get("enabled", False))
              and float(vcfg.get("vol_pct", 2.5) or 0.0) > 0
              and float(vcfg.get("drift_pct", 2.0) or 0.0) > 0):
            # Enabled with sane thresholds but no tape read (data gap):
            # no opinion (a gap can never block a trade) — but it still rides
            # along in `variants` with reads=None so the gap is visible in
            # the Trade result JSON. (Disabled / non-positive thresholds are
            # inert, not gaps — they don't ride along.)
            gaps.append(name)
    if not evaluated and not gaps:
        return {"pass": True}

    if merge == "and":
        # All AVAILABLE LIVE reads must be quiet (shadow variants never
        # merge — they only accrue). A missing live read (gap/disabled) is
        # no opinion — it cannot block, and it cannot release the other
        # variant's quiet verdict (BTC quiet + ALT gap still blocks, so a
        # broken alt feed cannot silently revert to BTC-alone protection).
        live_eval = [r for r in evaluated.values() if r["live"]]
        blocked = bool(live_eval) and all(r["would_block"] for r in live_eval)
    else:  # "or"
        blocked = any(r["would_block"] and r["live"] for r in evaluated.values())
    _blocked_pre_momentum = blocked

    # ── Momentum-release clause (2026-10-06, WATCHLIST §B.17) ─────────
    # The rip accrual (scratch/_quiet_tape_rip_accrual.py re-run 2026-10-06:
    # 111 blocks, 32 sole-blocks, 16 episodes, 13 ripped) showed the gate's
    # blind spot: every ripped episode sat on a coin ALREADY moving in the
    # LLM's called direction (side-aligned trail24h drift +12…+19%, coin
    # vol 7–10%) while the BTC/alt tape read quiet. The clause releases a
    # quiet_tape block when the COIN's own trail24h read is loud in the
    # called direction: side-aligned |drift| >= `aligned_drift_pct` OR
    # coin vol >= `coin_vol_pct` (in-sample sweep: A=12/V=8 catches 11/13
    # rips at the cost of 3 saves; n=16 is thin, so SHADOW-FIRST — the
    # clause logs WOULD-HAVE-RELEASED accruals until ~15 more sole-block
    # episodes price it out-of-sample).
    # Config: `momentum_release: {enabled, shadow_mode, aligned_drift_pct,
    # coin_vol_pct}` under quiet_tape_gate (either config shape). Absent or
    # disabled = clause inert (byte-identical legacy behaviour). A coin-tape
    # data gap is no opinion — it can never release a block.
    momentum = None
    mcfg = cfg.get("momentum_release") or {}
    if blocked and bool(mcfg.get("enabled", False)):
        from hermes_trader.agents.market_regime import coin_tape_activity
        _A = float(mcfg.get("aligned_drift_pct", 12.0) or 0.0)
        _V = float(mcfg.get("coin_vol_pct", 8.0) or 0.0)
        _ct = coin_tape_activity(ctx.coin)
        if _ct is not None and _A > 0 and _V > 0:
            # side-aligned drift: long wants drift >= +A, short <= -A
            _ad = _ct["drift"] if ctx.trade_side == "long" else -_ct["drift"]
            _fires = _ad >= _A or _ct["vol"] >= _V
            _live = not bool(mcfg.get("shadow_mode", True))
            momentum = {
                "fires": _fires,
                "live": _live,
                "reads": {"vol": round(float(_ct["vol"]), 4),
                          "drift": round(float(_ct["drift"]), 4),
                          "aligned_drift": round(float(_ad), 4)},
                "thresholds": {"aligned_drift_pct": _A, "coin_vol_pct": _V},
            }
            if _fires:
                momentum["reason"] = (
                    f"quiet_tape_momentum (coin {ctx.coin} trail24h "
                    f"aligned drift {_ad:+.2f}% vs bar {_A:.2f}% OR coin vol "
                    f"{_ct['vol']:.2f}% vs bar {_V:.2f}% — coin is loud in "
                    f"the called direction while the broad tape reads quiet)")
                if _live:
                    blocked = False  # release the entry

    shadow_fired = [r for r in evaluated.values() if r["would_block"] and not r["live"]]
    live_fired = [r for r in evaluated.values() if r["would_block"] and r["live"]]
    # OR-vs-AND counterfactual: the entry passes (or is blocked elsewhere)
    # while ≥1 LIVE variant would have blocked alone. Under OR this is the
    # same set as `live_fired` minus the blocked case; under AND it is the
    # released cohort. Always computed — cheap, and it is the join key.
    # NOTE: computed from the PRE-momentum-release verdict (the momentum
    # clause has its own `momentum` accrual key; the two must not conflate).
    released_by_merge = (not _blocked_pre_momentum) and bool(live_fired)

    reason = " ; ".join(r["reason"] for r in evaluated.values() if r["would_block"])
    out: Dict[str, Any] = {"pass": not blocked}
    if reason:
        out["reason"] = reason
    if shadow_fired:
        out["shadow_would_block"] = True
        out["shadow_reasons"] = [r["reason"] for r in shadow_fired]
    out["variants"] = {
        name: {
            "would_block": r["would_block"],
            "live": r["live"],
            "reads": r["reads"],
        }
        for name, r in evaluated.items()
    }
    for name in gaps:
        vcfg = variants.get(name) or {}
        out["variants"][name] = {
            "would_block": False,
            "live": not bool(vcfg.get("shadow_mode", True)),
            "reads": None,  # data gap — no opinion
        }
    out["merge"] = merge
    if momentum is not None:
        # Momentum-release accrual key (rides gate_results → Trade result
        # JSON; the executor logs `quiet_tape_momentum WOULD HAVE RELEASED`
        # when fires && shadow). `blocked_pre` marks the pre-clause verdict
        # so the join can tell a shadow clause from a live release.
        momentum["blocked_pre"] = _blocked_pre_momentum
        out["momentum"] = momentum
    if released_by_merge:
        out["released_by_merge"] = True
        out["released_reasons"] = [r["reason"] for r in live_fired]
    return out


def eval_all_gates(
    ctx: GateContext,
    config: Optional[Dict[str, Any]] = None,
    last_trade_time: Optional[int] = None,
) -> Dict[str, Any]:
    """Evaluate all risk gates and collect results."""
    from hermes_trader.agents.config import get_config
    runtime_config = get_config()
    effective_config = config or runtime_config
    results = {}
    # Regime-aware confidence floor: a WITH-TREND (aligned) trade — long in an up
    # regime, SHORT in a DOWN regime — gets a lower bar (`aligned_min_conf`) than
    # the default `min_ai_confidence`. The 0.78 default was calibrated on the
    # LONG-side 0.70-0.80 leak; applying it to aligned shorts made us sit out
    # selloffs (e.g. SOL SHORT 0.72 / -6.3% / $399M blocked). Demand full
    # conviction only to fight the trend (neutral/counter-trend keep the default).
    min_conf = float(_cfg(effective_config, "min_ai_confidence", 0.8))
    aligned_min_conf = effective_config.get("aligned_min_conf")
    if aligned_min_conf is not None:
        try:
            from hermes_trader.agents.market_regime import detect_regime
            _rg = detect_regime(ctx.coin)  # cached (TTL); market_regime_gate reuses it
            _aligned = (_rg == "up" and ctx.trade_side == "long") or \
                       (_rg == "down" and ctx.trade_side == "short")
            if _aligned:
                min_conf = min(min_conf, float(aligned_min_conf))
        except Exception:
            pass
    results["confidence"] = _cohort_conf_admission(
        ctx, effective_config, confidence_gate(ctx, min_conf), min_conf)
    results["max_concurrent"] = max_concurrent_positions_gate(ctx, _cfg(effective_config, "max_concurrent", 3))
    results["notional_cap"] = per_trade_notional_cap_gate(ctx, _cfg(effective_config, "max_trade_notional_usd", 300))
    # Equity-relative daily kill + halt timer (see daily_loss_kill_switch).
    _kill_thr = effective_daily_kill_usd(effective_config, ctx.equity)
    _halt_min = 0.0
    if _kill_thr > 0:
        try:
            from hermes_trader.agents import memory as _mem
            _halt_min = _mem.memory.daily_halt_remaining_min()
        except Exception:
            pass
    results["daily_loss"] = daily_loss_kill_switch(ctx, _kill_thr, _halt_min)
    results["daily_giveback"] = daily_giveback_gate(
        ctx,
        float(_cfg(effective_config, "daily_giveback_halt_pct", 0.0) or 0.0),
        float(_cfg(effective_config, "daily_giveback_min_peak_usd", 20.0) or 0.0),
    )
    runner_gate_cfg = effective_config.get("runner_entry_gate") or {}
    results["liquidity"] = market_liquidity_floor(
        ctx,
        float(_cfg(effective_config, "min_market_volume_usd", 5_000_000) or 5_000_000),
        float(_cfg(effective_config, "min_hip3_volume_usd", 500_000) or 500_000),
        gate_config=runner_gate_cfg,
    )
    results["short_liquidity"] = short_liquidity_floor(
        ctx, float(_cfg(effective_config, "min_short_volume_usd", 0) or 0)
    )
    results["coin_filter"] = coin_allowlist_gate(
        ctx,
        _cfg(effective_config, "coin_allowlist", []),
        _cfg(effective_config, "coin_blocklist", []),
    )
    results["no_pyramid"] = opposite_direction_guard(ctx)
    results["cooldown"] = cooldown_gate(ctx, last_trade_time, _cfg(effective_config, "cooldown_min", 60))
    results["correlation"] = correlation_cap(ctx, int(_cfg(effective_config, "max_crypto_long_correlated", 2)))
    results["equity_risk"] = equity_risk_cap(ctx, _cfg(effective_config, "max_total_notional_pct", 1.0))
    results["market_regime"] = market_regime_gate(
        ctx, _cfg(effective_config, "counter_regime_min_conf", 0.7),
        bool(_cfg(effective_config, "block_counter_trend_bypass", False)),
        float(_cfg(effective_config, "crowded_with_min_conf", 0.0) or 0.0),
        no_composite_escape=bool(
            _cfg(effective_config, "counter_regime_no_composite_escape", False)),
    )
    results["news"] = news_blackout_gate(ctx)
    # Shadow by default (see gate docstring): structurally passes and logs a
    # would-block until shadow_mode is flipped in .agent-config.json.
    results["chronos_mismatch"] = chronos_mismatch_gate(
        ctx, effective_config.get("chronos_mismatch_gate") or {})
    # Tail-trigger (shape-based) counter-forecast veto. Same warm cache and
    # same elevated-conviction bar as chronos_mismatch, but keys off the
    # ADVERSE quantile PATH (min q10 / max q90 over the first `window_steps`
    # 5m steps) rather than the path-mean scalar. SHADOW until shadow_mode
    # is flipped. Passes when the warm cache has no per-step paths.
    results["chronos_tail_trigger"] = chronos_tail_trigger_gate(
        ctx, effective_config.get("chronos_tail_trigger_gate") or {})
    # Log-only comparator (plan D4): path-tail vs band-width veto agreement.
    # Structurally cannot block; disabled unless tail_spread_comparator.enabled.
    results["tail_spread_cmp"] = tail_spread_comparator(
        ctx, effective_config.get("tail_spread_comparator") or {})
    # Band counter-trend breach (GRASS shape): disabled by default in the
    # shadow-gate config block below; when enabled it runs in shadow_mode
    # (would-block marker) until the operator promotes it.
    results["band_counter_breach"] = band_counter_breach_gate(
        ctx, effective_config.get("band_counter_breach_gate") or {})
    # Duelist veto: the A/B duelist's EXPLICIT VETO or opposite-side call on
    # the primary's directional entry (re-keyed 2026-09-03: a neutral PASS is
    # no longer an objection). Strict veto (no escape by default); shadow
    # until the operator flips shadow_mode to false. Passes when the duelist
    # has no verdict (disabled / failed) or AGREES with the primary — a data
    # gap can never block a trade.
    results["duelist_veto"] = duelist_veto_gate(
        ctx, effective_config.get("duelist_veto_gate") or {})
    # Forecast-agreement tail veto: chronos AND timesfm both adverse. Shadow
    # until shadow_mode is flipped (2026-09-02 sweep: the AND shape is the
    # most split-half-robust veto cell). Passes when either model's paths
    # are missing — a data gap can never block a trade.
    results["forecast_agreement_veto"] = forecast_agreement_veto_gate(
        ctx, effective_config.get("forecast_agreement_veto_gate") or {})
    # TimesFM mirror legs (per-forecaster counterfactuals — timesfm-alone;
    # the AND leg is forecast_agreement_veto above). timesfm_mismatch stays
    # shadow-only by construction (pass-only code path). timesfm_tail_trigger
    # is shadow-CAPABLE since the 2026-09-17 C.9 promotion: accrual posture by
    # default, armed live via shadow_mode:false in config. enabled controls
    # whether each runs. A data gap (missing median / q-paths) can never flag.
    results["timesfm_mismatch"] = timesfm_mismatch_gate(
        ctx, effective_config.get("timesfm_mismatch_gate") or {})
    results["timesfm_tail_trigger"] = timesfm_tail_trigger_gate(
        ctx, effective_config.get("timesfm_tail_trigger_gate") or {})
    # TiRex tail-trigger mirror (2026-09-17): accrual-first twin of the armed
    # chronos gate — best stop-out AUC of all four forecasters offline, but
    # capture-tied with chronos at the live operating point (see gate
    # docstring), so it shadows until executed-cohort evidence picks either.
    results["tirex_tail_trigger"] = tirex_tail_trigger_gate(
        ctx, effective_config.get("tirex_tail_trigger_gate") or {})
    # Quiet broad-tape entry gate (2026-09-10, WATCHLIST §B.17): blocks a new
    # entry when BTC's trailing-24h vol AND |drift| are both below the sweep-
    # chosen thresholds (2.5/2.0). The bleed-day broad-tape was the quietest
    # of the month; per-coin variants measured vacuous. Shadow by default —
    # the would-block marker accrues until shadow_mode is flipped. A data gap
    # (btc_tape_activity() → None) can never block a trade.
    results["quiet_tape"] = quiet_tape_gate(
        ctx, effective_config.get("quiet_tape_gate") or {},
        coin_blocklist=_cfg(effective_config, "coin_blocklist", []),
    )

    block_reasons = []
    blocked = False
    for key, result in results.items():
        if not result.get("pass"):
            blocked = True
            block_reasons.append(result.get("reason", key))

    return {"results": results, "blocked": blocked, "block_reasons": block_reasons}
