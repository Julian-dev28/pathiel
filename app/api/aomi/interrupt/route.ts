import { NextResponse } from 'next/server'

export const runtime = 'nodejs'

// Client-side AbortController handles stream cancellation.
// This endpoint exists for compatibility and always returns ok.
export async function POST() {
  return NextResponse.json({ ok: true })
}
