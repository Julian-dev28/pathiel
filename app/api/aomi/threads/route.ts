import { NextRequest, NextResponse } from 'next/server'
import { createAomiClient } from '@/lib/aomi-session'

export const runtime = 'nodejs'

export async function GET(req: NextRequest) {
  const publicKey = req.nextUrl.searchParams.get('publicKey')
  if (!publicKey) return NextResponse.json({ threads: [] })
  try {
    const client = createAomiClient()
    const threads = await client.listThreads(publicKey)
    return NextResponse.json({ threads: threads.filter(t => !t.is_archived) })
  } catch {
    return NextResponse.json({ threads: [] })
  }
}

export async function DELETE(req: NextRequest) {
  const sessionId = req.nextUrl.searchParams.get('sessionId')
  if (!sessionId) return NextResponse.json({ error: 'missing sessionId' }, { status: 400 })
  try {
    const client = createAomiClient()
    await client.deleteThread(sessionId)
    return NextResponse.json({ ok: true })
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 })
  }
}
