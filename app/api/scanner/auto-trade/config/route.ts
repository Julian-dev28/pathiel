// ── Auto-trade config: GET/POST .scanner-config.json ─

import { NextRequest, NextResponse } from 'next/server'
import { readAutoTradeConfig, writeAutoTradeConfig, type AutoTradeConfig } from '@/lib/scanner/config'

export const runtime = 'nodejs'

export async function GET() {
  return NextResponse.json(readAutoTradeConfig())
}

export async function POST(req: NextRequest) {
  let body: Partial<AutoTradeConfig>
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'invalid json' }, { status: 400 })
  }

  // LIVE mode requires env flag
  if (body.mode === 'LIVE' && !process.env.ALLOW_LIVE_TRADING) {
    return NextResponse.json({ error: 'LIVE mode requires ALLOW_LIVE_TRADING env var' }, { status: 403 })
  }

  const merged = writeAutoTradeConfig(body)
  return NextResponse.json(merged)
}
