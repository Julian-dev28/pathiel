// Pure stateless trigger functions for the perception engine.
// Indicator math (ema, sma, atr, rsi, adx) ported verbatim from scripts/backtest.mjs.

export type Candle = { t: number; o: number; h: number; l: number; c: number; v: number };
export type TriggerHit = { name: string; score: number; reason: string; fired: boolean };

// ── Indicator helpers (ported verbatim from scripts/backtest.mjs) ──────────────

export function ema(values: number[], period: number): number[] {
  const k = 2 / (period + 1);
  const out = new Array<number>(values.length).fill(NaN);
  if (!values.length) return out;
  let e = values[0];
  out[0] = e;
  for (let i = 1; i < values.length; i++) {
    e = values[i] * k + e * (1 - k);
    out[i] = e;
  }
  return out;
}

export function sma(values: number[], period: number): number[] {
  const out = new Array<number>(values.length).fill(NaN);
  let acc = 0;
  for (let i = 0; i < values.length; i++) {
    acc += values[i];
    if (i >= period) acc -= values[i - period];
    if (i >= period - 1) out[i] = acc / period;
  }
  return out;
}

export function atr(candles: Candle[], period = 14): number[] {
  const tr = new Array<number>(candles.length).fill(0);
  for (let i = 1; i < candles.length; i++) {
    const h = candles[i].h, l = candles[i].l, pc = candles[i - 1].c;
    tr[i] = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
  }
  const out = new Array<number>(candles.length).fill(NaN);
  if (candles.length <= period) return out;
  let acc = 0;
  for (let i = 1; i <= period; i++) acc += tr[i];
  out[period] = acc / period;
  for (let i = period + 1; i < candles.length; i++) {
    out[i] = (out[i - 1] * (period - 1) + tr[i]) / period;
  }
  return out;
}

export function rsi(candles: Candle[], period = 14): number[] {
  const out = new Array<number>(candles.length).fill(NaN);
  if (candles.length <= period) return out;
  let g = 0, l = 0;
  for (let i = 1; i <= period; i++) {
    const d = candles[i].c - candles[i - 1].c;
    if (d >= 0) g += d; else l -= d;
  }
  let avgG = g / period, avgL = l / period;
  out[period] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL);
  for (let i = period + 1; i < candles.length; i++) {
    const d = candles[i].c - candles[i - 1].c;
    avgG = (avgG * (period - 1) + (d > 0 ? d : 0)) / period;
    avgL = (avgL * (period - 1) + (d < 0 ? -d : 0)) / period;
    out[i] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL);
  }
  return out;
}

export function adx(candles: Candle[], period = 14): number[] {
  const n = candles.length;
  const out = new Array<number>(n).fill(NaN);
  if (n <= period * 2) return out;
  const tr = new Array<number>(n).fill(0);
  const pDM = new Array<number>(n).fill(0);
  const mDM = new Array<number>(n).fill(0);
  for (let i = 1; i < n; i++) {
    const h = candles[i].h, l = candles[i].l, pc = candles[i - 1].c, ph = candles[i - 1].h, pl = candles[i - 1].l;
    tr[i] = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
    const up = h - ph, dn = pl - l;
    pDM[i] = (up > dn && up > 0) ? up : 0;
    mDM[i] = (dn > up && dn > 0) ? dn : 0;
  }
  let trS = 0, pS = 0, mS = 0;
  for (let i = 1; i <= period; i++) { trS += tr[i]; pS += pDM[i]; mS += mDM[i]; }
  const dx = new Array<number>(n).fill(NaN);
  const computeDX = () => {
    const pdi = trS === 0 ? 0 : 100 * pS / trS;
    const mdi = trS === 0 ? 0 : 100 * mS / trS;
    const sum = pdi + mdi;
    return sum === 0 ? 0 : 100 * Math.abs(pdi - mdi) / sum;
  };
  dx[period] = computeDX();
  for (let i = period + 1; i < n; i++) {
    trS = trS - trS / period + tr[i];
    pS = pS - pS / period + pDM[i];
    mS = mS - mS / period + mDM[i];
    dx[i] = computeDX();
  }
  let adxS = 0;
  for (let i = period; i < period * 2; i++) adxS += dx[i];
  out[period * 2 - 1] = adxS / period;
  for (let i = period * 2; i < n; i++) {
    out[i] = (out[i - 1] * (period - 1) + dx[i]) / period;
  }
  return out;
}

// ── Individual Triggers ──────────────────────────────────────────────────────

/**
 * Current-bar return z-score vs trailing 96-bar std.
 */
export function pctMoveSpike(candles: Candle[], sigmaThreshold: number): TriggerHit {
  if (candles.length < 3) {
    return { name: 'pctMoveSpike', score: 0, reason: 'flat', fired: false };
  }

  // Compute returns for all bars
  const returns: number[] = [];
  for (let i = 1; i < candles.length; i++) {
    returns.push((candles[i].c - candles[i - 1].c) / candles[i - 1].c);
  }

  const currentReturn = returns[returns.length - 1];
  // Prior returns: exclude current, take up to 96 trailing bars
  const prior = returns.slice(0, -1).slice(-96);

  if (prior.length < 2) {
    return { name: 'pctMoveSpike', score: 0, reason: 'flat', fired: false };
  }

  const mean = prior.reduce((s, v) => s + v, 0) / prior.length;
  const variance = prior.reduce((s, v) => s + (v - mean) ** 2, 0) / prior.length;
  const std = Math.sqrt(variance);

  if (std === 0) {
    return { name: 'pctMoveSpike', score: 0, reason: 'flat', fired: false };
  }

  const zScore = Math.abs(currentReturn - mean) / std;
  const fired = zScore >= sigmaThreshold;
  const score = Math.max(0, Math.min(10, zScore));
  const direction = currentReturn > mean ? 'up' : 'down';

  return {
    name: 'pctMoveSpike',
    score: fired ? score : 0,
    reason: fired ? `${zScore.toFixed(1)}σ return spike ${direction}` : 'flat',
    fired,
  };
}

/**
 * Current volume z-score vs 20-bar rolling.
 */
export function volumeSpike(candles: Candle[], sigmaThreshold: number): TriggerHit {
  const vols = candles.map(c => c.v);
  if (vols.length < 21) {
    return { name: 'volumeSpike', score: 0, reason: 'flat', fired: false };
  }

  const window = vols.slice(-21, -1);
  const currentVol = vols[vols.length - 1];

  // Check for sparse market: skip if >50% of volume samples are 0
  const zeroCount = window.filter(v => v === 0).length;
  if (zeroCount > window.length * 0.5) {
    return { name: 'volumeSpike', score: 0, reason: 'sparse', fired: false };
  }

  const mean = window.reduce((s, v) => s + v, 0) / window.length;
  const variance = window.reduce((s, v) => s + (v - mean) ** 2, 0) / window.length;
  const std = Math.sqrt(variance);

  if (std === 0) {
    return { name: 'volumeSpike', score: 0, reason: 'flat', fired: false };
  }

  const zScore = Math.abs(currentVol - mean) / std;
  const fired = zScore >= sigmaThreshold;
  const score = Math.max(0, Math.min(10, zScore));

  return {
    name: 'volumeSpike',
    score: fired ? score : 0,
    reason: fired ? `${zScore.toFixed(1)}σ volume spike` : 'flat',
    fired,
  };
}

/**
 * Breakout detection: prior range high/low over lookback bars.
 */
export function breakout(candles: Candle[], lookback: number): TriggerHit {
  if (candles.length < lookback + 2) {
    return { name: 'breakout', score: 0, reason: 'flat', fired: false };
  }

  const current = candles[candles.length - 1];
  const priorStart = candles.length - lookback - 1;
  const priorEnd = candles.length - 1;

  let priorHigh = -Infinity;
  let priorLow = Infinity;
  for (let i = priorStart; i < priorEnd; i++) {
    if (candles[i].h > priorHigh) priorHigh = candles[i].h;
    if (candles[i].l < priorLow) priorLow = candles[i].l;
  }

  const range = priorHigh - priorLow;

  if (current.c > priorHigh) {
    const pctBreak = (current.c - priorHigh) / priorHigh * 100;
    return {
      name: 'breakout',
      score: Math.max(0, Math.min(10, pctBreak)),
      reason: `breakout above ${lookback}-bar high`,
      fired: true,
    };
  }

  if (current.c < priorLow) {
    const pctBreak = (priorLow - current.c) / priorLow * 100;
    return {
      name: 'breakout',
      score: Math.max(0, Math.min(10, pctBreak)),
      reason: `breakout below ${lookback}-bar low`,
      fired: true,
    };
  }

  // Score proportional to distance from nearest range edge
  const distUp = priorHigh - current.c;
  const distDown = current.c - priorLow;
  const closest = Math.min(distUp, distDown);
  const score = range > 0 ? Math.max(0, (1 - closest / range)) * 5 : 0;

  return {
    name: 'breakout',
    score,
    reason: 'inside range',
    fired: false,
  };
}

/**
 * Bollinger Band squeeze detection: current bandwidth vs last 100 bars.
 */
export function rangeCompression(candles: Candle[], bbLength: number, bbStdDev: number): TriggerHit {
  const closes = candles.map(c => c.c);
  if (closes.length < bbLength + 1) {
    return { name: 'rangeCompression', score: 0, reason: 'flat', fired: false };
  }

  const mid = sma(closes, bbLength);
  const upper = new Array<number>(closes.length).fill(NaN);
  const lower = new Array<number>(closes.length).fill(NaN);

  for (let i = 0; i < closes.length; i++) {
    if (!isFinite(mid[i])) continue;
    let sumSq = 0;
    let count = 0;
    for (let j = i - bbLength + 1; j <= i; j++) {
      if (j < 0) continue;
      sumSq += (closes[j] - mid[i]) ** 2;
      count++;
    }
    if (count < bbLength) continue;
    const sd = Math.sqrt(sumSq / bbLength);
    upper[i] = mid[i] + sd * bbStdDev;
    lower[i] = mid[i] - sd * bbStdDev;
  }

  // Compute bandwidths for each valid position
  const bandwidths: number[] = [];
  for (let i = 0; i < closes.length; i++) {
    if (isFinite(mid[i]) && isFinite(upper[i]) && isFinite(lower[i]) && mid[i] !== 0) {
      bandwidths.push((upper[i] - lower[i]) / Math.abs(mid[i]));
    }
  }

  if (bandwidths.length < 2) {
    return { name: 'rangeCompression', score: 0, reason: 'flat', fired: false };
  }

  const currentBandwidth = bandwidths[bandwidths.length - 1];

  // Compare to last 100 bars of bandwidths
  const history = bandwidths.slice(-100);
  const sorted = [...history].sort((a, b) => a - b);

  // Percentile rank (where current bandwidth falls in sorted history)
  let percentile = 0;
  for (let i = 0; i < sorted.length; i++) {
    if (sorted[i] < currentBandwidth) {
      percentile = ((i + 1) / sorted.length) * 100;
    }
  }

  const fired = percentile <= 10;
  const score = 10 * (1 - percentile / 100);

  return {
    name: 'rangeCompression',
    score: fired ? Math.min(10, score) : 0,
    reason: fired ? `BB squeeze (P${percentile.toFixed(0)})` : 'BB normal',
    fired,
  };
}

/**
 * Trend strength via ADX(14).
 */
export function trendStrength(candles: Candle[], adxPeriod: number): TriggerHit {
  if (candles.length < adxPeriod * 2 + 1) {
    return { name: 'trendStrength', score: 0, reason: 'flat', fired: false };
  }

  const adxValues = adx(candles, adxPeriod);
  const lastAdx = adxValues[adxValues.length - 1];

  if (!isFinite(lastAdx)) {
    return { name: 'trendStrength', score: 0, reason: 'flat', fired: false };
  }

  const fired = lastAdx >= 25;
  const score = Math.max(0, Math.min(10, lastAdx / 4));

  return {
    name: 'trendStrength',
    score: fired ? score : 0,
    reason: fired ? `ADX ${lastAdx.toFixed(1)} trending` : 'flat',
    fired,
  };
}

// ── Composite Scoring ────────────────────────────────────────────────────────

/**
 * Weighted composite score from triggered hits.
 * Sum(hit.score * weight) / sum(weights for fired triggers only).
 * If nothing fired, return 0. Clamp 0-100.
 */
export function compositeScore(hits: TriggerHit[], weights: Record<string, number>): number {
  const firedHits = hits.filter(h => h.fired);
  if (firedHits.length === 0) return 0;

  let weightedSum = 0;
  for (const hit of firedHits) {
    weightedSum += hit.score * (weights[hit.name] ?? 0);
  }

  // Normalize over ALL trigger weights (not just fired) so single-trigger coins
  // score proportionally low and multi-trigger setups score high.
  const totalWeight = Object.values(weights).reduce((s, w) => s + w, 0) || 1;
  const raw = (weightedSum / totalWeight) * 10;  // scale to 0-100
  return Math.min(100, Math.max(0, raw));
}
