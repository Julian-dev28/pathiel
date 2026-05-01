import { AomiClient, Session } from '@aomi-labs/client'

const AOMI_BASE_URL = process.env.AOMI_BASE_URL ?? 'https://api.aomi.dev'
const AOMI_APP      = process.env.AOMI_APP      ?? 'default'

export function createAomiClient() {
  return new AomiClient({ baseUrl: AOMI_BASE_URL })
}

export function createSession(sessionId?: string) {
  return new Session(
    { baseUrl: AOMI_BASE_URL },
    { app: AOMI_APP, sessionId: sessionId ?? crypto.randomUUID() },
  )
}

/** Register Kalshi credentials with AOMI so the backend Kalshi plugin can authenticate. */
export async function ingestKalshiSecrets(clientId: string, secrets: Record<string, string>) {
  const client = createAomiClient()
  return client.ingestSecrets(clientId, secrets)
}

const SYSTEM = `You are an AI trading analyst for Kalshi BTC prediction markets. You have access to brave_search — USE IT before every trading analysis to find the latest BTC news, technical signals, and market sentiment. Never ask for information already provided. Always deliver a direct verdict.`

const SEARCH_INSTRUCTION = `Before answering, use brave_search to research: "BTC price action today", "Bitcoin technical analysis", "crypto market sentiment". Synthesize what you find with the market snapshot below.`

const FORMAT = `Reply in 4–6 bullet points. No headers, no paragraphs. First bullet MUST be your verdict: BUY YES / BUY NO / PASS — one sentence why. Next 2–3 bullets: key data points from your search + market data. Last bullet: confidence level and main risk. Be direct and specific.`

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
