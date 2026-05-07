import { encode } from '@msgpack/msgpack'
import { keccak256 } from 'viem'
import { privateKeyToAccount } from 'viem/accounts'

const HL_API = 'https://api.hyperliquid.xyz'

export const HL_WALLET  = process.env.HYPERLIQUID_WALLET_ADDRESS ?? ''   // API wallet — signer
export const HL_MASTER  = process.env.HYPERLIQUID_MASTER_ADDRESS ?? ''   // master account — holds funds
const PRIVATE_KEY        = process.env.HYPERLIQUID_PRIVATE_KEY ?? ''

// When master address is set, API wallet acts as agent for master
const IS_AGENT           = !!HL_MASTER && HL_MASTER.toLowerCase() !== HL_WALLET.toLowerCase()
export const HL_ACCOUNT  = IS_AGENT ? HL_MASTER : HL_WALLET   // address to query for balance/positions

export const HL_LEVERAGE = 5  // 5× cross margin

// ── Types ─────────────────────────────────────────────────────────────────────

export interface HLPosition {
  side:          'long' | 'short'
  sizeBTC:       number
  entryPx:       number
  unrealizedPnl: number
  leverage:      number
}

export interface HLAccount {
  equity:      number   // perp account value USD
  spotUSDC:    number   // spot USDC balance
  totalEquity: number   // equity + spotUSDC
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
  combined[actionBytes.length + 8] = 0  // always 0 — HL routes to master via authorized agent table

  const connectionId = keccak256(combined)
  const signer       = privateKeyToAccount(PRIVATE_KEY as `0x${string}`)

  const sigHex = await signer.signTypedData({
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

function exchangeBody(action: object, nonce: number, sig: { r: string; s: string; v: number }) {
  return JSON.stringify({ action, nonce, signature: sig })
}

// Strip trailing decimal zeros so p/s fields match HL's canonical msgpack hash
function stripZeros(s: string): string {
  if (!s.includes('.')) return s
  const n = s.replace(/\.?0+$/, '')
  return n === '-0' ? '0' : (n || '0')
}

// ── Order placement ───────────────────────────────────────────────────────────

export async function transferSpotToPerp(amount: number): Promise<void> {
  if (!PRIVATE_KEY || amount <= 0) return
  const nonce  = Date.now()
  const action = { type: 'usdClassTransfer', amount: amount.toFixed(2), toPerp: true }
  const sig    = await signAction(action, nonce)
  await fetch(`${HL_API}/exchange`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    exchangeBody(action, nonce, sig),
  })
}

export async function setLeverage(leverage: number): Promise<void> {
  if (!PRIVATE_KEY) return
  const nonce  = Date.now()
  const action = { type: 'updateLeverage', asset: 0, isCross: true, leverage }
  const sig    = await signAction(action, nonce)
  await fetch(`${HL_API}/exchange`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    exchangeBody(action, nonce, sig),
  })
}

export async function placeHLOrder(
  isBuy:    boolean,
  sizeBTC:  number,
  midPrice: number,
): Promise<{ ok: boolean; orderId?: string; error?: string }> {
  if (!PRIVATE_KEY) return { ok: false, error: 'HYPERLIQUID_PRIVATE_KEY not set' }

  // HL normalizes p and s (strips trailing zeros) before computing the hash —
  // our signed action must use the same canonical form or ecrecover returns garbage.
  const limitPx = isBuy
    ? String(Math.round(midPrice * 1.05))
    : String(Math.round(midPrice * 0.95))
  const sizeStr = stripZeros(sizeBTC.toFixed(5))

  const nonce  = Date.now()
  const action = {
    type:   'order',
    orders: [{
      a: 0,
      b: isBuy,
      p: limitPx,
      s: sizeStr,
      r: false,
      t: { limit: { tif: 'Ioc' } },
    }],
    grouping: 'na',
  }

  const sig    = await signAction(action, nonce)
  const res    = await fetch(`${HL_API}/exchange`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    exchangeBody(action, nonce, sig),
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

// Place a reduce-only trigger order (stop-loss or take-profit) that fires at triggerPx as a market close.
export async function placeHLTriggerOrder(
  isLongPosition: boolean,
  sizeBTC:        number,
  triggerPx:      number,
  kind:           'sl' | 'tp',
): Promise<{ ok: boolean; orderId?: string; error?: string }> {
  if (!PRIVATE_KEY) return { ok: false, error: 'HYPERLIQUID_PRIVATE_KEY not set' }
  if (sizeBTC <= 0 || triggerPx <= 0) return { ok: false, error: 'invalid size/price' }

  const triggerStr = stripZeros(String(Math.round(triggerPx)))
  const sizeStr    = stripZeros(sizeBTC.toFixed(5))
  // limit price: for a market trigger HL still requires a p — use a wide one so it always fills
  const limitPx = isLongPosition
    ? String(Math.round(triggerPx * 0.95))     // closing a long = sell, accept down to 5% below trigger
    : String(Math.round(triggerPx * 1.05))     // closing a short = buy, accept up to 5% above trigger

  const nonce  = Date.now()
  const action = {
    type:   'order',
    orders: [{
      a: 0,
      b: !isLongPosition,                       // opposite side closes the position
      p: limitPx,
      s: sizeStr,
      r: true,                                  // reduce-only
      t: { trigger: { isMarket: true, triggerPx: triggerStr, tpsl: kind } },
    }],
    grouping: 'na',
  }

  const sig = await signAction(action, nonce)
  const res = await fetch(`${HL_API}/exchange`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    exchangeBody(action, nonce, sig),
  })
  const result = await res.json() as {
    status: string
    response?: { data?: { statuses?: Array<{ resting?: { oid: number }; error?: string }> } }
  }
  if (result.status === 'ok') {
    const st = result.response?.data?.statuses?.[0]
    if (st?.resting) return { ok: true, orderId: String(st.resting.oid) }
    if (st?.error)   return { ok: false, error: st.error }
    return { ok: true }
  }
  return { ok: false, error: JSON.stringify(result) }
}

// Compute ATR(14) on a given HL interval. Defaults to 4h, the timeframe used for backtested entries.
export async function getHLATR(interval: '1h' | '4h' | '1d' = '4h', period = 14): Promise<number> {
  const intervalMs: Record<string, number> = { '1h': 3600_000, '4h': 4 * 3600_000, '1d': 86400_000 }
  const ms = intervalMs[interval]
  const endTime   = Date.now()
  const startTime = endTime - (period + 4) * ms
  const res = await fetch(`${HL_API}/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'candleSnapshot', req: { coin: 'BTC', interval, startTime, endTime } }),
  })
  const raw = await res.json() as Array<{ t: number; h: string; l: string; c: string }>
  if (!Array.isArray(raw) || raw.length < period + 1) return 0
  const candles = raw.map(c => ({ h: parseFloat(c.h), l: parseFloat(c.l), c: parseFloat(c.c) }))
  const tr: number[] = []
  for (let i = 1; i < candles.length; i++) {
    const cur = candles[i], pc = candles[i - 1].c
    tr.push(Math.max(cur.h - cur.l, Math.abs(cur.h - pc), Math.abs(cur.l - pc)))
  }
  if (tr.length < period) return 0
  let atr = tr.slice(0, period).reduce((s, x) => s + x, 0) / period
  for (let i = period; i < tr.length; i++) atr = (atr * (period - 1) + tr[i]) / period
  return atr
}

// Backwards-compat alias
export const getHL1hATR = (period = 14) => getHLATR('1h', period)
