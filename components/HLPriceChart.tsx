'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import type { PricePoint } from '@/hooks/useHLTick'

interface Candle {
  t: number
  o: number
  h: number
  l: number
  c: number
  v: number
}

const WINDOWS = [
  { label: '15m', windowMs: 15 * 60 * 1000, candleMs: 60 * 1000, apiWindow: '15m' },
  { label: '1h',  windowMs: 60 * 60 * 1000, candleMs: 60 * 1000, apiWindow: '1h'  },
] as const

// Padding: left minimal, right wide for price labels, bottom for time + volume
const PAD   = { t: 20, r: 78, b: 24, l: 6 }
const VOL_H = 38   // volume bars height
const GAP   =  8   // gap between price chart and volume

// Design system colors (matching globals.css)
const C = {
  green:     '#2E9E68',
  greenDark: '#1C7A4E',
  greenFill: 'rgba(46,158,104,0.08)',
  red:       '#BE4A40',
  redDark:   '#983530',
  redFill:   'rgba(190,74,64,0.08)',
  blue:      '#3C6EA0',
  blueFill:  'rgba(60,110,160,0.10)',
  grid:      'rgba(0,0,0,0.045)',
  gridText:  '#96938E',
  chartBg:   '#F9F8F6',
  volGreen:  'rgba(46,158,104,0.45)',
  volRed:    'rgba(190,74,64,0.45)',
}

function niceStep(range: number, targetTicks = 6): number {
  const raw  = range / targetTicks
  const mag  = Math.pow(10, Math.floor(Math.log10(raw)))
  const norm = raw / mag
  const nice = norm < 1.5 ? 1 : norm < 3.5 ? 2 : norm < 7.5 ? 5 : 10
  return nice * mag
}

function fmtPrice(p: number): string {
  if (p >= 1000) return `${(p / 1000).toFixed(1)}k`
  return p.toFixed(0)
}

function buildLiveCandle(
  points: PricePoint[],
  candleMs: number,
  now: number,
  price: number,
): Candle | null {
  const start = Math.floor(now / candleMs) * candleMs
  const pts   = points.filter(p => p.timestamp >= start)
  if (pts.length === 0 && price <= 0) return null
  const prices = pts.map(p => p.price)
  if (price > 0) prices.push(price)
  const open = pts.length > 0 ? pts[0].price : price
  return {
    t: start,
    o: open,
    h: Math.max(...prices),
    l: Math.min(...prices),
    c: price > 0 ? price : pts[pts.length - 1].price,
    v: 0,
  }
}

interface HLPriceChartProps {
  priceHistory:  PricePoint[]
  currentPrice:  number
  entryPrice?:   number
  positionSide?: 'long' | 'short'
}

export default function HLPriceChart({ priceHistory, currentPrice, entryPrice, positionSide }: HLPriceChartProps) {
  const [windowIdx, setWindowIdx] = useState(1)
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef    = useRef<HTMLCanvasElement>(null)
  const rafRef       = useRef<number | undefined>(undefined)

  const live = useRef({
    candles:      [] as Candle[],
    priceHistory: [] as PricePoint[],
    entryPrice:   0,
    currentPrice: 0,
    windowMs:     WINDOWS[1].windowMs,
    candleMs:     WINDOWS[1].candleMs,
    pulseT:       0,
    cssW:         600,
    cssH:         340,
  })

  live.current.priceHistory = priceHistory
  live.current.entryPrice   = entryPrice ?? 0
  live.current.currentPrice = currentPrice
  live.current.windowMs     = WINDOWS[windowIdx].windowMs
  live.current.candleMs     = WINDOWS[windowIdx].candleMs

  const fetchCandles = useCallback((idx: number) => {
    const w = WINDOWS[idx]
    fetch(`/api/hl/candles?window=${w.apiWindow}`, { cache: 'no-store' })
      .then(r => r.json())
      .then((d: { candles?: Candle[] }) => {
        if (d.candles && d.candles.length > 0) live.current.candles = d.candles
      })
      .catch(() => {})
  }, [])

  useEffect(() => { fetchCandles(windowIdx) }, [windowIdx, fetchCandles])

  useEffect(() => {
    const canvas    = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    const dpr = window.devicePixelRatio || 1
    const ctx  = canvas.getContext('2d')!

    const resize = () => {
      const rect = container.getBoundingClientRect()
      live.current.cssW = rect.width  || 600
      live.current.cssH = rect.height || 340
      canvas.width  = live.current.cssW * dpr
      canvas.height = live.current.cssH * dpr
      canvas.style.width  = `${live.current.cssW}px`
      canvas.style.height = `${live.current.cssH}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(container)

    let prevNow = performance.now()

    const draw = (now: number) => {
      const dt = Math.min((now - prevNow) / 1000, 0.05)
      prevNow  = now

      const s              = live.current
      const { cssW: W, cssH: H } = s
      s.pulseT = (s.pulseT + dt * 0.6) % 1

      // Layout
      const iW     = W - PAD.l - PAD.r
      const priceH = H - PAD.t - PAD.b - VOL_H - GAP
      const priceY = PAD.t
      const volY   = PAD.t + priceH + GAP
      const tNow   = Date.now()
      const tStart = tNow - s.windowMs

      // Merge candles
      const hist = s.candles.filter(c => c.t >= tStart)
      const live_ = buildLiveCandle(s.priceHistory, s.candleMs, tNow, s.currentPrice)
      const all   = [...hist]
      if (live_) {
        const last = all[all.length - 1]
        if (last && last.t === live_.t) all[all.length - 1] = live_
        else all.push(live_)
      }

      const toX = (t: number) => PAD.l + ((t - tStart) / s.windowMs) * iW

      // Y range for price chart
      const lows  = all.map(c => c.l)
      const highs = all.map(c => c.h)
      if (s.currentPrice > 0) { lows.push(s.currentPrice); highs.push(s.currentPrice) }
      if (s.entryPrice  > 0) { lows.push(s.entryPrice);   highs.push(s.entryPrice)   }
      const pMin = lows.length  > 0 ? Math.min(...lows)  : (s.currentPrice || 95000) - 500
      const pMax = highs.length > 0 ? Math.max(...highs) : (s.currentPrice || 95000) + 500
      const pRng = Math.max(pMax - pMin, 100)
      const pad5 = pRng * 0.12
      const yMin = pMin - pad5
      const yMax = pMax + pad5
      const toY  = (p: number) => priceY + priceH - ((p - yMin) / (yMax - yMin)) * priceH

      // Volume Y
      const maxVol = Math.max(...all.map(c => c.v), 1)
      const toVolH = (v: number) => Math.max(1, (v / maxVol) * (VOL_H * 0.85))

      // ── Background ────────────────────────────────────────────────────────
      ctx.fillStyle = C.chartBg
      ctx.beginPath()
      ctx.roundRect(PAD.l, priceY, iW, priceH, 6)
      ctx.fill()

      // ── Grid lines ────────────────────────────────────────────────────────
      const step = niceStep(yMax - yMin, 5)
      ctx.setLineDash([2, 5])
      ctx.strokeStyle = C.grid
      ctx.lineWidth   = 1
      for (let p = Math.ceil(yMin / step) * step; p <= yMax; p += step) {
        const y = toY(p)
        if (y < priceY || y > priceY + priceH) continue
        ctx.beginPath()
        ctx.moveTo(PAD.l, y)
        ctx.lineTo(PAD.l + iW, y)
        ctx.stroke()
      }
      ctx.setLineDash([])

      // ── Entry price line ──────────────────────────────────────────────────
      if (s.entryPrice > 0) {
        const ey     = toY(s.entryPrice)
        const pnl    = s.currentPrice - s.entryPrice
        const isProfit = pnl >= 0
        const entryColor = isProfit ? C.green : C.red

        ctx.strokeStyle = `${entryColor}88`
        ctx.lineWidth   = 1
        ctx.setLineDash([4, 4])
        ctx.beginPath()
        ctx.moveTo(PAD.l, ey)
        ctx.lineTo(PAD.l + iW, ey)
        ctx.stroke()
        ctx.setLineDash([])

        // Entry chip on right
        const chipW = PAD.r - 6
        const chipH = 18
        const chipX = PAD.l + iW + 4
        const chipY = Math.max(priceY + chipH / 2, Math.min(priceY + priceH - chipH / 2, ey))
        ctx.fillStyle = `${entryColor}18`
        ctx.strokeStyle = `${entryColor}55`
        ctx.lineWidth   = 1
        ctx.beginPath()
        ctx.roundRect(chipX, chipY - chipH / 2, chipW, chipH, 4)
        ctx.fill()
        ctx.stroke()
        ctx.fillStyle = entryColor
        ctx.font      = '9px ui-monospace, monospace'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        const pnlStr = `${isProfit ? '+' : ''}$${Math.abs(pnl).toFixed(0)}`
        ctx.fillText(pnlStr, chipX + chipW / 2, chipY)
      }

      // ── Candle bodies + wicks ─────────────────────────────────────────────
      const numCandles = s.windowMs / s.candleMs
      const candleW    = Math.max(2, Math.min(14, (iW / numCandles) * 0.72))
      const halfW      = candleW / 2

      for (const c of all) {
        const cx    = toX(c.t + s.candleMs / 2)
        if (cx < PAD.l - halfW || cx > PAD.l + iW + halfW) continue
        const isUp  = c.c >= c.o
        const color = isUp ? C.green : C.red

        const yH     = toY(c.h)
        const yL     = toY(c.l)
        const yBodyT = Math.min(toY(c.o), toY(c.c))
        const yBodyB = Math.max(toY(c.o), toY(c.c))
        const bodyH  = Math.max(yBodyB - yBodyT, 1.5)

        // Wick
        ctx.strokeStyle = color
        ctx.lineWidth   = 1
        ctx.beginPath()
        ctx.moveTo(cx, yH)
        ctx.lineTo(cx, yL)
        ctx.stroke()

        // Body
        ctx.fillStyle = color
        if (candleW >= 4) {
          ctx.beginPath()
          ctx.roundRect(cx - halfW, yBodyT, candleW, bodyH, 1.5)
          ctx.fill()
        } else {
          ctx.fillRect(cx - halfW, yBodyT, candleW, bodyH)
        }
      }

      // ── Volume bars ───────────────────────────────────────────────────────
      for (const c of all) {
        const cx   = toX(c.t + s.candleMs / 2)
        if (cx < PAD.l - halfW || cx > PAD.l + iW + halfW) continue
        const isUp = c.c >= c.o
        const vh   = toVolH(c.v)
        ctx.fillStyle = isUp ? C.volGreen : C.volRed
        if (candleW >= 4) {
          ctx.beginPath()
          ctx.roundRect(cx - halfW, volY + VOL_H - vh, candleW, vh, 1)
          ctx.fill()
        } else {
          ctx.fillRect(cx - halfW, volY + VOL_H - vh, candleW, vh)
        }
      }

      // ── Current price line + chip ─────────────────────────────────────────
      if (s.currentPrice > 0) {
        const ly = toY(s.currentPrice)
        const hasEntry = s.entryPrice > 0
        const isAbove  = hasEntry && s.currentPrice > s.entryPrice
        const priceColor = hasEntry ? (isAbove ? C.green : C.red) : C.blue

        // Horizontal dashed line
        ctx.strokeStyle = `${priceColor}55`
        ctx.lineWidth   = 1
        ctx.setLineDash([3, 4])
        ctx.beginPath()
        ctx.moveTo(PAD.l, ly)
        ctx.lineTo(PAD.l + iW, ly)
        ctx.stroke()
        ctx.setLineDash([])

        // Pulsing rings
        for (let ring = 0; ring < 2; ring++) {
          const phase = (s.pulseT + ring * 0.5) % 1
          const r     = 4 + phase * 16
          const alpha = (1 - phase) * 0.4
          ctx.beginPath()
          ctx.arc(PAD.l + iW, ly, r, 0, Math.PI * 2)
          ctx.strokeStyle = `${priceColor}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`
          ctx.lineWidth   = 1.5
          ctx.stroke()
        }
        // Dot
        ctx.beginPath()
        ctx.arc(PAD.l + iW, ly, 4, 0, Math.PI * 2)
        ctx.fillStyle = priceColor
        ctx.fill()
        ctx.beginPath()
        ctx.arc(PAD.l + iW, ly, 1.8, 0, Math.PI * 2)
        ctx.fillStyle = '#fff'
        ctx.fill()

        // Price chip on right axis
        const chipW = PAD.r - 6
        const chipH = 20
        const chipX = PAD.l + iW + 6
        const chipYClamped = Math.max(priceY + chipH / 2, Math.min(priceY + priceH - chipH / 2, ly))

        ctx.fillStyle   = priceColor
        ctx.strokeStyle = priceColor
        ctx.lineWidth   = 0
        ctx.beginPath()
        ctx.roundRect(chipX, chipYClamped - chipH / 2, chipW, chipH, 4)
        ctx.fill()

        ctx.fillStyle    = '#fff'
        ctx.font         = '10px ui-monospace, monospace'
        ctx.textAlign    = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(
          `$${s.currentPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}`,
          chipX + chipW / 2,
          chipYClamped,
        )
      }

      // ── Y-axis price labels ───────────────────────────────────────────────
      ctx.fillStyle    = C.gridText
      ctx.font         = '9px ui-monospace, monospace'
      ctx.textAlign    = 'right'
      ctx.textBaseline = 'middle'
      for (let p = Math.ceil(yMin / step) * step; p <= yMax; p += step) {
        const y = toY(p)
        if (y < priceY + 8 || y > priceY + priceH - 8) continue
        ctx.fillText(`$${fmtPrice(p)}`, PAD.l + iW - 4, y)
      }

      // ── X-axis time labels ────────────────────────────────────────────────
      ctx.fillStyle    = C.gridText
      ctx.font         = '9px ui-monospace, monospace'
      ctx.textAlign    = 'center'
      ctx.textBaseline = 'top'
      const numTicks = 5
      for (let i = 0; i <= numTicks; i++) {
        const t = tStart + (i / numTicks) * s.windowMs
        const x = toX(t)
        if (x < PAD.l || x > PAD.l + iW) continue
        const label = new Date(t).toLocaleTimeString('en-US', {
          hour12: false, hour: '2-digit', minute: '2-digit',
        })
        ctx.fillText(label, x, priceY + priceH + GAP + VOL_H + 4)
      }

      rafRef.current = requestAnimationFrame(draw)
    }

    rafRef.current = requestAnimationFrame(draw)
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      ro.disconnect()
    }
  }, [])

  const hasEntry   = (entryPrice ?? 0) > 0
  const priceAbove = hasEntry && currentPrice > (entryPrice ?? 0)
  const pnl        = hasEntry ? currentPrice - (entryPrice ?? 0) : null
  const displayColor = hasEntry ? (priceAbove ? 'var(--green-dark)' : 'var(--pink-dark)') : 'var(--text-primary)'
  const posLabel   = positionSide === 'long' ? 'LONG' : positionSide === 'short' ? 'SHORT' : null
  const posColor   = positionSide === 'long' ? 'var(--green-dark)' : 'var(--pink-dark)'
  const posBg      = positionSide === 'long' ? 'var(--green-pale)' : 'var(--pink-pale)'
  const posBorder  = positionSide === 'long' ? 'rgba(46,158,104,0.25)' : 'rgba(190,74,64,0.25)'

  return (
    <div className="card" style={{ padding: '16px 14px 12px', display: 'flex', flexDirection: 'column', width: '100%', boxSizing: 'border-box' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12, padding: '0 2px' }}>

        {/* Left: price + position badge */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div>
            <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 3 }}>
              BTC-PERP · Hyperliquid
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <span style={{
                fontFamily: 'var(--font-geist-mono)', fontSize: 26, fontWeight: 800,
                color: displayColor, letterSpacing: '-0.02em', transition: 'color 0.4s',
              }}>
                {currentPrice > 0 ? `$${currentPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—'}
              </span>
              {pnl !== null && (
                <span style={{
                  fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 700,
                  color: priceAbove ? 'var(--green-dark)' : 'var(--pink-dark)',
                  transition: 'color 0.4s',
                }}>
                  {priceAbove ? '+' : ''}{pnl.toFixed(0)}
                </span>
              )}
            </div>
          </div>

          {posLabel && (
            <div style={{
              padding: '4px 10px', borderRadius: 8,
              background: posBg, border: `1px solid ${posBorder}`,
              fontSize: 10, fontWeight: 800, color: posColor,
              letterSpacing: '0.05em',
            }}>
              {posLabel === 'LONG' ? '↑ ' : '↓ '}{posLabel}
            </div>
          )}
        </div>

        {/* Right: entry legend + window buttons */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8 }}>
          {hasEntry && entryPrice && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 10, color: 'var(--text-muted)' }}>
              <svg width="16" height="2" viewBox="0 0 16 2">
                <line x1="0" y1="1" x2="16" y2="1" stroke={priceAbove ? C.green : C.red} strokeWidth="1.5" strokeDasharray="4 3" />
              </svg>
              <span style={{ fontFamily: 'var(--font-geist-mono)', fontWeight: 600 }}>
                Entry ${entryPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}
              </span>
            </div>
          )}
          <div style={{ display: 'flex', gap: 3 }}>
            {WINDOWS.map((w, i) => (
              <button key={w.label} onClick={() => setWindowIdx(i)} style={{
                background:   windowIdx === i ? 'var(--text-primary)' : 'transparent',
                color:        windowIdx === i ? 'var(--bg-card)' : 'var(--text-muted)',
                border:       `1px solid ${windowIdx === i ? 'var(--text-primary)' : 'var(--border)'}`,
                borderRadius: 6, padding: '3px 10px',
                fontSize: 10, fontWeight: 700, letterSpacing: '0.02em',
                cursor: 'pointer', transition: 'all 0.15s',
              }}>
                {w.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Chart canvas */}
      <div ref={containerRef} style={{ width: '100%', height: 340, position: 'relative' }}>
        <canvas ref={canvasRef} style={{ display: 'block' }} />
        {currentPrice <= 0 && (
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <div style={{
              fontSize: 10, letterSpacing: '0.07em', fontWeight: 600,
              color: 'var(--text-muted)', textTransform: 'uppercase',
            }}>
              Loading chart…
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
