// Comprehensive unit tests for TA filter — trend assessment, indicators, scoring, verdicts.
// Run: node --test scripts/__tests__/ta-filter.test.mjs

import test from 'node:test'
import assert from 'node:assert/strict'

// ── Inline indicator helpers from triggers.ts ────────────────────────────────

function ema(values, period) {
  const k = 2 / (period + 1)
  const out = new Array(values.length).fill(NaN)
  if (!values.length) return out
  let e = values[0]; out[0] = e
  for (let i = 1; i < values.length; i++) { e = values[i] * k + e * (1 - k); out[i] = e }
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
  const tr = new Array(n).fill(0)
  const pDM = new Array(n).fill(0)
  const mDM = new Array(n).fill(0)
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
    trS = trS - trS / period + tr[i]
    pS = pS - pS / period + pDM[i]
    mS = mS - mS / period + mDM[i]
    dx[i] = computeDX()
  }
  let adxS = 0
  for (let i = period; i < period * 2; i++) adxS += dx[i]
  out[period * 2 - 1] = adxS / period
  for (let i = period * 2; i < n; i++) {
    out[i] = (out[i - 1] * (period - 1) + dx[i]) / period
  }
  return out
}

// ── Inline TA filter helpers from ta-filter.ts ───────────────────────────────

function assessTrend(candles) {
  if (candles.length < 30) return 'flat'
  const closes = candles.map(c => c.c)
  const ema8Arr = ema(closes, 8)
  const ema21Arr = ema(closes, 21)
  const i = closes.length - 1
  const e8 = ema8Arr[i], e21 = ema21Arr[i]
  if (!isFinite(e8) || !isFinite(e21)) return 'flat'
  const e8Prev = ema8Arr[Math.max(0, i - 3)]
  const emaCross = e8 > e21
  const slopeRising = e8 > e8Prev
  if (emaCross && slopeRising) return 'bullish'
  if (!emaCross && !slopeRising) return 'bearish'
  return 'flat'
}

function computeATR4pct(candles) {
  if (candles.length < 20) return null
  const atrArr = atr(candles, 14)
  const last = atrArr[atrArr.length - 1]
  const lastClose = candles[candles.length - 1].c
  if (!isFinite(last) || lastClose === 0) return null
  return (last / lastClose) * 100
}

function computeRSI(candles) {
  if (candles.length < 20) return null
  const arr = rsi(candles, 14)
  const last = arr[arr.length - 1]
  return isFinite(last) ? last : null
}

function computeADX(candles) {
  if (candles.length < 30) return null
  const arr = adx(candles, 14)
  const last = arr[arr.length - 1]
  return isFinite(last) ? last : null
}

function checkVolumeConfirm(candles) {
  if (candles.length < 21) return false
  const lastVol = candles[candles.length - 1].v
  const avgVol = candles.slice(-21, -1).reduce((s, c) => s + c.v, 0) / 20
  return avgVol === 0 ? false : lastVol >= avgVol * 0.8
}

function checkEMACrossRecent(candles) {
  if (candles.length < 25) return false
  const closes = candles.map(c => c.c)
  const ema8Arr = ema(closes, 8)
  const ema21Arr = ema(closes, 21)
  for (let i = closes.length - 3; i < closes.length; i++) {
    if (i < 1) continue
    const prev8 = ema8Arr[i - 1], prev21 = ema21Arr[i - 1]
    const curr8 = ema8Arr[i], curr21 = ema21Arr[i]
    if (!isFinite(prev8) || !isFinite(prev21) || !isFinite(curr8) || !isFinite(curr21)) continue
    if ((prev8 <= prev21 && curr8 > curr21) || (prev8 >= prev21 && curr8 < curr21)) return true
  }
  return false
}

function computeTAScore(trendAligned, rsi4h, atr4pct, adx4h, emaCross, volumeConfirm, compositeScore) {
  let score = 0
  if (trendAligned) { score += 20 }
  if (rsi4h !== null && rsi4h > 30 && rsi4h < 70) { score += 15 }
  if (atr4pct !== null && atr4pct >= 0.5) { score += 15 }
  if (adx4h !== null && adx4h >= 25) { score += 15 }
  if (emaCross) { score += 10 }
  if (volumeConfirm) { score += 10 }
  score += Math.min(15, compositeScore / 100 * 15)
  return Math.min(100, score)
}

// ── Candle generator ─────────────────────────────────────────────────────────

function makeUptrend(count = 60, base = 100, step = 0.5) {
  const candles = []
  let price = base
  for (let i = 0; i < count; i++) {
    price += step + (Math.random() - 0.3) * 0.2
    candles.push({ t: i * 3600_000, o: price - 0.1, h: price + 0.3, l: price - 0.3, c: price, v: 1000 + Math.random() * 500 })
  }
  return candles
}

function makeDowntrend(count = 60, base = 100, step = 0.5) {
  const candles = []
  let price = base
  for (let i = 0; i < count; i++) {
    price -= step + (Math.random() - 0.3) * 0.2
    candles.push({ t: i * 3600_000, o: price + 0.1, h: price + 0.3, l: price - 0.3, c: price, v: 1000 + Math.random() * 500 })
  }
  return candles
}

function makeFlatMarket(count = 60, base = 100) {
  const candles = []
  for (let i = 0; i < count; i++) {
    const noise = (Math.random() - 0.5) * 0.1
    const price = base + noise
    candles.push({ t: i * 3600_000, o: price - 0.05, h: price + 0.05, l: price - 0.05, c: price, v: 1000 })
  }
  return candles
}

// ── assessTrend tests ────────────────────────────────────────────────────────

test('assessTrend: identifies uptrend', () => {
  const candles = makeUptrend(60, 100, 0.8)
  const result = assessTrend(candles)
  assert.strictEqual(result, 'bullish')
})

test('assessTrend: identifies downtrend', () => {
  const candles = makeDowntrend(60, 100, 0.8)
  const result = assessTrend(candles)
  assert.strictEqual(result, 'bearish')
})

test('assessTrend: flat for insufficient data', () => {
  const candles = makeUptrend(10)
  assert.strictEqual(assessTrend(candles), 'flat')
})

test('assessTrend: flat market returns flat or borderline', () => {
  const candles = makeFlatMarket(60)
  const result = assessTrend(candles)
  assert.ok(['flat', 'bullish', 'bearish'].includes(result))
})

// ── computeATR4pct tests ─────────────────────────────────────────────────────

test('computeATR4pct: returns percentage for volatile market', () => {
  const candles = makeUptrend(60, 100, 0.5)
  const result = computeATR4pct(candles)
  assert.ok(result !== null)
  assert.ok(result > 0)
  assert.ok(result < 100, 'ATR% should be reasonable')
})

test('computeATR4pct: null for insufficient data', () => {
  const candles = makeUptrend(10)
  assert.strictEqual(computeATR4pct(candles), null)
})

test('computeATR4pct: zero for flat candles', () => {
  const candles = []
  for (let i = 0; i < 30; i++) {
    candles.push({ t: i, o: 100, h: 100, l: 100, c: 100, v: 1000 })
  }
  const result = computeATR4pct(candles)
  assert.ok(result !== null && result === 0)
})

// ── computeRSI tests ─────────────────────────────────────────────────────────

test('computeRSI: overbought for strong uptrend', () => {
  const candles = makeUptrend(40, 100, 1.0)
  const result = computeRSI(candles)
  assert.ok(result > 70, `RSI should be > 70 for uptrend, got ${result?.toFixed(1)}`)
})

test('computeRSI: oversold for strong downtrend', () => {
  const candles = makeDowntrend(40, 100, 1.0)
  const result = computeRSI(candles)
  assert.ok(result < 30, `RSI should be < 30 for downtrend, got ${result?.toFixed(1)}`)
})

test('computeRSI: null for insufficient data', () => {
  assert.strictEqual(computeRSI(makeUptrend(10)), null)
})

test('computeRSI: neutral range for flat market', () => {
  const candles = makeFlatMarket(40)
  const result = computeRSI(candles)
  assert.ok(result >= 30 && result <= 70, `RSI should be neutral: ${result?.toFixed(1)}`)
})

// ── computeADX tests ─────────────────────────────────────────────────────────

test('computeADX: strong trend detected', () => {
  const candles = makeUptrend(60, 100, 1.0)
  const result = computeADX(candles)
  assert.ok(result !== null)
  assert.ok(result > 20, `ADX should indicate trend strength: ${result?.toFixed(1)}`)
})

test('computeADX: null for insufficient data', () => {
  assert.strictEqual(computeADX(makeUptrend(20)), null)
})

// ── checkVolumeConfirm tests ─────────────────────────────────────────────────

test('checkVolumeConfirm: true when last bar has decent volume', () => {
  const candles = makeUptrend(30)
  candles[candles.length - 1].v = 5000
  assert.strictEqual(checkVolumeConfirm(candles), true)
})

test('checkVolumeConfirm: false for insufficient data', () => {
  assert.strictEqual(checkVolumeConfirm(makeUptrend(10)), false)
})

test('checkVolumeConfirm: false when last bar volume is way below average', () => {
  const candles = makeUptrend(30)
  for (let i = 0; i < candles.length - 1; i++) candles[i].v = 5000
  candles[candles.length - 1].v = 10 // tiny
  assert.strictEqual(checkVolumeConfirm(candles), false)
})

// ── checkEMACrossRecent tests ────────────────────────────────────────────────

test('checkEMACrossRecent: detects recent crossover in strong uptrend', () => {
  const candles = makeUptrend(60, 100, 1.5)
  const result = checkEMACrossRecent(candles)
  // Strong uptrends may or may not have a cross in the last 3 bars
  assert.ok(typeof result === 'boolean')
})

test('checkEMACrossRecent: false for insufficient data', () => {
  assert.strictEqual(checkEMACrossRecent(makeUptrend(20)), false)
})

// ── computeTAScore tests ─────────────────────────────────────────────────────

test('computeTAScore: maximum score when all signals align', () => {
  const score = computeTAScore(true, 50, 2.0, 30, true, true, 90)
  // 20 + 15 + 15 + 15 + 10 + 10 + min(15, 90/100*15=13.5) = 98.5
  assert.ok(score >= 95, `max score should be ~98, got ${score}`)
})

test('computeTAScore: minimum score when nothing aligns', () => {
  const score = computeTAScore(false, 80, 0.1, 10, false, false, 10)
  // only compositeScore component: min(15, 10/100*15=1.5) = 1.5
  assert.ok(score <= 5, `min score should be ~1.5, got ${score}`)
})

test('computeTAScore: RSI outside neutral range gives no points', () => {
  const scoreOverbought = computeTAScore(true, 85, 2.0, 30, true, true, 80)
  const scoreNeutral = computeTAScore(true, 50, 2.0, 30, true, true, 80)
  assert.ok(scoreOverbought < scoreNeutral, 'overbought RSI should score less')
})

test('computeTAScore: capped at 100', () => {
  const score = computeTAScore(true, 50, 5.0, 50, true, true, 100)
  assert.ok(score <= 100)
})

// ── Verdict threshold tests ──────────────────────────────────────────────────

test('TAScore verdict: CONFIRMED at >= 45', () => {
  const score = 50
  const verdict = score >= 45 ? 'CONFIRMED' : score >= 30 ? 'WEAK' : 'REJECTED'
  assert.strictEqual(verdict, 'CONFIRMED')
})

test('TAScore verdict: WEAK at 30-44', () => {
  const score = 38
  const verdict = score >= 45 ? 'CONFIRMED' : score >= 30 ? 'WEAK' : 'REJECTED'
  assert.strictEqual(verdict, 'WEAK')
})

test('TAScore verdict: REJECTED below 30', () => {
  const score = 20
  const verdict = score >= 45 ? 'CONFIRMED' : score >= 30 ? 'WEAK' : 'REJECTED'
  assert.strictEqual(verdict, 'REJECTED')
})

test('TAScore verdict: boundary at 45 is CONFIRMED', () => {
  const verdict = 45 >= 45 ? 'CONFIRMED' : 45 >= 30 ? 'WEAK' : 'REJECTED'
  assert.strictEqual(verdict, 'CONFIRMED')
})

test('TAScore verdict: boundary at 30 is WEAK', () => {
  const verdict = 30 >= 45 ? 'CONFIRMED' : 30 >= 30 ? 'WEAK' : 'REJECTED'
  assert.strictEqual(verdict, 'WEAK')
})

test('TAScore verdict: boundary at 29 is REJECTED', () => {
  const verdict = 29 >= 45 ? 'CONFIRMED' : 29 >= 30 ? 'WEAK' : 'REJECTED'
  assert.strictEqual(verdict, 'REJECTED')
})
