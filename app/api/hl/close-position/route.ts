import { NextResponse } from 'next/server'
import { placeHLOrder, getHLPrice, getHLAccount, HL_ACCOUNT } from '@/lib/hyperliquid'

export const runtime = 'nodejs'

export async function POST() {
  try {
    const [midPrice, account] = await Promise.all([
      getHLPrice(),
      getHLAccount(HL_ACCOUNT),
    ])

    if (!account.position) return NextResponse.json({ ok: false, error: 'no open position' })
    if (midPrice <= 0)      return NextResponse.json({ ok: false, error: 'invalid price' })

    const { sizeBTC, side } = account.position
    const isBuy = side === 'short'
    const result = await placeHLOrder(isBuy, sizeBTC, midPrice)

    return NextResponse.json({ ...result, sizeBTC, midPrice })
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 })
  }
}
