'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import type { PricePoint } from '@/hooks/useHLTick'

interface Candle {
  t: number  // candle open time ms
  o: number
  h: number
  l: number
  c: number
}

const WINDOWS = [
  { label: '15m', windowMs:     15 * 60 * 1000, candleMs:     60 * 1000, apiWindow: '15m' },
  { label: '1h',  windowMs:     60 * 60 * 1000, candleMs:     60 * 1000, apiWindow: '1h'  },
  { label: '4h',  windowMs: 4 * 60 * 60 * 1000, candleMs: 5 * 60 * 1000, apiWindow: '4h'  },
] as const

const PAD = { t: 12, r: 20, b: 30, l: 64 }

function tickStep(range: number): number {
  const raw  = range / 6
  const mag  = Math.pow(10, Math.floor(Math.log10(raw)))
  const norm = raw / mag
  const nice = norm < 1.5 ? 1 : norm < 3.5 ? 2 : norm < 7.5 ? 5 : 10
  return nice * mag
}

function buildLiveCandle(
  points: PricePoint[],
  candleMs: number,
  now: number,
  currentPrice: number,
): Candle | null {
  const candleStart = Math.floor(now / candleMs) * candleMs
  const pts = points.filter(p => p.timestamp >= candleStart)
  if (pts.length === 0 && currentPrice <= 0) return null
  const prices = pts.map(p => p.price)
  if (currentPrice > 0) prices.push(currentPrice)
  const open = pts.length > 0 ? pts[0].price : currentPrice
  return {
    t: candleStart,
    o: open,
    h: Math.max(...prices),
    l: Math.min(...prices),
    c: currentPrice > 0 ? currentPrice : pts[pts.length - 1].price,
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
    cssH:         300,
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
      live.current.cssH = rect.height || 300
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

      const s = live.current
      const { cssW: W, cssH: H } = s
      s.pulseT = (s.pulseT + dt * 0.5) % 1

      ctx.clearRect(0, 0, W, H)

      const iW = W - PAD.l - PAD.r
      const iH = H - PAD.t - PAD.b
      const tNow   = Date.now()
      const tStart = tNow - s.windowMs

      // Merge historical + live candle
      const histCandles = s.candles.filter(c => c.t >= tStart)
      const liveCandle  = buildLiveCandle(s.priceHistory, s.candleMs, tNow, s.currentPrice)
      const allCandles  = [...histCandles]
      if (liveCandle) {
        const last = allCandles[allCandles.length - 1]
        if (last && last.t === liveCandle.t) allCandles[allCandles.length - 1] = liveCandle
        else allCandles.push(liveCandle)
      }

      const toX = (t: number) => PAD.l + ((t - tStart) / s.windowMs) * iW

      // Y range
      const lows  = allCandles.map(c => c.l)
      const highs = allCandles.map(c => c.h)
      if (s.currentPrice > 0) { lows.push(s.currentPrice); highs.push(s.currentPrice) }
      if (s.entryPrice  > 0) { lows.push(s.entryPrice);   highs.push(s.entryPrice)   }
      const pMin  = lows.length  > 0 ? Math.min(...lows)  : (s.currentPrice || 95000) - 500
      const pMax  = highs.length > 0 ? Math.max(...highs) : (s.currentPrice || 95000) + 500
      const pRng  = Math.max(pMax - pMin, 200)
      const pCtr  = (pMax + pMin) / 2
      const yMin  = pCtr - pRng * 0.65
      const yMax  = pCtr + pRng * 0.65
      const toY = (p: number) => PAD.t + iH - ((p - yMin) / (yMax - yMin)) * iH

      // Grid
      const step = tickStep(yMax - yMin)
      ctx.setLineDash([2, 6])
      ctx.strokeStyle = 'rgba(0,0,0,0.07)'
      ctx.lineWidth   = 1
      for (let p = Math.ceil(yMin / step) * step; p <= yMax; p += step) {
        const y = toY(p)
        if (y < PAD.t || y > PAD.t + iH) continue
        ctx.beginPath(); ctx.moveTo(PAD.l, y); ctx.lineTo(PAD.l + iW, y); ctx.stroke()
      }
      ctx.setLineDash([])

      // Entry line
      if (s.entryPrice > 0) {
        const ey = toY(s.entryPrice)
        ctx.strokeStyle = 'rgba(100,150,255,0.7)'
        ctx.lineWidth   = 1.5
        ctx.setLineDash([5, 3])
        ctx.beginPath(); ctx.moveTo(PAD.l, ey); ctx.lineTo(PAD.l + iW, ey); ctx.stroke()
        ctx.setLineDash([])
        ctx.fillStyle = 'rgba(100,150,255,0.85)'
        ctx.font      = '9px ui-monospace, monospace'
        ctx.textAlign = 'right'
        ctx.fillText('ENTRY', PAD.l + iW, ey - 4)
      }

      // Candles
      const numCandles = s.windowMs / s.candleMs
      const candleW    = Math.max(2, (iW / numCandles) * 0.7)
      const halfW      = candleW / 2

      for (const c of allCandles) {
        const cx   = toX(c.t + s.candleMs / 2)
        const isUp = c.c >= c.o
        const upColor   = 'rgba(48,168,108,0.92)'
        const downColor = 'rgba(220,70,95,0.92)'
        const color = isUp ? upColor : downColor

        const yH     = toY(c.h)
        const yL     = toY(c.l)
        const yBodyT = Math.min(toY(c.o), toY(c.c))
        const yBodyB = Math.max(toY(c.o), toY(c.c))
        const bodyH  = Math.max(yBodyB - yBodyT, 1)

        // Wick
        ctx.strokeStyle = color
        ctx.lineWidth   = 1
        ctx.beginPath(); ctx.moveTo(cx, yH); ctx.lineTo(cx, yL); ctx.stroke()

        // Body
        ctx.fillStyle = color
        ctx.fillRect(cx - halfW, yBodyT, candleW, bodyH)
      }

      // Pulsing dot
      if (s.currentPrice > 0) {
        const lx = toX(tNow)
        const ly = toY(s.currentPrice)
        let cr: number, cg: number, cb: number
        if (s.entryPrice > 0) {
          ;[cr, cg, cb] = s.currentPrice > s.entryPrice ? [48, 168, 108] : [220, 70, 95]
        } else {
          ;[cr, cg, cb] = [100, 150, 220]
        }

        ctx.strokeStyle = `rgba(${cr},${cg},${cb},0.35)`
        ctx.lineWidth   = 1
        ctx.setLineDash([3, 4])
        ctx.beginPath(); ctx.moveTo(PAD.l, ly); ctx.lineTo(lx, ly); ctx.stroke()
        ctx.setLineDash([])

        for (let ring = 0; ring < 2; ring++) {
          const phase = (s.pulseT + ring * 0.45) % 1
          const r     = 5 + phase * 18
          const alpha = (1 - phase) * 0.45
          ctx.beginPath(); ctx.arc(lx, ly, r, 0, Math.PI * 2)
          ctx.strokeStyle = `rgba(${cr},${cg},${cb},${alpha})`
          ctx.lineWidth   = 1.5
          ctx.stroke()
        }
        ctx.beginPath(); ctx.arc(lx, ly, 4.5, 0, Math.PI * 2)
        ctx.fillStyle = `rgb(${cr},${cg},${cb})`; ctx.fill()
        ctx.beginPath(); ctx.arc(lx, ly, 2, 0, Math.PI * 2)
        ctx.fillStyle = '#fff'; ctx.fill()
      }

      // Y labels
      ctx.fillStyle = 'rgba(0,0,0,0.28)'
      ctx.font      = '9px ui-monospace, monospace'
      ctx.textAlign = 'right'
      for (let p = Math.ceil(yMin / step) * step; p <= yMax; p += step) {
        const y = toY(p)
        if (y < PAD.t || y > PAD.t + iH) continue
        ctx.fillText(`$${(p / 1000).toFixed(1)}k`, PAD.l - 6, y + 3)
      }

      // X labels
      ctx.textAlign = 'center'
      for (let i = 0; i <= 5; i++) {
        const t = tStart + (i / 5) * s.windowMs
        const x = toX(t)
        const label = new Date(t).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' })
        ctx.fillText(label, x, PAD.t + iH + 18)
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
  const displayColor = hasEntry ? (priceAbove ? '#30a86c' : '#dc4660') : '#6496dc'

  const posLabel = positionSide === 'long' ? '↑ LONG' : positionSide === 'short' ? '↓ SHORT' : null
  const posColor = positionSide === 'long' ? '#30a86c' : '#dc4660'
  const posBg    = positionSide === 'long' ? 'rgba(48,168,108,0.10)' : 'rgba(220,70,95,0.10)'
  const posBdr   = positionSide === 'long' ? 'rgba(48,168,108,0.25)' : 'rgba(220,70,95,0.25)'

  return (
    <div className="card" style={{ padding: '18px 14px 10px 14px', display: 'flex', flexDirection: 'column', width: '100%', boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14, padding: '0 4px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 2 }}>BTC / USD</div>
            <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 28, fontWeight: 800, color: displayColor, letterSpacing: '-0.02em', transition: 'color 0.4s ease' }}>
              {currentPrice > 0 ? `$${currentPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—'}
            </div>
          </div>
          {posLabel && (
            <div style={{ padding: '6px 12px', borderRadius: 9, background: posBg, border: `1px solid ${posBdr}`, transition: 'all 0.4s ease' }}>
              <div style={{ fontSize: 11, fontWeight: 800, color: posColor }}>{posLabel}</div>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {hasEntry && entryPrice && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--text-muted)' }}>
              <div style={{ width: 18, height: 2, background: 'rgba(100,150,255,0.7)', borderRadius: 1 }} />
              <span>Entry ${entryPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
            </div>
          )}
          <div style={{ display: 'flex', gap: 2 }}>
            {WINDOWS.map((w, i) => (
              <button key={w.label} onClick={() => setWindowIdx(i)} style={{
                background:   windowIdx === i ? 'var(--blue)' : 'none',
                color:        windowIdx === i ? '#fff' : 'var(--text-muted)',
                border:       `1px solid ${windowIdx === i ? 'var(--blue)' : 'var(--border)'}`,
                borderRadius: 6, padding: '3px 8px', fontSize: 10, fontWeight: 700,
                cursor: 'pointer', transition: 'all 0.15s',
              }}>
                {w.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div ref={containerRef} style={{ width: '100%', height: 300, position: 'relative' }}>
        <canvas ref={canvasRef} style={{ display: 'block', borderRadius: 8 }} />
        {currentPrice <= 0 && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ fontSize: 10, letterSpacing: '0.07em', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Collecting price data…</div>
          </div>
        )}
      </div>
    </div>
  )
}
