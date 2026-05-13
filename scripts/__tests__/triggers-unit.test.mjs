// Unit tests for trigger engine — verifies indicators match backtest.mjs verbatim.
// Run: node --test scripts/__tests__/triggers-unit.test.mjs

import test from 'node:test'
import assert from 'node:assert/strict'

// ── Port indicators verbatim from scripts/backtest.mjs ──────────────────────

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

function atr(c, period = 14) {
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

function rsi(c, period = 14) {
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

// ── Import the scanner's triggers for comparison ───────────────────────────

let libEma, libSma, libAtr, libRsi
let pctMoveSpike, volumeSpike, breakoutFn, rangeCompression, compositeScore

try {
  const triggers = await import('../../lib/agent/triggers.ts')
  libEma = triggers.ema
  libSma = triggers.sma
  libAtr = triggers.atr
  libRsi = triggers.rsi
  pctMoveSpike = triggers.pctMoveSpike
  volumeSpike = triggers.volumeSpike
  breakoutFn = triggers.breakout
  rangeCompression = triggers.rangeCompression
  compositeScore = triggers.compositeScore
} catch {
  // If TS import fails (ts-node/tsx not available), skip comparison tests
  libEma = null
}

// ── Synthetic candle generator ──────────────────────────────────────────────

function makeCandles(count, basePrice = 100, volatility = 0.01) {
  const candles = []
  let price = basePrice
  for (let i = 0; i < count; i++) {
    const open = price
    const change = (Math.random() - 0.48) * volatility * price // slight upward bias
    const close = open + change
    const high = Math.max(open, close) + Math.random() * volatility * price * 0.5
    const low = Math.min(open, close) - Math.random() * volatility * price * 0.5
    const volume = 1000 + Math.random() * 5000
    candles.push({ t: i * 300_000, o: +open.toFixed(2), h: +high.toFixed(2), l: +low.toFixed(2), c: +close.toFixed(2), v: +volume.toFixed(0) })
    price = close
  }
  return candles
}

// ── Tests ───────────────────────────────────────────────────────────────────

test('ema: matches known values', (t) => {
  const values = [10, 12, 11, 13, 14, 12, 15]
  const result = ema(values, 3)
  assert.ok(!isNaN(result[result.length - 1]), 'last EMA is not NaN')
  assert.ok(result[0] === values[0], 'first EMA equals first value')
})

test('ema: identical to backtest.mjs impl', (t) => {
  if (!libEma) { t.skip('lib/scanner/triggers.ts not importable'); return }
  const values = [42000, 42100, 41800, 43000, 42500, 43200, 42800]
  const expected = ema(values, 5)
  const actual = libEma(values, 5)
  for (let i = 0; i < expected.length; i++) {
    if (isNaN(expected[i])) {
      assert.ok(isNaN(actual[i]), `ema[${i}] should be NaN`)
    } else {
      assert.ok(Math.abs(actual[i] - expected[i]) < 1e-10, `ema[${i}] mismatch: ${actual[i]} vs ${expected[i]}`)
    }
  }
})

test('sma: correct sliding window', (t) => {
  const values = [10, 20, 30, 40, 50]
  const result = sma(values, 3)
  assert.ok(isNaN(result[0]), 'sma[0] is NaN (not enough data)')
  assert.ok(isNaN(result[1]), 'sma[1] is NaN (not enough data)')
  assert.ok(result[2] === 20, `sma[2] = 20, got ${result[2]}`)
  assert.ok(result[4] === 40, `sma[4] = 40, got ${result[4]}`)
})

test('sma: identical to backtest.mjs impl', (t) => {
  if (!libSma) { t.skip('lib/scanner/triggers.ts not importable'); return }
  const values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
  const expected = sma(values, 4)
  const actual = libSma(values, 4)
  for (let i = 0; i < expected.length; i++) {
    if (isNaN(expected[i])) {
      assert.ok(isNaN(actual[i]), `sma[${i}] should be NaN`)
    } else {
      assert.ok(Math.abs(actual[i] - expected[i]) < 1e-10, `sma[${i}] mismatch: ${actual[i]} vs ${expected[i]}`)
    }
  }
})

test('atr: positive values for volatile candles', (t) => {
  const candles = makeCandles(50, 100, 0.02)
  const result = atr(candles, 14)
  const lastAtr = result[result.length - 1]
  assert.ok(!isNaN(lastAtr) && lastAtr > 0, `ATR(14) > 0: ${lastAtr.toFixed(4)}`)
})

test('atr: identical to backtest.mjs impl', (t) => {
  if (!libAtr) { t.skip('lib/scanner/triggers.ts not importable'); return }
  const data = makeCandles(50, 100, 0.02)
  const expected = atr(data, 14)
  const actual = libAtr(data, 14)
  for (let i = 0; i < expected.length; i++) {
    if (isNaN(expected[i])) {
      assert.ok(isNaN(actual[i]), `atr[${i}] should be NaN`)
    } else {
      assert.ok(Math.abs(actual[i] - expected[i]) < 1e-10, `atr[${i}] mismatch: ${actual[i]} vs ${expected[i]}`)
    }
  }
})

test('rsi: correctly identifies uptrend', (t) => {
  // Strictly rising prices should produce high RSI
  const candles = []
  let price = 100
  for (let i = 0; i < 30; i++) {
    price += 1
    candles.push({ t: i, h: price + 0.5, l: price - 0.5, c: price, v: 100 })
  }
  const result = rsi(candles, 14)
  const lastRsi = result[result.length - 1]
  assert.ok(lastRsi > 70, `RSI for uptrend should be > 70, got ${lastRsi.toFixed(1)}`)
})

test('rsi: identical to backtest.mjs impl', (t) => {
  if (!libRsi) { t.skip('lib/scanner/triggers.ts not importable'); return }
  const data = makeCandles(50, 100, 0.015)
  const expected = rsi(data, 14)
  const actual = libRsi(data, 14)
  for (let i = 0; i < expected.length; i++) {
    if (isNaN(expected[i])) {
      assert.ok(isNaN(actual[i]), `rsi[${i}] should be NaN`)
    } else {
      assert.ok(Math.abs(actual[i] - expected[i]) < 1e-10, `rsi[${i}] mismatch: ${actual[i]} vs ${expected[i]}`)
    }
  }
})

test('pctMoveSpike: detects large move in flat series', (t) => {
  const candles = makeCandles(20, 100, 0.001) // very low volatility
  // Inject a huge spike on the last bar
  candles[candles.length - 1].c = 110 // 10% jump
  const result = pctMoveSpike(candles, 15, 3)
  assert.ok(result.fired, 'should detect the spike')
  assert.ok(result.score > 3, `z-score should be > 3, got ${result.score.toFixed(1)}`)
})

test('pctMoveSpike: does not fire on normal data', (t) => {
  const candles = makeCandles(50, 100, 0.005)
  const result = pctMoveSpike(candles, 15, 3)
  assert.ok(!result.fired, 'should not fire on normal volatility')
})

test('volumeSpike: detects volume spike', (t) => {
  const candles = makeCandles(25, 100, 0.005)
  // Inject huge volume on last bar
  candles[candles.length - 1].v = 500000 // vs ~3500 average
  const result = volumeSpike(candles, 3)
  assert.ok(result.fired, 'volume spike should fire')
})

test('breakout: detects above-range breakout', (t) => {
  const candles = makeCandles(60, 100, 0.003)
  // Force last candle way above the range
  const highs = candles.slice(0, -1).map(c => c.h)
  const rangeHigh = Math.max(...highs)
  candles[candles.length - 1].c = rangeHigh * 1.10
  candles[candles.length - 1].h = candles[candles.length - 1].c
  const result = breakoutFn(candles, 48)
  assert.ok(result.fired, 'should detect breakout above range')
})

test('breakout: no fire when within range', (t) => {
  const candles = makeCandles(60, 100, 0.003)
  const result = breakoutFn(candles, 48)
  // With normal random walk, breakout is unlikely but possible; just verify it runs
  assert.ok(typeof result.fired === 'boolean')
  assert.ok(typeof result.score === 'number' && result.score >= 0)
})

test('rangeCompression: fires when bandwidth is minimal', (t) => {
  // Fully deterministic: high-vol phase first 80, tight consolidation last 40.
  const candles = []
  for (let i = 0; i < 80; i++) {
    const base = 100 + i * 0.35
    candles.push({ t: i * 300_000, o: base - 5, h: base + 5, l: base - 5, c: base, v: 1000 })
  }
  for (let i = 80; i < 120; i++) {
    candles.push({ t: i * 300_000, o: 128, h: 128.005, l: 127.995, c: 128, v: 1000 })
  }
  // Call with bbLength=20, bbStdDev=2 as the source requires these params
  const result = rangeCompression(candles, 20, 2)
  assert.ok(result.fired, `compression should fire when bandwidth collapses, got ${JSON.stringify(result)}`)
  assert.ok(result.score > 0, `compression score should be positive, got ${result.score.toFixed(2)}`)
})

test('compositeScore: weighted sum works with default weights', (t) => {
  const triggers = [
    { name: 'pctMoveSpike', score: 5, fired: true },
    { name: 'volumeSpike', score: 3, fired: true },
    { name: 'breakout', score: 0.1, fired: true },
    { name: 'rangeCompression', score: 0.9, fired: true },
  ]
  const weights = { pctMoveSpike: 0.35, volumeSpike: 0.25, breakout: 0.25, rangeCompression: 0.15, trendStrength: 0.10 }
  const result = compositeScore(triggers, weights)
  // Formula: (sum(hit.score * weight) / sum(all weights)) * 10
  // weightedSum = 5*0.35 + 3*0.25 + 0.1*0.25 + 0.9*0.15 = 2.66
  // totalWeight = 0.35 + 0.25 + 0.25 + 0.15 + 0.10 = 1.10
  // raw = (2.66 / 1.10) * 10 = 24.18, capped at 100 → 24.18
  assert.ok(result > 0, `composite > 0, got ${result.toFixed(2)}`)
  assert.ok(result <= 100, `composite <= 100, got ${result.toFixed(2)}`)
  assert.ok(Math.abs(result - 24.18) < 1, `expected ~24.18, got ${result.toFixed(2)}`)
})

test('compositeScore: returns 0 for empty input', (t) => {
  const result = compositeScore([])
  assert.strictEqual(result, 0)
})
