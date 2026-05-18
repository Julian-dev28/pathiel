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

from hermes_agent.agents.config_store import read_agent_config
from hermes_agent.agents.memory import memory
from hermes_agent.agents.system_prompt import build_system_prompt
from hermes_agent.client.hl_client import (
    fetch_account_state,
    fetch_funding_history,
    fetch_hl_candles,
    resolve_user_address,
)
from hermes_agent.indicators.math import adx, atr, candle_val, ema, rsi
from hermes_agent.models.types import Candle

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


def _build_user_message(
    coin: str,
    perception: Dict[str, Any],
    tf1h: Dict[str, Any],
    tf4h: Dict[str, Any],
    tf1d: Dict[str, Any],
    funding_rate: str,
    equity: float,
    open_positions: List[Dict[str, Any]],
    mode: str,
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

    def _indicator_block(label: str, snap: Dict[str, Any]) -> str:
        parts = []
        if snap.get("ema8") is not None and snap.get("ema21") is not None:
            direction = "bullish" if snap["ema8"] > snap["ema21"] else "bearish"
            parts.append(
                f"EMA8={snap['ema8']:.4f}, EMA21={snap['ema21']:.4f}, {direction}"
            )
        if snap.get("slope_up") is not None:
            parts.append(f"EMA8 slope: {'rising' if snap['slope_up'] else 'falling'}")
        if snap.get("rsi14") is not None:
            parts.append(f"RSI(14)={snap['rsi14']:.1f}")
        if snap.get("atr14") is not None:
            parts.append(f"ATR(14)={snap['atr14']:.4f}")
        if snap.get("adx14") is not None:
            parts.append(f"ADX(14)={snap['adx14']:.1f}")
        parts.append(f"last close={snap.get('last_close', 0):.4f}")
        return f"{label}: {' | '.join(parts)}"

    position_block = (
        "Open positions: " + ", ".join(
            f"{p['coin']} {p['side']} ${p.get('size_usd', 0):.0f}"
            for p in open_positions
        )
        if open_positions
        else "Open positions: none"
    )

    return "\n".join([
        f"Candidate: {coin} (HL {perception.get('type', 'perp')}-PERP)",
        f"Current mid: ${perception.get('mid', 0):.4f}",
        f"Perception score: {perception.get('composite_score', 0)}/100",
        f"Fired triggers: {trigger_summary}",
        "",
        "Market context (multi-timeframe):",
        _indicator_block("1h", tf1h),
        _indicator_block("4h", tf4h),
        _indicator_block("1d", tf1d),
        "",
        f"Funding rate (latest): {funding_rate}",
        f"Equity: ${equity:.2f}",
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

    if len(c4h) < 30:
        logger.warning(f"[research] thin 4h history for {coin}: only {len(c4h)} candles")

    tf1h = _compute_indicators(c1h)
    tf4h = _compute_indicators(c4h)
    tf1d = _compute_indicators(c1d)

    config = read_agent_config()
    mode = str(config.get("mode", "OFF"))

    equity = 0.0
    open_positions: List[Dict[str, Any]] = []
    user = resolve_user_address()

    if user:
        state = fetch_account_state(user)
        equity = float(state.get("equity", "0"))
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
        funding_raw, equity, open_positions, mode,
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
        "news_context": "no news",
        "created_at": int(time.time() * 1000),
    }

    memory.record_analysis(analysis)
    return analysis
