'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import TriggerCard from '@/components/TriggerCard'
import type { StoredTrigger } from '@/lib/scanner/store'
import type { MarketCategory } from '@/lib/market-data'

interface AutoTradeConfig {
  mode: 'OFF' | 'DRY' | 'LIVE'
  minAiConfidence: number
  maxConcurrent: number
  maxTradeNotionalUsd: number
  maxDailyLossUsd: number
  minMarketVolumeUsd: number
  maxTotalNotionalPct: number
  cooldownMin: number
  coinAllowlist: string[]
  coinBlocklist: string[]
}

const DEFAULT_CONFIG: AutoTradeConfig = {
  mode: 'OFF',
  minAiConfidence: 0.80,
  maxConcurrent: 3,
  maxTradeNotionalUsd: 200,
  maxDailyLossUsd: 100,
  minMarketVolumeUsd: 5000000,
  maxTotalNotionalPct: 0.30,
  cooldownMin: 60,
  coinAllowlist: [],
  coinBlocklist: [],
}

const MODE_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  OFF: { bg: 'rgba(190,74,64,0.10)', border: 'rgba(190,74,64,0.3)', text: '#BE4A40' },
  DRY: { bg: 'rgba(245,158,11,0.10)', border: 'rgba(245,158,11,0.3)', text: '#F59E0B' },
  LIVE: { bg: 'rgba(46,158,104,0.10)', border: 'rgba(46,158,104,0.3)', text: '#2E9E68' },
}

const CATEGORY_TABS: Array<{ key: MarketCategory; label: string; icon: string; desc: string }> = [
  { key: 'crypto', label: 'Crypto', icon: '₿', desc: 'Hyperliquid perp & spot' },
  { key: 'commodity', label: 'Commodities', icon: '🛢', desc: 'Oil, Gas, Metals, Agri' },
  { key: 'equity', label: 'Equities', icon: '📈', desc: 'Stock perps (future)' },
]

function LiveConfirmDialog({ onConfirm, onCancel }: { onConfirm: () => void; onCancel: () => void }) {
  const [text, setText] = useState('')
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)',
    }}>
      <div className="card" style={{ padding: '24px 28px', maxWidth: 440, width: '100%' }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: 'var(--pink-dark)', marginBottom: 12 }}>⚠ LIVE MODE</div>
        <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6, marginBottom: 16 }}>
          This will execute <strong>real trades</strong>. Type <strong style={{ color: 'var(--text-primary)' }}>LIVE</strong> to confirm.
        </p>
        <input
          autoFocus value={text} onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && text === 'LIVE') onConfirm() }}
          style={{
            width: '100%', padding: '8px 12px', borderRadius: 6,
            border: '1px solid var(--border)', background: 'var(--bg-card)',
            color: 'var(--text-primary)', fontSize: 13, fontFamily: 'var(--font-geist-mono)',
            marginBottom: 16, outline: 'none',
          }}
        />
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onCancel} style={{
            padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)',
            background: 'var(--bg-card)', color: 'var(--text-muted)', cursor: 'pointer',
            fontSize: 12, fontWeight: 600,
          }}>Cancel</button>
          <button onClick={onConfirm} disabled={text !== 'LIVE'} style={{
            padding: '8px 16px', borderRadius: 6, border: 'none',
            background: text === 'LIVE' ? 'var(--pink-dark)' : 'var(--border)',
            color: text === 'LIVE' ? '#fff' : 'var(--text-muted)',
            cursor: text === 'LIVE' ? 'pointer' : 'not-allowed',
            fontSize: 12, fontWeight: 700,
          }}>Enable LIVE</button>
        </div>
      </div>
    </div>
  )
}

// ── Main page ─

export default function ScannerPage() {
  const router = useRouter()
  const [tab, setTab] = useState<MarketCategory>('crypto')
  const [active, setActive] = useState<StoredTrigger[]>([])
  const [history, setHistory] = useState<StoredTrigger[]>([])
  const [config, setConfig] = useState<AutoTradeConfig>(DEFAULT_CONFIG)
  const [commodities, setCommodities] = useState<Array<{ symbol: string; name: string; mid: number; leverage: number; dataProvider: string; extra?: string; category?: 'commodity' | 'equity' }>>([])
  const [commoditiesLive, setCommoditiesLive] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [killBanner, setKillBanner] = useState(false)
  const [loading, setLoading] = useState(true)
  const configRef = useRef(config)
  configRef.current = config

  const fetchTriggers = useCallback(async () => {
    try {
      const res = await fetch('/api/scanner/triggers')
      if (!res.ok) return
      const data = await res.json()
      setActive(data.active ?? [])
      setHistory(data.history ?? [])
      setLoading(false)
    } catch { /* poll silently fails */ }
  }, [])

  const fetchCommodities = useCallback(async () => {
    try {
      const res = await fetch('/api/scanner/commodities')
      if (!res.ok) return
      const data = await res.json()
      setCommodities(data.markets ?? [])
      setCommoditiesLive(data.isLive ?? false)
    } catch { /* ignore */ }
  }, [])

  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch('/api/scanner/auto-trade/config')
      if (!res.ok) return
      const data = await res.json()
      setConfig(data)
    } catch { /* ignore */ }
  }, [])

  const cycleMode = useCallback(async () => {
    const next = configRef.current.mode === 'OFF' ? 'DRY' : configRef.current.mode === 'DRY' ? 'LIVE' : 'OFF'
    if (next === 'LIVE') { setShowConfirm(true); return }
    try {
      await fetch('/api/scanner/auto-trade/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: next }),
      })
      await fetchConfig()
    } catch { /* ignore */ }
  }, [fetchConfig])

  const confirmLive = useCallback(async () => {
    try {
      await fetch('/api/scanner/auto-trade/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'LIVE' }),
      })
      setShowConfirm(false)
      await fetchConfig()
    } catch { /* ignore */ }
  }, [fetchConfig])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        fetch('/api/scanner/auto-trade/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: 'OFF' }),
        }).then(() => { fetchConfig(); setKillBanner(true); setTimeout(() => setKillBanner(false), 3000) })
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [fetchConfig])

  useEffect(() => {
    fetchTriggers()
    fetchCommodities()
    fetchConfig()
    const id = setInterval(() => { fetchTriggers(); fetchCommodities() }, 5000)
    return () => clearInterval(id)
  }, [fetchTriggers, fetchCommodities, fetchConfig])

  const handleAnalyze = useCallback((coin: string) => {
    router.push(`/agent?auto=1&coin=${encodeURIComponent(coin)}`)
  }, [router])

  const mc = MODE_COLORS[config.mode]

  return (
    <div style={{ height: '100vh', background: 'var(--bg-primary)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {killBanner && (
        <div style={{
          padding: '8px 0', textAlign: 'center',
          background: 'rgba(190,74,64,0.15)', color: '#BE4A40',
          fontSize: 12, fontWeight: 700, letterSpacing: '0.05em',
          fontFamily: 'var(--font-geist-mono)',
        }}>⚡ KILL SWITCH — Auto-trade OFF</div>
      )}

      {/* Header */}
      <header style={{
        padding: '12px 16px',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        borderBottom: '1px solid var(--border)', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <h1 style={{ fontSize: 16, fontWeight: 800, letterSpacing: '-0.02em', margin: 0 }}>
            Market Scanner
          </h1>
          <span style={{
            fontSize: 10, fontWeight: 600, color: 'var(--text-muted)',
            padding: '2px 8px', borderRadius: 10,
            background: 'var(--bg-secondary)',
          }}>
            {active.length} triggers
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--font-geist-mono)' }}>
            ⌨ Cmd+K
          </span>
          <button
            onClick={cycleMode}
            style={{
              padding: '6px 16px', borderRadius: 20, border: `1px solid ${mc.border}`,
              background: mc.bg, color: mc.text, cursor: 'pointer',
              fontWeight: 700, fontSize: 11, letterSpacing: '0.06em',
              fontFamily: 'var(--font-geist-mono)', transition: 'all 0.15s',
            }}
          >
            AUTO-TRADE: {config.mode}
          </button>
        </div>
      </header>

      {showConfirm && <LiveConfirmDialog onConfirm={confirmLive} onCancel={() => setShowConfirm(false)} />}

      {/* Market category tabs */}
      <nav style={{
        display: 'flex', gap: 0, borderBottom: '2px solid var(--border)',
        padding: '0 16px', flexShrink: 0,
      }}>
        {CATEGORY_TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              padding: '10px 18px 10px', border: 'none',
              cursor: 'pointer', fontWeight: 700, fontSize: 12, letterSpacing: '0.03em',
              background: tab === t.key ? 'transparent' : 'transparent',
              color: tab === t.key ? 'var(--text-primary)' : 'var(--text-muted)',
              borderBottom: tab === t.key ? `2px solid var(--text-primary)` : '2px solid transparent',
              marginBottom: '-2px',
              transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: 8,
            }}
          >
            <span>{t.icon}</span>
            <span>{t.label}</span>
            {!commoditiesLive && t.key !== 'crypto' && (
              <span style={{
                fontSize: 8, fontWeight: 600, color: 'var(--amber)',
                background: 'rgba(245,158,11,0.12)', padding: '1px 5px', borderRadius: 4,
              }}>SIM</span>
            )}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: 'var(--text-muted)', alignSelf: 'center', paddingRight: 4 }}>
          {CATEGORY_TABS.find(t => t.key === tab)?.desc}
        </span>
      </nav>

      {/* Main content */}
      <main style={{ flex: 1, overflowY: 'auto', padding: '16px', display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* ── CRYPTO TAB ── */}
        {tab === 'crypto' && (
          <>
            <section>
              <h2 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--text-muted)', margin: '0 0 10px' }}>
                Active Triggers ({active.length})
              </h2>
              {loading ? (
                <div style={{ fontSize: 12, color: 'var(--text-muted)', fontFamily: 'var(--font-geist-mono)' }}>
                  // waiting for scanner…
                </div>
              ) : active.length === 0 ? (
                <div style={{ fontSize: 12, color: 'var(--text-muted)', fontFamily: 'var(--font-geist-mono)', padding: '20px 0' }}>
                  // no active triggers yet — the daemon polls every 60s
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 10 }}>
                  {active.map((t, i) => (
                    <TriggerCard key={`${t.coin}-${t.firedAt}-${i}`} trigger={t} onAnalyze={handleAnalyze} />
                  ))}
                </div>
              )}
            </section>

            {history.length > 0 && (
              <section>
                <h2 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--text-muted)', margin: '0 0 10px' }}>
                  Recent Trigger History
                </h2>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-geist-mono)' }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border)' }}>
                        {['Time', 'Coin', 'Score', 'Triggers', 'Verdict', 'Status'].map(h => (
                          <th key={h} style={{
                            textAlign: 'left', padding: '6px 10px',
                            fontSize: 9, fontWeight: 700, color: 'var(--text-muted)',
                            textTransform: 'uppercase', letterSpacing: '0.07em',
                          }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {history.slice(0, 50).map((t, i) => (
                        <tr key={`${t.coin}-${t.firedAt}-${i}`} style={{ borderBottom: '1px solid var(--border)' }}>
                          <td style={{ padding: '6px 10px', color: 'var(--text-muted)' }}>{new Date(t.firedAt).toLocaleTimeString()}</td>
                          <td style={{ padding: '6px 10px', fontWeight: 700, color: 'var(--text-primary)' }}>{t.coin}</td>
                          <td style={{ padding: '6px 10px', color: t.compositeScore >= 5 ? '#2E9E68' : t.compositeScore >= 2 ? '#F59E0B' : '#BE4A40' }}>{t.compositeScore.toFixed(1)}</td>
                          <td style={{ padding: '6px 10px', color: 'var(--text-secondary)', maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.triggers.map(tr => tr.reason).join('; ')}</td>
                          <td style={{ padding: '6px 10px', color: t.analyzed === 'long' ? '#2E9E68' : t.analyzed === 'short' ? '#BE4A40' : 'var(--text-muted)' }}>{t.analyzed ?? '—'}</td>
                          <td style={{ padding: '6px 10px', color: t.orderId ? '#2E9E68' : 'var(--text-muted)' }}>{t.orderId ? 'EXECUTED' : (t.gateResults && Object.values(t.gateResults).some(g => !g.pass) ? 'BLOCKED' : '—')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </>
        )}

        {/* ── COMMODITIES TAB ── */}
        {tab === 'commodity' && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <div>
                <h2 style={{ fontSize: 14, fontWeight: 800, color: 'var(--text-primary)', margin: 0, letterSpacing: '-0.01em' }}>
                  🛢 Commodity Perps
                </h2>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Oil, Gas, Metals, Agriculture</span>
              </div>
              <span style={{
                fontSize: 10, fontFamily: 'var(--font-geist-mono)', fontWeight: 600,
                color: commoditiesLive ? '#2E9E68' : 'var(--text-muted)',
                padding: '3px 10px', borderRadius: 10,
                background: commoditiesLive ? 'rgba(46,158,104,0.10)' : 'var(--bg-secondary)',
                border: `1px solid ${commoditiesLive ? 'rgba(46,158,104,0.3)' : 'var(--border)'}`,
              }}>
                {commoditiesLive ? '● Polygon Live' : '○ Not Connected'}
              </span>
            </div>

            {commodities.length === 0 ? (
              <div style={{
                padding: '48px 24px', textAlign: 'center', borderRadius: 12,
                background: 'var(--bg-card)', border: '1px dashed var(--border)',
                display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
              }}>
                <div style={{ fontSize: 32 }}>🛢</div>
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                  No commodity data source connected
                </div>
                <p style={{
                  fontSize: 12, color: 'var(--text-secondary)', maxWidth: 400,
                  lineHeight: 1.6, margin: 0,
                }}>
                  Add a Polygon.io API key to <code style={{ background: 'rgba(0,0,0,0.15)', padding: '2px 8px', borderRadius: 4, fontFamily: 'var(--font-geist-mono)', fontSize: 11 }}>.env.local</code> to stream live Brent crude, WTI oil, natural gas, uranium, soy beans, copper, gold, silver & corn prices.
                </p>
                <code style={{
                  background: 'var(--bg-secondary)', padding: '8px 16px', borderRadius: 8,
                  fontSize: 11, fontFamily: 'var(--font-geist-mono)', color: 'var(--amber)',
                }}>
                  POLYGON_API_KEY=sk_live_xxx
                </code>
              </div>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, fontFamily: 'var(--font-geist-mono)' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      {['Symbol', 'Name', 'Price', 'Lev', 'Type', 'Source'].map(h => (
                        <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {commodities.filter(m => m.category === 'commodity').map(m => (
                      <tr key={m.symbol} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '8px 12px', fontWeight: 700, color: 'var(--text-primary)' }}>{m.symbol}</td>
                        <td style={{ padding: '8px 12px', color: 'var(--text-secondary)', fontSize: 11 }}>{m.name}</td>
                        <td style={{ padding: '8px 12px', color: 'var(--amber)', fontWeight: 700 }}>{m.mid >= 1000 ? `$${m.mid.toLocaleString('en-US', { maximumFractionDigits: 0 })}` : `$${m.mid.toFixed(m.mid >= 1 ? 2 : 4)}`}</td>
                        <td style={{ padding: '8px 12px' }}><span style={{ padding: '1px 7px', borderRadius: 4, fontSize: 10, fontWeight: 700, background: 'rgba(46,158,104,0.10)', color: '#2E9E68', border: '1px solid rgba(46,158,104,0.25)' }}>{m.leverage}×</span></td>
                        <td style={{ padding: '8px 12px', fontSize: 10, color: 'var(--text-muted)' }}>
                          <span style={{ padding: '1px 6px', borderRadius: 4, fontSize: 9, background: 'rgba(74,127,165,0.10)', color: '#3C6EA0', border: '1px solid rgba(74,127,165,0.2)' }}>
                            {m.symbol.includes('OIL') || m.symbol === 'NATGAS' ? 'Energy' : m.symbol === 'URNM' ? 'Metals' : m.symbol === 'SOY' || m.symbol === 'CORN' ? 'Agri' : m.symbol === 'COPPER' || m.symbol === 'GOLD' || m.symbol === 'SILVER' ? 'Metals' : m.symbol}
                          </span>
                        </td>
                        <td style={{ padding: '8px 12px', fontSize: 9, color: 'var(--text-muted)' }}>{m.dataProvider}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* ── EQUITIES TAB ── */}
        {tab === 'equity' && (() => {
          const stocks = commodities.filter(m => m.category === 'equity')
          return (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div>
                  <h2 style={{ fontSize: 14, fontWeight: 800, color: 'var(--text-primary)', margin: 0, letterSpacing: '-0.01em' }}>📈 Equity Perps</h2>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Stock CFDs on Hyperliquid</span>
                </div>
                <span style={{
                  fontSize: 10, fontFamily: 'var(--font-geist-mono)', fontWeight: 600,
                  color: 'var(--text-muted)',
                  padding: '3px 10px', borderRadius: 10,
                  background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                }}>○ Not Connected</span>
              </div>

              {stocks.length === 0 ? (
                <div style={{
                  padding: '48px 24px', textAlign: 'center', borderRadius: 12,
                  background: 'var(--bg-card)', border: '1px dashed var(--border)',
                  display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
                }}>
                  <div style={{ fontSize: 32 }}>📈</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                    No equity data source connected
                  </div>
                  <p style={{
                    fontSize: 12, color: 'var(--text-secondary)', maxWidth: 400,
                    lineHeight: 1.6, margin: 0,
                  }}>
                    Add a Polygon.io API key to stream live TSLA, NVDA, AAPL, AMZN, GOOGL, MSFT, META, COIN & MSTR perp prices.
                  </p>
                  <code style={{
                    background: 'var(--bg-secondary)', padding: '8px 16px', borderRadius: 8,
                    fontSize: 11, fontFamily: 'var(--font-geist-mono)', color: 'var(--amber)',
                  }}>
                    POLYGON_API_KEY=sk_live_xxx
                  </code>
                </div>
              ) : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, fontFamily: 'var(--font-geist-mono)' }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border)' }}>
                        {['Symbol', 'Name', 'Price', 'Lev', 'Source'].map(h => (
                          <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {stocks.map(m => (
                        <tr key={m.symbol} style={{ borderBottom: '1px solid var(--border)' }}>
                          <td style={{ padding: '8px 12px', fontWeight: 700, color: 'var(--text-primary)' }}>{m.symbol}</td>
                          <td style={{ padding: '8px 12px', color: 'var(--text-secondary)', fontSize: 11 }}>{m.name}</td>
                          <td style={{ padding: '8px 12px', color: 'var(--amber)', fontWeight: 700 }}>${m.mid.toLocaleString('en-US', { maximumFractionDigits: 0 })}</td>
                          <td style={{ padding: '8px 12px' }}><span style={{ padding: '1px 7px', borderRadius: 4, fontSize: 10, fontWeight: 700, background: 'rgba(46,158,104,0.10)', color: '#2E9E68', border: '1px solid rgba(46,158,104,0.25)' }}>{m.leverage}×</span></td>
                          <td style={{ padding: '8px 12px', fontSize: 9, color: 'var(--text-muted)' }}>{m.dataProvider}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )
        })()}
      </main>
    </div>
  )
}
