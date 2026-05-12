// Shared Hyperliquid API client — single source for HL API calls and helpers.
import type { HLClearinghouseState, HLSpotClearinghouseState, HLCandleRow, Candle } from './types'

export const HL_API = 'https://api.hyperliquid.xyz'

// ── Generic HL info API POST ──
export async function hlCall<T>(body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${HL_API}/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(10_000),
  })
  if (!res.ok) throw new Error(`HL API ${res.status}`)
  return res.json() as Promise<T>
}

// ── Candle interval map ──
export const MS_PER_CANDLE: Record<string, number> = {
  '1m': 60_000, '5m': 300_000, '15m': 900_000,
  '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000,
}

// ── Fetch candles from HL ──
export async function fetchHLCandles(coin: string, interval: string, count: number): Promise<Candle[]> {
  const ms = MS_PER_CANDLE[interval] ?? 300_000
  const endTime = Date.now()
  const startTime = endTime - ms * count
  const raw = await hlCall<HLCandleRow[]>({
    type: 'candleSnapshot',
    req: { coin, interval, startTime, endTime },
  })
  if (!Array.isArray(raw)) return []
  return raw.map(c => ({
    t: c.t, o: parseFloat(c.o), h: parseFloat(c.h),
    l: parseFloat(c.l), c: parseFloat(c.c), v: parseFloat(c.v ?? '0'),
  }))
}

// ── Account state (perp margin + spot balances) ──
export async function fetchAccountState(user: string): Promise<{
  equity: number
  totalNtl: number
  spotBalances: Array<{ coin: string; total: string }>
  assetPositions: Array<{ coin: string; szi: string }>
}> {
  const [perp, spot] = await Promise.all([
    hlCall<HLClearinghouseState>({ type: 'clearinghouseState', user }),
    hlCall<HLSpotClearinghouseState>({ type: 'spotClearinghouseState', user }),
  ])

  // On unified accounts, equity = perp marginValue OR spot USDC (whichever is > 0)
  const perpEquity = parseFloat(perp.marginSummary?.accountValue ?? '0')
  const totalNtl = parseFloat(perp.marginSummary?.totalNtlPos ?? '0')
  const spotBalances = (spot.balances ?? [])
    .filter(b => ['USDC', 'USDT', 'USD'].includes(b.coin))
  const assetPositions = (perp.assetPositions ?? [])
    .filter(p => parseFloat(p.position.szi) !== 0)
    .map(p => ({ coin: p.position.coin, szi: p.position.szi }))

  const spotUSDC = spotBalances.find(b => b.coin === 'USDC')
  const equity = perpEquity > 0 ? perpEquity : (spotUSDC ? parseFloat(spotUSDC.total) : 0)

  return { equity, totalNtl, spotBalances, assetPositions }
}
