import { NextResponse } from 'next/server'
import { getHLPrice } from '@/lib/hyperliquid'

export const runtime = 'nodejs'
export const revalidate = 0

export async function GET() {
  try {
    const price = await getHLPrice()
    return NextResponse.json({ price })
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 })
  }
}
