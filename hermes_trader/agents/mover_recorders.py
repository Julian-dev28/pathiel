"""Zero-capital + bounded-live mover recorders (Lane M follow-ups).

Books hosted here (mover_pass LONG and b15_up were REFUTED and removed
2026-07-22; their ledgers stay as evidence):

- mover_pass_short (LIVE): SHORT the mover the AI just PASSed. The
  reverse-refuted-direction audit found the AI PASS veto SAVES money in this
  tape, so its inverse (fading the PASSed mover) grades +6.745%/sig, mc_p
  0.0005, both OOS halves + (see reverse_refuted_direction_audit.md).
- young_mover_short (LIVE): SHORT the young listing the history floor blocks
  from a long (that cohort does -2.71%/next-day vs a flat mature-xyz tape).
- news_ta_quadrant (recorder): tags each directional verdict news-vs-TA
  aligned/conflict/neutral for forward grading.
- trend_block_news_long (recorder): catalyst-positive LONG that died only at
  the trend filter, zero-capital counterfactual.

All record to the unified shadow ledger; scripts/shadow_status.py grades them.
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
    """Call on every AI PASS verdict. Records the inverse SHORT
    counterfactual and opens the bounded live short when
    mover_recorders.pass_short_live.shadow_only=false. Own dedup key
    ("pass_short")."""
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


def _ntq_aligned_analysis(coin: str, side: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Bounded book order for the LIVE news_ta_aligned arm. confidence=0.99 (like the
    other book legs) so the executor's runner/confidence gates pass; the REAL AI
    confidence is preserved in the ledger meta for grading. leverage default 3: a 15%
    stop needs 1/lev - maint > 0.15, i.e. lev <= ~4x, so the DSL stop fires before
    liquidation even on a high-maint alt."""
    stop_pct = float(cfg.get("stop_pct", 15.0))
    leverage = max(1, int(cfg.get("leverage", 3)))
    verdict = "LONG" if side == "long" else "SHORT"
    short_floor = 250_000.0 if ":" in coin else float(cfg.get("min_volume_usd", 5_000_000.0))
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": verdict, "side": side,
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (f"[news_ta_aligned] AI {verdict} confirmed by aligned news polarity — "
                      f"validated quadrant (aligned +1.80%/sig, win 0.86)"),
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": "news_ta_aligned",
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


def maybe_trade_news_ta_aligned(analysis: Dict[str, Any], config: Dict[str, Any],
                                execute_fn: Optional[Callable] = None) -> bool:
    """LIVE leg for the VALIDATED aligned quadrant. news_ta_quadrant graded the
    news-vs-TA split forward: ALIGNED (AI direction agrees with news polarity) =
    +1.80%/sig, win 0.86 (n=7); the pooled book VALIDATED at n=10, both OOS halves +,
    survives 25bps. CONFLICT is noise (+0.80%, win 0.33, one outlier). This trades ONLY
    the aligned subset — the AI's OWN directional verdict when news agrees — which is the
    +EV slice the blanket main-engine-off drops on the floor. Conflict/neutral NEVER trade.

    Runs on the same raw verdict the recorder tags (before route_verdict mutates it), so
    live policy == graded policy. Records to its OWN ledger (news_ta_aligned) for a clean
    live-policy forward grade, separate from the research quadrant ledger. Kill:
    news_ta_aligned.enabled=false or shadow_only=true."""
    cfg = config.get("news_ta_aligned") or {}
    if not bool(cfg.get("enabled", False)):
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
    polarity, source = classify_news_polarity(analysis.get("news_risk"), news)
    side = "long" if verdict == "LONG" else "short"
    aligned = (polarity != "neutral") and ((polarity == "positive") == (side == "long"))
    if not aligned:
        return False
    conf = float(analysis.get("confidence") or 0.0)
    if conf < float(cfg.get("min_confidence", 0.0)):
        return False
    now_ms = int(time.time() * 1000)
    if not _dedup_key_hit("ntq_trade", coin, now_ms):   # own key — independent of the recorder
        return False
    stop_pct = float(cfg.get("stop_pct", 15.0))
    live = (not bool(cfg.get("shadow_only", False))) and execute_fn is not None
    shadow_ledger.record("news_ta_aligned", coin=coin, side=side,
                         signal_bar_t=(now_ms // 3_600_000) * 3_600_000,
                         entry_ref_px=px, horizon_days=float(cfg.get("hold_days", 1.0)),
                         stop_pct=stop_pct,
                         meta={"quadrant": "aligned", "news_polarity": polarity,
                               "polarity_source": source, "confidence": conf,
                               "macro_regime": _macro_regime(coin),
                               "web_search_used": bool(analysis.get("web_search_used")),
                               "shadow": not live})
    logger.info(f"[news-ta-aligned] recorded: {coin} {side} aligned "
                f"(news {polarity}/{source}, conf {conf:.2f}, {'LIVE' if live else 'shadow'})")
    if live:
        claims = get_claims_registry()
        if (coin not in claims.claimed_by_others("news_ta_aligned")
                and claims.claim(coin, "news_ta_aligned")):
            try:
                result = execute_fn(_ntq_aligned_analysis(coin, side, cfg))
                opened = isinstance(result, dict) and (
                    bool(result.get("executed"))
                    or bool((result.get("result") or {}).get("executed")
                            if isinstance(result.get("result"), dict) else False))
                if opened:
                    claims.save()
                    logger.info(f"[news-ta-aligned] LIVE opened {side} {coin}")
                else:
                    claims.release(coin, "news_ta_aligned")
                    why = (result.get("reason") or result.get("blocked_by")) if isinstance(result, dict) else result
                    logger.warning(f"[news-ta-aligned] {coin} not opened: {why}")
            except Exception as exc:
                claims.release(coin, "news_ta_aligned")
                logger.warning(f"[news-ta-aligned] open {coin} failed: {exc}")
    return True