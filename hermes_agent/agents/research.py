"""Deep-analysis pipeline: perception -> multi-TF indicators -> AI verdict -> persist.

Translation of lib/agent/research.ts.
Orchestrates the full AI research flow: fetch candles on multiple timeframes,
compute indicators, call the LLM, parse verdict, and persist results.

All functions are SYNC — no async/await needed.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

from hermes_agent.agents.config_store import read_agent_config
from hermes_agent.agents.memory import memory
from hermes_agent.agents.system_prompt import build_system_prompt
from hermes_agent.client.hl_client import HL_API, fetch_hl_candles, fetch_account_state
from hermes_agent.indicators.math import ema, atr, rsi, adx

logger = logging.getLogger(__name__)


def _compute_indicators(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute EMA8/21, RSI, ATR, ADX for a set of candles."""
    # Handle both dict and Candle objects
    def _get(cl, key):
        if isinstance(cl, dict):
            return cl.get(key, 0)
        return getattr(cl, key, 0)
    
    closes = [_get(c, "c") for c in candles]
    ema8_arr = ema(closes, 8)
    ema21_arr = ema(closes, 21)

    last_ema8 = ema8_arr[-1]
    last_ema21 = ema21_arr[-1]

    slope_up = None
    if math.isfinite(last_ema8) and len(ema8_arr) >= 3:
        slope_up = last_ema8 > ema8_arr[-3]

    rsi_arr = rsi(candles, 14)
    atr_arr = atr(candles, 14)
    adx_arr = adx(candles, 14)

    return {
        "ema8": last_ema8 if math.isfinite(last_ema8) else None,
        "ema21": last_ema21 if math.isfinite(last_ema21) else None,
        "slope_up": slope_up,
        "rsi14": rsi_arr[-1] if math.isfinite(rsi_arr[-1]) else None,
        "atr14": atr_arr[-1] if math.isfinite(atr_arr[-1]) else None,
        "adx14": adx_arr[-1] if math.isfinite(adx_arr[-1]) else None,
        "last_close": closes[-1] if closes else 0,
        "last_time": candles[-1]["t"] if candles else 0,
    }


def _fetch_funding_rate(coin: str) -> str:
    """Fetch latest funding rate for a coin."""
    try:
        from hermes_agent.client.hl_client import _make_info
        info = _make_info()
        start_time = int(time.time() * 1000) - 86_400_000
        raw = info.funding_history(coin, start_time)
        if isinstance(raw, list) and len(raw) > 0:
            r = float(raw[-1].get("fundingRate", "0"))
            if math.isfinite(r):
                return f"{r * 100:.4f}%/hr"
    except Exception:
        pass
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
    """Build the user message passed to the LLM.

    Translation of buildUserMessage() from lib/agent/research.ts.
    """
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
            f"{p['coin']} {p['side']} ${p.get('sizeUsd', 0):.0f}"
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
    """Call the OpenRouter LLM API.

    Translation of callAI() from lib/agent/research.ts.
    Uses sync httpx in a new event loop for simplicity.
    """
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    model = os.environ.get("OPENROUTER_MODEL", "qwen/qwen3.6-35b-a3b")

    import logging
    _logger = logging.getLogger(__name__)
    _logger.warning(f"[research] _call_ai: key={'set' if openrouter_key else 'NOT SET'}, model={model}")

    if not openrouter_key:
        _logger.warning("[research] OPENROUTER_API_KEY not set — returning empty response")
        return ""

    # Run async httpx in a new event loop
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_async_do_call(openrouter_key, model, system_prompt, user_message))
        _logger.warning(f"[research] _call_ai result length: {len(result) if result else 0}")
        return result
    finally:
        loop.close()


import asyncio


async def _async_do_call(
    openrouter_key: str,
    model: str,
    system_prompt: str,
    user_message: str,
) -> str:
    """Async version of _call_ai."""
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
    """Parse the AI response and extract the structured verdict.

    Translation of parseVerdict() from lib/agent/research.ts.
    Looks for JSON on the last line, then falls back to regex matching.
    """
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
    """Full AI research pipeline for a perception.

    Translation of research() from lib/agent/research.ts.
    Returns an AgentAnalysis dict.

    NOTE: This is SYNC. All fetch calls are synchronous.
    """
    try:
        # All fetch calls are sync — no await
        c1h = fetch_hl_candles(coin, "1h", 100)
        c4h = fetch_hl_candles(coin, "4h", 100)
        c1d = fetch_hl_candles(coin, "1d", 60)
        
        # Convert Candle objects to dicts (Candle objects don't support subscript)
        def _candle_to_dict(c):
            if isinstance(c, dict):
                return c
            try:
                return {"t": c.t, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "v": c.v}
            except Exception as e:
                logger.error(f"[research] Failed to convert candle: {e}, type={type(c)}, candle={c}")
                raise
        
        logger.info(f"[research] Converting {len(c1h)} 1h candles, first type: {type(c1h[0]) if c1h else 'empty'}")
        c1h = [_candle_to_dict(c) for c in c1h]
        logger.info(f"[research] After conversion, first 1h candle type: {type(c1h[0]) if c1h else 'empty'}")
        c4h = [_candle_to_dict(c) for c in c4h]
        c1d = [_candle_to_dict(c) for c in c1d]
        
        funding_raw = _fetch_funding_rate(coin)

        if len(c4h) < 30:
            logger.warning(f"[research] thin 4h history for {coin}: only {len(c4h)} candles")

        tf1h = _compute_indicators(c1h)
        tf4h = _compute_indicators(c4h)
        tf1d = _compute_indicators(c1d)

        config = read_agent_config()
        mode = str(config.get("mode", "OFF"))

        # Fetch equity + positions (sync)
        equity = 0.0
        open_positions = []
        user = os.environ.get("HYPERLIQUID_MASTER_ADDRESS") or os.environ.get("HYPERLIQUID_WALLET_ADDRESS", "")

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
        system_prompt = build_system_prompt(mode, wr.get("rate", 0), wr.get("total", 0))
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

    except Exception as err:
        import traceback
        msg = f"{err}\n{traceback.format_exc()}"
        # Write full traceback to file for debugging
        with open('/tmp/hermes_research_error.log', 'w') as f:
            f.write(msg)
        logger.error(f"[research] FAILED for {coin}: {err}")
        fallback = {
            "id": str(uuid.uuid4()),
            "perception_id": "unknown",
            "coin": coin,
            "verdict": "PASS",
            "confidence": 0,
            "side": None,
            "entry_px": perception.get("mid", 0),
            "stop_px": 0,
            "tp_px": 0,
            "reasoning": f"Research failed: {msg}",
            "created_at": int(time.time() * 1000),
        }
        memory.record_analysis(fallback)
        return fallback
