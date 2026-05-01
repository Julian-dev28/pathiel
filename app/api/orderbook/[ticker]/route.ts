import { NextRequest, NextResponse } from 'next/server'
import { KALSHI_BASE } from '@/lib/kalshi'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ ticker: string }> }
) {
  const { ticker } = await params
  try {
    const res = await fetch(
      `${KALSHI_BASE}/markets/${encodeURIComponent(ticker)}/orderbook`,
      {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
        signal: AbortSignal.timeout(5_000),
      }
    )

    if (!res.ok) {
      return NextResponse.json(
        { error: `Kalshi orderbook ${res.status}` },
        { status: res.status }
      )
    }

    const data = await res.json()
    return NextResponse.json(data)
  } catch (e) {
    return NextResponse.json(
      { error: `Orderbook fetch failed: ${e instanceof Error ? e.message : String(e)}` },
      { status: 502 }
    )
  }
}
