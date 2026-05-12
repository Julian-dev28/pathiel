#!/usr/bin/env node
// E2E pipeline test for hermes-trader
// Run: node scripts/e2e-pipeline-test.mjs

import { config } from 'dotenv'
config({ path: '.env.local' })

const HL_API = 'https://api.hyperliquid.xyz'
const BASE = process.env.NEXT_PUBLIC_BASE_URL || 'http://localhost:3000'
let passed = 0, failed = 0

function test(name, fn) {
  return fn().then(ok => {
    if (ok) { console.log(`  ✓ ${name}`); passed++ }
    else { console.log(`  ✗ ${name}`); failed++ }
    return ok
  }).catch(e => {
    console.log(`  ✗ ${name} -- ${e.message}`)
    failed++
    return false
  })
}

// Test HL API directly
console.log('═══ DIRECT HL API TESTS ═══')

await test('HL API reachable', async () => {
  const r = await fetch(HL_API + '/info', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'allMids' })
  })
  const mids = await r.json()
  const btc = mids.BTC
  console.log(`    BTC mid: $${btc}`)
  return parseFloat(btc) > 0
})

const USER = process.env.HYPERLIQUID_MASTER_ADDRESS || process.env.HYPERLIQUID_WALLET_ADDRESS || ''
console.log(`  User: ${USER}`)

await test('Perp account state', async () => {
  const r = await fetch(HL_API + '/info', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'clearinghouseState', user: USER })
  })
  const d = await r.json()
  const eq = parseFloat(d.marginSummary?.accountValue ?? '0')
  const pos = d.assetPositions?.length || 0
  console.log(`    Perp equity: $${eq.toFixed(2)}, positions: ${pos}`)
  return eq > 0
})

await test('Spot account state', async () => {
  const r = await fetch(HL_API + '/info', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'spotClearinghouseState', user: USER })
  })
  const d = await r.json()
  const usdc = (d.balances ?? []).find(b => b.coin === 'USDC')
  const eq = usdc ? parseFloat(usdc.total) : 0
  console.log(`    Spot USDC: $${eq.toFixed(4)}`)
  return eq > 0
})

await test('Candle fetch (AAVE 4h)', async () => {
  const now = Date.now()
  const r = await fetch(HL_API + '/info', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      type: 'candleSnapshot',
      req: { coin: 'AAVE', interval: '4h', startTime: now - 100 * 14_400_000, endTime: now }
    })
  })
  const candles = await r.json()
  if (!Array.isArray(candles) || candles.length === 0) {
    console.log(`    No candles returned`)
    return false
  }
  const last = candles[candles.length - 1]
  const close = parseFloat(last.c)
  console.log(`    Got ${candles.length} candles, last close: $${close}`)
  return close > 0
})

await test('Candle fetch (KAS 4h - low price coin)', async () => {
  const now = Date.now()
  const r = await fetch(HL_API + '/info', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      type: 'candleSnapshot',
      req: { coin: 'KAS', interval: '4h', startTime: now - 100 * 14_400_000, endTime: now }
    })
  })
  const candles = await r.json()
  if (!Array.isArray(candles) || candles.length === 0) {
    console.log(`    No candles returned`)
    return false
  }
  const last = candles[candles.length - 1]
  console.log(`    Got ${candles.length} candles, last: o=${last.o} h=${last.h} l=${last.l} c=${last.c}`)
  return parseFloat(last.c) > 0
})

// Test Next.js API routes
console.log('\n═══ NEXT.JS API ROUTE TESTS ═══')

await test('GET /api/hl/portfolio', async () => {
  const r = await fetch(BASE + '/api/hl/portfolio')
  const d = await r.json()
  console.log(`    Equity: $${d.equity?.toFixed(2) || 0}, positions: ${d.positions?.length || 0}`)
  for (const p of (d.positions || [])) {
    console.log(`      ${p.coin} ${p.side} | entry $${p.entryPx} | PnL $${p.livePnl}`)
  }
  return d.equity > 0
})

await test('GET /api/agent/state', async () => {
  const r = await fetch(BASE + '/api/agent/state')
  const d = await r.json()
  console.log(`    memory.equity: $${d.equity?.toFixed(2) || 0}`)
  return d.equity > 0
})

await test('GET /api/agent/config', async () => {
  const r = await fetch(BASE + '/api/agent/config')
  const d = await r.json()
  console.log(`    mode=${d.mode} minConf=${d.minAiConfidence} maxTrade=$${d.maxTradeNotionalUsd}`)
  return d.mode === 'LIVE'
})

// Give scan rate limit a moment
await new Promise(r => setTimeout(r, 31000))

await test('POST /api/agent/scan', async () => {
  const r = await fetch(BASE + '/api/agent/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ minScore: 70, withTA: false })
  })
  const d = await r.json()
  const count = d.count || d.perceptions?.length || 0
  console.log(`    Perceptions: ${count}`)
  if (count > 0) {
    const top = (d.perceptions || [])[0]
    console.log(`    Top: ${top.coin} score=${top.compositeScore} mid=$${top.mid}`)
    return top.mid > 0
  }
  return count === 0 // may legitimately have 0
})

console.log(`\n${passed} passed, ${failed} failed out of ${passed + failed}`)
process.exit(failed > 0 ? 1 : 0)
