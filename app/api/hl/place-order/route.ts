import { NextRequest, NextResponse } from 'next/server'
import { placeHLOrder, getHLPrice, getHLAccount, HL_WALLET } from '@/lib/hyperliquid'

export const runtime = 'nodejs'

interface PlaceOrderRequest {
  side:    'long' | 'short'
  riskPct: number
}

export async function POST(req: NextRequest) {
  const { side, riskPct } = (await req.json()) as PlaceOrderRequest

  try {
    const [midPrice, account] = await Promise.all([
      getHLPrice(),
      getHLAccount(HL_WALLET),
    ])

    if (midPrice <= 0) return NextResponse.json({ ok: false, error: 'invalid price' })

    // Kelly-size: risk% of equity, min 0.001 BTC
    const equity  = account.equity
    const riskUSD = equity > 0 ? (equity * riskPct / 100) : 0
    const sizeBTC = Math.max(0.001, parseFloat((riskUSD / midPrice).toFixed(5)))

    const isBuy = side === 'long'
    const result = await placeHLOrder(isBuy, sizeBTC, midPrice)

    return NextResponse.json({
      ...result,
      sizeBTC,
      midPrice,
      equity,
    })
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 })
  }
}
