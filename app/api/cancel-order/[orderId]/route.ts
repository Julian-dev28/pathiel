import { NextRequest, NextResponse } from 'next/server'
import { cancelOrder } from '@/lib/kalshi-trade'
import { hasKalshiAuth } from '@/lib/kalshi-auth'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function DELETE(_req: NextRequest, { params }: { params: Promise<{ orderId: string }> }) {
  if (!hasKalshiAuth()) {
    return NextResponse.json({ ok: false, error: 'Kalshi credentials not configured' }, { status: 401 })
  }
  const { orderId } = await params
  const result = await cancelOrder(orderId)
  return NextResponse.json(result, { status: result.ok ? 200 : 422 })
}
