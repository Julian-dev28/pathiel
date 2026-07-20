"""Zero-capital mover recorders (Lane M follow-ups, 2026-07-11).

Two hypotheses earned FORWARD ledgers — not capital — from the W-M studies:

1. mover_pass: W-M4 gate audit measured the AI's PASS veto on researched
   movers forfeiting +4.48% mean forward-24h (n=15, 79% positive, one coin
   PASSed 28x while running +29.5%). Small n, no matched null, one 14-day
   window — so every researched mover the AI PASSes now records a
   hypothetical LONG for the fixed grader. Promotion bar: >=30 episodes,
   EV25 > 0 both halves.

2. b15_up: W-M1's single near-miss in the 624-cell grid — LONG when rolling
   24h return crosses +15% on >= $5M volume in a BTC-20d-up regime (n=66,
   EV25 +0.85%, OOS +0.58/+1.14, p=0.022) — which FAILED Bonferroni (alpha
   8e-5), i.e. plausibly grid luck. The recorder settles it forward at zero
   cost. Promotion bar: >=30 episodes, EV25 > 0 both halves, and the W-M1
   cell's own re-run agreeing.

Both record to the unified shadow ledger; scripts/shadow_status.py grades
them with the funding-aware, dedup-correct pipeline.

3. mover_pass_short (LIVE, operator flip 2026-07-20): the reverse-refuted-
   direction audit found mover_pass's own forward ledger interim REFUTED-lean
   (-6.36%/sig @12bps, n=17, both halves negative — the PASS veto is SAVING
   money in this tape, not forfeiting it). Its exact inverse — SHORT the
   mover the AI just PASSed — grades +6.745%/sig, excess +6.89% over the
   matched same-coin random-time null, mc_p 0.0005, both OOS halves
   +6.70/+6.79 (n=17, no outlier dependency, mixed crypto + xyz equities;
   see research/alpha_swarm/findings/reverse_refuted_direction_audit.md).
   Shares the exact same trigger + dedup window as mover_pass (same PASS
   event, same call site) but its OWN dedup key ("pass_short") so the two
   books rate-limit independently. Claims-registry mutual exclusion still
   applies: whichever book claims a coin first this cycle holds it, the
   other backs off (tests/test_live_book_wiring_integrity.py). Mandatory
   review at 8 resolved forward episodes.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, Callable, Dict, Optional, Tuple

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file

logger = logging.getLogger(__name__)


def _macro_regime(coin: str) -> Optional[str]:
    """Cheap, cached macro-regime tag (up/down/neutral) for a coin's asset
    class — BTC for crypto, the xyz equity index for tokenized equities.

    Zero-capital metadata ONLY: it never gates a trade. It exists so the
    forward grader can split a book by regime and settle, on live evidence,
    whether a regime tilt helps — the 2026-07-20 finding that young-listing
    shorts pay +6.03%/85% when the equity index is UP over the prior 7d but
    only +0.18%/48% when it is down (n=55/71, not time-clustered) was a
    RETROSPECTIVE slice; this makes it a forward-gradeable hypothesis via
    `shadow_status.py --book <book> --meta macro_regime=up`. Never raises."""
    try:
        from hermes_trader.agents.market_regime import (
            CRYPTO_PROXY, EQUITY_PROXY, classify_asset, detect_regime,
        )
        proxy = EQUITY_PROXY if classify_asset(coin) == "equity" else CRYPTO_PROXY
        return str(detect_regime(proxy))
    except Exception:
        return None


_DAY_MS = 86_400_000
_SEEN_FILE = state_file(".mover_recorders_seen.json")


def _load_seen() -> Dict[str, int]:
    try:
        raw = json.load(open(_SEEN_FILE))
        return {str(k): int(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_seen(seen: Dict[str, int]) -> None:
    try:
        with open(_SEEN_FILE, "w") as fh:
            json.dump(seen, fh, sort_keys=True)
    except Exception:
        pass


def _dedup_key_hit(kind: str, coin: str, now_ms: int) -> bool:
    """True (and marks) if this (kind, coin) hasn't recorded this UTC day."""
    seen = _load_seen()
    key = f"{kind}:{coin}"
    day = now_ms // _DAY_MS
    if seen.get(key) == day:
        return False
    seen[key] = day
    _save_seen(seen)
    return True


def _pass_live_analysis(coin: str, move: float, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Bounded book order for the LIVE mover_pass arm (operator flip
    2026-07-12): buy the mover the AI just PASSed, recorded geometry
    ($20/1x, 15% stop, 1d hold, no trail). W-M4 basis: PASS vetoes forfeited
    +4.48% mean fwd-24h (n=15, no null — thin; kill at 10 episodes EV25<0)."""
    stop_pct = float(cfg.get("stop_pct", 15.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "LONG", "side": "long",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": f"[mover_pass] AI PASSed a +{move:.1f}% mover — W-M4 counter-buy",
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": "mover_pass",
        "strategy_book_notional": float(cfg.get("notional_usd", 20.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "dsl_exit_override": {
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            "protect_pct": 9999.0,
            "retrace_threshold": 0.5,
            "hard_timeout_minutes": float(cfg.get("hold_days", 1.0)) * 1440.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
        },
    }


def record_mover_pass(analysis: Dict[str, Any], config: Dict[str, Any],
                      execute_fn: Optional[Callable] = None) -> bool:
    """Call on every AI PASS verdict. Records a hypothetical LONG when the
    PASSed coin is a real mover (daily move >= min_move_pct on >= min vol).
    When mover_recorders.pass_live.shadow_only=false and an execute_fn is
    provided, also opens the bounded live counter-buy (same dedup: one per
    coin per UTC day, so live entries mirror the ledger 1:1)."""
    cfg = (config.get("mover_recorders") or {})
    if not bool(cfg.get("enabled", True)):
        return False
    try:
        move = float(analysis.get("daily_move_pct") or 0.0)
        vol = float(analysis.get("daily_volume_usd") or 0.0)
        px = float(analysis.get("last_price") or analysis.get("entry_ref_px") or 0.0)
        coin = analysis.get("coin") or ""
    except (TypeError, ValueError):
        return False
    if not coin or move < float(cfg.get("pass_min_move_pct", 8.0)):
        return False
    if vol and vol < float(cfg.get("min_volume_usd", 5_000_000.0)):
        return False
    now_ms = int(time.time() * 1000)
    if not _dedup_key_hit("pass", coin, now_ms):
        return False
    if px <= 0:
        # entry reference from live mid is the caller's job; degrade to skip
        return False
    live_cfg = cfg.get("pass_live") or {}
    live = (bool(live_cfg.get("enabled", False))
            and not bool(live_cfg.get("shadow_only", True))
            and execute_fn is not None)
    shadow_ledger.record("mover_pass", coin=coin, side="long",
                         signal_bar_t=(now_ms // 3_600_000) * 3_600_000,
                         entry_ref_px=px, horizon_days=1.0, stop_pct=15.0,
                         meta={"confidence": float(analysis.get("confidence") or 0),
                               "move_pct": round(move, 2), "shadow": not live})
    logger.info(f"[mover-recorders] PASS-veto counterfactual recorded: {coin} "
                f"(+{move:.1f}%, conf {float(analysis.get('confidence') or 0):.2f})")
    if live:
        claims = get_claims_registry()
        if coin not in claims.claimed_by_others("mover_pass") and claims.claim(coin, "mover_pass"):
            try:
                result = execute_fn(_pass_live_analysis(coin, move, live_cfg))
                opened = isinstance(result, dict) and (
                    bool(result.get("executed"))
                    or bool((result.get("result") or {}).get("executed") if isinstance(result.get("result"), dict) else False)
                )
                if opened:
                    claims.save()
                    logger.info(f"[mover-pass] LIVE opened long {coin} (+{move:.1f}% PASSed mover)")
                else:
                    claims.release(coin, "mover_pass")
                    why = (result.get("reason") or result.get("blocked_by")) if isinstance(result, dict) else result
                    logger.warning(f"[mover-pass] {coin} not opened: {why}")
            except Exception as exc:
                claims.release(coin, "mover_pass")
                logger.warning(f"[mover-pass] open {coin} failed: {exc}")
    return True


def _pass_short_live_analysis(coin: str, move: float, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Bounded book order for the LIVE mover_pass_short arm (operator flip
    2026-07-20, reverse-refuted-direction audit): SHORT the mover the AI just
    PASSed, $20/10x, 15% stop, 1d hold, no trail — the exact geometry the
    audit graded. Short-liquidity floor is asset-class-aware: xyz equities
    use the $250k convention (xs_xyz_live), crypto uses the trigger's own
    $5M eligibility floor (already cleared by definition)."""
    stop_pct = float(cfg.get("stop_pct", 15.0))
    leverage = max(1, int(cfg.get("leverage", 10)))
    short_floor = 250_000.0 if ":" in coin else float(cfg.get("min_volume_usd", 5_000_000.0))
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "SHORT", "side": "short",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (f"[mover_pass_short] AI PASSed a +{move:.1f}% mover, "
                      f"inverse-of-refuted mover_pass — fading it"),
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": "mover_pass_short",
        "strategy_book_notional": float(cfg.get("notional_usd", 20.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "min_short_volume_usd_override": short_floor,
        "dsl_exit_override": {
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            "protect_pct": 9999.0,
            "retrace_threshold": 0.5,
            "hard_timeout_minutes": float(cfg.get("hold_days", 1.0)) * 1440.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
        },
    }


def record_mover_pass_short(analysis: Dict[str, Any], config: Dict[str, Any],
                            execute_fn: Optional[Callable] = None) -> bool:
    """Call on every AI PASS verdict (same call site as record_mover_pass).
    Records the inverse SHORT counterfactual; opens the bounded live short
    when mover_recorders.pass_short_live.shadow_only=false. Uses its own
    dedup key so it never blocks — or gets blocked by — record_mover_pass's
    ledger row for the same coin/day."""
    cfg = (config.get("mover_recorders") or {})
    if not bool(cfg.get("enabled", True)):
        return False
    try:
        move = float(analysis.get("daily_move_pct") or 0.0)
        vol = float(analysis.get("daily_volume_usd") or 0.0)
        px = float(analysis.get("last_price") or analysis.get("entry_ref_px") or 0.0)
        coin = analysis.get("coin") or ""
    except (TypeError, ValueError):
        return False
    if not coin or move < float(cfg.get("pass_min_move_pct", 8.0)):
        return False
    if vol and vol < float(cfg.get("min_volume_usd", 5_000_000.0)):
        return False
    now_ms = int(time.time() * 1000)
    if not _dedup_key_hit("pass_short", coin, now_ms):
        return False
    if px <= 0:
        return False
    live_cfg = cfg.get("pass_short_live") or {}
    live = (bool(live_cfg.get("enabled", False))
            and not bool(live_cfg.get("shadow_only", True))
            and execute_fn is not None)
    shadow_ledger.record("mover_pass_short", coin=coin, side="short",
                         signal_bar_t=(now_ms // 3_600_000) * 3_600_000,
                         entry_ref_px=px, horizon_days=1.0, stop_pct=15.0,
                         meta={"confidence": float(analysis.get("confidence") or 0),
                               "move_pct": round(move, 2),
                               "macro_regime": _macro_regime(coin), "shadow": not live})
    logger.info(f"[mover-recorders] PASS-veto SHORT inverse recorded: {coin} "
                f"(+{move:.1f}%, conf {float(analysis.get('confidence') or 0):.2f})")
    if live:
        claims = get_claims_registry()
        if (coin not in claims.claimed_by_others("mover_pass_short")
                and claims.claim(coin, "mover_pass_short")):
            try:
                result = execute_fn(_pass_short_live_analysis(coin, move, live_cfg))
                opened = isinstance(result, dict) and (
                    bool(result.get("executed"))
                    or bool((result.get("result") or {}).get("executed") if isinstance(result.get("result"), dict) else False)
                )
                if opened:
                    claims.save()
                    logger.info(f"[mover-pass-short] LIVE opened short {coin} (+{move:.1f}% PASSed mover)")
                else:
                    claims.release(coin, "mover_pass_short")
                    why = (result.get("reason") or result.get("blocked_by")) if isinstance(result, dict) else result
                    logger.warning(f"[mover-pass-short] {coin} not opened: {why}")
            except Exception as exc:
                claims.release(coin, "mover_pass_short")
                logger.warning(f"[mover-pass-short] open {coin} failed: {exc}")
    return True


def _young_short_live_analysis(coin: str, days: int, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Bounded book order for young_mover_short (operator flip 2026-07-20)."""
    stop_pct = float(cfg.get("stop_pct", 6.0))
    leverage = max(1, int(cfg.get("leverage", 10)))
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "SHORT", "side": "short",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (f"[young_mover_short] {days}d-old listing flagged by the scan and "
                      f"blocked from LONG by the history floor — fading it instead"),
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": "young_mover_short",
        "strategy_book_notional": float(cfg.get("notional_usd", 20.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "min_short_volume_usd_override": float(cfg.get("min_volume_usd", 250_000.0)),
        "dsl_exit_override": {
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            "protect_pct": 9999.0,
            "retrace_threshold": 0.5,
            "hard_timeout_minutes": float(cfg.get("hold_days", 1.0)) * 1440.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
        },
    }


_HISTORY_FLOOR_RE = re.compile(r"history_floor_preflight \((\d+)d")


def record_young_mover_short(coin: str, preblock_reason: str, mid_px: float,
                             config: Dict[str, Any],
                             execute_fn: Optional[Callable] = None) -> bool:
    """Call when the pre-research preflight blocks a scan candidate on the
    history-age floor. That floor is CORRECT about direction — the blocked
    population's next-day LONG return is -2.71% (n=126 coin-days, 28 xyz
    coins, coin-cluster bootstrap 95% CI -3.52%..-1.87%) against a matched
    same-day MATURE-xyz baseline of -0.13% (flat tape, 50% win) — so it keeps
    blocking longs. This records the INVERSE: 25 of 28 coins would have paid
    a short, +2.71%/episode gross (~+2.46% net 25bps).

    Live trading is xyz-equities-only: that is where the n=126 sample lives.
    Young CRYPTO listings pointed the same way (-5.75%) but on n=9, which is
    not evidence — those record at zero capital until they earn their own n.

    Measured retrospectively from the loop's own block log, so this is a
    PRIOR, not a forward verdict: the ledger it writes is what settles it.
    Mandatory review at 8 resolved forward episodes."""
    cfg = (config.get("mover_recorders") or {})
    if not bool(cfg.get("enabled", True)):
        return False
    m = _HISTORY_FLOOR_RE.search(str(preblock_reason or ""))
    if not m:
        return False
    try:
        px = float(mid_px or 0.0)
    except (TypeError, ValueError):
        return False
    if not coin or px <= 0:
        return False
    days = int(m.group(1))
    now_ms = int(time.time() * 1000)
    if not _dedup_key_hit("young_short", coin, now_ms):
        return False
    live_cfg = cfg.get("young_short_live") or {}
    live = (bool(live_cfg.get("enabled", False))
            and not bool(live_cfg.get("shadow_only", True))
            and execute_fn is not None
            and ":" in coin)          # evidence boundary: xyz equities only
    shadow_ledger.record("young_mover_short", coin=coin, side="short",
                         signal_bar_t=(now_ms // 3_600_000) * 3_600_000,
                         entry_ref_px=px, horizon_days=1.0,
                         stop_pct=float(live_cfg.get("stop_pct", 6.0)),
                         meta={"listing_days": days, "equity": ":" in coin,
                               "macro_regime": _macro_regime(coin), "shadow": not live})
    logger.info(f"[mover-recorders] young_mover_short recorded: {coin} "
                f"({days}d listing, short @ {px})")
    if live:
        claims = get_claims_registry()
        if (coin not in claims.claimed_by_others("young_mover_short")
                and claims.claim(coin, "young_mover_short")):
            try:
                result = execute_fn(_young_short_live_analysis(coin, days, live_cfg))
                opened = isinstance(result, dict) and (
                    bool(result.get("executed"))
                    or bool((result.get("result") or {}).get("executed") if isinstance(result.get("result"), dict) else False)
                )
                if opened:
                    claims.save()
                    logger.info(f"[young-mover-short] LIVE opened short {coin} ({days}d listing)")
                else:
                    claims.release(coin, "young_mover_short")
                    why = (result.get("reason") or result.get("blocked_by")) if isinstance(result, dict) else result
                    logger.warning(f"[young-mover-short] {coin} not opened: {why}")
            except Exception as exc:
                claims.release(coin, "young_mover_short")
                logger.warning(f"[young-mover-short] open {coin} failed: {exc}")
    return True


def record_trend_block_news_long(analysis: Dict[str, Any], result: Any,
                                 config: Dict[str, Any]) -> bool:
    """W-G pocket test (2026-07-12, ARB/Robinhood case): the trend filter is
    forward-validated on the FULL population (blocked entries avg −1.68%/ep,
    n=4,644 decision audit) — but catalyst-backed longs were rare in that
    sample. Every LONG with positive news risk that dies ONLY at the trend
    filter records a hypothetical entry; the ledger answers whether a real
    catalyst overrides a daily downtrend. Promotion bar: >=20 episodes,
    EV25 > 0 both halves — until then the gate keeps blocking."""
    cfg = (config.get("mover_recorders") or {})
    if not bool(cfg.get("enabled", True)):
        return False
    if not isinstance(result, dict) or result.get("executed"):
        return False
    blocked = result.get("blocked_by") or []
    if isinstance(blocked, str):
        blocked = [blocked]
    if not any("trend_filter" in str(b) or "200d-MA" in str(b) for b in blocked):
        return False
    if (analysis.get("verdict") or "").upper() != "LONG":
        return False
    if (analysis.get("news_risk") or "").lower() != "positive":
        return False
    coin = analysis.get("coin") or ""
    try:
        px = float(analysis.get("last_price") or analysis.get("entry_px") or 0.0)
    except (TypeError, ValueError):
        px = 0.0
    if not coin or px <= 0:
        return False
    now_ms = int(time.time() * 1000)
    if not _dedup_key_hit("tbnl", coin, now_ms):
        return False
    shadow_ledger.record("trend_block_news_long", coin=coin, side="long",
                         signal_bar_t=(now_ms // 3_600_000) * 3_600_000,
                         entry_ref_px=px, horizon_days=2.0, stop_pct=15.0,
                         meta={"confidence": float(analysis.get("confidence") or 0),
                               "web_search_used": bool(analysis.get("web_search_used")),
                               "shadow": True})
    logger.info(f"[mover-recorders] trend-blocked catalyst LONG recorded: {coin} "
                f"(conf {float(analysis.get('confidence') or 0):.2f}, news positive)")
    return True


# ── W-V news-vs-TA quadrant (2026-07-13, SKHX case) ─────────────────────────
# Operator question: is NEWS stronger than PRICE ACTION? History cannot answer
# it (session log carries no news_context; polar news_risk verdicts n=9, zero
# conflicts — see research/alpha_swarm/findings/W-V_news_vs_ta.md), so every
# directional verdict researched WITH a real headline string records a
# zero-capital row tagged aligned/conflict/neutral. shadow_status grades the
# quadrants forward. Insight bar: n>=30 per quadrant; pre-registered rule in
# the findings doc. Hot-kill: mover_recorders.enabled=false.

_NEWS_POS_RE = re.compile(
    r"rall(?:y|ies)|surge[sd]?\b|soar(?:s|ed)?|jump(?:s|ed)?|record\b|"
    r"all-time high|\bath\b|partnership|integrat(?:es|ion)|adoption|"
    r"launch(?:es|ed)?|listing|debut|approv(?:al|es|ed)|upgrade[sd]?|bullish|"
    r"\bburn(?:s|ing)?\b|buyback|beats?\b|inflow|milestone|expan(?:ds|sion)",
    re.IGNORECASE,
)
_NEWS_NEG_RE = re.compile(
    r"hack(?:ed|er)?\b|exploit|lawsuit|\bsues?\b|\bsued\b|fraud|"
    r"investigation|probe\b|crash(?:es|ed)?|plunge[sd]?|dump(?:s|ed)?|"
    r"drop(?:s|ped)?\b|fall(?:s|ing)?\b|\bfell\b|declin(?:es|ed|ing|e)|"
    r"bearish|delist|outage|bankrupt(?:cy)?|sell-?off|liquidation[s]?\b|"
    r"unlock(?:s|ed)?\b|downgrade[sd]?|miss(?:es|ed)\b|outflow|tension[s]?\b",
    re.IGNORECASE,
)


def classify_news_polarity(news_risk: Optional[str],
                           news_context: Optional[str]) -> Tuple[str, str]:
    """(polarity, source). The AI's own event-time read (news_risk) wins when
    polar; otherwise deterministic keyword polarity over the headline string.
    Same classifier the W-V historical scripts import — one implementation."""
    nr = (news_risk or "").lower()
    if nr in ("positive", "negative"):
        return nr, "news_risk"
    text = news_context or ""
    pos = len(_NEWS_POS_RE.findall(text))
    neg = len(_NEWS_NEG_RE.findall(text))
    if pos > neg:
        return "positive", "keywords"
    if neg > pos:
        return "negative", "keywords"
    return "neutral", "keywords"


def record_news_ta_quadrant(analysis: Dict[str, Any],
                            config: Dict[str, Any]) -> bool:
    """Call on every research verdict. Directional verdicts (LONG/SHORT) that
    were researched with a REAL news_context record a hypothetical trade in
    the verdict direction (1d horizon, 15% stop) tagged with the news-vs-TA
    quadrant, so 'do conflict verdicts underperform aligned ones?' grades
    itself forward. One row per coin per UTC day. Nothing here trades."""
    cfg = (config.get("mover_recorders") or {})
    if not bool(cfg.get("enabled", True)):
        return False
    verdict = (analysis.get("verdict") or "").upper()
    if verdict not in ("LONG", "SHORT"):
        return False
    news = (analysis.get("news_context") or "").strip()
    if not news or news.lower() == "no news":
        return False
    coin = analysis.get("coin") or ""
    try:
        px = float(analysis.get("last_price") or analysis.get("entry_px") or 0.0)
    except (TypeError, ValueError):
        px = 0.0
    if not coin or px <= 0:
        return False
    now_ms = int(time.time() * 1000)
    if not _dedup_key_hit("ntq", coin, now_ms):
        return False
    polarity, source = classify_news_polarity(analysis.get("news_risk"), news)
    side = "long" if verdict == "LONG" else "short"
    if polarity == "neutral":
        quadrant = "neutral"
    elif (polarity == "positive") == (side == "long"):
        quadrant = "aligned"
    else:
        quadrant = "conflict"
    shadow_ledger.record("news_ta_quadrant", coin=coin, side=side,
                         signal_bar_t=(now_ms // 3_600_000) * 3_600_000,
                         entry_ref_px=px, horizon_days=1.0, stop_pct=15.0,
                         meta={"quadrant": quadrant,
                               "news_polarity": polarity,
                               "polarity_source": source,
                               "news_risk": (analysis.get("news_risk") or "none"),
                               "confidence": float(analysis.get("confidence") or 0),
                               "web_search_used": bool(analysis.get("web_search_used")),
                               "shadow": True})
    logger.info(f"[mover-recorders] news_ta_quadrant recorded: {coin} {side} "
                f"{quadrant} (news {polarity}/{source}, "
                f"conf {float(analysis.get('confidence') or 0):.2f})")
    return True


def record_b15_crossings(universe, btc_up: Optional[bool],
                         config: Dict[str, Any]) -> int:
    """Call once per scan with the fresh universe. Records hypothetical LONGs
    for coins whose 24h move sits at/above the +15% band in a BTC-up regime
    (first touch per coin per UTC day)."""
    cfg = (config.get("mover_recorders") or {})
    if not bool(cfg.get("enabled", True)) or not btc_up:
        return 0
    band = float(cfg.get("b15_band_pct", 15.0))
    min_vol = float(cfg.get("min_volume_usd", 5_000_000.0))
    now_ms = int(time.time() * 1000)
    n = 0
    for m in universe or []:
        coin = m.get("coin") or ""
        if not coin or coin.startswith("@") or m.get("type") == "spot":
            continue
        try:
            prev = float(m.get("prevDayPx") or 0)
            cur = float(m.get("midPx") or m.get("markPx") or 0)
            vol = float(m.get("dayNtlVlm") or 0)
        except (TypeError, ValueError):
            continue
        if prev <= 0 or cur <= 0 or vol < min_vol:
            continue
        move = (cur / prev - 1.0) * 100.0
        if move < band:
            continue
        if not _dedup_key_hit("b15", coin, now_ms):
            continue
        shadow_ledger.record("mover_b15_up", coin=coin, side="long",
                             signal_bar_t=(now_ms // 3_600_000) * 3_600_000,
                             entry_ref_px=round(cur, 8), horizon_days=1.0,
                             stop_pct=15.0,
                             meta={"move_pct": round(move, 2), "btc_up": True,
                                   "shadow": True})
        n += 1
    if n:
        logger.info(f"[mover-recorders] b15_up recorded {n} crossing(s)")
    return n
