// e2e test: verify the full Hyperliquid data pipeline works end-to-end.
// No auth required — all public endpoints.
// Run: node --test scripts/__tests__/e2e-market-data.test.mjs

import test from 'node:test'
import assert from 'node:assert/strict'

const HL_API = 'https://api.hyperliquid.xyz'

async function hlPost(body) {
  const res = await fetch(`${HL_API}/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HL API ${res.status}: ${await res.text().slice(0, 200)}`)
  return res.json()
}

test('universe: perp meta returns coins with szDecimals, maxLeverage', async (t) => {
  const meta = await hlPost({ type: 'meta' })
  assert.ok(Array.isArray(meta.universe), 'meta.universe is array')
  assert.ok(meta.universe.length >= 150, `has >= 150 perp markets (got ${meta.universe.length})`)

  for (const u of meta.universe.slice(0, 10)) {
    assert.ok(typeof u.name === 'string' && u.name.length > 0, `coin name: ${u.name}`)
    assert.ok(typeof u.szDecimals === 'number', `${u.name} szDecimals is number`)
    assert.ok(typeof u.maxLeverage === 'number', `${u.name} maxLeverage is number`)
  }
})

test('universe: spot meta returns tokens + pairs', async (t) => {
  const spotMeta = await hlPost({ type: 'spotMeta' })
  assert.ok(Array.isArray(spotMeta.tokens), 'spotMeta.tokens is array')
  assert.ok(Array.isArray(spotMeta.universe), 'spotMeta.universe is array')
  assert.ok(spotMeta.universe.length >= 10, `has >= 10 spot pairs (got ${spotMeta.universe.length})`)
})

test('allMids: returns numeric prices for every perp coin', async (t) => {
  const [meta, allMids] = await Promise.all([
    hlPost({ type: 'meta' }),
    hlPost({ type: 'allMids' }),
  ])

  assert.ok(typeof allMids === 'object', 'allMids is object')

  const missing = []
  for (const u of meta.universe) {
    const priceStr = allMids[u.name]
    if (!priceStr) { missing.push(u.name); continue }
    const price = parseFloat(priceStr)
    assert.ok(!isNaN(price) && price > 0, `${u.name} price is positive number: ${priceStr}`)
  }
  assert.ok(missing.length === 0, `all perp coins have prices; missing: ${missing.slice(0, 20).join(', ')}`)
  assert.ok(Object.keys(allMids).length >= 200, `allMids has >= 200 entries (got ${Object.keys(allMids).length})`)
})

test('allMids: equity perps that exist actually have prices', async (t) => {
  const [meta, allMids] = await Promise.all([
    hlPost({ type: 'meta' }),
    hlPost({ type: 'allMids' }),
  ])

  // Check whatever equity-like perps HL actually lists
  const equityCandidates = ['TSLA', 'NVDA', 'AAPL', 'AMZN', 'GOOGL', 'MSFT', 'META', 'COIN', 'MSTR']
  const actuallyListed = equityCandidates.filter(c => meta.universe.some(u => u.name === c))

  if (actuallyListed.length === 0) {
    // HL doesn't list any equity perps right now — skip gracefully
    t.skip('no equity perps currently listed on HL')
    return
  }

  for (const coin of actuallyListed) {
    const p = parseFloat(allMids[coin] ?? '0')
    assert.ok(p > 0, `${coin}-PERP has price $${p}`)
  }
})

test('candles: fetch 5m/100-bar for diverse perp coins', async (t) => {
  const [meta, allMids] = await Promise.all([
    hlPost({ type: 'meta' }),
    hlPost({ type: 'allMids' }),
  ])

  // Pick top-volume coins by price (approximate — major coins have recognizable prices)
  // BTC, ETH, SOL are always active; rest we pick by price > $1 to filter dead coins
  const knownGood = ['BTC', 'ETH', 'SOL', 'DOGE', 'AVAX', 'ARB']
  const sample = []
  for (const c of knownGood) {
    if (meta.universe.some(u => u.name === c) && parseFloat(allMids[c] ?? '0') > 0) {
      sample.push(c)
    }
  }
  assert.ok(sample.length >= 4, `got >= 4 sample coins (got ${sample.join(',')})`)

  for (const coin of sample) {
    const endTime = Date.now()
    const startTime = endTime - 100 * 300_000 // 5m * 100
    const candles = await hlPost({
      type: 'candleSnapshot',
      req: { coin, interval: '5m', startTime, endTime },
    })
    assert.ok(Array.isArray(candles), `${coin} candles is array`)
    assert.ok(candles.length >= 50, `${coin} got ${candles.length} candles (>= 50)`)

    for (const c of candles.slice(0, 3)) {
      assert.ok(typeof c.t === 'number', `${coin} candle has timestamp`)
      assert.ok(parseFloat(c.o) > 0, `${coin} open > 0`)
      assert.ok(parseFloat(c.h) > 0, `${coin} high > 0`)
      assert.ok(parseFloat(c.l) > 0, `${coin} low > 0`)
      assert.ok(parseFloat(c.c) > 0, `${coin} close > 0`)
    }
  }
})

test('candles: OHLCV invariants hold (h >= o,l,c and l <= o,h,c)', async (t) => {
  const candles = await hlPost({
    type: 'candleSnapshot',
    req: { coin: 'BTC', interval: '1h', startTime: Date.now() - 200 * 3600_000, endTime: Date.now() },
  })
  for (const c of candles) {
    const o = parseFloat(c.o), h = parseFloat(c.h), l = parseFloat(c.l), close = parseFloat(c.c)
    assert.ok(h >= o && h >= l && h >= close, `BTC high >= all: h=${h} o=${o} l=${l} c=${close}`)
    assert.ok(l <= o && l <= h && l <= close, `BTC low <= all`)
    const vol = parseFloat(c.v ?? '0')
    assert.ok(vol >= 0, `BTC volume >= 0`)
  }
})

test('combined: universe + allMids + candles pipeline (simulates scanner flow)', async (t) => {
  // This is the exact data flow the scanner uses:
  // 1. get universe (meta)
  // 2. get allMids (one call)
  // 3. for each with price > 0, fetch candles
  const [meta, allMids] = await Promise.all([
    hlPost({ type: 'meta' }),
    hlPost({ type: 'allMids' }),
  ])

  // Filter to markets with price > 0
  const eligible = meta.universe.filter(u => {
    const p = parseFloat(allMids[u.name] ?? '0')
    return p > 0
  })
  assert.ok(eligible.length >= 180, `${eligible.length} eligible markets with price > 0`)

  // Spot-check 3: fetch candles and verify real price variance
  const checks = ['BTC', 'ETH', 'SOL']
  for (const coin of checks) {
    const endTime = Date.now()
    const startTime = endTime - 100 * 300_000
    const candles = await hlPost({
      type: 'candleSnapshot',
      req: { coin, interval: '5m', startTime, endTime },
    })
    assert.ok(candles.length >= 80, `${coin} got ${candles.length} candles`)

    // Verify closes are not all identical (real price data)
    const closes = candles.map(c => parseFloat(c.c))
    const uniqueCloses = new Set(closes.map(v => v.toFixed(4)))
    assert.ok(uniqueCloses.size > 5, `${coin} has price variance (${uniqueCloses.size} unique close prices)`)
  }
})
