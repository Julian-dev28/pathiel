import { NextResponse } from 'next/server'

export const runtime = 'nodejs'

export async function POST() {
  return NextResponse.json({ ok: true, registered: 0, note: 'secrets not used with Hyperliquid' })
}
