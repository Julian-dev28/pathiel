export interface TriggerConfig {
  weights: {
    pctMoveSpike: number;
    volumeSpike: number;
    breakout: number;
    rangeCompression: number;
    trendStrength: number;
  };
  thresholds: {
    sigmaThreshold: number;
    breakoutLookback: number;
    bbLength: number;
    bbStdDev: number;
    adxPeriod: number;
  };
  scan: {
    minCompositeScore: number;
    maxConcurrency: number;
    candleInterval: string;
    candleCount: number;
    cacheTtlMs: number;
  };
}

export const DEFAULT_CONFIG: TriggerConfig = {
  weights: {
    pctMoveSpike: 0.35,
    volumeSpike: 0.25,
    breakout: 0.20,
    rangeCompression: 0.10,
    trendStrength: 0.10,
  },
  thresholds: {
    sigmaThreshold: 3,
    breakoutLookback: 48,
    bbLength: 20,
    bbStdDev: 2,
    adxPeriod: 14,
  },
  scan: {
    minCompositeScore: 20,  // normalized over all weights: ~10=single trigger, ~30=2 triggers, ~60=3+ strong
    maxConcurrency: 8,
    candleInterval: '5m',
    candleCount: 100,
    cacheTtlMs: 300_000,
  },
};
