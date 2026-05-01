'use client'

import { useState, useEffect } from 'react'
import { useMarketTick } from '@/hooks/useMarketTick'
import Header from '@/components/Header'
import MarketCard from '@/components/MarketCard'
import PriceChart from '@/components/PriceChart'
import AomiChat from '@/components/AomiChat'
import PositionsPanel from '@/components/PositionsPanel'

export default function Dashboard() {
  const [marketTicker, setMarketTicker] = useState<string | null>(null)

  const {
    liveMarket,
    liveOrderbook,
    liveBTCPrice,
    livePriceHistory,
    refresh: refreshMarket,
  } = useMarketTick(marketTicker)

  const strikePrice = (liveMarket?.yes_sub_title
    ? parseFloat(liveMarket.yes_sub_title.replace(/[^0-9.]/g, ''))
    : 0) || liveMarket?.floor_strike || 0

  const secondsUntilExpiry = liveMarket?.close_time
    ? Math.max(0, Math.floor((new Date(liveMarket.close_time).getTime() - Date.now()) / 1000))
    : 0

  const btcPrice = liveBTCPrice ?? 0

  // Auto-discover market ticker
  useEffect(() => {
    if (liveMarket?.ticker && !marketTicker) setMarketTicker(liveMarket.ticker)
  }, [liveMarket?.ticker, marketTicker])

  // Clear ticker when window expires so auto-discovery picks up the next one
  const expired = liveMarket?.close_time
    ? new Date(liveMarket.close_time).getTime() < Date.now()
    : false
  useEffect(() => { if (expired) setMarketTicker(null) }, [expired])

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-primary)', position: 'relative' }}>
      <div className="noise-overlay" />
      <Header cycleId={0} isRunning={false} />

      <main style={{ padding: '20px 24px', position: 'relative', zIndex: 1 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr 260px', gap: 12, alignItems: 'start' }}>

          {/* ─── LEFT: live market data ─── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <MarketCard
              market={liveMarket ?? null}
              orderbook={liveOrderbook}
              strikePrice={strikePrice}
              currentBTCPrice={btcPrice}
              secondsUntilExpiry={secondsUntilExpiry}
              liveMode={true}
              onRefresh={refreshMarket}
            />

            {/* Session info */}
            <div className="card" style={{ padding: '12px 14px' }}>
              <div style={{ fontSize: 9, fontWeight: 800, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
                Powered by
              </div>
              {[
                { label: 'Agent',    value: 'AOMI SDK',       color: 'var(--blue)'      },
                { label: 'Backend',  value: 'api.aomi.dev',   color: 'var(--text-secondary)' },
                { label: 'Market',   value: 'Kalshi KXBTC15M',color: 'var(--text-secondary)' },
                { label: 'Data',     value: 'Coinbase + Kalshi live', color: 'var(--text-secondary)' },
              ].map(({ label, value, color }) => (
                <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0', borderBottom: '1px solid var(--border)' }}>
                  <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>{label}</span>
                  <span style={{ fontSize: 10, color, fontWeight: 700, fontFamily: label === 'Backend' ? 'var(--font-geist-mono)' : undefined }}>{value}</span>
                </div>
              ))}
            </div>
          </div>

          {/* ─── CENTER: price chart + AOMI chat ─── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
            <PriceChart
              priceHistory={livePriceHistory}
              strikePrice={strikePrice}
              currentPrice={btcPrice}
            />
            <AomiChat
              market={liveMarket ?? null}
              btcPrice={btcPrice}
              strikePrice={strikePrice}
              secondsLeft={secondsUntilExpiry}
            />
          </div>

          {/* ─── RIGHT: portfolio ─── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <PositionsPanel liveMode={true} />
          </div>

        </div>
      </main>
    </div>
  )
}
