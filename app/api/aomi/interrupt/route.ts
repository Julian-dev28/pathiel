import { NextRequest, NextResponse } from 'next/server'
import { createAomiClient } from '@/lib/aomi-session'

export const runtime = 'nodejs'

export async function POST(req: NextRequest) {
  try {
    const { sessionId } = await req.json()
    if (!sessionId) return NextResponse.json({ error: 'missing sessionId' }, { status: 400 })
    const client = createAomiClient()
    await client.interrupt(sessionId)
    return NextResponse.json({ ok: true })
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 })
  }
}
