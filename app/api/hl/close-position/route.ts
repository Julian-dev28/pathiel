import { NextRequest, NextResponse } from 'next/server'
import { placeHLOrder, getCoinIndex } from '@/lib/hyperliquid'
import { HL_API, HL_ACCOUNT } from '@/lib/hyperliquid'

export const runtime = 'nodejs'

export async function POST(req: NextRequest) {
  try {
    const body = await req.json() as { coin?: string }
    const coin = (body?.coin || 'BTC').toUpperCase()

    const midRes = await fetch(`${HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'allMids' }),
    })
    const mids = await midRes.json() as Record<string, string>
    const midPrice = parseFloat(mids[coin] || '0')
    if (midPrice <= 0) return NextResponse.json({ ok: false, error: `invalid price for ${coin}` })

    const idx = await getCoinIndex(coin)
    const acctRes = await fetch(`${HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'clearinghouseState', user: HL_ACCOUNT }),
    })
    const acct = await acctRes.json() as {
      assetPositions?: Array<{ position: { coin: string; szi: string } }>
    }
    const pos = (acct.assetPositions ?? []).find(p => p.position.coin === coin)
    if (!pos) return NextResponse.json({ ok: false, error: `no open position for ${coin}` })

    const szi = parseFloat(pos.position.szi)
    const isLong = szi > 0
    // Close: sell if long, buy if short
    const result = await placeHLOrder(!isLong, Math.abs(szi), midPrice, coin, idx.index)
    return NextResponse.json({ ...result, coin, side: isLong ? 'long' : 'short', size: Math.abs(szi), midPrice })
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 })
  }
}
