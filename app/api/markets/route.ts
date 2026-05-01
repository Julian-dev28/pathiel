import { NextResponse } from 'next/server'
import { buildKalshiHeaders } from '@/lib/kalshi-auth'
import { normalizeKalshiMarket } from '@/lib/types'
import { KALSHI_HOST, getCurrentEventTicker } from '@/lib/kalshi'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/** A market is still tradeable: window hasn't closed and prices are live (not 0 or 100 = settled extremes) */
function isTradeable(m: { yes_ask: number; close_time?: string }): boolean {
  if (m.close_time && new Date(m.close_time).getTime() <= Date.now()) return false
  return m.yes_ask > 0 && m.yes_ask < 100
}

export async function GET() {
  let lastStatus = 0
  let lastError  = ''

  // ── Attempt 1: query by current event_ticker (most precise) ──────────────
  try {
    const eventTicker = getCurrentEventTicker()
    const path = `/trade-api/v2/markets?event_ticker=${eventTicker}&limit=5`
    const res = await fetch(`${KALSHI_HOST}${path}`, {
      headers: { ...buildKalshiHeaders('GET', path), Accept: 'application/json' },
      cache: 'no-store',
    })
    lastStatus = res.status
    if (res.ok) {
      const data = await res.json()
      const active = (data.markets ?? []).map(normalizeKalshiMarket).filter(isTradeable)
      if (active.length > 0) return NextResponse.json({ ...data, markets: active })
      lastError = 'no_tradeable_markets'
    } else {
      lastError = `kalshi_${res.status}`
    }
  } catch (e) {
    lastError  = 'network_error'
    lastStatus = 0
    console.error('[/api/markets] attempt 1 threw:', e)
  }

  // ── Attempt 2: series query with auth ─────────────────────────────────────
  try {
    const path = '/trade-api/v2/markets?series_ticker=KXBTC15M&limit=20'
    const res = await fetch(`${KALSHI_HOST}${path}`, {
      headers: { ...buildKalshiHeaders('GET', path), Accept: 'application/json' },
      cache: 'no-store',
    })
    lastStatus = res.status
    if (res.ok) {
      const data = await res.json()
      const active = (data.markets ?? []).map(normalizeKalshiMarket).filter(isTradeable)
      if (active.length > 0) return NextResponse.json({ ...data, markets: active })
      lastError = 'no_tradeable_markets'
    } else {
      lastError = `kalshi_${res.status}`
    }
  } catch (e) {
    lastError  = 'network_error'
    lastStatus = 0
    console.error('[/api/markets] attempt 2 threw:', e)
  }

  return NextResponse.json(
    { error: lastError, kalshi_status: lastStatus, markets: [] },
    { status: 503 },
  )
}
