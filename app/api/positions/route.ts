import { NextResponse } from 'next/server'
import { getPositions, getOrders, getFills } from '@/lib/kalshi-trade'
import { hasKalshiAuth } from '@/lib/kalshi-auth'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET() {
  if (!hasKalshiAuth()) {
    return NextResponse.json({ error: 'Kalshi credentials not configured in .env.local' }, { status: 401 })
  }
  const [posResult, ordResult, fillResult] = await Promise.all([
    getPositions(),
    getOrders('resting'),
    getFills(15),
  ])

  if (!posResult.ok) {
    return NextResponse.json({ error: posResult.error }, { status: posResult.status ?? 502 })
  }

  return NextResponse.json({
    positions: posResult.positions ?? [],
    orders: ordResult.orders ?? [],
    fills: fillResult.fills ?? [],
  })
}
