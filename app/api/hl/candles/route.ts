import { NextRequest, NextResponse } from 'next/server'

export const runtime = 'nodejs'

type CandleInterval = '1m' | '5m' | '15m' | '30m' | '1h' | '4h' | '1d'

const VALID_INTERVALS = new Set<CandleInterval>(['1m', '5m', '15m', '30m', '1h', '4h', '1d'])

const INTERVAL_MS: Record<CandleInterval, number> = {
  '1m': 60_000,
  '5m': 5 * 60_000,
  '15m': 15 * 60_000,
  '30m': 30 * 60_000,
  '1h': 60 * 60_000,
  '4h': 4 * 60 * 60_000,
  '1d': 24 * 60 * 60_000,
}

// Legacy window param mapping (preserved for backward compat)
const WINDOW_TO_INTERVAL: Record<string, CandleInterval> = {
  '15m': '1m',
  '30m': '1m',
  '1h': '1m',
}
const WINDOW_MS: Record<string, number> = {
  '15m': 15 * 60_000,
  '30m': 30 * 60_000,
  '1h': 60 * 60_000,
}

export async function GET(req: NextRequest) {
  const params = req.nextUrl.searchParams

  // Backward compat: legacy window param
  const window = params.get('window')

  // New params
  const coin = params.get('coin') ?? 'BTC'
  const intervalParam = params.get('interval')
  const count = params.get('count') ?? params.get('lookback')

  // ── Resolve interval ────────────────────────────────────────────────────
  let interval: CandleInterval
  let lookbackCount: number

  if (window) {
    // legacy path: window → interval + time-based lookback
    interval = WINDOW_TO_INTERVAL[window] ?? '1m'
    const ms = WINDOW_MS[window] ?? WINDOW_MS['1h']
    lookbackCount = Math.round(ms / INTERVAL_MS[interval])
  } else {
    interval = (VALID_INTERVALS.has(intervalParam as CandleInterval) ? intervalParam : '1m') as CandleInterval
    lookbackCount = parseInt(count ?? '100', 10)
    if (isNaN(lookbackCount) || lookbackCount < 1) lookbackCount = 100
  }

  // ── Compute time window ─────────────────────────────────────────────────
  const intvMs = INTERVAL_MS[interval]
  const endTime = Date.now()
  const startTime = endTime - lookbackCount * intvMs

  try {
    const res = await fetch('https://api.hyperliquid.xyz/info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'candleSnapshot', req: { coin, interval, startTime, endTime } }),
    })

    const raw = await res.json() as Array<{ t: number; o: string; h: string; l: string; c: string; v: string }>
    const candles = raw.map(c => ({
      t: c.t,
      o: parseFloat(c.o),
      h: parseFloat(c.h),
      l: parseFloat(c.l),
      c: parseFloat(c.c),
      v: parseFloat(c.v ?? '0'),
    }))

    return NextResponse.json({ candles })
  } catch (err) {
    return NextResponse.json({ candles: [], error: String(err) })
  }
}
