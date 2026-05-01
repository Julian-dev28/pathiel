import { NextRequest, NextResponse } from 'next/server'
import { ingestKalshiSecrets } from '@/lib/aomi-session'

export const runtime = 'nodejs'

export async function POST(req: NextRequest) {
  const { clientId } = (await req.json()) as { clientId: string }
  if (!clientId) return NextResponse.json({ error: 'clientId required' }, { status: 400 })

  const secrets: Record<string, string> = {}
  if (process.env.KALSHI_API_KEY) secrets['KALSHI_API_KEY'] = process.env.KALSHI_API_KEY

  if (!Object.keys(secrets).length) {
    return NextResponse.json({ ok: true, registered: 0, note: 'no credentials configured' })
  }

  try {
    const result = await ingestKalshiSecrets(clientId, secrets)
    return NextResponse.json({ ok: true, registered: Object.keys(secrets).length, handles: result })
  } catch (err) {
    return NextResponse.json({ ok: false, error: err instanceof Error ? err.message : 'failed' }, { status: 500 })
  }
}
