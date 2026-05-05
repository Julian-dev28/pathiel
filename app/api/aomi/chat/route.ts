import { NextRequest } from 'next/server'
import { createSession, buildPrompt } from '@/lib/aomi-session'

export const runtime = 'nodejs'
export const maxDuration = 60

interface ChatRequest {
  message:    string
  hint?:      string
  sessionId?: string
  marketData?: Record<string, unknown>
  riskPct?:   number
}

export async function POST(req: NextRequest) {
  const { message, hint, sessionId, marketData, riskPct } = (await req.json()) as ChatRequest
  console.log('[aomi/chat] POST — sessionId:', sessionId?.slice(0, 8), 'app:', process.env.AOMI_APP ?? 'hyperliquid')

  const session = createSession(sessionId)
  const prompt  = buildPrompt(message, hint)

  if (marketData) session.addExtValue('market_context', marketData)
  if (riskPct != null) session.addExtValue('risk_pct', riskPct)

  const encoder = new TextEncoder()
  let closed = false

  const stream = new ReadableStream({
    async start(controller) {
      function send(data: object) {
        if (closed) return
        try { controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`)) }
        catch { /* write-after-close */ }
      }

      session.on('processing_start', () => send({ type: 'processing_start' }))
      session.on('processing_end',   () => send({ type: 'processing_end'   }))

      session.on('tool_update',  (ev) => send({ type: 'tool', name: (ev as { name?: string }).name ?? 'tool', status: 'running' }))
      session.on('tool_complete',(ev) => send({ type: 'tool', name: (ev as { name?: string }).name ?? 'tool', status: 'done'    }))

      session.on('wallet_eip712_request', async (req) => {
        try { await session.reject(req.id, 'Execution handled by trading system — provide text verdict only') } catch { /* ignore */ }
      })

      session.on('wallet_tx_request', async (req) => {
        try { await session.reject(req.id, 'Execution handled by trading system — provide text verdict only') } catch { /* ignore */ }
      })

      session.on('system_error', ({ message: msg }) => send({ type: 'error', text: msg }))

      try {
        const result    = await session.send(prompt)
        const msgs      = result.messages ?? []
        const assistant = [...msgs].reverse().find(m => m.sender === 'agent')
        const text      = typeof assistant?.content === 'string' && assistant.content
          ? assistant.content : 'No response from agent.'
        send({ type: 'message', text })
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'AOMI request failed'
        console.error('[aomi/chat] session.send failed —',
          'AOMI_BASE_URL:', process.env.AOMI_BASE_URL,
          'AOMI_APP:', process.env.AOMI_APP ?? 'hyperliquid',
          'AOMI_API_KEY set:', !!process.env.AOMI_API_KEY,
          '— error:', msg)
        send({ type: 'error', text: msg })
      } finally {
        send({ type: 'done' })
        closed = true
        try { controller.close() } catch { /* ignore */ }
        session.close()
      }
    },
  })

  return new Response(stream, {
    headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' },
  })
}
