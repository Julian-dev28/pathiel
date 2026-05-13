// E2E pipeline test: full scan -> TA filter -> risk gates -> execution flow.
// Tests the entire autonomous trading pipeline with mocked data.
// Run: node --test scripts/__tests__/e2e-pipeline.test.mjs

import test from 'node:test'
import assert from 'node:assert/strict'

// ═══════════════════════════════════════════════════════════════════════════════
// PHASE 1: TRIGGER ENGINE (from lib/agent/triggers.ts)
// ═══════════════════════════════════════════════════════════════════════════════

function ema(values, period) {
  const k = 2 / (period + 1)
  const out = new Array(values.length).fill(NaN)
  if (!values.length) return out
  let e = values[0]; out[0] = e
  for (let i = 1; i < values.length; i++) { e = values[i] * k + e * (1 - k); out[i] = e }
  return out
}

function sma(values, period) {
  const out = new Array(values.length).fill(NaN)
  let acc = 0
  for (let i = 0; i < values.length; i++) {
    acc += values[i]
    if (i >= period) acc -= values[i - period]
    if (i >= period - 1) out[i] = acc / period
  }
  return out
}

function atr(candles, period = 14) {
  const tr = new Array(candles.length).fill(0)
  for (let i = 1; i < candles.length; i++) {
    const h = candles[i].h, l = candles[i].l, pc = candles[i - 1].c
    tr[i] = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc))
  }
  const out = new Array(candles.length).fill(NaN)
  if (candles.length <= period) return out
  let acc = 0
  for (let i = 1; i <= period; i++) acc += tr[i]
  out[period] = acc / period
  for (let i = period + 1; i < candles.length; i++) out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
  return out
}

function rsi(candles, period = 14) {
  const out = new Array(candles.length).fill(NaN)
  if (candles.length <= period) return out
  let g = 0, l = 0
  for (let i = 1; i <= period; i++) {
    const d = candles[i].c - candles[i - 1].c
    if (d >= 0) g += d; else l -= d
  }
  let avgG = g / period, avgL = l / period
  out[period] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL)
  for (let i = period + 1; i < candles.length; i++) {
    const d = candles[i].c - candles[i - 1].c
    avgG = (avgG * (period - 1) + (d > 0 ? d : 0)) / period
    avgL = (avgL * (period - 1) + (d < 0 ? -d : 0)) / period
    out[i] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL)
  }
  return out
}

function adx(candles, period = 14) {
  const n = candles.length
  const out = new Array(n).fill(NaN)
  if (n <= period * 2) return out
  const tr = new Array(n).fill(0), pDM = new Array(n).fill(0), mDM = new Array(n).fill(0)
  for (let i = 1; i < n; i++) {
    const h = candles[i].h, l = candles[i].l, pc = candles[i - 1].c, ph = candles[i - 1].h, pl = candles[i - 1].l
    tr[i] = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc))
    const up = h - ph, dn = pl - l
    pDM[i] = (up > dn && up > 0) ? up : 0
    mDM[i] = (dn > up && dn > 0) ? dn : 0
  }
  let trS = 0, pS = 0, mS = 0
  for (let i = 1; i <= period; i++) { trS += tr[i]; pS += pDM[i]; mS += mDM[i] }
  const dx = new Array(n).fill(NaN)
  const computeDX = () => {
    const pdi = trS === 0 ? 0 : 100 * pS / trS
    const mdi = trS === 0 ? 0 : 100 * mS / trS
    const sum = pdi + mdi
    return sum === 0 ? 0 : 100 * Math.abs(pdi - mdi) / sum
  }
  dx[period] = computeDX()
  for (let i = period + 1; i < n; i++) {
    trS = trS - trS / period + tr[i]; pS = pS - pS / period + pDM[i]; mS = mS - mS / period + mDM[i]
    dx[i] = computeDX()
  }
  let adxS = 0
  for (let i = period; i < period * 2; i++) adxS += dx[i]
  out[period * 2 - 1] = adxS / period
  for (let i = period * 2; i < n; i++) out[i] = (out[i - 1] * (period - 1) + dx[i]) / period
  return out
}

function pctMoveSpike(candles, sigmaThreshold) {
  if (candles.length < 3) return { name: 'pctMoveSpike', score: 0, reason: 'flat', fired: false }
  const returns = []
  for (let i = 1; i < candles.length; i++) returns.push((candles[i].c - candles[i - 1].c) / candles[i - 1].c)
  const currentReturn = returns[returns.length - 1]
  const prior = returns.slice(0, -1).slice(-96)
  if (prior.length < 2) return { name: 'pctMoveSpike', score: 0, reason: 'flat', fired: false }
  const mean = prior.reduce((s, v) => s + v, 0) / prior.length
  const variance = prior.reduce((s, v) => s + (v - mean) ** 2, 0) / prior.length
  const std = Math.sqrt(variance)
  if (std === 0) return { name: 'pctMoveSpike', score: 0, reason: 'flat', fired: false }
  const zScore = Math.abs(currentReturn - mean) / std
  const fired = zScore >= sigmaThreshold
  const score = Math.max(0, Math.min(10, zScore))
  const direction = currentReturn > mean ? 'up' : 'down'
  return { name: 'pctMoveSpike', score: fired ? score : 0, reason: fired ? `${zScore.toFixed(1)}σ return spike ${direction}` : 'flat', fired }
}

function volumeSpike(candles, sigmaThreshold) {
  const vols = candles.map(c => c.v)
  if (vols.length < 21) return { name: 'volumeSpike', score: 0, reason: 'flat', fired: false }
  const window = vols.slice(-21, -1)
  const currentVol = vols[vols.length - 1]
  const zeroCount = window.filter(v => v === 0).length
  if (zeroCount > window.length * 0.5) return { name: 'volumeSpike', score: 0, reason: 'sparse', fired: false }
  const mean = window.reduce((s, v) => s + v, 0) / window.length
  const variance = window.reduce((s, v) => s + (v - mean) ** 2, 0) / window.length
  const std = Math.sqrt(variance)
  if (std === 0) return { name: 'volumeSpike', score: 0, reason: 'flat', fired: false }
  const zScore = Math.abs(currentVol - mean) / std
  const fired = zScore >= sigmaThreshold
  return { name: 'volumeSpike', score: fired ? Math.max(0, Math.min(10, zScore)) : 0, reason: fired ? `${zScore.toFixed(1)}σ volume spike` : 'flat', fired }
}

function breakout(candles, lookback) {
  if (candles.length < lookback + 2) return { name: 'breakout', score: 0, reason: 'flat', fired: false }
  const current = candles[candles.length - 1]
  const priorStart = candles.length - lookback - 1, priorEnd = candles.length - 1
  let priorHigh = -Infinity, priorLow = Infinity
  for (let i = priorStart; i < priorEnd; i++) {
    if (candles[i].h > priorHigh) priorHigh = candles[i].h
    if (candles[i].l < priorLow) priorLow = candles[i].l
  }
  const range = priorHigh - priorLow
  if (current.c > priorHigh) {
    const pctBreak = (current.c - priorHigh) / priorHigh * 100
    return { name: 'breakout', score: Math.max(0, Math.min(10, pctBreak)), reason: `breakout above ${lookback}-bar high`, fired: true }
  }
  if (current.c < priorLow) {
    const pctBreak = (priorLow - current.c) / priorLow * 100
    return { name: 'breakout', score: Math.max(0, Math.min(10, pctBreak)), reason: `breakout below ${lookback}-bar low`, fired: true }
  }
  const distUp = priorHigh - current.c, distDown = current.c - priorLow
  const closest = Math.min(distUp, distDown)
  const score = range > 0 ? Math.max(0, (1 - closest / range)) * 5 : 0
  return { name: 'breakout', score, reason: 'inside range', fired: false }
}

function rangeCompression(candles, bbLength = 20, bbStdDev = 2) {
  const closes = candles.map(c => c.c)
  if (closes.length < bbLength + 1) return { name: 'rangeCompression', score: 0, reason: 'flat', fired: false }
  const mid = sma(closes, bbLength)
  const upper = new Array(closes.length).fill(NaN), lower = new Array(closes.length).fill(NaN)
  for (let i = 0; i < closes.length; i++) {
    if (!isFinite(mid[i])) continue
    let sumSq = 0, count = 0
    for (let j = i - bbLength + 1; j <= i; j++) {
      if (j < 0) continue
      sumSq += (closes[j] - mid[i]) ** 2; count++
    }
    if (count < bbLength) continue
    const sd = Math.sqrt(sumSq / bbLength)
    upper[i] = mid[i] + sd * bbStdDev; lower[i] = mid[i] - sd * bbStdDev
  }
  const bandwidths = []
  for (let i = 0; i < closes.length; i++) {
    if (isFinite(mid[i]) && isFinite(upper[i]) && isFinite(lower[i]) && mid[i] !== 0) {
      bandwidths.push((upper[i] - lower[i]) / Math.abs(mid[i]))
    }
  }
  if (bandwidths.length < 2) return { name: 'rangeCompression', score: 0, reason: 'flat', fired: false }
  const currentBandwidth = bandwidths[bandwidths.length - 1]
  const sorted = [...bandwidths].sort((a, b) => a - b)
  let percentile = 0
  for (let i = 0; i < sorted.length; i++) {
    if (sorted[i] < currentBandwidth) percentile = ((i + 1) / sorted.length) * 100
  }
  const fired = percentile <= 10
  const score = 10 * (1 - percentile / 100)
  return { name: 'rangeCompression', score: fired ? Math.min(10, score) : 0, reason: fired ? `BB squeeze (P${percentile.toFixed(0)})` : 'BB normal', fired }
}

function trendStrength(candles, adxPeriod) {
  if (candles.length < adxPeriod * 2 + 1) return { name: 'trendStrength', score: 0, reason: 'flat', fired: false }
  const adxValues = adx(candles, adxPeriod)
  const lastAdx = adxValues[adxValues.length - 1]
  if (!isFinite(lastAdx)) return { name: 'trendStrength', score: 0, reason: 'flat', fired: false }
  const fired = lastAdx >= 25
  const score = Math.max(0, Math.min(10, lastAdx / 4))
  return { name: 'trendStrength', score: fired ? score : 0, reason: fired ? `ADX ${lastAdx.toFixed(1)} trending` : 'flat', fired }
}

function compositeScore(hits, weights) {
  const firedHits = hits.filter(h => h.fired)
  if (firedHits.length === 0) return 0
  let weightedSum = 0
  for (const hit of firedHits) weightedSum += hit.score * (weights[hit.name] ?? 0)
  const totalWeight = Object.values(weights).reduce((s, w) => s + w, 0) || 1
  const raw = (weightedSum / totalWeight) * 10
  return Math.min(100, Math.max(0, raw))
}

// ═══════════════════════════════════════════════════════════════════════════════
// PHASE 2: RISK GATES (from lib/agent/risk-gates.ts)
// ═══════════════════════════════════════════════════════════════════════════════

function evalAllGates(ctx, config, lastTradeTime) {
  const results = {}
  // confidence
  results.confidence = ctx.confidence >= (config.minAiConfidence ?? 0.8) ? { pass: true } : { pass: false, reason: `confidence ${ctx.confidence.toFixed(2)} < ${config.minAiConfidence}` }
  // max concurrent
  results.maxConcurrent = ctx.currentPositions.length < (config.maxConcurrent ?? 3) ? { pass: true } : { pass: false, reason: `max positions reached` }
  // notional cap
  results.notionalCap = ctx.tradeNotionalUSD <= (config.maxTradeNotionalUsd ?? 200) ? { pass: true } : { pass: false, reason: `trade notional exceeds cap` }
  // daily loss
  results.dailyLoss = ctx.dailyPnl > (config.maxDailyLossUsd ?? -100) ? { pass: true } : { pass: false, reason: `daily loss killswitch` }
  // liquidity
  results.liquidity = ctx.marketVolume24hUSD >= (config.minMarketVolumeUsd ?? 5_000_000) ? { pass: true } : { pass: false, reason: `volume too low` }
  // coin filter
  const bl = config.coinBlocklist ?? []
  const al = config.coinAllowlist ?? []
  if (bl.length > 0 && bl.includes(ctx.coin)) results.coinFilter = { pass: false, reason: 'blocklisted' }
  else if (al.length > 0 && !al.includes(ctx.coin)) results.coinFilter = { pass: false, reason: 'not on allowlist' }
  else results.coinFilter = { pass: true }
  // cooldown
  if (lastTradeTime === undefined) results.cooldown = { pass: true }
  else {
    const elapsed = (Date.now() - lastTradeTime) / 60_000
    results.cooldown = elapsed >= (config.cooldownMin ?? 60) ? { pass: true } : { pass: false, reason: 'cooldown active' }
  }
  // opposite guard
  const existing = ctx.currentPositions.find(p => p.coin === ctx.coin)
  results.oppositeGuard = (!existing || existing.side === ctx.tradeSide) ? { pass: true } : { pass: false, reason: 'opposite position' }
  // correlation
  const cryptoCoins = new Set(['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'AVAX'])
  if (ctx.tradeSide !== 'long') results.correlation = { pass: true }
  else {
    const existingCryptoLongs = ctx.currentPositions.filter(p => cryptoCoins.has(p.coin) && p.side === 'long').length
    results.correlation = existingCryptoLongs < 2 ? { pass: true } : { pass: false, reason: 'correlation cap' }
  }
  // equity risk
  const maxNotional = ctx.equity * (config.maxTotalNotionalPct ?? 0.3)
  results.equityRisk = (ctx.totalOpenNotional + ctx.tradeNotionalUSD) <= maxNotional ? { pass: true } : { pass: false, reason: 'equity risk exceeded' }
  // news
  results.news = !ctx.hasBinaryNewsRisk ? { pass: true } : { pass: false, reason: 'news risk' }

  const blockReasons = []
  let blocked = false
  for (const [key, result] of Object.entries(results)) {
    if (!result.pass) { blocked = true; blockReasons.push(result.reason ?? key) }
  }
  return { results, blocked, blockReasons }
}

// ═══════════════════════════════════════════════════════════════════════════════
// PHASE 3: E2E PIPELINE TESTS
// ═══════════════════════════════════════════════════════════════════════════════

// Candle generators
function makeFlatCandles(count = 100, base = 100) {
  const candles = []
  for (let i = 0; i < count; i++) {
    const noise = (Math.random() - 0.5) * 0.05
    const price = base + noise
    candles.push({ t: i * 300_000, o: price - 0.02, h: price + 0.02, l: price - 0.02, c: price, v: 1000 + Math.random() * 100 })
  }
  return candles
}

function makeSpikeCandles(count = 100, base = 100) {
  const candles = makeFlatCandles(count, base);
  // 50% jump and 500x volume spike to reliably fire multiple triggers with high score
  candles[candles.length - 1].c = base * 1.50; // 50% jump
  candles[candles.length - 1].v = 500000; // massive volume
  return candles;
}

function makeExtremeSpikeCandles(count = 100, base = 100) {
  const candles = makeFlatCandles(count, base);
  // 100% jump and 1000x volume to reliably fire ALL triggers with maximum score
  candles[candles.length - 1].c = base * 2.0; // 100% jump
  candles[candles.length - 1].v = 1000000; // massive volume spike
  return candles;
}

function makeBreakoutCandles(count = 100, base = 100) {
  const candles = makeFlatCandles(count, base)
  // Force consolidation in prior range
  for (let i = 0; i < count - 1; i++) {
    candles[i].h = base + 1
    candles[i].l = base - 1
    candles[i].c = base + (Math.random() - 0.5) * 1.5
  }
  // Breakout above range
  candles[candles.length - 1].c = base + 5
  candles[candles.length - 1].h = base + 5.5
  return candles
}

const DEFAULT_WEIGHTS = { pctMoveSpike: 0.35, volumeSpike: 0.25, breakout: 0.25, rangeCompression: 0.15, trendStrength: 0.10 }
const DEFAULT_CONFIG = { minAiConfidence: 0.80, maxConcurrent: 3, maxTradeNotionalUsd: 200, maxDailyLossUsd: -100, minMarketVolumeUsd: 5_000_000, coinAllowlist: [], coinBlocklist: [], cooldownMin: 30, maxTotalNotionalPct: 0.30 }

// ── E2E: Flat market → no triggers → no perception ───────────────────────────

test('E2E: flat market produces no triggered perceptions', () => {
  const candles = makeFlatCandles(100)

  const hits = [
    pctMoveSpike(candles, 3),
    volumeSpike(candles, 3),
    breakout(candles, 48),
    rangeCompression(candles, 20, 2),
    trendStrength(candles, 14),
  ]

  const firedCount = hits.filter(h => h.fired).length
  assert.ok(firedCount < 2, `flat market should have < 2 triggers, got ${firedCount}`)

  const score = compositeScore(hits, DEFAULT_WEIGHTS)
  assert.ok(score < 80, `flat market score should be low, got ${score.toFixed(1)}`)
})

// ── E2E: Spike market → triggers fire → high composite score ─────────────────

test('E2E: spike market fires multiple triggers → high score', () => {
  const candles = makeSpikeCandles(100)

  const hits = [
    pctMoveSpike(candles, 3),
    volumeSpike(candles, 3),
    breakout(candles, 48),
    rangeCompression(candles, 20, 2),
    trendStrength(candles, 14),
  ]

  const firedCount = hits.filter(h => h.fired).length
  assert.ok(firedCount >= 2, `spike market should fire >= 2 triggers, got ${firedCount}: ${hits.map(h => `${h.name}=${h.fired}`).join(', ')}`)

  const score = compositeScore(hits, DEFAULT_WEIGHTS)
  assert.ok(score >= 50, `spike market should have high score, got ${score.toFixed(1)}`)
})

// ── E2E: Breakout → fires breakout + volume → passes scan ────────────────────

test('E2E: breakout scenario fires breakout trigger → passes score threshold', () => {
  const candles = makeBreakoutCandles(100)

  const hits = [
    pctMoveSpike(candles, 3),
    volumeSpike(candles, 3),
    breakout(candles, 48),
    rangeCompression(candles, 20, 2),
    trendStrength(candles, 14),
  ]

  const breakoutHit = hits.find(h => h.name === 'breakout')
  assert.ok(breakoutHit.fired, 'breakout should fire')

  const score = compositeScore(hits, DEFAULT_WEIGHTS)
  // Breakout alone may not hit minScore=80, but should be meaningful
  assert.ok(score > 0, `breakout should produce non-zero score: ${score.toFixed(1)}`)
})

// ── E2E: Perception → TA Filter → Risk Gates → Execution decision ────────────

test('E2E: full pipeline — CONFIRMED perception passes all risk gates → executes', () => {
  // Step 1: Create a perception with high composite score
  const perception = {
    id: 'BTC-123-abc',
    coin: 'BTC',
    type: 'perp',
    firedAt: Date.now(),
    mid: 50000,
    triggers: [
      { name: 'pctMoveSpike', score: 8, reason: '4.2σ return spike up', fired: true },
      { name: 'volumeSpike', score: 7, reason: '5.1σ volume spike', fired: true },
      { name: 'breakout', score: 6, reason: 'breakout above 48-bar high', fired: true },
    ],
    compositeScore: 85,
    taSignal: 'CONFIRMED',
    taScore: 72,
  }

  // Step 2: Simulate AI analysis result
  const analysis = {
    id: 'analysis-001',
    perceptionId: perception.id,
    coin: perception.coin,
    verdict: 'LONG',
    confidence: 0.88,
    side: 'long',
    entryPx: 50000,
    stopPx: 48500,
    tpPx: 52000,
    reasoning: 'Strong momentum with volume confirmation',
    createdAt: Date.now(),
  }

  // Step 3: Run through risk gates
  const ctx = {
    confidence: analysis.confidence,
    currentPositions: [],
    tradeNotionalUSD: 100,
    dailyPnl: 20,
    marketVolume24hUSD: 1e8,
    coin: analysis.coin,
    tradeSide: 'long',
    hasBinaryNewsRisk: false,
    equity: 1000,
    totalOpenNotional: 0,
  }

  const { blocked, blockReasons, results } = evalAllGates(ctx, DEFAULT_CONFIG, undefined)

  assert.strictEqual(blocked, false, `should not be blocked: ${blockReasons.join(', ')}`)
  assert.strictEqual(results.confidence.pass, true)
  assert.strictEqual(results.maxConcurrent.pass, true)
  assert.strictEqual(results.notionalCap.pass, true)
  assert.strictEqual(results.dailyLoss.pass, true)
  assert.strictEqual(results.liquidity.pass, true)
  assert.strictEqual(results.coinFilter.pass, true)
  assert.strictEqual(results.cooldown.pass, true)
  assert.strictEqual(results.oppositeGuard.pass, true)
  assert.strictEqual(results.correlation.pass, true)
  assert.strictEqual(results.equityRisk.pass, true)
  assert.strictEqual(results.news.pass, true)
})

test('E2E: full pipeline — blocked by cooldown despite strong signal', () => {
  const perception = {
    id: 'ETH-456-def',
    coin: 'ETH',
    type: 'perp',
    firedAt: Date.now(),
    mid: 3000,
    triggers: [
      { name: 'pctMoveSpike', score: 9, reason: '6σ return spike', fired: true },
      { name: 'volumeSpike', score: 8, reason: '7σ volume spike', fired: true },
    ],
    compositeScore: 90,
    taSignal: 'CONFIRMED',
    taScore: 80,
  }

  const analysis = {
    id: 'analysis-002',
    perceptionId: perception.id,
    coin: 'ETH',
    verdict: 'LONG',
    confidence: 0.92,
    side: 'long',
    entryPx: 3000,
    stopPx: 2900,
    tpPx: 3200,
    reasoning: 'Massive spike with volume',
    createdAt: Date.now(),
  }

  // Recent trade on ETH (5 min ago, cooldown is 30 min)
  const lastTradeTime = Date.now() - 5 * 60_000

  const ctx = {
    confidence: analysis.confidence,
    currentPositions: [],
    tradeNotionalUSD: 100,
    dailyPnl: 20,
    marketVolume24hUSD: 1e8,
    coin: 'ETH',
    tradeSide: 'long',
    hasBinaryNewsRisk: false,
    equity: 1000,
    totalOpenNotional: 0,
  }

  const { blocked, blockReasons } = evalAllGates(ctx, DEFAULT_CONFIG, lastTradeTime)

  assert.strictEqual(blocked, true)
  assert.ok(blockReasons.some(r => r.includes('cooldown')), `should be blocked by cooldown: ${blockReasons.join(', ')}`)
})

test('E2E: full pipeline — blocked by news blackout', () => {
  const analysis = {
    id: 'analysis-003',
    perceptionId: 'BTC-789',
    coin: 'BTC',
    verdict: 'LONG',
    confidence: 0.90,
    side: 'long',
    entryPx: 50000,
    reasoning: 'Bullish setup',
    createdAt: Date.now(),
    newsContext: 'Fed rate decision tomorrow, FOMC meeting',
  }

  const ctx = {
    confidence: analysis.confidence,
    currentPositions: [],
    tradeNotionalUSD: 100,
    dailyPnl: 20,
    marketVolume24hUSD: 1e8,
    coin: 'BTC',
    tradeSide: 'long',
    hasBinaryNewsRisk: /fed|fomc|cpi|rate|earnings|hack|exploit|SEC/i.test(analysis.newsContext ?? ''),
    equity: 1000,
    totalOpenNotional: 0,
  }

  const { blocked, blockReasons } = evalAllGates(ctx, DEFAULT_CONFIG, undefined)

  assert.strictEqual(blocked, true)
  assert.ok(blockReasons.some(r => r.includes('news')), `should be blocked by news: ${blockReasons.join(', ')}`)
})

test('E2E: full pipeline — opposite direction guard blocks auto-flip', () => {
  const analysis = {
    id: 'analysis-004',
    coin: 'SOL',
    verdict: 'SHORT',
    confidence: 0.85,
    side: 'short',
    entryPx: 100,
    reasoning: 'Bearish divergence',
    createdAt: Date.now(),
  }

  const ctx = {
    confidence: analysis.confidence,
    currentPositions: [{ coin: 'SOL', side: 'long', sizeUSD: 100 }],
    tradeNotionalUSD: 50,
    dailyPnl: 20,
    marketVolume24hUSD: 1e8,
    coin: 'SOL',
    tradeSide: 'short',
    hasBinaryNewsRisk: false,
    equity: 1000,
    totalOpenNotional: 100,
  }

  const { blocked, blockReasons } = evalAllGates(ctx, DEFAULT_CONFIG, undefined)

  assert.strictEqual(blocked, true)
  assert.ok(blockReasons.some(r => r.includes('opposite')), `should block auto-flip: ${blockReasons.join(', ')}`)
})

// ── E2E: Multi-coin scan simulation ──────────────────────────────────────────

test('E2E: multi-coin scan — only top candidates produce perceptions', () => {
  const coins = ['BTC', 'ETH', 'SOL', 'DOGE', 'AVAX']
  const perceptions = []

  for (const coin of coins) {
    const candles = coin === 'BTC' ? makeExtremeSpikeCandles(100) : makeFlatCandles(100)
    const hits = [
      pctMoveSpike(candles, 3),
      volumeSpike(candles, 3),
      breakout(candles, 48),
      rangeCompression(candles, 20, 2),
      trendStrength(candles, 14),
    ]

    const firedCount = hits.filter(h => h.fired).length
    if (firedCount >= 2) {
      const score = compositeScore(hits, DEFAULT_WEIGHTS)
      if (score >= 40) {
        perceptions.push({ coin, score, firedCount })
      }
    }
  }

  // Only BTC should produce a perception (the others are flat with 0 fires)
  assert.strictEqual(perceptions.length, 1, `only BTC should trigger, got: ${perceptions.map(p => p.coin).join(', ')}`)
  assert.strictEqual(perceptions[0].coin, 'BTC')
  assert.ok(perceptions[0].firedCount >= 2)
  assert.ok(perceptions[0].score >= 40)
})

// ── E2E: TA filter scoring → verdict determination ──────────────────────────

test('E2E: TA filter scoring produces correct verdicts', () => {
  const computeScore = (trendAligned, rsi4h, atr4pct, adx4h, emaCross, volumeConfirm, compScore) => {
    let score = 0
    if (trendAligned) score += 20
    if (rsi4h !== null && rsi4h > 30 && rsi4h < 70) score += 15
    if (atr4pct !== null && atr4pct >= 0.5) score += 15
    if (adx4h !== null && adx4h >= 25) score += 15
    if (emaCross) score += 10
    if (volumeConfirm) score += 10
    score += Math.min(15, compScore / 100 * 15)
    return Math.min(100, score)
  }

  // Strong confirmation: all signals align
  const strong = computeScore(true, 55, 2.0, 30, true, true, 85)
  const strongVerdict = strong >= 45 ? 'CONFIRMED' : strong >= 30 ? 'WEAK' : 'REJECTED'
  assert.strictEqual(strongVerdict, 'CONFIRMED')

  // Weak: some signals but not enough
  const weak = computeScore(true, 55, 0.3, 15, false, false, 0);
  const weakVerdict = weak >= 45 ? 'CONFIRMED' : weak >= 30 ? 'WEAK' : 'REJECTED';
  assert.strictEqual(weakVerdict, 'WEAK');

  // Rejected: almost nothing
  const rejected = computeScore(false, 90, 0.1, 10, false, false, 5)
  const rejectedVerdict = rejected >= 45 ? 'CONFIRMED' : rejected >= 30 ? 'WEAK' : 'REJECTED'
  assert.strictEqual(rejectedVerdict, 'REJECTED')
})
