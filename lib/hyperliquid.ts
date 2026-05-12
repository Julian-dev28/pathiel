import { encode } from '@msgpack/msgpack'
import { keccak256 } from 'viem'
import { privateKeyToAccount } from 'viem/accounts'

export const HL_API = 'https://api.hyperliquid.xyz'

export const HL_WALLET  = process.env.HYPERLIQUID_WALLET_ADDRESS ?? ''   // API wallet — signer
export const HL_MASTER  = process.env.HYPERLIQUID_MASTER_ADDRESS ?? ''   // master account — holds funds
const PRIVATE_KEY        = process.env.HYPERLIQUID_PRIVATE_KEY ?? ''

// Unified account with agent wallet:
//   - MASTER holds funds → query MASTER for balance
//   - WALLET signs orders → use WALLET private key for signing
const IS_AGENT = !!(HL_MASTER && HL_WALLET && HL_MASTER.toLowerCase() !== HL_WALLET.toLowerCase())
export const HL_ACCOUNT  = IS_AGENT ? HL_MASTER : HL_WALLET

export const HL_LEVERAGE = 5  // 5× cross margin

// ── Coin to HL asset index resolver ───────────────────────────────────────
let _coinIndexCache: Map<string, { index: number; szDecimals: number }> | null = null

export async function getCoinIndex(coin: string): Promise<{ index: number; szDecimals: number }> {
  if (!_coinIndexCache) {
    try {
      const res = await fetch(`${HL_API}/info`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'meta' }),
      })
      const meta = await res.json() as { universe?: Array<{ name: string; szDecimals: number }> }
      _coinIndexCache = new Map()
      meta.universe?.forEach((u, i) => _coinIndexCache!.set(u.name, { index: i, szDecimals: u.szDecimals }))
    } catch { _coinIndexCache = new Map() }
  }
  const entry = _coinIndexCache.get(coin)
  if (!entry) throw new Error(`Unknown coin: ${coin}`)
  return entry
}

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
  // Unified account: equity = perp margin account value + spot USDC balance
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
    marginSummary?: { accountValue: string; totalNtlPos: string }
    assetPositions?: Array<{
      position: {
        coin: string; szi: string; entryPx: string
        unrealizedPnl: string; leverage?: { value: string }
      }
    }>
  }

  const spot = await spotRes.json() as {
    balances?: Array<{ coin: string; total: string; hold: string }>
  }

  const perpEquity = parseFloat(perp.marginSummary?.accountValue ?? '0')
  const totalNtl = parseFloat(perp.marginSummary?.totalNtlPos ?? '0')
  const spotUSDC = (spot.balances ?? [])
    .filter(b => ['USDC', 'USDT', 'USD'].includes(b.coin))
    .reduce((sum, b) => sum + parseFloat(b.total), 0)

  const equity = perpEquity

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
        leverage:      parseFloat(btcPos.position.leverage?.value ?? '5'),
      }
    }
  }

  return { equity, spotUSDC, totalEquity: equity, totalNtl, position }
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
  // Unified account: no transfer needed — spot and perp share margin pool
  return
}

export async function setLeverage(asset: number, leverage: number): Promise<void> {
  if (!PRIVATE_KEY) return
  const nonce  = Date.now()
  const action = { type: 'updateLeverage', asset, isCross: true, leverage }
  const sig    = await signAction(action, nonce)
  await fetch(`${HL_API}/exchange`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    exchangeBody(action, nonce, sig),
  })
}

export async function placeHLOrder(
  isBuy:     boolean,
  size:     number,
  midPrice: number,
  coin:     string = 'BTC',
  assetIdx?: number,  // if not provided, resolved via getCoinIndex
): Promise<{ ok: boolean; orderId?: string; error?: string }> {
  if (!PRIVATE_KEY) return { ok: false, error: 'HYPERLIQUID_PRIVATE_KEY not set' }

  // Resolve asset index
  const idx = assetIdx ?? (await getCoinIndex(coin)).index
  const szDec = assetIdx !== undefined ? 5 : (await getCoinIndex(coin)).szDecimals

  // Price precision: HL mid prices use up to 6 decimals (not szDecimals which is for size)
  const priceStr = isBuy
    ? String(parseFloat((midPrice * 1.005).toFixed(6)))
    : String(parseFloat((midPrice * 0.995).toFixed(6)))
  const sizeStr = stripZeros(size.toFixed(szDec))

  if (!priceStr || isNaN(parseFloat(priceStr))) return { ok: false, error: `invalid price for ${coin}` }

  const nonce  = Date.now()
  const action = {
    type:   'order',
    orders: [{
      a: idx,
      b: isBuy,
      p: priceStr,
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
  size:           number,
  triggerPx:      number,
  kind:           'sl' | 'tp',
  assetIdx:       number = 0,
): Promise<{ ok: boolean; orderId?: string; error?: string }> {
  if (!PRIVATE_KEY) return { ok: false, error: 'HYPERLIQUID_PRIVATE_KEY not set' }
  if (size <= 0 || triggerPx <= 0) return { ok: false, error: 'invalid size/price' }

  const triggerStr = stripZeros(triggerPx.toFixed(5))
  const sizeStr    = stripZeros(size.toFixed(5))
  const priceStr = isLongPosition
    ? (triggerPx * 0.95).toFixed(5)
    : (triggerPx * 1.05).toFixed(5)

  const nonce  = Date.now()
  const coinIdx = assetIdx
  const action = {
    type:   'order',
    orders: [{
      a: coinIdx,
      b: !isLongPosition,                       // opposite side closes the position
      p: priceStr,
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
export async function getHLATR(interval: '1h' | '4h' | '1d' = '4h', period = 14, coin: string = 'BTC'): Promise<number> {
  const intervalMs: Record<string, number> = { '1h': 3600_000, '4h': 4 * 3600_000, '1d': 86400_000 }
  const ms = intervalMs[interval]
  const endTime   = Date.now()
  const startTime = endTime - (period + 4) * ms
  const res = await fetch(`${HL_API}/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'candleSnapshot', req: { coin, interval, startTime, endTime } }),
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

export function getAllPositions(rawPerp: {
  marginSummary?: { accountValue: string; totalNtlPos: string }
  assetPositions?: Array<{
    position: { coin: string; szi: string; entryPx: string; unrealizedPnl: string; leverage?: { value: string } }
  }>
}): Array<{ coin: string; side: 'long' | 'short'; szi: number; entryPx: number; unrealizedPnl: number; leverage: number; notional: number }> {
  const mids: Record<string, number> = {}
  return (rawPerp.assetPositions ?? [])
    .map(p => {
      const szi = parseFloat(p.position.szi)
      if (szi === 0) return null
      const entryPx = parseFloat(p.position.entryPx)
      const notional = Math.abs(szi) * entryPx
      return {
        coin: p.position.coin,
        side: szi > 0 ? 'long' as const : 'short' as const,
        szi: Math.abs(szi),
        entryPx,
        unrealizedPnl: parseFloat(p.position.unrealizedPnl),
        leverage: parseFloat(p.position.leverage?.value ?? '5'),
        notional,
      }
    })
    .filter((p): p is NonNullable<typeof p> => p !== null)
}

// Cancel orders by asset index + order ID(s)
export async function cancelOrders(oid: number, a?: number): Promise<{ ok: boolean; error?: string }> {
  if (!PRIVATE_KEY) return { ok: false, error: 'PRIVATE_KEY not set' }
  const assetIdx = a ?? 0
  const nonce = Date.now()
  const action = {
    type: 'cancel',
    cancels: [{ a: assetIdx, o: oid }],
  }
  const sig = await signAction(action, nonce)
  const res = await fetch(`${HL_API}/exchange`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: exchangeBody(action, nonce, sig),
  })
  const result = await res.json() as {
    status: string
    response?: { data?: { statuses?: Array<{ status?: string; error?: string }> } }
  }
  if (result.status === 'ok') {
    const st = result.response?.data?.statuses?.[0]
    if (st?.status === 'success') return { ok: true }
    if (st?.error) return { ok: false, error: st.error }
    return { ok: true }
  }
  return { ok: false, error: JSON.stringify(result) }
}

