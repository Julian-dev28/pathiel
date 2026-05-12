import { HL_API } from './hyperliquid'

// ── Types ─────────────────────────────────────────────────────────────────────

export type HLMarket = {
  coin: string
  type: 'perp' | 'spot'
  category: 'crypto' | 'equity' | 'commodity'
  szDecimals: number
  maxLeverage: number
  minNotional?: number
}

// HL perp meta shape (the { type: "meta" } response)
interface HLMeta {
  universe: Array<{
    name: string
    szDecimals: number
    maxLeverage: number
    // minNtl is occasionally present for certain markets
    minNtl?: string
  }>
}

// HL spot meta shape (the { type: "spotMeta" } response)
interface HLSpotMeta {
  universe: Array<{
    name: string
    szDecimals: number
    // spot markets don't expose maxLeverage in the same way; treated as 1x
    tokens?: number[]
    index: number
  }>
  tokens: Array<{
    name: string
    szDecimals?: number
  }>
}

// ── Constants ─────────────────────────────────────────────────────────────────

// Known equity perp coins on HL. New ones appear via meta automatically.
const EQUITY_PERP_COINS = new Set([
  // US Tech / Growth
  'TSLA', 'NVDA', 'AAPL', 'AMZN', 'GOOGL', 'MSFT', 'META', 'COIN', 'MSTR',
  'INTC', 'AMD', 'NFLX', 'ADBE', 'CRM', 'AVGO', 'QCOM', 'TXN', 'MU', 'SNPS',
  'SNDK', 'LITE', 'CRDO', 'SMCI', 'ARM', 'PLTR', 'SOFI', 'HOOD', 'RKLB',
])

// Commodities on HL
const COMMODITY_COINS = new Set([
  'NATGAS', 'CRCL', 'SILVER', 'COPPER', 'GOLD', 'URNM',
])

const CACHE_TTL_MS = 60 * 60 * 1000 // 1 hour

// ── Cache ─────────────────────────────────────────────────────────────────────

let universeCache: { value: HLMarket[]; ttl: number } | null = null

// ── Fetchers ──────────────────────────────────────────────────────────────────

function categorize(coin: string): 'crypto' | 'equity' | 'commodity' {
  if (COMMODITY_COINS.has(coin)) return 'commodity'
  if (EQUITY_PERP_COINS.has(coin)) return 'equity'
  return 'crypto'
}

async function fetchPerpUniverse(): Promise<HLMarket[]> {
  const res = await fetch(`${HL_API}/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'meta' }),
  })
  if (!res.ok) throw new Error(`meta fetch failed: ${res.status} ${res.statusText}`)
  const meta = await res.json() as HLMeta

  return (meta.universe ?? []).map(u => ({
    coin: u.name,
    type: 'perp',
    category: categorize(u.name),
    szDecimals: u.szDecimals,
    maxLeverage: u.maxLeverage,
    minNotional: u.minNtl !== undefined ? parseFloat(u.minNtl) : undefined,
  }))
}

async function fetchSpotUniverse(): Promise<HLMarket[]> {
  const res = await fetch(`${HL_API}/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'spotMeta' }),
  })
  if (!res.ok) throw new Error(`spotMeta fetch failed: ${res.status} ${res.statusText}`)
  const spot = await res.json() as HLSpotMeta

  return (spot.universe ?? []).map(u => ({
    coin: u.name,
    type: 'spot',
    category: 'crypto' as const,
    szDecimals: typeof u.szDecimals === 'number'
      ? u.szDecimals
      : (spot.tokens[u.index]?.szDecimals ?? 6),
    maxLeverage: 1,
  }))
}

// ── Public API ────────────────────────────────────────────────────────────────

export async function getUniverse(forceRefresh = false): Promise<HLMarket[]> {
  if (!forceRefresh && universeCache && universeCache.ttl > Date.now()) {
    return universeCache.value
  }

  const [perps, spots] = await Promise.all([
    fetchPerpUniverse(),
    fetchSpotUniverse(),
  ])

  const all = [...perps, ...spots]
  universeCache = { value: all, ttl: Date.now() + CACHE_TTL_MS }
  return all
}

export function getMarketByCoin(coin: string): HLMarket | undefined {
  if (!universeCache) return undefined
  return universeCache.value.find(m => m.coin === coin)
}
