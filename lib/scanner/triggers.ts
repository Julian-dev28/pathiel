// ── Indicator functions — ported verbatim from scripts/backtest.mjs ──────────

type OHLCVCandle = { t: number; o: number; h: number; l: number; c: number; v: number }

function ema(values: number[], period: number): number[] {
  const k = 2 / (period + 1)
  const out = new Array(values.length).fill(NaN)
  if (!values.length) return out
  let e = values[0]; out[0] = e
  for (let i = 1; i < values.length; i++) { e = values[i] * k + e * (1 - k); out[i] = e }
  return out
}

function sma(values: number[], period: number): number[] {
  const out = new Array(values.length).fill(NaN)
  let acc = 0
  for (let i = 0; i < values.length; i++) {
    acc += values[i]
    if (i >= period) acc -= values[i - period]
    if (i >= period - 1) out[i] = acc / period
  }
  return out
}

function atr(c: OHLCVCandle[], period = 14): number[] {
  const tr = new Array(c.length).fill(0)
  for (let i = 1; i < c.length; i++) {
    const h = c[i].h, l = c[i].l, pc = c[i - 1].c
    tr[i] = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc))
  }
  const out = new Array(c.length).fill(NaN)
  if (c.length <= period) return out
  let acc = 0
  for (let i = 1; i <= period; i++) acc += tr[i]
  out[period] = acc / period
  for (let i = period + 1; i < c.length; i++) out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
  return out
}

function rsi(c: OHLCVCandle[], period = 14): number[] {
  const out = new Array(c.length).fill(NaN)
  if (c.length <= period) return out
  let g = 0, l = 0
  for (let i = 1; i <= period; i++) {
    const d = c[i].c - c[i - 1].c
    if (d >= 0) g += d; else l -= d
  }
  let avgG = g / period, avgL = l / period
  out[period] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL)
  for (let i = period + 1; i < c.length; i++) {
    const d = c[i].c - c[i - 1].c
    avgG = (avgG * (period - 1) + (d > 0 ? d : 0)) / period
    avgL = (avgL * (period - 1) + (d < 0 ? -d : 0)) / period
    out[i] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL)
  }
  return out
}

// ── Candle type for trigger functions ─────────────────────────────────────────

export type Candle = { t: number; o: number; h: number; l: number; c: number; v: number }

export type TriggerResult = { fired: boolean; score: number; reason: string }

// ── pctMoveSpike — detects abnormal price moves vs recent distribution ────────

export function pctMoveSpike(
  candles: Candle[],
  window = 15,
  sigmaThreshold = 3
): TriggerResult {
  if (candles.length < window + 1) return { fired: false, score: 0, reason: 'insufficient data' }

  const slice = candles.slice(-(window + 1))
  const returns: number[] = []
  for (let i = 1; i < slice.length; i++) {
    returns.push((slice[i].c - slice[i - 1].c) / slice[i - 1].c)
  }

  const currentReturn = returns[returns.length - 1]
  const priorReturns = returns.slice(0, -1)

  const mean = priorReturns.reduce((s, v) => s + v, 0) / priorReturns.length
  const variance = priorReturns.reduce((s, v) => s + (v - mean) ** 2, 0) / priorReturns.length
  const std = Math.sqrt(variance)

  if (std < 1e-12) return { fired: false, score: 0, reason: 'zero std — no signal' }

  const zScore = (currentReturn - mean) / std

  return {
    fired: Math.abs(zScore) >= sigmaThreshold,
    score: Math.abs(zScore),
    reason: `${Math.abs(zScore).toFixed(1)}σ ${zScore > 0 ? 'up' : 'down'} spike in ${window}bar return`,
  }
}

// ── volumeSpike — detects unusual volume vs trailing 20-bar distribution ──────

export function volumeSpike(
  candles: Candle[],
  sigmaThreshold = 3
): TriggerResult {
  const lookback = 20
  if (candles.length < lookback) return { fired: false, score: 0, reason: 'insufficient data' }

  const slice = candles.slice(-lookback)
  const volumes = slice.map(c => c.v)

  // Skip if volume history is sparse (>50% zero bars)
  const zeros = volumes.filter(v => v === 0).length
  if (zeros > volumes.length * 0.5) return { fired: false, score: 0, reason: 'sparse volume' }

  const mean = volumes.reduce((s, v) => s + v, 0) / volumes.length
  const variance = volumes.reduce((s, v) => s + (v - mean) ** 2, 0) / volumes.length
  const std = Math.sqrt(variance)

  if (std < 1e-12) return { fired: false, score: 0, reason: 'zero std' }

  // Current bar is the last one in the lookback window
  const currentVol = slice[slice.length - 1].v
  const zScore = (currentVol - mean) / std

  return {
    fired: Math.abs(zScore) >= sigmaThreshold,
    score: Math.abs(zScore),
    reason: `${zScore.toFixed(1)}σ volume spike`,
  }
}

// ── breakout — detects close outside N-bar range ──────────────────────────────

export function breakout(
  candles: Candle[],
  lookback = 48
): TriggerResult {
  if (candles.length < lookback + 1) return { fired: false, score: 0, reason: 'insufficient data' }

  const current = candles[candles.length - 1]
  const prior = candles.slice(-lookback - 1, -1)

  let priorHigh = -Infinity
  let priorLow = Infinity
  for (const c of prior) {
    if (c.h > priorHigh) priorHigh = c.h
    if (c.l < priorLow) priorLow = c.l
  }

  const close = current.c

  if (close > priorHigh) {
    const score = (close - priorHigh) / priorHigh
    return {
      fired: true,
      score,
      reason: `breakout above ${lookback}-bar high`,
    }
  }

  if (close < priorLow) {
    const score = (priorLow - close) / priorLow
    return {
      fired: true,
      score,
      reason: `breakout below ${lookback}-bar low`,
    }
  }

  // Not in breakout — score proportional to distance from range edges
  const distToHigh = Math.abs(close - priorHigh) / priorHigh
  const distToLow = Math.abs(close - priorLow) / priorLow
  return {
    fired: false,
    score: Math.max(distToHigh, distToLow),
    reason: `${((close - priorLow) / (priorHigh - priorLow) * 100).toFixed(0)}% within ${lookback}-bar range`,
  }
}

// ── rangeCompression — Bollinger Band squeeze ─────────────────────────────────

export function rangeCompression(
  candles: Candle[]
): TriggerResult {
  if (candles.length < 20) return { fired: false, score: 0, reason: 'insufficient data' }

  const closes = candles.map(c => c.c)
  const bbLength = 20
  const stdDev = 2

  // Compute bandwidth for all valid positions
  const bandwidths: number[] = []
  for (let i = bbLength - 1; i < closes.length; i++) {
    const window = closes.slice(i - bbLength + 1, i + 1)
    const mean = window.reduce((s, v) => s + v, 0) / window.length
    const variance = window.reduce((s, v) => s + (v - mean) ** 2, 0) / window.length
    const sd = Math.sqrt(variance)
    const upper = mean + stdDev * sd
    const lower = mean - stdDev * sd
    bandwidths.push(lower === 0 ? 0 : (upper - lower) / mean)
  }

  if (bandwidths.length === 0) return { fired: false, score: 0, reason: 'insufficient data' }

  const currentBW = bandwidths[bandwidths.length - 1]

  // Rank among all bandwidths (last 100 bars worth)
  const sorted = [...bandwidths].sort((a, b) => a - b)
  const rank = sorted.indexOf(currentBW)
  const percentile = (rank / sorted.length) * 100

  // fired if in lowest decile
  const fired = percentile <= 10
  const score = 1 - percentile / 100

  return {
    fired,
    score,
    reason: `Bollinger bandwidth at ${percentile.toFixed(0)}th percentile (compression)`,
  }
}

// ── compositeScore — weighted combination of individual trigger scores ─────────

const DEFAULT_WEIGHTS: Record<string, number> = {
  pctMoveSpike: 0.35,
  volumeSpike: 0.25,
  breakout: 0.25,
  rangeCompression: 0.15,
}

export function compositeScore(
  triggers: { name: string; score: number }[],
  weights?: Record<string, number>
): number {
  const w = weights ?? DEFAULT_WEIGHTS

  let sum = 0
  let weightTotal = 0

  for (const t of triggers) {
    const weight = w[t.name] ?? 0
    sum += t.score * weight
    weightTotal += weight
  }

  if (weightTotal === 0) return 0

  // Raw score is already 0-1 range; scale to 0-10
  return Math.min(10, (sum / weightTotal) * 10)
}

export { ema, sma, atr, rsi }
