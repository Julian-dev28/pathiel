import { NextRequest, NextResponse } from 'next/server'
import { limitSellOrder } from '@/lib/kalshi-trade'
import { hasKalshiAuth } from '@/lib/kalshi-auth'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function POST(req: NextRequest) {
  if (!hasKalshiAuth()) {
    return NextResponse.json({ ok: false, error: 'Kalshi credentials not configured' }, { status: 401 })
  }
  try {
    const { ticker, side, count } = await req.json()
    if (!ticker || !side || !count) {
      return NextResponse.json({ ok: false, error: 'Missing required fields: ticker, side, count' }, { status: 400 })
    }
    if (!['yes', 'no'].includes(side)) {
      return NextResponse.json({ ok: false, error: 'side must be "yes" or "no"' }, { status: 400 })
    }
    // Server-side ticker guard — only allow known Kalshi BTC series
    if (!/^KXBTC(15M|D)-/.test(ticker)) {
      return NextResponse.json({ ok: false, error: `Invalid ticker format: ${ticker}` }, { status: 400 })
    }
    const result = await limitSellOrder({ ticker, side, count })
    return NextResponse.json(result, { status: result.ok ? 200 : 422 })
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 })
  }
}
