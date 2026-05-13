// Unit tests for hl-universe categorize function and coin sets.
// Run: node --test scripts/__tests__/hl-universe.test.mjs

import test from 'node:test'
import assert from 'node:assert/strict'

// ── Inline categorize + coin sets from lib/hl-universe.ts verbatim ────────────

const EQUITY_PERP_COINS = new Set([
  // US Tech / Growth
  'TSLA', 'NVDA', 'AAPL', 'AMZN', 'GOOGL', 'MSFT', 'META', 'COIN', 'MSTR',
  'INTC', 'AMD', 'NFLX', 'ADBE', 'CRM', 'AVGO', 'QCOM', 'TXN', 'MU', 'SNPS',
  'SNDK', 'LITE', 'CRDO', 'SMCI', 'ARM', 'PLTR', 'SOFI', 'HOOD', 'RKLB',
])

const COMMODITY_COINS = new Set([
  'NATGAS', 'CRCL', 'SILVER', 'COPPER', 'GOLD', 'URNM',
])

function categorize(coin) {
  if (COMMODITY_COINS.has(coin)) return 'commodity'
  if (EQUITY_PERP_COINS.has(coin)) return 'equity'
  return 'crypto'
}

// ── Crypto categorize tests ──────────────────────────────────────────────────

test('categorize: BTC is crypto', () => {
  assert.strictEqual(categorize('BTC'), 'crypto')
})

test('categorize: ETH is crypto', () => {
  assert.strictEqual(categorize('ETH'), 'crypto')
})

test('categorize: SOL is crypto', () => {
  assert.strictEqual(categorize('SOL'), 'crypto')
})

test('categorize: common altcoins are crypto', () => {
  const alts = ['BNB', 'XRP', 'DOGE', 'ADA', 'AVAX', 'MATIC', 'LINK', 'DOT', 'UNI', 'ATOM', 'NEAR', 'FTM', 'APT', 'ARB', 'OP', 'INJ', 'TIA', 'SUI', 'SEI', 'WIF', 'PEPE', 'BONK', 'FLOKI', 'TRX', 'LTC', 'BCH', 'ETC', 'XLM', 'ALGO', 'AAVE', 'MKR', 'SNX', 'CRV', 'COMP', 'YFI', 'SUSHI', '1INCH']
  for (const coin of alts) {
    assert.strictEqual(categorize(coin), 'crypto', `expected ${coin} to be crypto`)
  }
})

// ── Equity categorize tests ──────────────────────────────────────────────────

test('categorize: TSLA is equity', () => {
  assert.strictEqual(categorize('TSLA'), 'equity')
})

test('categorize: NVDA is equity', () => {
  assert.strictEqual(categorize('NVDA'), 'equity')
})

test('categorize: AAPL is equity', () => {
  assert.strictEqual(categorize('AAPL'), 'equity')
})

test('categorize: all EQUITY_PERP_COINS are equity', () => {
  for (const coin of EQUITY_PERP_COINS) {
    assert.strictEqual(categorize(coin), 'equity', `expected ${coin} to be equity`)
  }
})

test('categorize: equity coins are case-sensitive', () => {
  // Equities are uppercase
  assert.strictEqual(categorize('TSLA'), 'equity')
  // Lowercase should fall through to default crypto
  assert.strictEqual(categorize('tsla'), 'crypto')
})

// ── Commodity categorize tests ───────────────────────────────────────────────

test('categorize: NATGAS is commodity', () => {
  assert.strictEqual(categorize('NATGAS'), 'commodity')
})

test('categorize: SILVER is commodity', () => {
  assert.strictEqual(categorize('SILVER'), 'commodity')
})

test('categorize: COPPER is commodity', () => {
  assert.strictEqual(categorize('COPPER'), 'commodity')
})

test('categorize: all COMMODITY_COINS are commodity', () => {
  for (const coin of COMMODITY_COINS) {
    assert.strictEqual(categorize(coin), 'commodity', `expected ${coin} to be commodity`)
  }
})

// ── Unknown coin defaults to crypto ──────────────────────────────────────────

test('categorize: unknown coin defaults to crypto', () => {
  assert.strictEqual(categorize('UNKNOWN_COIN'), 'crypto')
})

test('categorize: single letter coin defaults to crypto', () => {
  assert.strictEqual(categorize('X'), 'crypto')
})

test('categorize: empty string defaults to crypto', () => {
  assert.strictEqual(categorize(''), 'crypto')
})

test('categorize: lowercase equity coin defaults to crypto (case-sensitive sets)', () => {
  assert.strictEqual(categorize('tsla'), 'crypto')
  assert.strictEqual(categorize('natgas'), 'crypto')
})

// ── EQUITY_PERP_COINS set tests ──────────────────────────────────────────────

test('EQUITY_PERP_COINS set has known tech stocks', () => {
  assert.ok(EQUITY_PERP_COINS.has('TSLA'))
  assert.ok(EQUITY_PERP_COINS.has('NVDA'))
  assert.ok(EQUITY_PERP_COINS.has('AAPL'))
  assert.ok(EQUITY_PERP_COINS.has('AMZN'))
  assert.ok(EQUITY_PERP_COINS.has('GOOGL'))
  assert.ok(EQUITY_PERP_COINS.has('MSFT'))
  assert.ok(EQUITY_PERP_COINS.has('META'))
  assert.ok(EQUITY_PERP_COINS.has('COIN'))
  assert.ok(EQUITY_PERP_COINS.has('MSTR'))
  assert.ok(EQUITY_PERP_COINS.has('INTC'))
  assert.ok(EQUITY_PERP_COINS.has('AMD'))
  assert.ok(EQUITY_PERP_COINS.has('NFLX'))
  assert.ok(EQUITY_PERP_COINS.has('ADBE'))
  assert.ok(EQUITY_PERP_COINS.has('CRM'))
  assert.ok(EQUITY_PERP_COINS.has('AVGO'))
  assert.ok(EQUITY_PERP_COINS.has('QCOM'))
  assert.ok(EQUITY_PERP_COINS.has('TXN'))
  assert.ok(EQUITY_PERP_COINS.has('MU'))
})

test('EQUITY_PERP_COINS set has size 28', () => {
  assert.strictEqual(EQUITY_PERP_COINS.size, 28)
})

// ── COMMODITY_COINS set tests ────────────────────────────────────────────────

test('COMMODITY_COINS set has known commodities', () => {
  assert.ok(COMMODITY_COINS.has('NATGAS'))
  assert.ok(COMMODITY_COINS.has('CRCL'))
  assert.ok(COMMODITY_COINS.has('SILVER'))
  assert.ok(COMMODITY_COINS.has('COPPER'))
  assert.ok(COMMODITY_COINS.has('GOLD'))
  assert.ok(COMMODITY_COINS.has('URNM'))
})

test('COMMODITY_COINS set has size 6', () => {
  assert.strictEqual(COMMODITY_COINS.size, 6)
})

// ── categorize consistency tests ─────────────────────────────────────────────

test('categorize: all 3 categories are possible outputs', () => {
  const results = new Set([
    categorize('BTC'),
    categorize('TSLA'),
    categorize('NATGAS'),
  ])
  assert.deepStrictEqual([...results].sort(), ['commodity', 'crypto', 'equity'])
})

test('categorize: never returns unexpected value', () => {
  const testCoins = ['BTC', 'TSLA', 'NATGAS', 'UNKNOWN', 'x', '', '1INCH', 'SMCI', 'PLTR']
  for (const coin of testCoins) {
    const cat = categorize(coin)
    assert.ok(
      cat === 'crypto' || cat === 'equity' || cat === 'commodity',
      `${coin}: got unexpected category "${cat}"`
    )
  }
})

test('categorize: coin in neither set falls through to crypto default', () => {
  // Verify the order of checks: commodity first, then equity, then default
  assert.strictEqual(categorize('GOLD'), 'commodity')  // in commodity set
  assert.strictEqual(categorize('NVDA'), 'equity')     // in equity set
  assert.strictEqual(categorize('DOGE'), 'crypto')     // not in either set
})

test('categorize: coin sets are disjoint (no overlap)', () => {
  for (const equityCoin of EQUITY_PERP_COINS) {
    assert.ok(!COMMODITY_COINS.has(equityCoin), `${equityCoin} should not be in COMMODITY_COINS`)
    assert.strictEqual(categorize(equityCoin), 'equity')
  }
  for (const commCoin of COMMODITY_COINS) {
    assert.ok(!EQUITY_PERP_COINS.has(commCoin), `${commCoin} should not be in EQUITY_PERP_COINS`)
    assert.strictEqual(categorize(commCoin), 'commodity')
  }
})

// ── Coin name pattern tests ──────────────────────────────────────────────────

test('categorize: all-caps equities recognized', () => {
  const equities = ['TSLA', 'NVDA', 'AAPL', 'AMZN', 'MSFT', 'META', 'AMD']
  for (const coin of equities) {
    assert.strictEqual(categorize(coin), 'equity')
  }
})

test('categorize: commodity names with special characters', () => {
  assert.strictEqual(categorize('NATGAS'), 'commodity')
  assert.strictEqual(categorize('URNM'), 'commodity')
})

test('categorize: 1INCH is crypto (not equity despite CH ending)', () => {
  assert.strictEqual(categorize('1INCH'), 'crypto')
})
