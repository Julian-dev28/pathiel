"""Agent trigger configuration: trigger weights, thresholds, and scan settings."""

from __future__ import annotations

from typing import Any, Dict


TRIGGER_CONFIG: Dict[str, Any] = {
    "weights": {
        # Fast / explosive signals (5m timeframe)
        "pctMoveSpike": 0.35,
        "volumeSpike": 0.25,
        "breakout": 0.20,
        "rangeCompression": 0.10,
        "trendStrength": 0.10,
        "momentumBurst": 0.30,
        # Slow-burn / accumulation signals (1h timeframe). Heavier weights so a
        # single one can push composite past the 50 counter-regime bypass — that
        # was the empirical gap: WLFI/ICP/AR/HMSTR-style breakouts had clean
        # 1h structure long before any 5m trigger fired.
        "volumeBuildup1h": 0.60,
        "trendFlip1h": 0.55,
        "higherLows1h": 0.40,
    },
    "thresholds": {
        "sigmaThreshold": 2.0,
        "breakoutLookback": 48,
        "bbLength": 20,
        "bbStdDev": 2,
        "adxPeriod": 14,
        "momentumLookback": 2,   # 5m bars in the momentum_burst window (-> 10 min)
        "momentumPct": 4.0,      # min % move over that window to fire momentum_burst
        "volBuildupRatio": 2.5,  # 4h vs prior 20h avg, on 1h candles
        "trendFlipBars": 3,      # EMA8/21 cross within last N 1h bars
        "higherLowsRequired": 4, # of last 6 1h bars
    },
    "scan": {
        "minCompositeScore": 20,
        "candleInterval": "5m",
        "candleCount": 100,
        "cacheTtlMs": 50_000,
        # 1h candles don't change mid-hour; cache 10min so we only refetch
        # every 10 scans, keeping the per-cycle weight budget intact.
        "cacheTtlMs1h": 600_000,
    },
}


def get_config() -> Dict[str, Any]:
    """Return the default trigger configuration."""
    return TRIGGER_CONFIG
