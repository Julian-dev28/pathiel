"""Zero-capital + bounded-live mover recorders (Lane M follow-ups).

Books hosted here (mover_pass LONG and b15_up were REFUTED and removed
2026-07-22; their ledgers stay as evidence):

- mover_pass_short (LIVE): SHORT the mover the AI just PASSed. The
  reverse-refuted-direction audit found the AI PASS veto SAVES money in this
  tape, so its inverse (fading the PASSed mover) grades +6.745%/sig, mc_p
  0.0005, both OOS halves + (see reverse_refuted_direction_audit.md).
- young_mover_short (LIVE): SHORT the young listing the history floor blocks
  from a long (that cohort does -2.71%/next-day vs a flat mature-xyz tape).
Removed 2026-08-30 — operator directive "nothing should be a recorder":
- news_ta_aligned (was LIVE): REFUTED on its own forward ledger, -5.02%/sig at
  n=9, mc_p=0.9915.
- news_ta_quadrant, trend_block_news_long: zero-capital counterfactuals with no
  path to capital, which is the shape the directive rules out.

Both survivors have a switch in scripts/autonomous_cycle.py, so the evidence
loop can promote or demote them without a code change.
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


_EQ7_CACHE: Dict[str, Optional[float]] = {}


def _equity_index_7d() -> Optional[float]:
    """W-Y4 regime-gate signal: 7d return of the xyz equity index (xyz:SP500)
    from COMPLETED daily closes, strictly PIT (drops the forming bar). Cached per
    UTC day — the completed-close 7d return only moves at the daily boundary.
    Fail-closed: any fetch/parse failure returns None, which the caller treats as
    gate FAIL (W-Y4: pooled ungated EV ~0, down-regime EV -3.3%, so an unknown
    regime forgoes ~nothing and dodges the tail).

    The live 1h EMA `_macro_regime` tag does NOT carry the edge (W-Y4 validated it
    useless: up-subset +0.52%, win 46%) — this computes the 7d daily slope fresh.
    Do not substitute the tag. Only successful reads are cached, so a transient
    fetch blip fails one signal closed but does not poison the whole day."""
    key = str(int(time.time() * 1000) // _DAY_MS)
    if key in _EQ7_CACHE:
        return _EQ7_CACHE[key]
    val: Optional[float] = None
    try:
        from hermes_trader.client.hl_client import fetch_hl_candles
        bars = fetch_hl_candles("xyz:SP500", "1d", 12) or []
        closes = [float(b.get("c") if isinstance(b, dict) else getattr(b, "c"))
                  for b in bars[:-1]]                    # completed bars only
        if len(closes) >= 8 and closes[-8]:
            val = closes[-1] / closes[-8] - 1.0
    except Exception:
        val = None
    if val is not None:
        _EQ7_CACHE[key] = val                            # cache successes only
    return val


def _pass_short_live_analysis(coin: str, move: float, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Bounded book order for the LIVE mover_pass_short arm (operator flip
    2026-07-20, reverse-refuted-direction audit): SHORT the mover the AI just
    PASSed, $20/10x, 15% stop, 1d hold, no trail — the exact geometry the
    audit graded. Short-liquidity floor is asset-class-aware: xyz equities
    use the $250k convention, crypto uses the trigger's own
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
    # W-Y4 regime gate transferred from young_short (overnight -$6.26 confirmed
    # mover_pass_short bleeds shorting an up-sector on eq7<0 days). Record ALWAYS,
    # gate the LIVE leg only, fail-closed. Both sides grade forward via
    # --meta regime_gate=pass|fail so the transfer VALIDATES for this book itself.
    eq7 = _equity_index_7d()
    gate_on = bool(live_cfg.get("regime_gate_eq7", True))
    gate_pass = (eq7 is not None and eq7 > 0)
    live = (bool(live_cfg.get("enabled", False))
            and not bool(live_cfg.get("shadow_only", True))
            and execute_fn is not None
            and (gate_pass or not gate_on))          # regime gate (fail-closed)
    shadow_ledger.record("mover_pass_short", coin=coin, side="short",
                         signal_bar_t=(now_ms // 3_600_000) * 3_600_000,
                         entry_ref_px=px, horizon_days=1.0, stop_pct=15.0,
                         meta={"confidence": float(analysis.get("confidence") or 0),
                               "move_pct": round(move, 2),
                               "macro_regime": _macro_regime(coin),
                               "eq_idx_7d": round(eq7, 5) if eq7 is not None else None,
                               "regime_gate": "pass" if gate_pass else "fail",
                               "shadow": not live})
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
    # W-Y4 regime gate (VALIDATED, mc_p 0.0005): the young short pays +5.56%/ep only
    # when the equity index is UP over the prior 7d (eq7>0); on down/unknown days it
    # bleeds -3.3%/ep. Record on EVERY day (both sides grade forward via
    # `--meta regime_gate=pass|fail`); gate ONLY the live leg, fail-closed.
    eq7 = _equity_index_7d()
    gate_on = bool(live_cfg.get("regime_gate_eq7", True))
    gate_pass = (eq7 is not None and eq7 > 0)
    live = (bool(live_cfg.get("enabled", False))
            and not bool(live_cfg.get("shadow_only", True))
            and execute_fn is not None
            and ":" in coin                        # evidence boundary: xyz equities only
            and (gate_pass or not gate_on))        # regime gate (fail-closed)
    shadow_ledger.record("young_mover_short", coin=coin, side="short",
                         signal_bar_t=(now_ms // 3_600_000) * 3_600_000,
                         entry_ref_px=px, horizon_days=1.0,
                         stop_pct=float(live_cfg.get("stop_pct", 6.0)),
                         meta={"listing_days": days, "equity": ":" in coin,
                               "macro_regime": _macro_regime(coin),
                               "eq_idx_7d": round(eq7, 5) if eq7 is not None else None,
                               "regime_gate": "pass" if gate_pass else "fail",
                               "shadow": not live})
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


