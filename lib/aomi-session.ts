import { AomiClient, Session } from '@aomi-labs/client'

const AOMI_BASE_URL = process.env.AOMI_BASE_URL ?? 'https://api.aomi.dev'
const AOMI_APP      = process.env.AOMI_APP ?? 'default'
const AOMI_API_KEY  = process.env.AOMI_API_KEY
const HL_WALLET     = process.env.HYPERLIQUID_WALLET_ADDRESS ?? ''

export function createAomiClient() {
  return new AomiClient({ baseUrl: AOMI_BASE_URL, apiKey: AOMI_API_KEY })
}

export function createSession(sessionId?: string) {
  return new Session(
    { baseUrl: AOMI_BASE_URL, apiKey: AOMI_API_KEY },
    {
      app:       AOMI_APP,
      sessionId: sessionId ?? crypto.randomUUID(),
      apiKey:    AOMI_API_KEY,
      // Provide wallet address so agent can call get_clearinghouse_state, get_open_orders etc.
      publicKey: HL_WALLET || undefined,
      userState: HL_WALLET ? {
        address:      HL_WALLET,
        is_connected: true,
        chain_id:     1337,
      } : undefined,
    },
  )
}

const SYSTEM = `You are an AI trading analyst for Hyperliquid BTC-PERP perpetual futures. The current market data (BTC price, order book, account position) is provided in each message — analyze it directly. Do NOT call any tools or web searches. Output your verdict as text only — the system executes trades based on your analysis.`

const SEARCH_INSTRUCTION = `Analyze the live market snapshot below. No tools needed — all data is provided.`

const FORMAT = `Reply in 4–6 bullet points. No headers, no paragraphs. First bullet MUST be your verdict: LONG / SHORT / PASS — one sentence why. Next 2–3 bullets: key data points from the snapshot. Last bullet: confidence % and main risk. Be direct and specific. Do NOT use any tools.`

export function buildPrompt(userMessage: string, hint?: string): string {
  const parts = [SYSTEM]
  if (hint) {
    parts.push(SEARCH_INSTRUCTION)
    parts.push(`Market snapshot:\n${hint}`)
  }
  parts.push(userMessage)
  parts.push(FORMAT)
  return parts.join('\n\n')
}
