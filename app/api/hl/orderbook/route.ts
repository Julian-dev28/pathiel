import { NextResponse } from 'next/server'

export const runtime = 'nodejs'

export async function GET() {
  try {
    const res = await fetch('https://api.hyperliquid.xyz/info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'l2Book', coin: 'BTC' }),
    })

    const data = await res.json() as {
      levels: Array<Array<{ px: string; sz: string; n: number }>>
    }

    const [bidsRaw, asksRaw] = data.levels ?? [[], []]

    const bids = bidsRaw.slice(0, 8).map(l => ({ px: l.px, sz: l.sz }))
    const asks = asksRaw.slice(0, 8).map(l => ({ px: l.px, sz: l.sz }))

    return NextResponse.json({ bids, asks })
  } catch (err) {
    return NextResponse.json({ bids: [], asks: [], error: String(err) }, { status: 500 })
  }
}
