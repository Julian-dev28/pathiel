import { NextResponse } from 'next/server'
import { getHLAccount, HL_ACCOUNT } from '@/lib/hyperliquid'

export const runtime = 'nodejs'
export const revalidate = 0

export async function GET() {
  try {
    const account = await getHLAccount(HL_ACCOUNT)
    return NextResponse.json(account)
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 })
  }
}
