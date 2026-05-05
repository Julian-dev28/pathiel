'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import type { PricePoint } from '@/hooks/useHLTick'

interface Candle { t: number; o: number; h: number; l: number; c: number; v: number }

const WINDOWS = [
  { label: '1h', windowMs: 60 * 60 * 1000, candleMs: 60 * 1000, apiWindow: '1h' },
] as const

const PAD = { t: 16, r: 80, b: 28, l: 52 }

const C = {
  green:    '#2E9E68',
  red:      '#BE4A40',
  blue:     '#3C6EA0',
  grid:     'rgba(0,0,0,0.04)',
  label:    '#A09D99',
  bg:       '#F9F8F6',
}

function fmtPrice(p: number) {
  return p >= 1000 ? `${(p / 1000).toFixed(1)}k` : p.toFixed(0)
}

function niceStep(range: number) {
  const raw  = range / 4
  const mag  = Math.pow(10, Math.floor(Math.log10(raw)))
  const norm = raw / mag
  return (norm < 1.5 ? 1 : norm < 3.5 ? 2 : norm < 7.5 ? 5 : 10) * mag
}

interface Props {
  priceHistory:  PricePoint[]
  currentPrice:  number
  entryPrice?:   number
  positionSide?: 'long' | 'short'
}

export default function HLPriceChart({ priceHistory, currentPrice, entryPrice, positionSide }: Props) {
  const [windowIdx, setWindowIdx] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef    = useRef<HTMLCanvasElement>(null)
  const rafRef       = useRef<number | undefined>(undefined)

  const live = useRef({
    candles:      [] as Candle[],
    priceHistory: [] as PricePoint[],
    entryPrice:   0,
    currentPrice: 0,
    windowMs:     WINDOWS[0].windowMs,
    candleMs:     WINDOWS[0].candleMs,
    pulseT:       0,
    cssW:         600,
    cssH:         280,
  })

  live.current.priceHistory = priceHistory
  live.current.entryPrice   = entryPrice ?? 0
  live.current.currentPrice = currentPrice
  live.current.windowMs     = WINDOWS[windowIdx].windowMs
  live.current.candleMs     = WINDOWS[windowIdx].candleMs

  const fetchCandles = useCallback((idx: number) => {
    fetch(`/api/hl/candles?window=${WINDOWS[idx].apiWindow}`, { cache: 'no-store' })
      .then(r => r.json())
      .then((d: { candles?: Candle[] }) => { if (d.candles?.length) live.current.candles = d.candles })
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
      live.current.cssH = rect.height || 280
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

    const draw = (ts: number) => {
      const dt = Math.min((ts - prevNow) / 1000, 0.05)
      prevNow  = ts

      const s = live.current
      const { cssW: W, cssH: H } = s
      ctx.clearRect(0, 0, W, H)
      s.pulseT = (s.pulseT + dt * 0.6) % 1

      const iW     = W - PAD.l - PAD.r
      const iH     = H - PAD.t - PAD.b
      const tNow   = Date.now()
      const tStart = tNow - s.windowMs

      // Build points array from candle closes + live price
      const hist   = s.candles.filter(c => c.t >= tStart)
      const points: { t: number; p: number }[] = hist.map(c => ({ t: c.t + s.candleMs / 2, p: c.c }))

      // Append live price ticks
      for (const pt of s.priceHistory) {
        if (pt.timestamp >= tStart) points.push({ t: pt.timestamp, p: pt.price })
      }
      if (s.currentPrice > 0) points.push({ t: tNow, p: s.currentPrice })

      // Dedupe + sort
      points.sort((a, b) => a.t - b.t)

      // Y range
      const allP = points.map(pt => pt.p)
      if (s.entryPrice > 0) allP.push(s.entryPrice)
      const pMin = allP.length > 0 ? Math.min(...allP) : (s.currentPrice || 95000) - 200
      const pMax = allP.length > 0 ? Math.max(...allP) : (s.currentPrice || 95000) + 200
      const pRng = Math.max(pMax - pMin, 50)
      const pad  = pRng * 0.15
      const yMin = pMin - pad
      const yMax = pMax + pad

      const toX = (t: number) => PAD.l + ((t - tStart) / s.windowMs) * iW
      const toY = (p: number) => PAD.t + iH - ((p - yMin) / (yMax - yMin)) * iH

      // Line color: green if current >= first point, else red
      const lineColor = points.length < 2 ? C.blue
        : s.currentPrice >= points[0].p ? C.green : C.red

      // ── Background ────────────────────────────────────────────────────────
      ctx.fillStyle = C.bg
      ctx.beginPath()
      ctx.roundRect(PAD.l, PAD.t, iW, iH, 6)
      ctx.fill()

      // ── Grid lines ────────────────────────────────────────────────────────
      const step = niceStep(yMax - yMin)
      ctx.setLineDash([2, 5])
      ctx.strokeStyle = C.grid
      ctx.lineWidth   = 1
      for (let p = Math.ceil(yMin / step) * step; p <= yMax; p += step) {
        const y = toY(p)
        if (y < PAD.t || y > PAD.t + iH) continue
        ctx.beginPath(); ctx.moveTo(PAD.l, y); ctx.lineTo(PAD.l + iW, y); ctx.stroke()
      }
      ctx.setLineDash([])

      // ── Entry price line ──────────────────────────────────────────────────
      if (s.entryPrice > 0) {
        const ey = toY(s.entryPrice)
        const isProfit = s.currentPrice >= s.entryPrice
        const ec = isProfit ? C.green : C.red
        ctx.strokeStyle = ec + '70'
        ctx.lineWidth   = 1
        ctx.setLineDash([5, 4])
        ctx.beginPath(); ctx.moveTo(PAD.l, ey); ctx.lineTo(PAD.l + iW, ey); ctx.stroke()
        ctx.setLineDash([])

        // Entry chip
        const cW = PAD.r - 8, cH = 18, cX = PAD.l + iW + 4
        const cY = Math.max(PAD.t + cH / 2, Math.min(PAD.t + iH - cH / 2, ey))
        ctx.fillStyle   = ec + '20'
        ctx.strokeStyle = ec + '60'
        ctx.lineWidth   = 1
        ctx.beginPath(); ctx.roundRect(cX, cY - cH / 2, cW, cH, 4); ctx.fill(); ctx.stroke()
        ctx.fillStyle    = ec
        ctx.font         = '9px ui-monospace, monospace'
        ctx.textAlign    = 'center'
        ctx.textBaseline = 'middle'
        const delta = s.currentPrice - s.entryPrice
        ctx.fillText(`${isProfit ? '+' : ''}$${Math.abs(delta).toFixed(0)}`, cX + cW / 2, cY)
      }

      // ── Price line + fill ─────────────────────────────────────────────────
      if (points.length >= 2) {
        // Gradient fill
        const grad = ctx.createLinearGradient(0, PAD.t, 0, PAD.t + iH)
        grad.addColorStop(0, lineColor + '28')
        grad.addColorStop(1, lineColor + '00')

        ctx.beginPath()
        ctx.moveTo(toX(points[0].t), toY(points[0].p))
        for (let i = 1; i < points.length; i++) ctx.lineTo(toX(points[i].t), toY(points[i].p))
        ctx.lineTo(toX(points[points.length - 1].t), PAD.t + iH)
        ctx.lineTo(toX(points[0].t), PAD.t + iH)
        ctx.closePath()
        ctx.fillStyle = grad
        ctx.fill()

        // Line
        ctx.beginPath()
        ctx.moveTo(toX(points[0].t), toY(points[0].p))
        for (let i = 1; i < points.length; i++) ctx.lineTo(toX(points[i].t), toY(points[i].p))
        ctx.strokeStyle = lineColor
        ctx.lineWidth   = 1.5
        ctx.lineJoin    = 'round'
        ctx.stroke()
      }

      // ── Live price dot + chip ─────────────────────────────────────────────
      if (s.currentPrice > 0) {
        const ly = toY(s.currentPrice)

        // Pulse rings
        for (let ring = 0; ring < 2; ring++) {
          const phase = (s.pulseT + ring * 0.5) % 1
          ctx.beginPath()
          ctx.arc(PAD.l + iW, ly, 4 + phase * 14, 0, Math.PI * 2)
          ctx.strokeStyle = lineColor + Math.round((1 - phase) * 0.35 * 255).toString(16).padStart(2, '0')
          ctx.lineWidth   = 1.5
          ctx.stroke()
        }
        ctx.beginPath(); ctx.arc(PAD.l + iW, ly, 4, 0, Math.PI * 2)
        ctx.fillStyle = lineColor; ctx.fill()
        ctx.beginPath(); ctx.arc(PAD.l + iW, ly, 1.8, 0, Math.PI * 2)
        ctx.fillStyle = '#fff'; ctx.fill()

        // Price chip
        const cW = PAD.r - 8, cH = 20, cX = PAD.l + iW + 6
        const cY = Math.max(PAD.t + cH / 2, Math.min(PAD.t + iH - cH / 2, ly))
        ctx.fillStyle = lineColor
        ctx.beginPath(); ctx.roundRect(cX, cY - cH / 2, cW, cH, 4); ctx.fill()
        ctx.fillStyle    = '#fff'
        ctx.font         = '10px ui-monospace, monospace'
        ctx.textAlign    = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(`$${s.currentPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}`, cX + cW / 2, cY)
      }

      // ── Y-axis labels ─────────────────────────────────────────────────────
      ctx.fillStyle    = C.label
      ctx.font         = '9px ui-monospace, monospace'
      ctx.textAlign    = 'right'
      ctx.textBaseline = 'middle'
      for (let p = Math.ceil(yMin / step) * step; p <= yMax; p += step) {
        const y = toY(p)
        if (y < PAD.t + 6 || y > PAD.t + iH - 6) continue
        ctx.fillText(`$${fmtPrice(p)}`, PAD.l - 6, y)
      }

      // ── X-axis labels ─────────────────────────────────────────────────────
      ctx.fillStyle    = C.label
      ctx.font         = '9px ui-monospace, monospace'
      ctx.textAlign    = 'center'
      ctx.textBaseline = 'top'
      for (let i = 0; i <= 4; i++) {
        const t = tStart + (i / 4) * s.windowMs
        const x = toX(t)
        if (x < PAD.l || x > PAD.l + iW) continue
        ctx.fillText(
          new Date(t).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' }),
          x, PAD.t + iH + 6,
        )
      }

      rafRef.current = requestAnimationFrame(draw)
    }

    rafRef.current = requestAnimationFrame(draw)
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); ro.disconnect() }
  }, [])

  const hasEntry   = (entryPrice ?? 0) > 0
  const priceAbove = hasEntry && currentPrice > (entryPrice ?? 0)
  const pnl        = hasEntry ? currentPrice - (entryPrice ?? 0) : null
  const dispColor  = hasEntry ? (priceAbove ? 'var(--green-dark)' : 'var(--pink-dark)') : 'var(--text-primary)'
  const posLabel   = positionSide === 'long' ? 'LONG' : positionSide === 'short' ? 'SHORT' : null
  const posColor   = positionSide === 'long' ? 'var(--green-dark)' : 'var(--pink-dark)'
  const posBg      = positionSide === 'long' ? 'rgba(46,158,104,0.10)' : 'rgba(190,74,64,0.10)'
  const posBorder  = positionSide === 'long' ? 'rgba(46,158,104,0.25)' : 'rgba(190,74,64,0.25)'

  return (
    <div className="card" style={{ padding: '16px 14px 12px', display: 'flex', flexDirection: 'column', width: '100%', boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12, padding: '0 2px' }}>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div>
            <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 3 }}>
              BTC-PERP · Hyperliquid
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 26, fontWeight: 800, color: dispColor, letterSpacing: '-0.02em', transition: 'color 0.4s' }}>
                {currentPrice > 0 ? `$${currentPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—'}
              </span>
              {pnl !== null && (
                <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 700, color: priceAbove ? 'var(--green-dark)' : 'var(--pink-dark)', transition: 'color 0.4s' }}>
                  {priceAbove ? '+' : ''}{pnl.toFixed(0)}
                </span>
              )}
            </div>
          </div>
          {posLabel && (
            <div style={{ padding: '4px 10px', borderRadius: 8, background: posBg, border: `1px solid ${posBorder}`, fontSize: 10, fontWeight: 800, color: posColor, letterSpacing: '0.05em' }}>
              {posLabel === 'LONG' ? '↑ ' : '↓ '}{posLabel}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8 }}>
          {hasEntry && entryPrice && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 10, color: 'var(--text-muted)' }}>
              <svg width="14" height="2" viewBox="0 0 14 2"><line x1="0" y1="1" x2="14" y2="1" stroke={priceAbove ? C.green : C.red} strokeWidth="1.5" strokeDasharray="4 3" /></svg>
              <span style={{ fontFamily: 'var(--font-geist-mono)', fontWeight: 600 }}>Entry ${entryPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
            </div>
          )}
          <div style={{ display: 'flex', gap: 3 }}>
            {WINDOWS.map((w, i) => (
              <button key={w.label} onClick={() => setWindowIdx(i)} style={{
                background: windowIdx === i ? 'var(--text-primary)' : 'transparent',
                color: windowIdx === i ? 'var(--bg-card)' : 'var(--text-muted)',
                border: `1px solid ${windowIdx === i ? 'var(--text-primary)' : 'var(--border)'}`,
                borderRadius: 6, padding: '3px 10px', fontSize: 10, fontWeight: 700,
                cursor: 'pointer', transition: 'all 0.15s',
              }}>{w.label}</button>
            ))}
          </div>
        </div>
      </div>

      <div ref={containerRef} style={{ width: '100%', height: 280, position: 'relative' }}>
        <canvas ref={canvasRef} style={{ display: 'block' }} />
        {currentPrice <= 0 && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ fontSize: 10, letterSpacing: '0.07em', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Loading chart…
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
