import { NextRequest, NextResponse } from 'next/server'
import { createSession } from '@/lib/aomi-session'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET(req: NextRequest) {
  const sessionId = req.nextUrl.searchParams.get('sessionId')
  if (!sessionId) return NextResponse.json({ messages: [] })
  try {
    const session = createSession(sessionId)
    await session.fetchCurrentState()
    const msgs = session.getMessages()
    session.close()
    return NextResponse.json({ messages: msgs })
  } catch {
    return NextResponse.json({ messages: [] })
  }
}
