import { NextRequest, NextResponse } from 'next/server'
import { placeHLOrder, placeHLTriggerOrder, getHLATR, setLeverage, transferSpotToPerp, getHLPrice, getHLAccount, HL_ACCOUNT, HL_LEVERAGE } from '@/lib/hyperliquid'

export const runtime = 'nodejs'

interface PlaceOrderRequest {
  side:      'long' | 'short'
  riskUSD?:  number   // explicit dollar amount
  riskPct?:  number   // fallback % of equity
  leverage?: number
  coin?:     string   // default BTC
}

// ROBUST brackets — profitable on 90/180/365d BTC after realistic costs.
// stop=3.5× ATR / single TP=1.0× ATR (no partial) — backtested +3.2-3.8% per window, 53-62% WR, PF 1.13-1.52.
const SL_ATR_MULT  = 3.5
const TP1_ATR_MULT = 1.0    // Full position TP at 1× 4h ATR
const TP2_ATR_MULT = 1.0    // Same target — partial logic disabled
const TP1_FRAC     = 1.0    // 100% closes at TP (no partial)

export async function POST(req: NextRequest) {
  const { side, riskUSD: riskUSDParam, riskPct, leverage = HL_LEVERAGE, coin = 'BTC' } = (await req.json()) as PlaceOrderRequest

  try {
    // Get all mids to find price for this coin
    const midsRes = await fetch('https://api.hyperliquid.xyz/info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'allMids' }),
    })
    const allMids = await midsRes.json() as Record<string, string>
    const midPrice = parseFloat(allMids[coin] ?? '0')

    const [account, atr] = await Promise.all([
      getHLAccount(HL_ACCOUNT),
      getHLATR('4h', 14, coin),
    ])

    if (midPrice <= 0) return NextResponse.json({ ok: false, error: 'invalid price' })

    if (account.equity === 0 && account.spotUSDC > 0) {
      await transferSpotToPerp(account.spotUSDC)
      const updated = await getHLAccount(HL_ACCOUNT)
      Object.assign(account, updated)
    }

    const totalEquity = account.totalEquity
    const riskUSD     = riskUSDParam != null && riskUSDParam > 0
      ? riskUSDParam
      : totalEquity > 0 ? (totalEquity * (riskPct ?? 2) / 100) : 0
    const notional    = Math.max(riskUSD * leverage, midPrice * 0.001)
    const sizeBTC     = parseFloat((notional / midPrice).toFixed(5))
    const isBuy       = side === 'long'

    const { getCoinIndex } = await import('@/lib/hyperliquid')
    const { index: assetIdx } = await getCoinIndex(coin)
    await setLeverage(assetIdx, leverage)
    const result = await placeHLOrder(isBuy, notional / midPrice, midPrice, coin)
    if (!result.ok) return NextResponse.json({ ...result, sizeBTC, midPrice, equity: totalEquity, leverage })

    // Auto-place SL + TP brackets sized via 4h ATR. ROBUST backtested defaults — single TP at 1× ATR.
    const brackets: { ok: boolean; sl?: string; tp1?: string; tp2?: string; atr: number; error?: string } = {
      ok: true, atr,
    }
    if (atr > 0 && sizeBTC > 0) {
      const slPx  = isBuy ? midPrice - atr * SL_ATR_MULT : midPrice + atr * SL_ATR_MULT
      const tp1Px = isBuy ? midPrice + atr * TP1_ATR_MULT : midPrice - atr * TP1_ATR_MULT
      const tp2Px = isBuy ? midPrice + atr * TP2_ATR_MULT : midPrice - atr * TP2_ATR_MULT
      const tp1Size = parseFloat((sizeBTC * TP1_FRAC).toFixed(5))
      const tp2Size = parseFloat((sizeBTC - tp1Size).toFixed(5))

      // place sequentially (HL nonces must increment) — log errors but don't abort the entry
      const sl  = await placeHLTriggerOrder(isBuy, sizeBTC, slPx, 'sl', assetIdx)
      const tp1 = tp1Size > 0 ? await placeHLTriggerOrder(isBuy, tp1Size, tp1Px, 'tp', assetIdx) : { ok: true }
      const tp2 = tp2Size > 0 ? await placeHLTriggerOrder(isBuy, tp2Size, tp2Px, 'tp', assetIdx) : null
      brackets.sl  = sl.ok ? `placed @ ${slPx.toFixed(0)}`  : `failed: ${sl.error}`
      brackets.tp1 = tp1.ok ? `placed @ ${tp1Px.toFixed(0)}` : `failed: ${tp1.error}`
      brackets.tp2 = tp2 ? (tp2.ok ? `placed @ ${tp2Px.toFixed(0)}` : `failed: ${tp2.error}`) : 'skipped (full close at tp1)'
      brackets.ok  = sl.ok && tp1.ok && (tp2 === null || tp2.ok)
    } else {
      brackets.ok = false
      brackets.error = atr <= 0 ? 'ATR unavailable' : 'size 0'
    }

    return NextResponse.json({ ...result, sizeBTC, midPrice, equity: totalEquity, leverage, brackets })
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 })
  }
}
