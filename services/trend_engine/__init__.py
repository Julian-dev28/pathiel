"""trend_engine — deterministic trend reads across three lanes.

Lane HL         : Hyperliquid perps, 7d+ trend per coin + market regime + next-week forecast.

Everything in this package is PURE MATH over data passed in, except the thin
fetch layer in each lane module (`*_scan` / `*_read` functions). No orders, no
state writes, no capital. The dashboard `/trends` tab is the only consumer.

The forecast is a TREND-EXTRAPOLATION BASELINE, not a validated edge. Its
honest hit rate ships with it: `python -m services.trend_engine.run --backtest`
walks it forward and scores it against a coin-flip and a random-walk null.
"""

__all__ = ["metrics", "hl_trends", "forecast", "flags", "ai"]
