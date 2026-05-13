// Comprehensive unit tests for risk gates — every gate, edge case, and evalAllGates.
// Run: node --test scripts/__tests__/risk-gates.test.mjs

import test from 'node:test'
import assert from 'node:assert/strict'

// Import gates directly from TS source (requires tsx/ts-node or we inline)
// Since we can't import .ts directly from node, we inline the pure functions verbatim.

// ── Inline risk-gates.ts (pure functions, verbatim) ──────────────────────────

function confidenceGate(ctx, minConfidence) {
  if (ctx.confidence >= minConfidence) return { pass: true }
  return { pass: false, reason: `confidence ${ctx.confidence.toFixed(2)} < ${minConfidence}` }
}

function maxConcurrentPositionsGate(ctx, maxConcurrent) {
  if (ctx.currentPositions.length < maxConcurrent) return { pass: true }
  return { pass: false, reason: `max positions reached (${ctx.currentPositions.length}/${maxConcurrent})` }
}

function perTradeNotionalCapGate(ctx, capUSD) {
  if (ctx.tradeNotionalUSD <= capUSD) return { pass: true }
  return { pass: false, reason: `trade notional $${ctx.tradeNotionalUSD.toFixed(0)} exceeds cap $${capUSD}` }
}

function dailyLossKillSwitch(ctx, maxDailyLoss) {
  if (ctx.dailyPnl > maxDailyLoss) return { pass: true }
  return { pass: false, reason: `daily loss killswitch triggered (PnL $${ctx.dailyPnl.toFixed(0)} <= $${maxDailyLoss})` }
}

function marketLiquidityFloor(ctx, minVolume) {
  if (ctx.marketVolume24hUSD >= minVolume) return { pass: true }
  return { pass: false, reason: `market 24h volume $${(ctx.marketVolume24hUSD / 1e6).toFixed(1)}M below floor $${(minVolume / 1e6).toFixed(1)}M` }
}

function coinAllowlistGate(ctx, allowlist, blocklist) {
  if (blocklist.length > 0 && blocklist.includes(ctx.coin)) {
    return { pass: false, reason: `${ctx.coin} is on the coin blocklist` }
  }
  if (allowlist.length > 0 && !allowlist.includes(ctx.coin)) {
    return { pass: false, reason: `${ctx.coin} not on the allowlist` }
  }
  return { pass: true }
}

function cooldownGate(ctx, lastTradeTime, cooldownMin) {
  if (lastTradeTime === undefined) return { pass: true }
  const elapsed = (Date.now() - lastTradeTime) / 60_000
  if (elapsed >= cooldownMin) return { pass: true }
  return { pass: false, reason: `cooldown active (${Math.floor(cooldownMin - elapsed)}min remaining)` }
}

function oppositeDirectionGuard(ctx) {
  const existing = ctx.currentPositions.find(p => p.coin === ctx.coin)
  if (!existing) return { pass: true }
  if (existing.side !== ctx.tradeSide) {
    return { pass: false, reason: `opposite position exists (${ctx.coin} ${existing.side}) — no auto-flip` }
  }
  return { pass: true }
}

function correlationCap(ctx, maxCryptoCorrelated) {
  if (ctx.tradeSide !== 'long') return { pass: true }
  const cryptoCoins = new Set(['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'AVAX', 'MATIC', 'LINK', 'DOT', 'UNI', 'ATOM', 'NEAR', 'FTM', 'APT', 'ARB', 'OP', 'INJ', 'TIA', 'SUI', 'SEI', 'WIF', 'PEPE', 'BONK', 'FLOKI', 'TRX', 'LTC', 'BCH', 'ETC', 'XLM', 'ALGO', 'AAVE', 'MKR', 'SNX', 'CRV', 'COMP', 'YFI', 'SUSHI', '1INCH'])
  const existingCryptoLongs = ctx.currentPositions.filter(p => cryptoCoins.has(p.coin) && p.side === 'long').length
  if (existingCryptoLongs < maxCryptoCorrelated) return { pass: true }
  return { pass: false, reason: `crypto long correlation cap reached (${existingCryptoLongs}/${maxCryptoCorrelated})` }
}

function equityRiskCap(ctx, maxTotalNotionalPct) {
  const maxNotional = ctx.equity * maxTotalNotionalPct
  const projectedNotional = ctx.totalOpenNotional + ctx.tradeNotionalUSD
  if (projectedNotional <= maxNotional) return { pass: true }
  return { pass: false, reason: `total notional $${projectedNotional.toFixed(0)} would exceed ${maxTotalNotionalPct * 100}% of equity ($${maxNotional.toFixed(0)})` }
}

function newsBlackoutGate(ctx) {
  if (!ctx.hasBinaryNewsRisk) return { pass: true }
  return { pass: false, reason: 'binary news risk detected (Fed, earnings, hack within 2h) — standing down' }
}

function evalAllGates(ctx, config, lastTradeTime) {
  const results = {}
  results.confidence = confidenceGate(ctx, config.minAiConfidence ?? 0.8)
  results.maxConcurrent = maxConcurrentPositionsGate(ctx, config.maxConcurrent ?? 3)
  results.notionalCap = perTradeNotionalCapGate(ctx, config.maxTradeNotionalUsd ?? 200)
  results.dailyLoss = dailyLossKillSwitch(ctx, config.maxDailyLossUsd ?? -100)
  results.liquidity = marketLiquidityFloor(ctx, config.minMarketVolumeUsd ?? 5_000_000)
  results.coinFilter = coinAllowlistGate(ctx, config.coinAllowlist ?? [], config.coinBlocklist ?? [])
  results.cooldown = cooldownGate(ctx, lastTradeTime, config.cooldownMin ?? 60)
  results.oppositeGuard = oppositeDirectionGuard(ctx)
  results.correlation = correlationCap(ctx, 2)
  results.equityRisk = equityRiskCap(ctx, config.maxTotalNotionalPct ?? 0.3)
  results.news = newsBlackoutGate(ctx)

  const blockReasons = []
  let blocked = false
  for (const [key, result] of Object.entries(results)) {
    if (!result.pass) { blocked = true; blockReasons.push(result.reason ?? key) }
  }
  return { results, blocked, blockReasons }
}

// ── Base context factory ──────────────────────────────────────────────────────

function makeCtx(overrides = {}) {
  return {
    confidence: 0.85,
    currentPositions: [],
    tradeNotionalUSD: 100,
    dailyPnl: 50,
    marketVolume24hUSD: 1e8,
    coin: 'BTC',
    tradeSide: 'long',
    hasBinaryNewsRisk: false,
    equity: 1000,
    totalOpenNotional: 200,
    ...overrides,
  }
}

// ── confidenceGate ───────────────────────────────────────────────────────────

test('confidenceGate: passes when confidence >= threshold', () => {
  const ctx = makeCtx({ confidence: 0.85 })
  const r = confidenceGate(ctx, 0.80)
  assert.strictEqual(r.pass, true)
})

test('confidenceGate: fails when confidence < threshold', () => {
  const ctx = makeCtx({ confidence: 0.70 })
  const r = confidenceGate(ctx, 0.80)
  assert.strictEqual(r.pass, false)
  assert.ok(r.reason.includes('0.70'))
  assert.ok(r.reason.includes('0.8'))
})

test('confidenceGate: passes at exact threshold (boundary)', () => {
  const ctx = makeCtx({ confidence: 0.80 })
  const r = confidenceGate(ctx, 0.80)
  assert.strictEqual(r.pass, true)
})

test('confidenceGate: zero confidence always fails', () => {
  const ctx = makeCtx({ confidence: 0 })
  const r = confidenceGate(ctx, 0.50)
  assert.strictEqual(r.pass, false)
})

// ── maxConcurrentPositionsGate ───────────────────────────────────────────────

test('maxConcurrentPositionsGate: passes when below limit', () => {
  const ctx = makeCtx({ currentPositions: [{ coin: 'BTC', side: 'long', sizeUSD: 100 }] })
  const r = maxConcurrentPositionsGate(ctx, 3)
  assert.strictEqual(r.pass, true)
})

test('maxConcurrentPositionsGate: fails when at limit', () => {
  const ctx = makeCtx({ currentPositions: [
    { coin: 'BTC', side: 'long', sizeUSD: 100 },
    { coin: 'ETH', side: 'long', sizeUSD: 100 },
    { coin: 'SOL', side: 'long', sizeUSD: 100 },
  ]})
  const r = maxConcurrentPositionsGate(ctx, 3)
  assert.strictEqual(r.pass, false)
  assert.ok(r.reason.includes('3/3'))
})

test('maxConcurrentPositionsGate: passes with 0 positions', () => {
  const ctx = makeCtx({ currentPositions: [] })
  const r = maxConcurrentPositionsGate(ctx, 1)
  assert.strictEqual(r.pass, true)
})

test('maxConcurrentPositionsGate: limit of 1 allows 0, blocks 1', () => {
  const ctx0 = makeCtx({ currentPositions: [] })
  assert.strictEqual(maxConcurrentPositionsGate(ctx0, 1).pass, true)
  const ctx1 = makeCtx({ currentPositions: [{ coin: 'BTC', side: 'long', sizeUSD: 100 }] })
  assert.strictEqual(maxConcurrentPositionsGate(ctx1, 1).pass, false)
})

// ── perTradeNotionalCapGate ──────────────────────────────────────────────────

test('perTradeNotionalCapGate: passes when under cap', () => {
  const ctx = makeCtx({ tradeNotionalUSD: 100 })
  const r = perTradeNotionalCapGate(ctx, 200)
  assert.strictEqual(r.pass, true)
})

test('perTradeNotionalCapGate: fails when over cap', () => {
  const ctx = makeCtx({ tradeNotionalUSD: 300 })
  const r = perTradeNotionalCapGate(ctx, 200)
  assert.strictEqual(r.pass, false)
  assert.ok(r.reason.includes('300'))
  assert.ok(r.reason.includes('200'))
})

test('perTradeNotionalCapGate: passes at exact cap', () => {
  const ctx = makeCtx({ tradeNotionalUSD: 200 })
  const r = perTradeNotionalCapGate(ctx, 200)
  assert.strictEqual(r.pass, true)
})

test('perTradeNotionalCapGate: zero notional always passes', () => {
  const ctx = makeCtx({ tradeNotionalUSD: 0 })
  const r = perTradeNotionalCapGate(ctx, 5)
  assert.strictEqual(r.pass, true)
})

// ── dailyLossKillSwitch ──────────────────────────────────────────────────────

test('dailyLossKillSwitch: passes when daily PnL is positive', () => {
  const ctx = makeCtx({ dailyPnl: 100 })
  const r = dailyLossKillSwitch(ctx, -100)
  assert.strictEqual(r.pass, true)
})

test('dailyLossKillSwitch: passes when PnL above threshold (small loss)', () => {
  const ctx = makeCtx({ dailyPnl: -50 })
  const r = dailyLossKillSwitch(ctx, -100)
  assert.strictEqual(r.pass, true)
})

test('dailyLossKillSwitch: fails when PnL at or below threshold', () => {
  const ctx = makeCtx({ dailyPnl: -100 })
  const r = dailyLossKillSwitch(ctx, -100)
  assert.strictEqual(r.pass, false)
})

test('dailyLossKillSwitch: fails with large loss', () => {
  const ctx = makeCtx({ dailyPnl: -200 })
  const r = dailyLossKillSwitch(ctx, -100)
  assert.strictEqual(r.pass, false)
  assert.ok(r.reason.includes('-200'))
})

test('dailyLossKillSwitch: zero PnL passes with negative threshold', () => {
  const ctx = makeCtx({ dailyPnl: 0 })
  const r = dailyLossKillSwitch(ctx, -100)
  assert.strictEqual(r.pass, true)
})

// ── marketLiquidityFloor ─────────────────────────────────────────────────────

test('marketLiquidityFloor: passes for major coin', () => {
  const ctx = makeCtx({ marketVolume24hUSD: 1e8 })
  const r = marketLiquidityFloor(ctx, 5_000_000)
  assert.strictEqual(r.pass, true)
})

test('marketLiquidityFloor: fails for illiquid coin', () => {
  const ctx = makeCtx({ marketVolume24hUSD: 1_000_000 })
  const r = marketLiquidityFloor(ctx, 5_000_000)
  assert.strictEqual(r.pass, false)
  assert.ok(r.reason.includes('1.0M'))
  assert.ok(r.reason.includes('5.0M'))
})

test('marketLiquidityFloor: passes at exact floor', () => {
  const ctx = makeCtx({ marketVolume24hUSD: 5_000_000 })
  const r = marketLiquidityFloor(ctx, 5_000_000)
  assert.strictEqual(r.pass, true)
})

// ── coinAllowlistGate ────────────────────────────────────────────────────────

test('coinAllowlistGate: passes with empty allowlist and blocklist', () => {
  const ctx = makeCtx({ coin: 'XYZ' })
  const r = coinAllowlistGate(ctx, [], [])
  assert.strictEqual(r.pass, true)
})

test('coinAllowlistGate: blocks coin on blocklist', () => {
  const ctx = makeCtx({ coin: 'SCAM' })
  const r = coinAllowlistGate(ctx, [], ['SCAM', 'RUG'])
  assert.strictEqual(r.pass, false)
  assert.ok(r.reason.includes('SCAM'))
  assert.ok(r.reason.includes('blocklist'))
})

test('coinAllowlistGate: blocks coin not on allowlist', () => {
  const ctx = makeCtx({ coin: 'XYZ' })
  const r = coinAllowlistGate(ctx, ['BTC', 'ETH', 'SOL'], [])
  assert.strictEqual(r.pass, false)
  assert.ok(r.reason.includes('not on the allowlist'))
})

test('coinAllowlistGate: passes coin on allowlist', () => {
  const ctx = makeCtx({ coin: 'ETH' })
  const r = coinAllowlistGate(ctx, ['BTC', 'ETH', 'SOL'], [])
  assert.strictEqual(r.pass, true)
})

test('coinAllowlistGate: blocklist takes priority over allowlist', () => {
  const ctx = makeCtx({ coin: 'ETH' })
  const r = coinAllowlistGate(ctx, ['ETH', 'BTC'], ['ETH'])
  assert.strictEqual(r.pass, false)
})

// ── cooldownGate ─────────────────────────────────────────────────────────────

test('cooldownGate: passes when no previous trade', () => {
  const ctx = makeCtx()
  const r = cooldownGate(ctx, undefined, 30)
  assert.strictEqual(r.pass, true)
})

test('cooldownGate: passes when cooldown has elapsed', () => {
  const ctx = makeCtx()
  const oldTime = Date.now() - 60 * 60_000 // 1 hour ago
  const r = cooldownGate(ctx, oldTime, 30)
  assert.strictEqual(r.pass, true)
})

test('cooldownGate: fails when still in cooldown', () => {
  const ctx = makeCtx()
  const recentTime = Date.now() - 5 * 60_000 // 5 min ago
  const r = cooldownGate(ctx, recentTime, 30)
  assert.strictEqual(r.pass, false)
  assert.ok(r.reason.includes('cooldown active'))
  assert.ok(r.reason.includes('min remaining'))
})

test('cooldownGate: passes at exact cooldown boundary', () => {
  const ctx = makeCtx()
  const exactTime = Date.now() - 30 * 60_000 // exactly 30 min ago
  const r = cooldownGate(ctx, exactTime, 30)
  assert.strictEqual(r.pass, true)
})

// ── oppositeDirectionGuard ───────────────────────────────────────────────────

test('oppositeDirectionGuard: passes when no existing position', () => {
  const ctx = makeCtx({ coin: 'SOL', tradeSide: 'long' })
  const r = oppositeDirectionGuard(ctx)
  assert.strictEqual(r.pass, true)
})

test('oppositeDirectionGuard: passes when same side position exists', () => {
  const ctx = makeCtx({
    coin: 'BTC',
    tradeSide: 'long',
    currentPositions: [{ coin: 'BTC', side: 'long', sizeUSD: 100 }],
  })
  const r = oppositeDirectionGuard(ctx)
  assert.strictEqual(r.pass, true)
})

test('oppositeDirectionGuard: blocks opposite direction', () => {
  const ctx = makeCtx({
    coin: 'BTC',
    tradeSide: 'short',
    currentPositions: [{ coin: 'BTC', side: 'long', sizeUSD: 100 }],
  })
  const r = oppositeDirectionGuard(ctx)
  assert.strictEqual(r.pass, false)
  assert.ok(r.reason.includes('opposite position'))
  assert.ok(r.reason.includes('no auto-flip'))
})

test('oppositeDirectionGuard: blocks long vs existing short', () => {
  const ctx = makeCtx({
    coin: 'ETH',
    tradeSide: 'long',
    currentPositions: [{ coin: 'ETH', side: 'short', sizeUSD: 50 }],
  })
  const r = oppositeDirectionGuard(ctx)
  assert.strictEqual(r.pass, false)
})

// ── correlationCap ───────────────────────────────────────────────────────────

test('correlationCap: passes for short trades (no correlation check)', () => {
  const ctx = makeCtx({
    tradeSide: 'short',
    currentPositions: [
      { coin: 'BTC', side: 'long', sizeUSD: 100 },
      { coin: 'ETH', side: 'long', sizeUSD: 100 },
      { coin: 'SOL', side: 'long', sizeUSD: 100 },
    ],
  })
  const r = correlationCap(ctx, 2)
  assert.strictEqual(r.pass, true)
})

test('correlationCap: passes when below cap', () => {
  const ctx = makeCtx({
    tradeSide: 'long',
    coin: 'AVAX',
    currentPositions: [{ coin: 'BTC', side: 'long', sizeUSD: 100 }],
  })
  const r = correlationCap(ctx, 2)
  assert.strictEqual(r.pass, true)
})

test('correlationCap: fails at cap with crypto longs', () => {
  const ctx = makeCtx({
    tradeSide: 'long',
    coin: 'AVAX',
    currentPositions: [
      { coin: 'BTC', side: 'long', sizeUSD: 100 },
      { coin: 'ETH', side: 'long', sizeUSD: 100 },
    ],
  })
  const r = correlationCap(ctx, 2)
  assert.strictEqual(r.pass, false)
  assert.ok(r.reason.includes('2/2'))
})

test('correlationCap: ignores non-crypto positions', () => {
  const ctx = makeCtx({
    tradeSide: 'long',
    coin: 'BTC',
    currentPositions: [
      { coin: 'TSLA', side: 'long', sizeUSD: 100 },
      { coin: 'AAPL', side: 'long', sizeUSD: 100 },
      { coin: 'NATGAS', side: 'long', sizeUSD: 100 },
    ],
  })
  const r = correlationCap(ctx, 2)
  assert.strictEqual(r.pass, true)
})

test('correlationCap: mixed crypto + non-crypto counts only crypto', () => {
  const ctx = makeCtx({
    tradeSide: 'long',
    coin: 'SOL',
    currentPositions: [
      { coin: 'BTC', side: 'long', sizeUSD: 100 },
      { coin: 'TSLA', side: 'long', sizeUSD: 100 },
    ],
  })
  const r = correlationCap(ctx, 2)
  assert.strictEqual(r.pass, true)
})

// ── equityRiskCap ────────────────────────────────────────────────────────────

test('equityRiskCap: passes when under equity cap', () => {
  const ctx = makeCtx({ equity: 1000, totalOpenNotional: 200, tradeNotionalUSD: 100 })
  const r = equityRiskCap(ctx, 0.30)
  // maxNotional = 1000 * 0.30 = 300, projected = 200 + 100 = 300
  assert.strictEqual(r.pass, true)
})

test('equityRiskCap: fails when exceeding equity cap', () => {
  const ctx = makeCtx({ equity: 1000, totalOpenNotional: 200, tradeNotionalUSD: 150 })
  const r = equityRiskCap(ctx, 0.30)
  // maxNotional = 300, projected = 350
  assert.strictEqual(r.pass, false)
  assert.ok(r.reason.includes('350'))
  assert.ok(r.reason.includes('300'))
})

test('equityRiskCap: passes with zero equity and zero trade', () => {
  const ctx = makeCtx({ equity: 0, totalOpenNotional: 0, tradeNotionalUSD: 0 })
  const r = equityRiskCap(ctx, 0.30)
  assert.strictEqual(r.pass, true)
})

// ── newsBlackoutGate ─────────────────────────────────────────────────────────

test('newsBlackoutGate: passes when no news risk', () => {
  const ctx = makeCtx({ hasBinaryNewsRisk: false })
  const r = newsBlackoutGate(ctx)
  assert.strictEqual(r.pass, true)
})

test('newsBlackoutGate: fails when binary news risk detected', () => {
  const ctx = makeCtx({ hasBinaryNewsRisk: true })
  const r = newsBlackoutGate(ctx)
  assert.strictEqual(r.pass, false)
  assert.ok(r.reason.includes('binary news risk'))
})

// ── evalAllGates: integration ────────────────────────────────────────────────

test('evalAllGates: all pass with clean context', () => {
  const ctx = makeCtx()
  const config = { minAiConfidence: 0.80, maxConcurrent: 3, maxTradeNotionalUsd: 200, maxDailyLossUsd: -100, minMarketVolumeUsd: 5_000_000, coinAllowlist: [], coinBlocklist: [], cooldownMin: 30, maxTotalNotionalPct: 0.30 }
  const { results, blocked, blockReasons } = evalAllGates(ctx, config, undefined)

  assert.strictEqual(blocked, false)
  assert.strictEqual(blockReasons.length, 0)
  // All 11 gates should exist
  const expectedGates = ['confidence', 'maxConcurrent', 'notionalCap', 'dailyLoss', 'liquidity', 'coinFilter', 'cooldown', 'oppositeGuard', 'correlation', 'equityRisk', 'news']
  for (const g of expectedGates) {
    assert.ok(results[g], `gate ${g} exists`)
    assert.strictEqual(results[g].pass, true, `gate ${g} passes`)
  }
})

test('evalAllGates: blocks with multiple failures', () => {
  const ctx = makeCtx({
    confidence: 0.40,
    tradeNotionalUSD: 500,
    dailyPnl: -200,
    hasBinaryNewsRisk: true,
  })
  const config = { minAiConfidence: 0.80, maxTradeNotionalUsd: 200, maxDailyLossUsd: -100, coinAllowlist: [], coinBlocklist: [], maxTotalNotionalPct: 0.30 }
  const { blocked, blockReasons } = evalAllGates(ctx, config, undefined)

  assert.strictEqual(blocked, true)
  assert.ok(blockReasons.length >= 3, `expected multiple blocks, got ${blockReasons.length}: ${blockReasons.join(', ')}`)
})

test('evalAllGates: no short-circuit — all gates evaluated even when blocked', () => {
  const ctx = makeCtx({ confidence: 0.10 })
  const config = { minAiConfidence: 0.80, maxConcurrent: 3, maxTradeNotionalUsd: 200, maxDailyLossUsd: -100, minMarketVolumeUsd: 5_000_000, coinAllowlist: [], coinBlocklist: [], maxTotalNotionalPct: 0.30 }
  const { results } = evalAllGates(ctx, config, undefined)

  // All 11 gates must have been evaluated (no short-circuit)
  assert.strictEqual(Object.keys(results).length, 11)
})

test('evalAllGates: defaults used when config missing', () => {
  const ctx = makeCtx()
  const { blocked } = evalAllGates(ctx, {}, undefined)
  assert.strictEqual(blocked, false)
})
