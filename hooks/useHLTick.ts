'use client'

import { useState, useEffect } from 'react'
import type { HLAccount } from '@/lib/hyperliquid'

export interface HLTick {
  btcPrice:   number | null
  account:    HLAccount | null
  error:      string | null
  refreshAccount: () => void
}

const PRICE_MS   = 2_000
const ACCOUNT_MS = 5_000

export function useHLTick(): HLTick {
  const [btcPrice, setBtcPrice] = useState<number | null>(null)
  const [account,  setAccount]  = useState<HLAccount | null>(null)
  const [error,    setError]    = useState<string | null>(null)
  const [acctTick, setAcctTick] = useState(0)

  useEffect(() => {
    let mounted = true
    async function tick() {
      try {
        const res  = await fetch('/api/hl/price', { cache: 'no-store' })
        const data = await res.json() as { price?: number; error?: string }
        if (mounted && data.price && data.price > 0) setBtcPrice(data.price)
      } catch { /* blip */ }
    }
    tick()
    const id = setInterval(tick, PRICE_MS)
    return () => { mounted = false; clearInterval(id) }
  }, [])

  useEffect(() => {
    let mounted = true
    async function tick() {
      try {
        const res  = await fetch('/api/hl/account', { cache: 'no-store' })
        if (!res.ok) { if (mounted) setError(`account ${res.status}`); return }
        const data = await res.json() as HLAccount & { error?: string }
        if (data.error) { if (mounted) setError(data.error); return }
        if (mounted) { setAccount(data); setError(null) }
      } catch (e) { if (mounted) setError(String(e)) }
    }
    tick()
    const id = setInterval(tick, ACCOUNT_MS)
    return () => { mounted = false; clearInterval(id) }
  }, [acctTick])

  return {
    btcPrice,
    account,
    error,
    refreshAccount: () => setAcctTick(n => n + 1),
  }
}
