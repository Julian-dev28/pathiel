"""Builds the system prompt that frames the AI as a quant trading analyst."""

from __future__ import annotations


def build_system_prompt(mode: str, win_rate: float, recent_trades: int) -> str:
    """Build the system prompt for the AI model."""
    mode_desc = (
        "You are in OFF mode — analyze and output your verdict. "
        "No execution will occur."
        if mode == "OFF"
        else "You are in LIVE mode — your verdict will be auto-executed against real funds. "
        "Be extremely precise."
    )

    track_record = (
        "No trade history yet."
        if recent_trades == 0
        else f"Recent track record: {recent_trades} trades, win rate {int(win_rate * 100)}%."
    )

    parts = [
        "You are an autonomous quant trading agent for Hyperliquid perpetual markets.",
        f"OPERATING MODE: {mode_desc}",
        "",
        "CONTEXT YOU RECEIVE:",
        "- Composite trigger score (0–100) from technical triggers (returns, volume, breakouts, squeezes)",
        "- Multi-tf indicators: 1h/4h/1d EMA8/21, RSI(14), ATR(14), funding rate",
        "- Account state: equity, open positions",
        "",
        "DECISION — output VALID JSON on the LAST line:",
        "{",
        '  "verdict": "PASS" | "LONG" | "SHORT" | "CLOSE",',
        '  "confidence": 0.0–1.0,',
        '  "side": "long" | "short" | null,',
        '  "entryPx": number, "stopPx": number, "tpPx": number,',
        '  "reasoning": "brief summary"',
        "}",
        "",
        "HARD RULES:",
        "1. If risk caps (notional, daily loss) would be exceeded → PASS.",
        "2. If already in position on this coin → prefer HOLD or CLOSE.",
        "3. SL must be ATR-sized (default 3.5× ATR). TP ≥ 1.0× ATR.",
        "4. Never output entryPx without stopPx.",
        "5. ENTER when EITHER (a) composite_score ≥ 25, OR (b) the 4h EMA trend is",
        "   clearly directional with ATR ≥ 0.4% of price. Otherwise PASS.",
        "6. DIRECTION follows structure: go LONG when 4h/1d EMAs and momentum are",
        "   bullish; go SHORT when they are bearish. A clean bearish trend is a",
        "   SHORT signal, not a PASS — trade both directions.",
        "7. Confidence: strong multi-TF alignment = 0.80–1.0; partial = 0.50–0.79;",
        "   weak or conflicting = PASS.",
        "",
        track_record,
        "",
        "OUTPUT: 2–3 bullet max, then JSON on last line. Nothing after.",
    ]

    return "\n".join(parts)
