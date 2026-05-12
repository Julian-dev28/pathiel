import { NextResponse } from 'next/server'
import { HL_API, HL_ACCOUNT, getAllPositions } from '@/lib/hyperliquid'

export const runtime = 'nodejs'

export async function GET() {
  try {
    // Fetch perp positions
    const perpRes = await fetch(`${HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'clearinghouseState', user: HL_ACCOUNT }),
    })
    const perpData = await perpRes.json() as {
      marginSummary?: { accountValue: string; totalNtlPos: string }
      assetPositions?: Array<{ position: { coin: string; szi: string; entryPx: string; unrealizedPnl: string; leverage?: { value: string } } }>
    }

    // On unified accounts, perp equity can show $0 — fall back to spot balance
    let equity = parseFloat(perpData.marginSummary?.accountValue ?? '0')
    let totalNotional = parseFloat(perpData.marginSummary?.totalNtlPos ?? '0')

    if (equity === 0) {
      const spotRes = await fetch(`${HL_API}/info`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'spotClearinghouseState', user: HL_ACCOUNT }),
      })
      const spotData = await spotRes.json() as { balances?: Array<{ coin: string; total: string }> }
      const usdc = (spotData.balances ?? []).find(b => b.coin === 'USDC')
      equity = usdc ? parseFloat(usdc.total) : 0
    }

    // Fetch allMids for live mark prices
    const midsRes = await fetch(`${HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'allMids' }),
    })
    const mids = await midsRes.json() as Record<string, string>

    const positions = getAllPositions(perpData)

    const enriched = positions.map(p => {
      const markPrice = parseFloat(mids[p.coin] ?? '0')
      const livePnl = markPrice > 0
        ? (p.side === 'long'
            ? (markPrice - p.entryPx) * p.szi
            : (p.entryPx - markPrice) * p.szi)
        : p.unrealizedPnl
      return { ...p, markPrice, livePnl }
    })

    return NextResponse.json({ equity, totalNotional, positions: enriched })
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 })
  }
}
