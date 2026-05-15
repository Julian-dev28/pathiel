"""Agent trigger configuration.

Translation of lib/agent/config.ts — TriggerConfig interface and DEFAULT_CONFIG.
"""

from __future__ import annotations

from typing import Any, Dict


TRIGGER_CONFIG: Dict[str, Any] = {
    "weights": {
        "pctMoveSpike": 0.35,
        "volumeSpike": 0.25,
        "breakout": 0.20,
        "rangeCompression": 0.10,
        "trendStrength": 0.10,
    },
    "thresholds": {
        "sigmaThreshold": 3,
        "breakoutLookback": 48,
        "bbLength": 20,
        "bbStdDev": 2,
        "adxPeriod": 14,
    },
    "scan": {
        # Normalized over all weights: ~10=single trigger, ~30=2 triggers, ~60=3+ strong
        "minCompositeScore": 20,
        "maxConcurrency": 8,
        "candleInterval": "5m",
        "candleCount": 100,
        "cacheTtlMs": 300_000,
    },
}


def get_config() -> Dict[str, Any]:
    """Return the default trigger configuration."""
    return TRIGGER_CONFIG
