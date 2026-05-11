import type { HLMarket } from '../hl-universe'
import { pctMoveSpike, volumeSpike, breakout, rangeCompression, compositeScore, type Candle } from './triggers'


// ── Types ─────────────────────────────────────────────────────────────────────

export type Trigger = {
  coin: string
  firedAt: number
  triggers: { name: string; score: number; reason: string }[]
  compositeScore: number
  mid: number
}

type ScanOpts = { universe: HLMarket[]; minScore: number }

// ── Candle cache ──────────────────────────────────────────────────────────────

type CacheEntry = { candles: Candle[]; fetchedAt: number }
const FIVE_MIN = 5 * 60 * 1000
const candleCache = new Map<string, CacheEntry>()

// ── Concurrency limiter ───────────────────────────────────────────────────────

async function withConcurrencyLimit<T>(items: T[], limit: number, fn: (item: T) => Promise<void>): Promise<void> {
  let idx = 0
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (true) {
      const i = idx++
      if (i >= items.length) return
      await fn(items[i])
    }
  })
  await Promise.all(workers)
}

// ── Candle fetcher ────────────────────────────────────────────────────────────

const BASE = typeof window !== 'undefined'
  ? typeof location !== 'undefined' && location.origin ? location.origin : 'http://localhost:3000'
  : process.env.NEXT_PUBLIC_BASE_URL ?? 'http://localhost:3000'

async function fetchCandles(coin: string): Promise<Candle[]> {
  const cached = candleCache.get(coin)
  if (cached && Date.now() - cached.fetchedAt < FIVE_MIN) return cached.candles

  const url = `${BASE}/api/hl/candles?coin=${encodeURIComponent(coin)}&interval=5m&count=100`
  const res = await fetch(url)
  const data = await res.json() as { candles?: Candle[]; error?: string }

  const candles = data.candles ?? []
  if (candles.length > 0) {
    candleCache.set(coin, { candles, fetchedAt: Date.now() })
  }
  return candles
}

async function fetchAllMids(): Promise<Record<string, string>> {
  const res = await fetch(`${BASE}/api/hl/all-mids`)
  const data = await res.json() as Record<string, string>
  return data
}

// ── Scan loop ─────────────────────────────────────────────────────────────────

export async function scanOnce(opts: ScanOpts): Promise<Trigger[]> {
  const t0 = Date.now()

  // 1. Fetch all mids once
  let mids: Record<string, string>
  try {
    mids = await fetchAllMids()
  } catch (err) {
    console.error('[scanner] all-mids fetch failed:', err instanceof Error ? err.message : err)
    return []
  }

  // 2. Filter to coins with price > 0 and in universe
  const eligible = opts.universe.filter(m => {
    const midStr = mids[m.coin]
    if (!midStr) return false
    const mid = parseFloat(midStr)
    return mid > 0
  })

  // 3. Per-coin analysis with bounded concurrency
  const results: Trigger[] = []

  await withConcurrencyLimit(eligible, 20, async (market) => {
    try {
      const candles = await fetchCandles(market.coin)
      if (candles.length < 48) return

      const mid = parseFloat(mids[market.coin]!)

      const triggerResults: { name: string; score: number; reason: string }[] = []

      const pms = pctMoveSpike(candles)
      triggerResults.push({ name: 'pctMoveSpike', score: pms.score, reason: pms.reason })

      const vs = volumeSpike(candles)
      triggerResults.push({ name: 'volumeSpike', score: vs.score, reason: vs.reason })

      const bo = breakout(candles)
      triggerResults.push({ name: 'breakout', score: bo.score, reason: bo.reason })

      const rc = rangeCompression(candles)
      triggerResults.push({ name: 'rangeCompression', score: rc.score, reason: rc.reason })

      const comp = compositeScore(triggerResults)

      if (comp >= opts.minScore) {
        results.push({
          coin: market.coin,
          firedAt: Date.now(),
          triggers: triggerResults,
          compositeScore: Math.round(comp * 100) / 100,
          mid,
        })
      }
    } catch (err) {
      console.error(`[scanner] ${market.coin} failed:`, err instanceof Error ? err.message : err)
    }
  })

  // Sort by score descending
  results.sort((a, b) => b.compositeScore - a.compositeScore)

  const elapsed = Date.now() - t0
  console.log(`[scanner] scan complete in ${elapsed}ms — ${results.length} triggers (of ${eligible.length} eligible)`)

  return results
}
