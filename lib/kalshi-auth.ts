/**
 * Kalshi RSA-PSS Authentication
 * ──────────────────────────────
 * Signs requests using PKCS#8 / PKCS#1 RSA private key.
 * Required headers:
 *   KALSHI-ACCESS-KEY       — API key ID
 *   KALSHI-ACCESS-TIMESTAMP — Unix ms timestamp (as string)
 *   KALSHI-ACCESS-SIGNATURE — Base64 RSA-PSS signature
 *
 * Signature payload: `${timestamp}${method}${path}` (direct concat, no separators)
 */

import { readFileSync } from 'fs'
import { createSign, constants } from 'crypto'
import { join } from 'path'

function loadCreds(): { apiKey: string; privateKey: string } | null {
  const apiKey = process.env.KALSHI_API_KEY
  if (!apiKey) return null

  // Support PEM content directly in env var (required for Vercel/serverless)
  const privateKeyEnv = process.env.KALSHI_PRIVATE_KEY
  if (privateKeyEnv) {
    return { apiKey, privateKey: privateKeyEnv.replace(/\\n/g, '\n') }
  }

  // Fall back to file path (local dev only)
  const keyPath = process.env.KALSHI_PRIVATE_KEY_PATH
  if (!keyPath) return null
  const resolved = keyPath.startsWith('/') ? keyPath : join(process.cwd(), keyPath.replace(/^\.\//, ''))
  try {
    const privateKey = readFileSync(resolved, 'utf-8')
    return { apiKey, privateKey }
  } catch {
    return null
  }
}

export function buildKalshiHeaders(method: string, path: string): Record<string, string> {
  const creds = loadCreds()
  if (!creds) return {}
  const { apiKey, privateKey } = creds

  const timestamp = String(Date.now())
  // Kalshi signing: direct concatenation, no separators
  const payload = `${timestamp}${method.toUpperCase()}${path}`

  try {
    const sign = createSign('RSA-SHA256')
    sign.update(payload)
    sign.end()
    const signature = sign.sign({
      key: privateKey,
      padding: constants.RSA_PKCS1_PSS_PADDING,
      saltLength: constants.RSA_PSS_SALTLEN_DIGEST,
    }, 'base64')

    return {
      'KALSHI-ACCESS-KEY': apiKey,
      'KALSHI-ACCESS-TIMESTAMP': timestamp,
      'KALSHI-ACCESS-SIGNATURE': signature,
      'Accept': 'application/json',
    }
  } catch (e) {
    console.error('[kalshi-auth] RSA signing failed — requests will be unauthenticated:', e)
    return { 'Accept': 'application/json' }
  }
}

export function hasKalshiAuth(): boolean {
  return !!loadCreds()
}
