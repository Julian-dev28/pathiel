// Comprehensive unit tests for executor — kelly sizing, execution decisions, risk scenarios.
// Run: node --test scripts/__tests__/executor.test.mjs

import test from 'node:test'
import assert from 'node:assert/strict'

// ── Inline kellySize from executor.ts ─────────────────────────────────────────

function kellySize(confidence, equity, rewardRiskRatio, maxTradeNotional) {
  const p = confidence
  const q = 1 - p
  const b = rewardRiskRatio
  const fStar = Math.max(0, (p * b - q) / b)
  const halfKelly = fStar / 2
  const notional = halfKelly * equity
  return Math.min(notional, maxTradeNotional)
}

// ── Kelly sizing tests ───────────────────────────────────────────────────────

test('kellySize: positive size for edge-positive bet', () => {
  const size = kellySize(0.80, 1000, 2.0, 200)
  // f* = (0.8 * 2 - 0.2) / 2 = (1.6 - 0.2) / 2 = 0.7
  // halfKelly = 0.35, notional = 0.35 * 1000 = 350, capped at 200
  assert.strictEqual(size, 200)
})

test('kellySize: zero size for edge-negative bet', () => {
  const size = kellySize(0.30, 1000, 1.0, 200)
  // f* = (0.3 * 1 - 0.7) / 1 = -0.4, max(0, -0.4) = 0
  assert.strictEqual(size, 0)
})

test('kellySize: zero size for 50/50 with 1:1 R:R', () => {
  const size = kellySize(0.50, 1000, 1.0, 200)
  // f* = (0.5 * 1 - 0.5) / 1 = 0
  assert.strictEqual(size, 0)
})

test('kellySize: scales with equity', () => {
  const size1 = kellySize(0.70, 1000, 2.0, 500)
  const size2 = kellySize(0.70, 2000, 2.0, 500)
  // f* = (0.7 * 2 - 0.3) / 2 = 0.55, half = 0.275
  // notional1 = 275 (not capped), notional2 = 550 (capped at 500)
  assert.ok(Math.abs(size1 - 275) < 0.01, `size1 ~275: ${size1}`)
  assert.strictEqual(size2, 500)
})

test('kellySize: higher confidence = larger size', () => {
  const low = kellySize(0.55, 1000, 2.0, 500)
  const high = kellySize(0.90, 1000, 2.0, 500)
  assert.ok(high > low, `high conf (${high.toFixed(0)}) > low conf (${low.toFixed(0)})`)
})

test('kellySize: higher R:R = larger size', () => {
  const rr1 = kellySize(0.70, 1000, 1.0, 500)
  const rr2 = kellySize(0.70, 1000, 3.0, 500)
  assert.ok(rr2 > rr1, 'higher R:R should give larger Kelly size')
})

test('kellySize: respects maxTradeNotional cap', () => {
  const size = kellySize(0.95, 10000, 3.0, 50)
  // f* = (0.95 * 3 - 0.05) / 3 = 2.8 / 3 = 0.933, half = 0.467
  // notional = 0.467 * 10000 = 4667, capped at 50
  assert.strictEqual(size, 50)
})

test('kellySize: zero equity produces zero size', () => {
  const size = kellySize(0.90, 0, 2.0, 200)
  assert.strictEqual(size, 0)
})

test('kellySize: micro-account sizing ($4 equity)', () => {
  const size = kellySize(0.70, 4, 2.0, 200)
  // f* = (0.7 * 2 - 0.3) / 2 = 0.55, half = 0.275
  // notional = 0.275 * 4 = 1.10
  assert.ok(size < 3, `micro account size should be small: $${size.toFixed(2)}`)
  assert.ok(size > 0, `micro account should still trade: $${size.toFixed(2)}`)
})

// ── News binary risk detection (from executor.ts) ────────────────────────────

const NEWS_PATTERN = /fed|fomc|cpi|rate|earnings|hack|exploit|SEC/i

test('newsBinaryRisk: detects Fed mention', () => {
  assert.strictEqual(NEWS_PATTERN.test('Fed rate decision tomorrow'), true)
})

test('newsBinaryRisk: detects FOMC', () => {
  assert.strictEqual(NEWS_PATTERN.test('FOMC meeting scheduled'), true)
})

test('newsBinaryRisk: detects CPI', () => {
  assert.strictEqual(NEWS_PATTERN.test('CPI data release'), true)
})

test('newsBinaryRisk: detects earnings', () => {
  assert.strictEqual(NEWS_PATTERN.test('Tesla earnings report'), true)
})

test('newsBinaryRisk: detects hack', () => {
  assert.strictEqual(NEWS_PATTERN.test('Protocol hack reported'), true)
})

test('newsBinaryRisk: detects exploit', () => {
  assert.strictEqual(NEWS_PATTERN.test('Smart contract exploit found'), true)
})

test('newsBinaryRisk: detects SEC', () => {
  assert.strictEqual(NEWS_PATTERN.test('SEC investigation'), true)
})

test('newsBinaryRisk: no false positive on normal news', () => {
  assert.strictEqual(NEWS_PATTERN.test('Bitcoin price analysis and technical outlook'), false)
})

test('newsBinaryRisk: no false positive on market update', () => {
  assert.strictEqual(NEWS_PATTERN.test('Weekly crypto market recap'), false)
})

// ── Execution decision logic (simulated) ─────────────────────────────────────

function simulateExecutionDecision(analysis, config, context = {}) {
  // Simplified version of maybeExecute decision tree
  const mode = config.mode || 'OFF'

  if (mode === 'OFF') return { executed: false, reason: 'mode_off' }
  if (analysis.verdict === 'PASS') return { executed: false, reason: 'verdict_pass' }
  if (analysis.confidence < (config.minAiConfidence || 0.8)) return { executed: false, reason: 'confidence_too_low' }

  // Risk gate simulation
  const gates = []
  if (context.positions?.length >= config.maxConcurrent) gates.push('max_concurrent')
  if (context.tradeNotional > config.maxTradeNotionalUsd) gates.push('notional_cap')
  if (context.dailyPnl <= config.maxDailyLossUsd) gates.push('daily_loss')

  if (gates.length > 0) return { executed: false, reason: 'blocked', blockedBy: gates }

  return { executed: true, reason: 'approved' }
}

test('execution: OFF mode always blocks', () => {
  const result = simulateExecutionDecision(
    { verdict: 'LONG', confidence: 0.95 },
    { mode: 'OFF', minAiConfidence: 0.8 }
  )
  assert.strictEqual(result.executed, false)
  assert.strictEqual(result.reason, 'mode_off')
})

test('execution: LIVE mode with PASS verdict blocks', () => {
  const result = simulateExecutionDecision(
    { verdict: 'PASS', confidence: 0.50 },
    { mode: 'LIVE', minAiConfidence: 0.8 }
  )
  assert.strictEqual(result.executed, false)
  assert.strictEqual(result.reason, 'verdict_pass')
})

test('execution: LIVE mode with low confidence blocks', () => {
  const result = simulateExecutionDecision(
    { verdict: 'LONG', confidence: 0.60 },
    { mode: 'LIVE', minAiConfidence: 0.8 }
  )
  assert.strictEqual(result.executed, false)
  assert.strictEqual(result.reason, 'confidence_too_low')
})

test('execution: LIVE mode with LONG and high confidence approves', () => {
  const result = simulateExecutionDecision(
    { verdict: 'LONG', confidence: 0.90 },
    { mode: 'LIVE', minAiConfidence: 0.8, maxConcurrent: 5, maxTradeNotionalUsd: 200, maxDailyLossUsd: -100 },
    { positions: [], tradeNotional: 100, dailyPnl: 50 }
  )
  assert.strictEqual(result.executed, true)
  assert.strictEqual(result.reason, 'approved')
})

test('execution: blocks when max concurrent positions reached', () => {
  const result = simulateExecutionDecision(
    { verdict: 'LONG', confidence: 0.90 },
    { mode: 'LIVE', minAiConfidence: 0.8, maxConcurrent: 2, maxTradeNotionalUsd: 200, maxDailyLossUsd: -100 },
    { positions: [{ coin: 'BTC' }, { coin: 'ETH' }], tradeNotional: 50, dailyPnl: 50 }
  )
  assert.strictEqual(result.executed, false)
  assert.ok(result.blockedBy?.includes('max_concurrent'))
})

test('execution: blocks when daily loss limit hit', () => {
  const result = simulateExecutionDecision(
    { verdict: 'LONG', confidence: 0.90 },
    { mode: 'LIVE', minAiConfidence: 0.8, maxConcurrent: 5, maxTradeNotionalUsd: 200, maxDailyLossUsd: -100 },
    { positions: [], tradeNotional: 50, dailyPnl: -150 }
  )
  assert.strictEqual(result.executed, false)
  assert.ok(result.blockedBy?.includes('daily_loss'))
})

test('execution: blocks when notional cap exceeded', () => {
  const result = simulateExecutionDecision(
    { verdict: 'LONG', confidence: 0.90 },
    { mode: 'LIVE', minAiConfidence: 0.8, maxConcurrent: 5, maxTradeNotionalUsd: 100, maxDailyLossUsd: -100 },
    { positions: [], tradeNotional: 150, dailyPnl: 50 }
  )
  assert.strictEqual(result.executed, false)
  assert.ok(result.blockedBy?.includes('notional_cap'))
})

// ── SL/TP price calculation (from executor.ts) ──────────────────────────────

function calcSLTP(isBuy, midPrice, atr) {
  const SL_ATR_MULT = 3.5
  const TP_ATR_MULT = 1.0
  const slPx = isBuy ? midPrice - atr * SL_ATR_MULT : midPrice + atr * SL_ATR_MULT
  const tpPx = isBuy ? midPrice + atr * TP_ATR_MULT : midPrice - atr * TP_ATR_MULT
  return { slPx, tpPx }
}

test('SL/TP: long position has SL below and TP above', () => {
  const { slPx, tpPx } = calcSLTP(true, 50000, 200)
  assert.ok(slPx < 50000, `SL ${slPx} should be below entry`)
  assert.ok(tpPx > 50000, `TP ${tpPx} should be above entry`)
  assert.strictEqual(slPx, 50000 - 200 * 3.5)
  assert.strictEqual(tpPx, 50000 + 200 * 1.0)
})

test('SL/TP: short position has SL above and TP below', () => {
  const { slPx, tpPx } = calcSLTP(false, 50000, 200)
  assert.ok(slPx > 50000, `SL ${slPx} should be above entry`)
  assert.ok(tpPx < 50000, `TP ${tpPx} should be below entry`)
})

test('SL/TP: SL is wider than TP (3.5x vs 1.0x ATR)', () => {
  const { slPx, tpPx } = calcSLTP(true, 50000, 200)
  const slDist = 50000 - slPx
  const tpDist = tpPx - 50000
  assert.strictEqual(slDist / tpDist, 3.5)
})

// ── Market volume lookup (from executor.ts) ──────────────────────────────────

const MAJOR_VOLUMES = new Map([
  ['BTC', 1e8], ['ETH', 1e8], ['SOL', 1e8], ['BNB', 1e8],
  ['XRP', 1e8], ['DOGE', 1e8], ['ADA', 1e8], ['AVAX', 1e8],
])

function getMarketVolume24h(coin) {
  return MAJOR_VOLUMES.get(coin) ?? 1e7
}

test('marketVolume: major coins have high volume', () => {
  assert.strictEqual(getMarketVolume24h('BTC'), 1e8)
  assert.strictEqual(getMarketVolume24h('ETH'), 1e8)
  assert.strictEqual(getMarketVolume24h('SOL'), 1e8)
})

test('marketVolume: altcoins default to 10M', () => {
  assert.strictEqual(getMarketVolume24h('ZEN'), 1e7)
  assert.strictEqual(getMarketVolume24h('UNKNOWN'), 1e7)
})
