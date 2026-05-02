import { encode } from '@msgpack/msgpack'
import { keccak256 } from 'viem'
import { privateKeyToAccount } from 'viem/accounts'

const HL_API = 'https://api.hyperliquid.xyz'

export const HL_WALLET = process.env.HYPERLIQUID_WALLET_ADDRESS ?? ''
const PRIVATE_KEY       = process.env.HYPERLIQUID_PRIVATE_KEY ?? ''

export const HL_LEVERAGE = 5  // 5× cross margin

// ── Types ─────────────────────────────────────────────────────────────────────

export interface HLPosition {
  side:         'long' | 'short'
  sizeBTC:      number
  entryPx:      number
  unrealizedPnl: number
  leverage:     number
}

export interface HLAccount {
  equity:      number   // perp account value USD
  spotUSDC:    number   // spot USDC balance
  totalEquity: number   // equity + spotUSDC (available for trading with unified account)
  totalNtl:    number   // total notional open
  position:    HLPosition | null
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
  const [perpRes, spotRes] = await Promise.all([
    fetch(`${HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'clearinghouseState', user: walletAddress }),
    }),
    fetch(`${HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'spotClearinghouseState', user: walletAddress }),
    }),
  ])

  const perp = await perpRes.json() as {
    marginSummary: { accountValue: string; totalNtlPos: string }
    assetPositions: Array<{
      position: {
        coin: string; szi: string; entryPx: string
        unrealizedPnl: string; leverage?: { value: string }
      }
    }>
  }

  const spot = await spotRes.json() as {
    balances?: Array<{ coin: string; total: string; hold: string }>
  }

  const equity   = parseFloat(perp.marginSummary?.accountValue ?? '0')
  const totalNtl = parseFloat(perp.marginSummary?.totalNtlPos ?? '0')

  // Sum all USD-equivalent spot balances (USDC, USDT, etc.)
  const spotUSDC = (spot.balances ?? [])
    .filter(b => ['USDC', 'USDT', 'USD'].includes(b.coin))
    .reduce((sum, b) => sum + parseFloat(b.total), 0)

  const btcPos = (perp.assetPositions ?? []).find(p => p.position.coin === 'BTC')
  let position: HLPosition | null = null
  if (btcPos) {
    const szi = parseFloat(btcPos.position.szi)
    if (szi !== 0) {
      position = {
        side:          szi > 0 ? 'long' : 'short',
        sizeBTC:       Math.abs(szi),
        entryPx:       parseFloat(btcPos.position.entryPx),
        unrealizedPnl: parseFloat(btcPos.position.unrealizedPnl),
        leverage:      parseFloat(btcPos.position.leverage?.value ?? String(HL_LEVERAGE)),
      }
    }
  }

  return { equity, spotUSDC, totalEquity: equity + spotUSDC, totalNtl, position }
}

// ── Signing utilities ─────────────────────────────────────────────────────────

async function signAction(action: object, nonce: number): Promise<{ r: string; s: string; v: number }> {
  const actionBytes = encode(action)
  const nonceBuf    = new Uint8Array(8)
  new DataView(nonceBuf.buffer).setBigUint64(0, BigInt(nonce), false)
  const combined = new Uint8Array(actionBytes.length + 9)
  combined.set(actionBytes)
  combined.set(nonceBuf, actionBytes.length)
  combined[actionBytes.length + 8] = 0  // no vault

  const connectionId = keccak256(combined)
  const account      = privateKeyToAccount(PRIVATE_KEY as `0x${string}`)

  const sigHex = await account.signTypedData({
    domain: {
      name:              'Exchange',
      version:           '1',
      chainId:           1337,
      verifyingContract: '0x0000000000000000000000000000000000000000',
    },
    types: {
      Agent: [
        { name: 'source',       type: 'string'  },
        { name: 'connectionId', type: 'bytes32' },
      ],
    },
    primaryType: 'Agent',
    message:     { source: 'a', connectionId },
  })

  return {
    r: sigHex.slice(0, 66),
    s: '0x' + sigHex.slice(66, 130),
    v: parseInt(sigHex.slice(130, 132), 16),
  }
}

// ── Order placement ───────────────────────────────────────────────────────────

export async function setLeverage(leverage: number): Promise<void> {
  if (!PRIVATE_KEY) return
  const nonce  = Date.now()
  const action = { type: 'updateLeverage', asset: 0, isCross: true, leverage }
  const sig    = await signAction(action, nonce)
  await fetch(`${HL_API}/exchange`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ action, nonce, signature: sig }),
  })
}

export async function placeHLOrder(
  isBuy:    boolean,
  sizeBTC:  number,
  midPrice: number,
): Promise<{ ok: boolean; orderId?: string; error?: string }> {
  if (!PRIVATE_KEY) return { ok: false, error: 'HYPERLIQUID_PRIVATE_KEY not set' }

  const limitPx = isBuy
    ? (midPrice * 1.05).toFixed(1)   // 5% above for guaranteed IOC fill
    : (midPrice * 0.95).toFixed(1)

  const nonce  = Date.now()
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

  const sig = await signAction(action, nonce)

  const res    = await fetch(`${HL_API}/exchange`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ action, nonce, signature: sig }),
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
