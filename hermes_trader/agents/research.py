"""Deep-analysis pipeline: perception -> multi-timeframe indicators -> AI verdict -> persist."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx

from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.agents.memory import memory
from hermes_trader.agents.system_prompt import build_system_prompt
from hermes_trader.client.hl_client import (
    fetch_account_state,
    fetch_funding_history,
    fetch_hl_candles,
    resolve_user_address,
)
from hermes_trader.indicators.math import adx, atr, candle_val, ema, rsi
from hermes_trader.models.types import Candle

logger = logging.getLogger(__name__)


def _compute_indicators(candles: List[Candle]) -> Dict[str, Any]:
    """Compute EMA8/21, RSI, ATR, ADX for a set of candles."""
    if not candles:
        return {
            "ema8": None, "ema21": None, "slope_up": None,
            "rsi14": None, "atr14": None, "adx14": None,
            "last_close": 0, "last_time": 0,
        }

    closes = [candle_val(c, "c") for c in candles]

    if len(closes) < 21:
        return {
            "ema8": None, "ema21": None, "slope_up": None,
            "rsi14": None, "atr14": None, "adx14": None,
            "last_close": closes[-1],
            "last_time": candles[-1].t,
        }

    ema8_arr = ema(closes, 8)
    ema21_arr = ema(closes, 21)

    last_ema8 = ema8_arr[-1] if ema8_arr else None
    last_ema21 = ema21_arr[-1] if ema21_arr else None

    slope_up = None
    if last_ema8 is not None and len(ema8_arr) >= 3:
        slope_up = last_ema8 > ema8_arr[-3]

    rsi_arr = rsi(candles, 14)
    atr_arr = atr(candles, 14)
    adx_arr = adx(candles, 14)

    return {
        "ema8": last_ema8 if last_ema8 is not None and math.isfinite(last_ema8) else None,
        "ema21": last_ema21 if last_ema21 is not None and math.isfinite(last_ema21) else None,
        "slope_up": slope_up,
        "rsi14": rsi_arr[-1] if rsi_arr and math.isfinite(rsi_arr[-1]) else None,
        "atr14": atr_arr[-1] if atr_arr and math.isfinite(atr_arr[-1]) else None,
        "adx14": adx_arr[-1] if adx_arr and math.isfinite(adx_arr[-1]) else None,
        "last_close": closes[-1],
        "last_time": candles[-1].t,
    }


def _fetch_funding_rate(coin: str) -> str:
    """Latest hourly funding rate for a coin, or 'N/A' if unavailable."""
    start_time = int(time.time() * 1000) - 86_400_000
    history = fetch_funding_history(coin, start_time)
    if history:
        rate = float(history[-1].get("fundingRate", "0"))
        if math.isfinite(rate):
            return f"{rate * 100:.4f}%/hr"
    return "N/A"


# Only surface news from the last N days. Without this, Brave returned
# year-old articles (e.g. AIXBT's 2025 hack) that then tripped the binary-news
# gate on a fresh 2026 trade. The gate reasons about *imminent* event risk, so
# stale headlines are noise — both to the gate and to the LLM prompt.
NEWS_FRESHNESS_DAYS = 2


def _fetch_news(coin: str) -> str:
    """Recent (last NEWS_FRESHNESS_DAYS) news headlines for a coin via the
    Brave Search API.

    Returns a compact ' | '-joined headline string, or 'no news' when no
    BRAVE_API_KEY is set or the request fails — news is a supplementary
    signal, so a fetch failure degrades gracefully and never blocks research.
    """
    key = os.environ.get("BRAVE_API_KEY", "")
    if not key:
        return "no news"
    # Brave `freshness` takes a YYYY-MM-DDtoYYYY-MM-DD range; a 2-day window
    # approximates "within 48h" (the closest the API offers to an hour-precise
    # bound without per-result age filtering).
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=NEWS_FRESHNESS_DAYS)
    freshness = f"{start.isoformat()}to{today.isoformat()}"
    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/news/search",
            params={"q": f"{coin} crypto", "count": 5, "freshness": freshness},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            timeout=10.0,
        )
        if not resp.is_success:
            return "no news"
        results = resp.json().get("results", []) or []
        headlines = [str(r.get("title", "")).strip() for r in results if r.get("title")]
        return " | ".join(headlines[:5]) if headlines else "no news"
    except Exception:
        return "no news"


def _signals_block(coin: str) -> str:
    """Free positioning signals (GEX / aggTrades whale / FINRA short-vol / news
    catalyst) formatted for the AI prompt. The executor already uses these, but
    the AI was BLIND to them — so it kept PASSing rippers it couldn't see the
    bullish context for. Surfacing them here lets the AI's own verdict reflect
    them. Wrapped so any signal outage never breaks research."""
    is_hip3 = ":" in (coin or "")
    lines: List[str] = []
    try:
        if is_hip3:
            from hermes_trader.agents.options_gex import gex_signal_cached
            g = gex_signal_cached(coin)
            if g:
                lines.append(
                    f"  - Dealer gamma (GEX): {g.regime}; call wall {g.call_wall} (overhead "
                    f"resistance / ride target), put wall {g.put_wall} (support), spot {g.spot:g}. "
                    + ("Negative gamma = squeeze-prone, lets moves RUN."
                       if g.regime == "trend_short_gamma"
                       else "Long gamma = pins/mean-reverts near the walls.")
                )
            from hermes_trader.agents.short_volume import short_volume_signal
            sv = short_volume_signal(coin)
            if sv:
                lines.append(
                    f"  - Short volume (FINRA): {sv.ratio * 100:.0f}% ({sv.regime}, {sv.trend})."
                    + (" Crowded short = SQUEEZE FUEL for a long."
                       if sv.regime == "crowded_short_squeeze_fuel" else "")
                )
        else:
            from hermes_trader.agents.crypto_whale import crypto_whale_signal
            w = crypto_whale_signal(coin, window_minutes=15)
            if w and w.whale_n:
                lines.append(
                    f"  - Whale order-flow (Binance aggTrades, 15m): {w.bias}, net "
                    f"${w.net_usd:+,.0f} across {w.whale_n} large prints."
                    + (" Large buyers stepping in (bullish)." if w.bias == "whale_buying"
                       else " Large sellers hitting bids (bearish)." if w.bias == "whale_selling"
                       else "")
                )
        from hermes_trader.agents.news_catalyst import catalyst_scan
        base = coin.split(":", 1)[1] if ":" in coin else coin
        n = catalyst_scan(base, timespan="1h")
        if n and (n.breaking or n.surge_x >= 1.5):
            top = n.headlines[0].title[:90] if n.headlines else ""
            lines.append(
                f"  - News catalyst: {'BREAKING' if n.breaking else f'elevated ({n.surge_x}x coverage)'}"
                f" — {top!r}"
            )
    except Exception as e:
        logger.debug(f"[research] signals block failed for {coin}: {e}")
    if not lines:
        return "Positioning signals (GEX/whale/short-vol/news): none flagged"
    return ("Positioning signals (free data — weigh these in your verdict):\n"
            + "\n".join(lines))


def _build_user_message(
    coin: str,
    perception: Dict[str, Any],
    tf1h: Dict[str, Any],
    tf4h: Dict[str, Any],
    tf1d: Dict[str, Any],
    funding_rate: str,
    news: str,
    equity: float,
    open_positions: List[Dict[str, Any]],
    mode: str,
    dex_equity: Dict[str, float] | None = None,
    recent_candles: List[Candle] | None = None,
) -> str:
    """Build the user message passed to the LLM."""
    trigger_summary = (
        ", ".join(
            f"{t['name']}: {t['reason']}"
            for t in perception.get("triggers", [])
            if t.get("fired")
        )
        or "no triggers fired"
    )

    # 1h-structure block — accumulation/exhaustion patterns the multi-tf
    # indicator blocks miss. Surfaced as an ENTRY-TIMING signal to be combined
    # WITH the 4h/1d trend, not as a reason to trade against it: in an uptrend a
    # 1h accumulation times a long pullback-entry; in a downtrend a 1h bounce is
    # a short entry (sell the rip), NOT a counter-trend dip-buy.
    _slow_burn_names = {"volumeBuildup1h", "trendFlip1h", "higherLows1h"}
    slow_burn_hits = [
        t for t in perception.get("triggers", [])
        if t.get("name") in _slow_burn_names and t.get("fired")
    ]
    if slow_burn_hits:
        structure_lines = ["1h structure signals (entry-timing — apply IN the 4h/1d trend direction):"]
        for t in slow_burn_hits:
            structure_lines.append(f"  - {t['name']}: {t['reason']}")
        structure_lines.append(
            "Use these to time the entry, not to pick the side. If 4h/1d are bullish, this is a "
            "long pullback-entry; if 4h/1d are bearish, a 1h pop is a SHORT entry (sell the rip) — "
            "do not buy the dip into a downtrend."
        )
        structure_block = "\n".join(structure_lines)
    else:
        structure_block = "1h structure signals: none fired (no accumulation / breakout setup detected)"

    def _fmt_px(p: float) -> str:
        """Adaptive precision so sub-cent coins (HMSTR at $0.000173 etc.) don't
        all read as '0.0002' to the LLM. Without this the AI returned identical
        entry/sl/tp on cheap coins because the prompt rounded them to the same
        4-decimal value."""
        if p == 0:
            return "0"
        ap = abs(p)
        if ap >= 1:
            return f"{p:.4f}"
        if ap >= 0.01:
            return f"{p:.5f}"
        if ap >= 0.0001:
            return f"{p:.6f}"
        return f"{p:.8f}"

    def _indicator_block(label: str, snap: Dict[str, Any]) -> str:
        parts = []
        if snap.get("ema8") is not None and snap.get("ema21") is not None:
            direction = "bullish" if snap["ema8"] > snap["ema21"] else "bearish"
            parts.append(
                f"EMA8={_fmt_px(snap['ema8'])}, EMA21={_fmt_px(snap['ema21'])}, {direction}"
            )
        if snap.get("slope_up") is not None:
            parts.append(f"EMA8 slope: {'rising' if snap['slope_up'] else 'falling'}")
        if snap.get("rsi14") is not None:
            parts.append(f"RSI(14)={snap['rsi14']:.1f}")
        if snap.get("atr14") is not None:
            parts.append(f"ATR(14)={_fmt_px(snap['atr14'])}")
        if snap.get("adx14") is not None:
            parts.append(f"ADX(14)={snap['adx14']:.1f}")
        parts.append(f"last close={_fmt_px(snap.get('last_close', 0))}")
        return f"{label}: {' | '.join(parts)}"

    # Only the coins/sides we already hold — purely so the LLM doesn't
    # double-trade a coin or can CLOSE one. Deliberately NO dollar sizes:
    # account notional/leverage must not influence the verdict (sizing and
    # every risk cap live in the execution gates, not here).
    position_block = (
        "Open positions (do not re-enter these; CLOSE only if structure flipped): "
        + ", ".join(f"{p['coin']} {p['side']}" for p in open_positions)
        if open_positions
        else "Open positions: none"
    )

    # Raw recent price action so the LLM can read candlestick/chart patterns
    # directly (shooting star, hammer, engulfing, flags) — the indicator blocks
    # above summarize away the candle bodies/wicks that patterns live in.
    def _ohlc_block(candles: List[Candle] | None, n: int = 12) -> str:
        if not candles:
            return ""
        rows = []
        for i, c in enumerate(candles[-n:]):
            idx = -(len(candles[-n:]) - i)  # ... -2, -1 (newest = last closed)
            o, h, l, cl = (candle_val(c, k) for k in ("o", "h", "l", "c"))
            rows.append(f"  [{idx:>3}] O={_fmt_px(o)} H={_fmt_px(h)} L={_fmt_px(l)} C={_fmt_px(cl)}")
        return ("Recent 1h candles (oldest→newest, last row = most recent closed bar):\n"
                + "\n".join(rows))

    ohlc_block = _ohlc_block(recent_candles)

    return "\n".join([
        f"Candidate: {coin} (HL {perception.get('type', 'perp')}-PERP)",
        f"Current mid: ${_fmt_px(perception.get('mid', 0))}",
        f"Perception score: {perception.get('composite_score', 0)}/100",
        f"Fired triggers: {trigger_summary}",
        "",
        "Market context (multi-timeframe):",
        _indicator_block("1h", tf1h),
        _indicator_block("4h", tf4h),
        _indicator_block("1d", tf1d),
        "",
        ohlc_block,
        "" if ohlc_block else "",
        structure_block,
        "",
        _signals_block(coin),
        "",
        f"Funding rate (latest): {funding_rate}",
        f"Recent news: {news}",
        position_block,
        "",
        f"Mode: {mode} — {'your verdict will execute against real funds' if mode == 'LIVE' else 'analysis only, no execution'}",
        "",
        'Respond with 3-5 bullet points of reasoning, then output your decision as VALID JSON on the very last line:',
        '{"verdict":"PASS"|"LONG"|"SHORT"|"CLOSE","confidence":0.0-1.0,"side":"long"|"short"|"null","entryPx":number,"stopPx":number,"tpPx":number,"reasoning":"brief"}',
        "Nothing after the JSON.",
    ])


def _call_ai(system_prompt: str, user_message: str) -> str:
    """Call the OpenRouter LLM API (runs the async client in a fresh event loop)."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    model = os.environ.get("OPENROUTER_MODEL", "x-ai/grok-4.3")

    if not openrouter_key:
        logger.warning("[research] OPENROUTER_API_KEY not set — returning empty response")
        return ""

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_async_do_call(openrouter_key, model, system_prompt, user_message))
    finally:
        loop.close()


async def _async_do_call(
    openrouter_key: str,
    model: str,
    system_prompt: str,
    user_message: str,
) -> str:
    """Async POST to the OpenRouter chat-completions endpoint.

    On a 402 that includes an affordability hint ("can only afford N tokens"),
    retries ONCE with max_tokens shrunk to the affordable budget. During the
    2026-06-11 credit drought the bot sat fully blind for ~12h while OpenRouter
    was offering 842 affordable tokens per call — enough for a non-truncated
    verdict on most prompts. Degraded thinking beats no thinking; if the
    shrunken reply still truncates, parse_verdict falls back to PASS exactly
    as before (no new failure mode).
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:

        async def _post(max_toks: int):
            return await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "stream": False,
                    # Output is a verdict JSON + 2-3 sentences (~150-300 visible
                    # tokens). 512 was fine for non-reasoning models, but REASONING
                    # models (qwen3.x-plus/max, etc.) emit ~1.5-2k hidden reasoning
                    # tokens that can count against max_tokens and truncate the JSON
                    # (qwen3.7-max did exactly this live). 2048 leaves room for
                    # reasoning + JSON; non-reasoning models ignore the extra.
                    "max_tokens": max_toks,
                    "temperature": 0.1,
                },
                headers={"Authorization": f"Bearer {openrouter_key}"},
            )

        try:
            initial_max_tokens = int(os.environ.get("OPENROUTER_MAX_TOKENS", "2048"))
        except (TypeError, ValueError):
            initial_max_tokens = 2048
        initial_max_tokens = max(500, min(initial_max_tokens, 4096))

        resp = await _post(initial_max_tokens)
        if resp.status_code == 402:
            # "...You requested up to N tokens, but can only afford 842..."
            m = re.search(r"can only afford (\d+)", resp.text or "")
            if m and int(m.group(1)) >= 500:
                budget = int(m.group(1)) - 50  # headroom for billing jitter
                logger.warning(
                    f"[research] 402 with affordability hint — retrying DEGRADED "
                    f"at max_tokens={budget} (add credits to restore full reasoning)"
                )
                resp = await _post(budget)

        if resp.is_success:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            logger.error("[research] LLM returned 200 but no choices — empty response")
            return ""
        # LOUD failure. A non-200 (esp. 402 Payment Required = out of OpenRouter
        # credits, or 401/429) previously returned "" silently → parse_verdict
        # defaulted every coin to PASS conf 0.0, so a billing/API outage looked
        # identical to "no setups" and the bot sat blind for hours. Make it scream.
        body = resp.text[:200] if resp.text else ""
        logger.error(
            f"[research] LLM call FAILED: HTTP {resp.status_code} — AI research is "
            f"DOWN, all verdicts will default to PASS until fixed. {body}"
        )
    return ""


def parse_verdict(
    ai_text: str,
    coin: str,
    perception: Dict[str, Any],
) -> Dict[str, Any]:
    """Parse the AI response: JSON on the last line, with a regex fallback."""
    if not ai_text:
        ai_text = ""

    verdict = "PASS"
    confidence = 0.0
    side = None
    entry_px = perception.get("mid", 0)
    stop_px = 0.0
    tp_px = 0.0
    news_risk = "none"
    reasoning = ai_text.strip()

    lines = ai_text.strip().split("\n")

    # Find JSON on the last line
    json_str = ""
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{") and "verdict" in line and line.endswith("}"):
            json_str = line
            break

    # Fallback: regex match
    if not json_str:
        match = re.search(r'\{[^{}]*"verdict"[^{}]*\}', ai_text)
        if match:
            json_str = match.group(0)

    if json_str:
        try:
            cleaned = re.sub(r'```json\s*|```', '', json_str).strip()
            parsed = json.loads(cleaned)

            raw = str(parsed.get("verdict", "")).upper()
            if raw == "LONG":
                verdict = "LONG"
            elif raw == "SHORT":
                verdict = "SHORT"
            elif raw == "CLOSE":
                verdict = "CLOSE"

            confidence = parsed.get("confidence", 0)
            side = parsed.get("side") if parsed.get("side") in ("long", "short") else None
            entry_px = parsed.get("entry_px") or parsed.get("entryPx", perception.get("mid", 0))
            stop_px = parsed.get("stop_px") or parsed.get("stopPx", 0)
            tp_px = parsed.get("tp_px") or parsed.get("tpPx", 0)
            nr = str(parsed.get("news_risk") or parsed.get("newsRisk") or "none").lower()
            news_risk = nr if nr in ("none", "positive", "negative") else "none"
            reasoning = parsed.get("reasoning", ai_text[:500])
        except json.JSONDecodeError:
            first_line = lines[0] if lines else ""
            if re.search(r"LONG", first_line, re.IGNORECASE):
                verdict = "LONG"
            elif re.search(r"SHORT", first_line, re.IGNORECASE):
                verdict = "SHORT"
            elif re.search(r"CLOSE", first_line, re.IGNORECASE):
                verdict = "CLOSE"

    # Coerce confidence to a clamped float — the LLM occasionally returns it
    # as a string ("0.8") or out of range; a string would TypeError at the
    # gate comparison (`ctx.confidence >= 0.85`) on a live trade.
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    # Derive side from verdict when the LLM omitted/nulled the side field.
    # Without this a SHORT verdict with side=None falls through to the
    # executor's `or "long"` default and executes in the WRONG direction.
    if verdict == "LONG":
        side = "long"
    elif verdict == "SHORT":
        side = "short"
    # CLOSE/PASS keep whatever side was parsed (unused downstream).

    return {
        "verdict": verdict,
        "confidence": confidence,
        "side": side,
        "entry_px": entry_px,
        "stop_px": stop_px,
        "tp_px": tp_px,
        "news_risk": news_risk,
        "reasoning": reasoning,
        # Empty ai_text = the LLM call failed (402/429/timeout) and this PASS is
        # an ERROR CODE, not an opinion. Tagged so the executor's structural/whale
        # override won't upgrade a failure-PASS into a blind LONG — on 2026-06-11
        # a 402 window let the override shotgun 8 PASS→LONG upgrades in one
        # minute, filling the book with unvetted longs that then blocked real
        # AI SHORT signals on the movers.
        "ai_down": not ai_text.strip(),
    }


def research(coin: str, perception: Dict[str, Any]) -> Dict[str, Any]:
    """Full AI research pipeline for a perception — returns an analysis dict."""
    c1h = fetch_hl_candles(coin, "1h", 100)
    c4h = fetch_hl_candles(coin, "4h", 100)
    c1d = fetch_hl_candles(coin, "1d", 60)

    funding_raw = _fetch_funding_rate(coin)
    news = _fetch_news(coin)

    # Thin-history guard: multi-timeframe TA is meaningless without enough 4h
    # bars (EMA21/ADX need history). A near-empty series produced confident-
    # looking but baseless entries (e.g. WLD entered at 0.68 conf on "0 candles"
    # then ran straight to the stop). Decline outright — PASS, no LLM call, no entry.
    if len(c4h) < 30:
        logger.warning(f"[research] thin 4h history for {coin}: only {len(c4h)} candles — PASS (skip)")
        analysis = {
            "id": str(uuid.uuid4()), "perception_id": perception.get("id", "unknown"),
            "coin": coin, "verdict": "PASS", "confidence": 0.0, "side": None,
            "entry_px": perception.get("mid", 0), "stop_px": 0.0, "tp_px": 0.0,
            "reasoning": f"insufficient 4h history ({len(c4h)} candles) for reliable multi-TF TA",
            "news_context": news, "news_risk": "none",
            "created_at": int(time.time() * 1000),
            "composite_score": float(perception.get("composite_score", 0) or 0),
            "momentum_burst_fired": False, "slow_burn_fired": False,
            "slow_burn_count": 0,
            "daily_mover_fired": any(
                t.get("name") == "dailyMover" and t.get("fired")
                for t in (perception.get("triggers") or [])
            ),
            "daily_move_pct": perception.get("daily_move_pct"),
            "daily_volume_usd": perception.get("daily_volume_usd"),
        }
        memory.record_analysis(analysis)
        return analysis

    tf1h = _compute_indicators(c1h)
    tf4h = _compute_indicators(c4h)
    tf1d = _compute_indicators(c1d)

    config = read_agent_config()
    mode = str(config.get("mode", "OFF"))

    equity = 0.0
    dex_equity: Dict[str, float] = {}
    open_positions: List[Dict[str, Any]] = []
    user = resolve_user_address()

    if user:
        # Aggregated equity so the LLM sees true capital across main + HIP-3
        # dexes when reasoning about risk caps for HIP-3 candidates.
        state = fetch_account_state(user, include_hip3=True)
        equity = float(state.get("equity", "0"))
        dex_equity = state.get("dex_equity") or {}
        memory.update_equity(equity)

        open_positions = [
            {
                "coin": p.get("position", {}).get("coin", ""),
                "side": "long" if float(p.get("position", {}).get("szi", "0")) > 0 else "short",
                "size_usd": float(p.get("position", {}).get("positionValue", "0")) or (
                    abs(float(p.get("position", {}).get("szi", "0"))) *
                    float(p.get("position", {}).get("entryPx", "0"))
                ),
            }
            for p in (state.get("asset_positions") or [])
            if float(p.get("position", {}).get("szi", "0")) != 0
        ]

    wr = memory.get_win_rate()
    system_prompt = build_system_prompt(mode, wr.get("rate", 0), int(wr.get("total", 0)))
    user_message = _build_user_message(
        coin, perception, tf1h, tf4h, tf1d,
        funding_raw, news, equity, open_positions, mode,
        dex_equity=dex_equity, recent_candles=c1h,
    )

    ai_text = _call_ai(system_prompt, user_message)
    parsed = parse_verdict(ai_text, coin, perception)

    analysis = {
        "id": str(uuid.uuid4()),
        "perception_id": perception.get("id", "unknown"),
        "coin": coin,
        "verdict": parsed["verdict"],
        "confidence": parsed["confidence"],
        "side": parsed["side"],
        "entry_px": parsed["entry_px"],
        "stop_px": parsed["stop_px"],
        "tp_px": parsed["tp_px"],
        "reasoning": parsed["reasoning"],
        "news_context": news,
        # AI's good/bad judgment of the recent news — drives the news gate
        # (only "negative" stands the trade down; an earnings beat is fine).
        "news_risk": parsed["news_risk"],
        # Failure-PASS marker — must survive this whitelist or the executor's
        # override guard never sees it (it didn't, on first deploy).
        "ai_down": bool(parsed.get("ai_down")),
        "created_at": int(time.time() * 1000),
        # Carry forward so risk gates can read own-coin signal strength.
        "composite_score": float(perception.get("composite_score", 0) or 0),
        "momentum_burst_fired": any(
            t.get("name") == "momentumBurst" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "slow_burn_fired": any(
            t.get("name") in ("volumeBuildup1h", "trendFlip1h", "higherLows1h")
            and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "slow_burn_count": sum(
            1 for t in (perception.get("triggers") or [])
            if t.get("name") in ("volumeBuildup1h", "trendFlip1h", "higherLows1h")
            and t.get("fired")
        ),
        # O'Neil-style context for the runner gate and post-trade analysis.
        "breakout_fired": any(
            t.get("name") == "breakout" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "shock_day_fired": any(
            t.get("name") == "shockDay" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "volume_spike_fired": any(
            t.get("name") == "volumeSpike" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "uptrend_momentum_fired": any(
            t.get("name") == "uptrendMomentum" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "downtrend_momentum_fired": any(
            t.get("name") == "downtrendMomentum" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "daily_mover_fired": any(
            t.get("name") == "dailyMover" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "daily_move_pct": perception.get("daily_move_pct"),
        "daily_volume_usd": perception.get("daily_volume_usd"),
    }

    memory.record_analysis(analysis)
    return analysis
