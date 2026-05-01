import { NextResponse } from 'next/server'
import { getBalance } from '@/lib/kalshi-trade'
import { hasKalshiAuth } from '@/lib/kalshi-auth'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET() {
  if (!hasKalshiAuth()) {
    return NextResponse.json({ error: 'Kalshi credentials not configured in .env.local' }, { status: 401 })
  }
  const result = await getBalance()
  if (!result.ok) {
    return NextResponse.json({ error: result.error }, { status: result.status ?? 502 })
  }
  return NextResponse.json(result.data)
}
