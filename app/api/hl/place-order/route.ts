import { NextRequest, NextResponse } from 'next/server'
import { placeHLOrder, setLeverage, getHLPrice, getHLAccount, HL_WALLET, HL_LEVERAGE } from '@/lib/hyperliquid'

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

    // Use total equity (perp + spot) for sizing; min 0.001 BTC
    const totalEquity = account.totalEquity
    const riskUSD     = totalEquity > 0 ? (totalEquity * riskPct / 100) : 0
    // With 5x leverage the position is worth riskUSD * HL_LEVERAGE
    const notional  = Math.max(riskUSD * HL_LEVERAGE, midPrice * 0.001)
    const sizeBTC   = parseFloat((notional / midPrice).toFixed(5))

    const isBuy = side === 'long'

    // Set leverage before placing order
    await setLeverage(HL_LEVERAGE)

    const result = await placeHLOrder(isBuy, sizeBTC, midPrice)

    return NextResponse.json({
      ...result,
      sizeBTC,
      midPrice,
      equity:    totalEquity,
      leverage:  HL_LEVERAGE,
    })
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 })
  }
}
