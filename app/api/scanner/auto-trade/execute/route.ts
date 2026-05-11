// ── Auto-trade execute: trigger -> AI analysis -> risk gates -> trade ─

import { NextRequest, NextResponse } from 'next/server'
import { maybeAutoTrade, type AIVerdict } from '@/lib/scanner/auto-trader'
import { triggerStore } from '@/lib/scanner/store'
import { readAutoTradeConfig } from '@/lib/scanner/config'
import type { AutoTradeConfig } from '@/lib/scanner/config'

export const runtime = 'nodejs'

const WALLET = process.env.HYPERLIQUID_WALLET_ADDRESS ?? ''

export async function POST(req: NextRequest) {
  const { triggerId } = await req.json() as { triggerId?: string }
  
  // Find trigger in store
  const allHistory = triggerStore.getHistory(500)
  const trigger = triggerId 
    ? allHistory.find(t => `${t.coin}-${t.firedAt}` === triggerId)
    : undefined

  if (!trigger) {
    return NextResponse.json({ error: 'trigger not found' }, { status: 404 })
  }

  // Load config
  const config = readAutoTradeConfig()

  // Run AI analysis 
  // TODO: extract the existing AI analysis pipeline from app/agent/page.tsx
  // into a callable lib function. For now, we call the chat API endpoint.
  const baseUrl = process.env.NEXT_PUBLIC_BASE_URL || 'http://localhost:3000'
  
  const aiRes = await fetch(`${baseUrl}/api/aomi/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: `Analyze ${trigger.coin}-PERP for swing trade. Current mid: $${trigger.mid}`,
      marketData: { coin: trigger.coin, price: trigger.mid },
    }),
  })

  if (!aiRes.ok || !aiRes.body) {
    return NextResponse.json({ error: 'ai_analysis_failed' }, { status: 500 })
  }

  // Read streaming response and parse verdict
  // The AI response format is: "LONG X%" / "SHORT X%" / "PASS X%"
  const reader = aiRes.body.getReader()
  const decoder = new TextDecoder()
  let fullText = ''
  let buf = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() ?? ''
    
    for (const part of parts) {
      if (!part.startsWith('data: ')) continue
      try {
        const ev = JSON.parse(part.slice(6))
        if (ev.type === 'message') fullText = ev.text
      } catch { /* skip malformed chunks */ }
    }
  }

  // Parse AI verdict from response
  const rawLine = fullText.split('\n').find(l => l.trim())?.trim() ?? ''
  const isLong = /^LONG\b/i.test(rawLine)
  const isShort = /^SHORT\b/i.test(rawLine)
  const isPass = /^PASS\b/i.test(rawLine)

  const inlineMatch = fullText.match(/^(?:LONG|SHORT|CLOSE|PASS)\s*[-–—]?\s*(\d+)\s*%/im)
  const labeledMatch = fullText.match(/confidence[^:]*:?\s*(\d+)\s*%/i)
  const confNum = inlineMatch ? parseInt(inlineMatch[1]) : labeledMatch ? parseInt(labeledMatch[1]) : 0

  const verdict: AIVerdict = {
    side: isLong ? 'long' : isShort ? 'short' : 'pass',
    confidence: confNum,
    summary: rawLine,
  }

  // Run auto-trade pipeline
  const result = await maybeAutoTrade(trigger, verdict, WALLET, config)

  return NextResponse.json(result)
}
