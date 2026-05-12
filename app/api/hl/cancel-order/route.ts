import { NextRequest, NextResponse } from 'next/server'
import { cancelOrders, getCoinIndex } from '@/lib/hyperliquid'

export const runtime = 'nodejs'

export async function POST(req: NextRequest) {
  const { oid, coin } = await req.json() as { oid: number; coin?: string }
  if (!oid) return NextResponse.json({ error: 'oid required' }, { status: 400 })
  
  let assetIdx = 0  // default BTC
  if (coin) {
    const idx = await getCoinIndex(coin)
    assetIdx = idx.index
  }
  
  const result = await cancelOrders(oid, assetIdx)
  return NextResponse.json(result)
}
