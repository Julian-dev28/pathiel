import { NextRequest } from 'next/server'
import { createSession, buildPrompt } from '@/lib/aomi-session'
import type { WalletRequest } from '@aomi-labs/client'

export const runtime = 'nodejs'
export const maxDuration = 60

interface ChatRequest {
  message: string
  hint?: string
  sessionId?: string
  marketData?: Record<string, unknown>
  riskPct?: number
}

export async function POST(req: NextRequest) {
  const { message, hint, sessionId, marketData, riskPct } = (await req.json()) as ChatRequest
  const session = createSession(sessionId)
  const prompt  = buildPrompt(message, hint)

  // Inject live market context and risk setting into session state — persists
  // across calls in the same session so the agent always has fresh numbers
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

      // ── Processing lifecycle ──────────────────────────────────────────────
      session.on('processing_start', () => send({ type: 'processing_start' }))
      session.on('processing_end',   () => send({ type: 'processing_end'   }))

      // ── Tool call visibility ──────────────────────────────────────────────
      session.on('tool_update',  (ev) => send({ type: 'tool', name: (ev as { name?: string }).name ?? 'tool', status: 'running' }))
      session.on('tool_complete',(ev) => send({ type: 'tool', name: (ev as { name?: string }).name ?? 'tool', status: 'done'    }))

      // ── wallet_tx_request: AOMI wants to execute a trade ─────────────────
      // AOMI's Kalshi plugin emits this when it's ready to place an order.
      // We call our signed Kalshi API, then resolve/reject back to AOMI.
      session.on('wallet_tx_request', async (req: WalletRequest) => {
        send({ type: 'trade_request', requestId: req.id, payload: req.payload })

        try {
          // The Kalshi plugin encodes order details in the payload.
          // We forward to our own authenticated Kalshi endpoint.
          const payload = req.payload as Record<string, unknown>
          const orderRes = await fetch(
            new URL('/api/place-order', process.env.NEXTAUTH_URL ?? 'http://localhost:3000').href,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                ticker:          payload.ticker ?? payload.market_ticker,
                side:            payload.side,
                count:           payload.count ?? 1,
                yesPrice:        payload.yes_price,
                noPrice:         payload.no_price,
                clientOrderId:   `aomi-${Date.now()}`,
              }),
            }
          )
          const orderData = await orderRes.json()
          if (orderRes.ok && orderData.ok) {
            await session.resolve(req.id, { txHash: orderData.orderId ?? 'placed' })
            send({ type: 'trade_confirmed', requestId: req.id, orderId: orderData.orderId })
          } else {
            await session.reject(req.id, orderData.error ?? 'order failed')
            send({ type: 'trade_rejected', requestId: req.id, reason: orderData.error })
          }
        } catch (err) {
          const reason = err instanceof Error ? err.message : 'execution error'
          await session.reject(req.id, reason)
          send({ type: 'trade_rejected', requestId: req.id, reason })
        }
      })

      session.on('system_error', ({ message: msg }) => send({ type: 'error', text: msg }))

      try {
        const result = await session.send(prompt)
        const msgs      = result.messages ?? []
        const assistant = [...msgs].reverse().find(m => m.sender === 'agent')
        const text      = typeof assistant?.content === 'string' && assistant.content
          ? assistant.content : 'No response from agent.'
        send({ type: 'message', text })
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'AOMI request failed'
        console.error('[aomi/chat] session.send failed — AOMI_APP:', process.env.AOMI_APP, '— error:', msg)
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
