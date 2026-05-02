import { NextRequest, NextResponse } from 'next/server'
import { placeHLOrder, setLeverage, transferSpotToPerp, getHLPrice, getHLAccount, HL_ACCOUNT, HL_LEVERAGE } from '@/lib/hyperliquid'

export const runtime = 'nodejs'

interface PlaceOrderRequest {
  side:      'long' | 'short'
  riskUSD?:  number   // explicit dollar amount
  riskPct?:  number   // fallback % of equity
  leverage?: number
}

export async function POST(req: NextRequest) {
  const { side, riskUSD: riskUSDParam, riskPct, leverage = HL_LEVERAGE } = (await req.json()) as PlaceOrderRequest

  try {
    const [midPrice, account] = await Promise.all([
      getHLPrice(),
      getHLAccount(HL_ACCOUNT),
    ])

    if (midPrice <= 0) return NextResponse.json({ ok: false, error: 'invalid price' })

    if (account.equity === 0 && account.spotUSDC > 0) {
      await transferSpotToPerp(account.spotUSDC)
      const updated = await getHLAccount(HL_ACCOUNT)
      Object.assign(account, updated)
    }

    const totalEquity = account.totalEquity
    const riskUSD     = riskUSDParam != null && riskUSDParam > 0
      ? riskUSDParam
      : totalEquity > 0 ? (totalEquity * (riskPct ?? 2) / 100) : 0
    const notional    = Math.max(riskUSD * leverage, midPrice * 0.001)
    const sizeBTC     = parseFloat((notional / midPrice).toFixed(5))
    const isBuy       = side === 'long'

    await setLeverage(leverage)
    const result = await placeHLOrder(isBuy, sizeBTC, midPrice)

    return NextResponse.json({ ...result, sizeBTC, midPrice, equity: totalEquity, leverage })
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 })
  }
}
