import { NextResponse } from 'next/server'
import { HL_API, HL_ACCOUNT, getAllPositions } from '@/lib/hyperliquid'

export const runtime = 'nodejs'

export async function GET() {
  try {
    const res = await fetch(`${HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'clearinghouseState', user: HL_ACCOUNT }),
    })
    const raw = await res.json() as {
      marginSummary?: { accountValue: string; totalNtlPos: string }
      assetPositions?: Array<{
        position: {
          coin: string
          szi: string
          entryPx: string
          unrealizedPnl: string
          leverage?: { value: string }
        }
      }>
    }
    const positions = getAllPositions(raw)
    const equity = parseFloat(raw.marginSummary?.accountValue ?? '0')
    const totalNotional = parseFloat(raw.marginSummary?.totalNtlPos ?? '0')

    // Fetch allMids for live mark prices
    const midsRes = await fetch(`${HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'allMids' }),
    })
    const mids = await midsRes.json() as Record<string, string>

    const enriched = positions.map(p => {
      const markPrice = parseFloat(mids[p.coin] ?? '0')
      const livePnl = markPrice > 0
        ? (p.side === 'long'
            ? (markPrice - p.entryPx) * p.szi
            : (p.entryPx - markPrice) * p.szi)
        : p.unrealizedPnl
      return {
        ...p,
        markPrice,
        livePnl,
      }
    })

    return NextResponse.json({
      equity,
      totalNotional,
      positions: enriched,
    })
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 })
  }
}
