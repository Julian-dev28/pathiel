"""Agent trigger configuration: trigger weights, thresholds, and scan settings."""

from __future__ import annotations

from typing import Any, Dict


TRIGGER_CONFIG: Dict[str, Any] = {
    "weights": {
        "pctMoveSpike": 0.35,
        "volumeSpike": 0.25,
        "breakout": 0.20,
        "rangeCompression": 0.10,
        "trendStrength": 0.10,
        "momentumBurst": 0.30,
    },
    "thresholds": {
        "sigmaThreshold": 2.0,
        "breakoutLookback": 48,
        "bbLength": 20,
        "bbStdDev": 2,
        "adxPeriod": 14,
        "momentumLookback": 2,   # bars in the momentum_burst window (5m bars -> 10 min)
        "momentumPct": 4.0,      # min % move over that window to fire momentum_burst
    },
    "scan": {
        # Normalized over all weights: ~10=single trigger, ~30=2 triggers, ~60=3+ strong
        "minCompositeScore": 20,
        "candleInterval": "5m",
        "candleCount": 100,
        # Must stay below the scan interval, or scans re-read stale cached candles.
        "cacheTtlMs": 50_000,
    },
}


def get_config() -> Dict[str, Any]:
    """Return the default trigger configuration."""
    return TRIGGER_CONFIG
