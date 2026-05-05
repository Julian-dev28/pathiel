import { NextRequest } from 'next/server'
import { createOpenAIClient, OPENROUTER_MODEL, SYSTEM, FORMAT, TOOLS, executeTool } from '@/lib/aomi-session'
import type OpenAI from 'openai'

export const runtime   = 'nodejs'
export const maxDuration = 60

interface ChatRequest {
  message:     string
  hint?:       string
  sessionId?:  string
  marketData?: Record<string, unknown>
  riskPct?:    number
}

export async function POST(req: NextRequest) {
  const { message, hint } = (await req.json()) as ChatRequest

  const client  = createOpenAIClient()
  const encoder = new TextEncoder()
  let closed    = false

  const stream = new ReadableStream({
    async start(controller) {
      function send(data: object) {
        if (closed) return
        try { controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`)) }
        catch { /* write-after-close */ }
      }

      const systemContent = [
        SYSTEM,
        hint ? `Live market snapshot (use tools to verify/supplement):\n${hint}` : '',
        FORMAT,
      ].filter(Boolean).join('\n\n')

      const messages: OpenAI.ChatCompletionMessageParam[] = [
        { role: 'system', content: systemContent },
        { role: 'user',   content: message },
      ]

      send({ type: 'processing_start' })

      try {
        let loops = 0

        while (loops < 10) {
          loops++

          const response = await client.chat.completions.create({
            model:       OPENROUTER_MODEL,
            messages,
            tools:       TOOLS,
            tool_choice: 'auto',
            stream:      true,
          })

          let assistantText = ''
          const toolAccum: Record<number, { id: string; name: string; arguments: string }> = {}
          let finishReason: string | null = null

          for await (const chunk of response) {
            const choice = chunk.choices[0]
            if (!choice) continue
            finishReason = choice.finish_reason ?? finishReason

            const delta = choice.delta

            if (delta.content) {
              assistantText += delta.content
              send({ type: 'message', text: assistantText })
            }

            if (delta.tool_calls) {
              for (const tc of delta.tool_calls) {
                const idx = tc.index
                if (!toolAccum[idx]) toolAccum[idx] = { id: '', name: '', arguments: '' }
                if (tc.id)                  toolAccum[idx].id        += tc.id
                if (tc.function?.name)      toolAccum[idx].name      += tc.function.name
                if (tc.function?.arguments) toolAccum[idx].arguments += tc.function.arguments
              }
            }
          }

          const toolCalls = Object.values(toolAccum)

          if (toolCalls.length > 0 && finishReason === 'tool_calls') {
            // Append assistant message with tool_calls
            messages.push({
              role:       'assistant',
              content:    assistantText || null,
              tool_calls: toolCalls.map(tc => ({
                id:       tc.id,
                type:     'function' as const,
                function: { name: tc.name, arguments: tc.arguments },
              })),
            } as OpenAI.ChatCompletionMessageParam)

            // Execute tools and append results
            for (const tc of toolCalls) {
              send({ type: 'tool', name: tc.name, status: 'running' })
              let args: Record<string, unknown> = {}
              try { args = JSON.parse(tc.arguments) } catch { /* use empty args */ }
              const result = await executeTool(tc.name, args)
              send({ type: 'tool', name: tc.name, status: 'done' })
              messages.push({ role: 'tool', tool_call_id: tc.id, content: result })
            }

            continue // loop back with tool results
          }

          // Final text response
          if (assistantText) {
            send({ type: 'message', text: assistantText })
          } else {
            send({ type: 'message', text: 'No response from agent.' })
          }
          break
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Request failed'
        console.error('[chat] error —', 'model:', OPENROUTER_MODEL, 'key set:', !!process.env.OPENROUTER_API_KEY, '—', msg)
        send({ type: 'error', text: msg })
      } finally {
        send({ type: 'processing_end' })
        send({ type: 'done' })
        closed = true
        try { controller.close() } catch { /* ignore */ }
      }
    },
  })

  return new Response(stream, {
    headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' },
  })
}
