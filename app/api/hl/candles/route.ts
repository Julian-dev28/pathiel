import { NextRequest, NextResponse } from 'next/server'

export const runtime = 'nodejs'

export async function GET(req: NextRequest) {
  const window = req.nextUrl.searchParams.get('window') ?? '1h'

  const windowMs: Record<string, number> = {
    '15m': 15 * 60 * 1000,
    '1h':  60 * 60 * 1000,
  }
  const interval: Record<string, string> = {
    '15m': '1m',
    '1h':  '1m',
  }

  const ms        = windowMs[window] ?? windowMs['1h']
  const intv      = interval[window] ?? '1m'
  const endTime   = Date.now()
  const startTime = endTime - ms

  try {
    const res = await fetch('https://api.hyperliquid.xyz/info', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ type: 'candleSnapshot', req: { coin: 'BTC', interval: intv, startTime, endTime } }),
    })

    const raw = await res.json() as Array<{ t: number; o: string; h: string; l: string; c: string; v: string }>
    const candles = raw.map(c => ({
      t: c.t,
      o: parseFloat(c.o),
      h: parseFloat(c.h),
      l: parseFloat(c.l),
      c: parseFloat(c.c),
      v: parseFloat(c.v ?? '0'),
    }))

    return NextResponse.json({ candles })
  } catch (err) {
    return NextResponse.json({ candles: [], error: String(err) })
  }
}
