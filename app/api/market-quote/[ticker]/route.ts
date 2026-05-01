import { NextRequest, NextResponse } from 'next/server'
import { buildKalshiHeaders } from '@/lib/kalshi-auth'
import { normalizeKalshiMarket } from '@/lib/types'
import { KALSHI_HOST } from '@/lib/kalshi'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/** Fetch a single market's live quote (yes/no bid/ask) with retry on 429 */
async function fetchWithRetry(url: string, path: string, retries = 3): Promise<Response> {
  for (let i = 0; i < retries; i++) {
    const res = await fetch(url, {
      headers: { ...buildKalshiHeaders('GET', path), Accept: 'application/json' },
      cache: 'no-store',
    })
    if (res.status !== 429) return res
    const retryAfter = res.headers.get('Retry-After')
    const waitMs = retryAfter ? parseFloat(retryAfter) * 1000 : 500 * (i + 1)
    await new Promise(r => setTimeout(r, waitMs))
  }
  // Last attempt — return whatever we get
  return fetch(url, {
    headers: { ...buildKalshiHeaders('GET', path), Accept: 'application/json' },
    cache: 'no-store',
  })
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ ticker: string }> }
) {
  const { ticker } = await params
  const path = `/trade-api/v2/markets/${encodeURIComponent(ticker)}`
  try {
    const res = await fetchWithRetry(`${KALSHI_HOST}${path}`, path)
    if (!res.ok) {
      return NextResponse.json({ error: `Kalshi ${res.status}` }, { status: res.status })
    }
    const data = await res.json()
    // Kalshi returns { market: {...} } for single-market endpoint
    const raw = data.market ?? data
    return NextResponse.json({ market: normalizeKalshiMarket(raw) })
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
}
