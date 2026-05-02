import { encode } from '@msgpack/msgpack'
import { keccak256 } from 'viem'
import { privateKeyToAccount } from 'viem/accounts'

const HL_API = 'https://api.hyperliquid.xyz'

export const HL_WALLET = process.env.HYPERLIQUID_WALLET_ADDRESS ?? ''
const PRIVATE_KEY       = process.env.HYPERLIQUID_PRIVATE_KEY ?? ''

// ── Types ─────────────────────────────────────────────────────────────────────

export interface HLPosition {
  side:         'long' | 'short'
  sizeBTC:      number
  entryPx:      number
  unrealizedPnl: number
  leverage:     number
}

export interface HLAccount {
  equity:   number        // USD equity
  totalNtl: number        // total notional open
  position: HLPosition | null
}

// ── Market data ───────────────────────────────────────────────────────────────

export async function getHLPrice(): Promise<number> {
  const res = await fetch(`${HL_API}/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'allMids' }),
  })
  const mids = await res.json() as Record<string, string>
  return parseFloat(mids['BTC'] ?? '0')
}

export async function getHLAccount(walletAddress: string): Promise<HLAccount> {
  const res = await fetch(`${HL_API}/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'clearinghouseState', user: walletAddress }),
  })
  const data = await res.json() as {
    marginSummary: { accountValue: string; totalNtlPos: string }
    assetPositions: Array<{
      position: {
        coin: string; szi: string; entryPx: string
        unrealizedPnl: string; leverage?: { value: string }
      }
    }>
  }

  const equity   = parseFloat(data.marginSummary?.accountValue ?? '0')
  const totalNtl = parseFloat(data.marginSummary?.totalNtlPos ?? '0')

  const btcPos = (data.assetPositions ?? []).find(p => p.position.coin === 'BTC')
  let position: HLPosition | null = null
  if (btcPos) {
    const szi = parseFloat(btcPos.position.szi)
    if (szi !== 0) {
      position = {
        side:          szi > 0 ? 'long' : 'short',
        sizeBTC:       Math.abs(szi),
        entryPx:       parseFloat(btcPos.position.entryPx),
        unrealizedPnl: parseFloat(btcPos.position.unrealizedPnl),
        leverage:      parseFloat(btcPos.position.leverage?.value ?? '1'),
      }
    }
  }

  return { equity, totalNtl, position }
}

// ── Order placement ───────────────────────────────────────────────────────────

export async function placeHLOrder(
  isBuy:    boolean,
  sizeBTC:  number,
  midPrice: number,
): Promise<{ ok: boolean; orderId?: string; error?: string }> {
  if (!PRIVATE_KEY) return { ok: false, error: 'HYPERLIQUID_PRIVATE_KEY not set' }

  // Use 5% slippage limit for IOC (market-like) order
  const limitPx = isBuy
    ? (midPrice * 1.05).toFixed(1)
    : (midPrice * 0.95).toFixed(1)

  const nonce = Date.now()
  const action = {
    type:   'order',
    orders: [{
      a: 0,          // BTC = asset index 0
      b: isBuy,
      p: limitPx,
      s: sizeBTC.toFixed(5),
      r: false,
      t: { limit: { tif: 'Ioc' } },
    }],
    grouping: 'na',
  }

  // Hyperliquid action hash: msgpack(action) || nonce_big_endian_8 || 0x00
  const actionBytes = encode(action)
  const nonceBuf    = new Uint8Array(8)
  new DataView(nonceBuf.buffer).setBigUint64(0, BigInt(nonce), false)
  const combined = new Uint8Array(actionBytes.length + 9)
  combined.set(actionBytes)
  combined.set(nonceBuf, actionBytes.length)
  combined[actionBytes.length + 8] = 0  // no vault

  const connectionId = keccak256(combined)

  const account = privateKeyToAccount(PRIVATE_KEY as `0x${string}`)
  const sigHex  = await account.signTypedData({
    domain: {
      name:              'Exchange',
      version:           '1',
      chainId:           1337,
      verifyingContract: '0x0000000000000000000000000000000000000000',
    },
    types: {
      Agent: [
        { name: 'source',       type: 'string' },
        { name: 'connectionId', type: 'bytes32' },
      ],
    },
    primaryType: 'Agent',
    message:     { source: 'a', connectionId },
  })

  const r = sigHex.slice(0, 66)
  const s = '0x' + sigHex.slice(66, 130)
  const v = parseInt(sigHex.slice(130, 132), 16)

  const res = await fetch(`${HL_API}/exchange`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ action, nonce, signature: { r, s, v } }),
  })
  const result = await res.json() as {
    status: string
    response?: {
      data?: {
        statuses?: Array<{
          filled?: { totalSz: string; avgPx: string; oid: number }
          error?:  string
        }>
      }
    }
  }

  if (result.status === 'ok') {
    const st = result.response?.data?.statuses?.[0]
    if (st?.filled) return { ok: true, orderId: String(st.filled.oid) }
    if (st?.error)  return { ok: false, error: st.error }
    return { ok: true }
  }

  return { ok: false, error: JSON.stringify(result) }
}
