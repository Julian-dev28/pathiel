"""Agent trigger configuration: trigger weights, thresholds, and scan settings."""

from __future__ import annotations

from typing import Any, Dict


TRIGGER_CONFIG: Dict[str, Any] = {
    "weights": {
        # RE-WEIGHTED 2026-06-02 to MEASURED MARGINAL LIFT (fired vs not-fired ROE,
        # n=497 trades). Prior weights were inverted: the 1h slow-burn signals carried
        # the heaviest weight (0.60/0.55/0.40) but had ~0/negative lift, while
        # trendStrength (the BEST signal, +2.08% lift) was only 0.10. Weights now
        # track lift; net-negative triggers (trendFlip1h -2.10%, rangeCompression
        # -3.08%) are ZEROED out of scoring.
        "trendStrength": 0.55,    # lift +2.08% (was 0.10) — strongest edge
        "pctMoveSpike": 0.40,     # lift +1.49%
        "breakout": 0.30,         # lift +1.29%
        "volumeSpike": 0.25,      # lift +1.05%
        "momentumBurst": 0.20,    # lift +0.77% (n=9, kept modest)
        "volumeBuildup1h": 0.15,  # lift +0.41% (was 0.60 — overweighted)
        "higherLows1h": 0.0,      # lift -0.51% — removed
        "trendFlip1h": 0.0,       # lift -2.10% — removed (net loser)
        "rangeCompression": 0.0,  # lift -3.08% — removed (worst)
        # Symmetric directional SURFACING triggers — weight 0 so they don't touch
        # the composite denominator (no gate recalibration). They surface trending
        # coins via the bypass in perception, not via score. Removes the long-bias
        # in surfacing so down-movers reach research and can be shorted.
        "uptrendMomentum": 0.0,
        "downtrendMomentum": 0.0,
    },
    "thresholds": {
        "sigmaThreshold": 2.0,
        "trendMomentumLookback": 72,  # 5m bars (~6h) for sustained up/down trend surfacing
        "trendMomentumPct": 5.0,      # min |%| move over ~6h to surface (5%: 3.0 over-surfaced — 22 triggers/scan, ~4.5x AI cost, flooded longs)
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
        "minCompositeScore": 54,  # recalibrated for new weights: P230 zeroed 3 triggers -> denom 2.85->1.85 -> scores ~1.54x. 54 preserves the old-35 selectivity (35*1.54). Without this the gate silently loosened.
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
