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


def _fetch_news(coin: str) -> str:
    """Recent news headlines for a coin via the Brave Search API.

    Returns a compact ' | '-joined headline string, or 'no news' when no
    BRAVE_API_KEY is set or the request fails — news is a supplementary
    signal, so a fetch failure degrades gracefully and never blocks research.
    """
    key = os.environ.get("BRAVE_API_KEY", "")
    if not key:
        return "no news"
    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/news/search",
            params={"q": f"{coin} crypto", "count": 5},
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

    # 1h-structure block — accumulation patterns the multi-tf indicator
    # blocks miss. Surfacing these explicitly because the LLM was reading
    # 4h/1d bearish EMA alignment and shorting coins whose 1h was actually
    # in a higher-lows / volume-buildup / EMA-flip setup.
    _slow_burn_names = {"volumeBuildup1h", "trendFlip1h", "higherLows1h"}
    slow_burn_hits = [
        t for t in perception.get("triggers", [])
        if t.get("name") in _slow_burn_names and t.get("fired")
    ]
    if slow_burn_hits:
        structure_lines = ["1h structure signals (accumulation / breakout setup):"]
        for t in slow_burn_hits:
            structure_lines.append(f"  - {t['name']}: {t['reason']}")
        structure_lines.append(
            "Weigh these against the 4h/1d trend. A clean 1h accumulation pattern "
            "(higher lows, vol surge, EMA flip) often precedes an UP move even when "
            "longer timeframes are still bearish — don't reflexively SHORT into "
            "developing 1h strength."
        )
        structure_block = "\n".join(structure_lines)
    else:
        structure_block = "1h structure signals: none fired (no accumulation / breakout setup detected)"

    # Whale-accumulation block: oi_funding_anomaly flag (deep-negative funding +
    # flat price + high OI = whales loading while retail shorts). When present
    # this is a strong LONG-bias signal — don't fight it.
    whale = perception.get("whale_signal")
    if whale:
        whale_block = (
            "Whale accumulation flag (oi_funding_anomaly):\n"
            f"  - funding rate: {whale.get('funding_rate', 0):.6f} (deeply negative = retail shorting)\n"
            f"  - 24h price change: {whale.get('price_24h_change_pct', 0):+.2f}% (relatively flat)\n"
            f"  - open interest: ${whale.get('oi', 0):,.0f}\n"
            f"  - confidence: {whale.get('confidence', 0):.2f}\n"
            "Interpretation: smart money is building long positions while retail pays them "
            "to short. When the shorts cover, price tends to squeeze UP. Bias LONG unless "
            "structure is overwhelmingly bearish."
        )
    else:
        whale_block = "Whale accumulation flag: not flagged for this coin"

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

    position_block = (
        "Open positions: " + ", ".join(
            f"{p['coin']} {p['side']} ${p.get('size_usd', 0):.0f}"
            for p in open_positions
        )
        if open_positions
        else "Open positions: none"
    )

    # For HIP-3 candidates the relevant collateral is the target dex's
    # balance, not the aggregated total — surface both so the LLM sizes
    # the risk caps against the right pool.
    equity_lines = [f"Equity (aggregated, perp + all HIP-3 dexes): ${equity:.2f}"]
    if dex_equity:
        main_eq = float(dex_equity.get("", 0) or 0)
        hip3_breakdown = ", ".join(
            f"{d}=${float(v):.2f}" for d, v in dex_equity.items()
            if d and float(v or 0) > 0.5
        )
        equity_lines.append(f"  main HL clearinghouse: ${main_eq:.2f}")
        if hip3_breakdown:
            equity_lines.append(f"  HIP-3 dex equity: {hip3_breakdown}")
        if ":" in coin:
            trade_dex = coin.split(":", 1)[0]
            trade_dex_eq = float(dex_equity.get(trade_dex, 0) or 0)
            equity_lines.append(
                f"  THIS TRADE lands on dex '{trade_dex}' which holds "
                f"${trade_dex_eq:.2f} — that's the relevant capital, not the "
                f"main-HL balance."
            )

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
        structure_block,
        "",
        whale_block,
        "",
        f"Funding rate (latest): {funding_rate}",
        f"Recent news: {news}",
        *equity_lines,
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
    """Async POST to the OpenRouter chat-completions endpoint."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "max_tokens": 1024,
                "temperature": 0.1,
            },
            headers={"Authorization": f"Bearer {openrouter_key}"},
        )
        if resp.is_success:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
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
            reasoning = parsed.get("reasoning", ai_text[:500])
        except json.JSONDecodeError:
            first_line = lines[0] if lines else ""
            if re.search(r"LONG", first_line, re.IGNORECASE):
                verdict = "LONG"
            elif re.search(r"SHORT", first_line, re.IGNORECASE):
                verdict = "SHORT"
            elif re.search(r"CLOSE", first_line, re.IGNORECASE):
                verdict = "CLOSE"

    return {
        "verdict": verdict,
        "confidence": confidence,
        "side": side,
        "entry_px": entry_px,
        "stop_px": stop_px,
        "tp_px": tp_px,
        "reasoning": reasoning,
    }


def research(coin: str, perception: Dict[str, Any]) -> Dict[str, Any]:
    """Full AI research pipeline for a perception — returns an analysis dict."""
    c1h = fetch_hl_candles(coin, "1h", 100)
    c4h = fetch_hl_candles(coin, "4h", 100)
    c1d = fetch_hl_candles(coin, "1d", 60)

    funding_raw = _fetch_funding_rate(coin)
    news = _fetch_news(coin)

    if len(c4h) < 30:
        logger.warning(f"[research] thin 4h history for {coin}: only {len(c4h)} candles")

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
        dex_equity=dex_equity,
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
        # OI+funding accumulation signal (oi_funding_anomaly). When present,
        # the coin shows whale-loading patterns (high OI, negative funding,
        # flat price). Used as a counter-regime bypass for LONGs.
        "whale_signal": perception.get("whale_signal"),
    }

    memory.record_analysis(analysis)
    return analysis
