'use client'

import { useEffect, useRef } from 'react'
import Link from 'next/link'
import s from './landing.module.css'

function useReveal() {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const root = ref.current
    if (!root) return
    const els = root.querySelectorAll(`.${s.reveal}`)
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => {
        if (e.isIntersecting) { e.target.classList.add(s.visible); io.unobserve(e.target) }
      }),
      { threshold: 0.1, rootMargin: '0px 0px -48px 0px' },
    )
    els.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [])
  return ref
}

function r(...extra: (string | undefined)[]) {
  return [s.reveal, ...extra].filter(Boolean).join(' ')
}

const PIPELINE = [
  { num: '01', name: 'Position & Orders',   desc: 'get_clearinghouse_state reads your live equity, open position, and unrealized PnL. get_open_orders catches any stale orders before placing new ones.' },
  { num: '02', name: 'Live Price & Book',   desc: 'get_all_mids pulls the live BTC mid price. get_l2_book snapshots full order book depth — bid and ask pressure at every level.' },
  { num: '03', name: 'Candle Momentum',     desc: 'get_candle_snapshot pulls the last 10 15-minute candles. Direction, acceleration, and structure — the primary signal driving every verdict.' },
  { num: '04', name: 'Funding & Fills',     desc: 'get_funding_history checks the current perpetual funding rate — positive means longs are paying. get_user_fills shows the last 5 executions for context.' },
  { num: '05', name: 'AOMI Verdict',        desc: 'Agent synthesizes all 7 data sources and streams a direct verdict: LONG / SHORT / CLOSE / PASS — with confidence %. 60%+ is enough to act.' },
  { num: '06', name: 'Risk Gate & Execute', desc: 'Kelly criterion sizes the trade from live equity. 5× leverage. Auto Mode places the IOC order on Hyperliquid. Real orders, real settlement, zero clicks.' },
]

const HL_TOOLS = [
  { fn: 'get_clearinghouse_state', label: 'Position & Equity',   desc: 'Live account equity, open position side/size, entry price, unrealized PnL, and margin usage — checked first every cycle.' },
  { fn: 'get_open_orders',         label: 'Open Orders',         desc: 'All pending open orders for the account — ensures stale orders are caught before a new one is placed.' },
  { fn: 'get_all_mids',            label: 'Live Mid Prices',     desc: 'Current mid prices for all listed assets. Used to anchor the BTC price snapshot at decision time.' },
  { fn: 'get_l2_book',             label: 'Order Book Depth',    desc: 'Full L2 order book — bid and ask sizes at every price level. Reveals real buying and selling pressure.' },
  { fn: 'get_candle_snapshot',     label: 'Candlestick Data',    desc: 'OHLCV candles at 15m and 1h intervals. Direction and acceleration of the last 10 candles drive the primary verdict signal.' },
  { fn: 'get_funding_history',     label: 'Funding Rate',        desc: 'Current perpetual funding rate. Positive = longs pay shorts, a cost that erodes hold time. Factored into LONG conviction.' },
  { fn: 'get_user_fills',          label: 'Trade History',       desc: 'Last 5 fills for the account — recent execution context so the agent knows what was just traded and at what price.' },
  { fn: 'get_meta',                label: 'Exchange Metadata',   desc: 'Asset specs, size decimals, tick sizes, and universe config from Hyperliquid — available on demand for order validation.' },
]

const FEATURES = [
  {
    icon: '⬡',
    title: 'Live Hyperliquid data',
    body: 'Before every decision: live BTC price, order book depth, your account equity and open positions — all from Hyperliquid via AOMI tools.',
  },
  {
    icon: '⊛',
    title: 'Continuous 24/7 loop',
    body: 'BTC-PERP never closes. Auto Mode runs every 90 seconds — Hyperliquid data + web search → verdict → execute → hold → repeat.',
  },
  {
    icon: '⟁',
    title: 'Grounded in live data',
    body: 'Every verdict is anchored in a live Brave web search and Hyperliquid order book query — pulled seconds before the decision.',
  },
  {
    icon: '⊕',
    title: 'Your keys, your orders',
    body: 'Your wallet key lives in your server environment. AOMI never holds funds. Auto Mode executes when confident; Manual Mode keeps you in the loop.',
  },
]

const CODE = [
  { k: 'btc_price',    v: '$78,243.50',  c: '// get_all_mids' },
  { k: 'bid_depth',    v: '22.2 BTC',    c: '// get_l2_book · bids',    hi: 'green' },
  { k: 'ask_depth',    v: '1.19 BTC',    c: '// get_l2_book · asks' },
  { k: 'candles_15m',  v: '↑↑↓↑↑',      c: '// get_candle_snapshot',   hi: 'green' },
  { k: 'funding_rate', v: '+0.0082%',    c: '// get_funding_history' },
  { k: 'open_orders',  v: '0',           c: '// get_open_orders · clear' },
  { k: 'last_fill',    v: 'LONG $77,940',c: '// get_user_fills' },
  { k: 'equity',       v: '$199.86',     c: '// get_clearinghouse_state',hi: 'amber' },
  { k: 'position',     v: 'FLAT',        c: '// no open position' },
  { k: 'confidence',   v: '72%',         c: '// agent certainty',        hi: 'green' },
  { k: 'verdict',      v: 'LONG',        c: '// execute',                hi: 'green' },
]

export default function Landing() {
  const rootRef = useReveal()

  return (
    <div className={s.root} ref={rootRef}>

      {/* Nav */}
      <nav className={s.nav}>
        <a href="/" className={s.navLogo}>
          AOMI <span className={s.navLogoAccent}>TRADER</span>
        </a>
        <div className={s.navLinks}>
          <a href="#how"      className={s.navLink}>How it works</a>
          <a href="#tools"    className={s.navLink}>Tools</a>
          <a href="#signals"  className={s.navLink}>Signals</a>
          <a href="#features" className={s.navLink}>Features</a>
        </div>
        <Link href="/agent" className={s.navCta}>Open Agent →</Link>
      </nav>

      {/* Hero */}
      <section className={s.hero}>
        <div className={s.heroInner}>
          <p className={s.heroEyebrow}>BTC-PERP · Hyperliquid · Powered by AOMI</p>
          <h1 className={s.heroHeadline}>
            TRADE<br />
            <span className={s.heroAccent}>BTC</span><br />
            PERP
          </h1>
          <p className={s.heroSub}>
            BTC-PERP runs 24/7. Before every trade, AOMI queries live Hyperliquid
            price and order book data, checks your position, and searches the web
            for current BTC news. LONG, SHORT, or PASS — grounded in what's
            happening right now.
          </p>
          <div className={s.heroCtas}>
            <Link href="/agent" className={s.btnPrimary}>Open Agent →</Link>
            <a href="#how"      className={s.btnSecondary}>How it works</a>
          </div>
        </div>
        <div className={s.scrollCue}>
          <div className={s.scrollLine} />
          Scroll
        </div>
      </section>

      {/* Stats */}
      <div className={s.statsRow}>
        {[
          { num: '24/7',  accent: '',    label: 'continuous market',   desc: 'BTC-PERP never closes. AOMI watches it continuously — no windows, no gaps, no missed signals.' },
          { num: '5',     accent: '×',   label: 'leverage',            desc: 'Cross-margin 5× leverage. Kelly-sized from your live equity. PASS on weak or ambiguous signals.' },
          { num: '0',     accent: ' clicks', label: 'after setup',     desc: 'Analysis, sizing, EIP-712 signing, order placement — all runs without you in Auto Mode.' },
        ].map(({ num, accent, label, desc }, i) => (
          <div className={`${s.statItem} ${r(i > 0 ? s.d1 : undefined)}`} key={label}>
            <div className={s.statNum}>{num}<span className={s.statAccent}>{accent}</span></div>
            <div className={s.statLabel}>{label}</div>
            <div className={s.statDesc}>{desc}</div>
          </div>
        ))}
      </div>

      {/* How it works */}
      <section className={s.section} id="how">
        <div className={s.inner}>
          <p className={`${s.label} ${r()}`}>How It Works</p>
          <h2 className={`${s.headline} ${r(s.d1)}`}>Six steps.<br />One decision.</h2>
          <p className={`${s.sub} ${r(s.d2)}`}>
            From analysis trigger to executed order in a single agent cycle.
            AOMI queries Hyperliquid, searches the web, reasons, and executes — or hands you the trade button.
          </p>
          <div className={s.pipelineList}>
            {PIPELINE.map((step, i) => (
              <div className={`${s.pipelineItem} ${r(i < 3 ? s.d1 : s.d2)}`} key={step.num}>
                <span className={s.pipelineNum}>{step.num}</span>
                <span className={s.pipelineName}>{step.name}</span>
                <span className={s.pipelineDesc}>{step.desc}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* AOMI Tools */}
      <section className={s.section} id="tools">
        <div className={s.inner}>
          <p className={`${s.label} ${r()}`}>AOMI Hyperliquid Tools</p>
          <h2 className={`${s.headline} ${r(s.d1)}`}>8 live data tools.<br />Called every cycle.</h2>
          <p className={`${s.sub} ${r(s.d2)}`}>
            Every agent cycle queries all 8 Hyperliquid tools before deciding. No stale data, no guesses — every verdict is grounded in the current state of the market and your account.
          </p>
          <div className={s.toolsGrid}>
            {HL_TOOLS.map((t, i) => (
              <div className={`${s.toolItem} ${r(i < 4 ? s.d1 : s.d2)}`} key={t.fn}>
                <span className={s.toolFn}>{t.fn}</span>
                <span className={s.toolLabel}>{t.label}</span>
                <span className={s.toolDesc}>{t.desc}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Signals */}
      <section className={s.section} id="signals">
        <div className={s.inner}>
          <p className={`${s.label} ${r()}`}>What AOMI Sees</p>
          <div className={s.signalsGrid}>
            <div>
              <h2 className={`${s.signalsHeadline} ${r(s.d1)}`}>
                Live data queried.<br />Agent searches.
              </h2>
              <p className={`${s.signalsBody} ${r(s.d2)}`}>
                AOMI calls Hyperliquid tools before every decision — live price,
                order book depth, your account state. Then searches the web for
                current BTC news and sentiment. Synthesises both and streams
                a direct verdict: LONG, SHORT, or PASS.
              </p>
              <ul className={`${s.signalsList} ${r(s.d2)}`}>
                {[
                  'get_clearinghouse_state — equity, position, PnL before every decision',
                  'get_open_orders — stale order check before placing new ones',
                  'get_all_mids — live BTC mid price at decision time',
                  'get_l2_book — full order book depth, bid vs ask pressure',
                  'get_candle_snapshot — last 10 × 15m candles, primary momentum signal',
                  'get_funding_history — perpetual funding rate, factored into hold cost',
                  'get_user_fills — last 5 fills for recent execution context',
                  'get_meta — exchange specs available on demand',
                ].map(item => (
                  <li className={s.signalItem} key={item}>
                    <span className={s.signalDot} />
                    {item}
                  </li>
                ))}
              </ul>
            </div>
            <div className={`${s.codeBlock} ${r(s.d1)}`}>
              {CODE.map(line => (
                <div className={s.codeLine} key={line.k}>
                  <span className={s.codeKey}>{line.k}</span>
                  <span className={line.hi === 'green' ? s.codeGreen : line.hi === 'amber' ? s.codeAmber : s.codeVal}>
                    {line.v}
                  </span>
                  <span className={s.codeComment}>{line.c}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className={s.section} id="features">
        <div className={s.inner}>
          <p className={`${s.label} ${r()}`}>Why AOMI</p>
          <h2 className={`${s.headline} ${r(s.d1)}`}>Built for traders<br />who can&apos;t watch 24/7.</h2>
          <div className={s.modesGrid}>
            {FEATURES.map((f, i) => (
              <div className={`${s.modeItem} ${r(i < 2 ? s.d1 : s.d2)}`} key={f.title}>
                <p className={s.modeName} style={{ fontSize: 20, marginBottom: 6 }}>{f.icon}</p>
                <p className={s.modeName}>{f.title}</p>
                <p className={s.modeDesc}>{f.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className={s.cta}>
        <div className={s.ctaInner}>
          <h2 className={`${s.ctaHeadline} ${r()}`}>
            YOUR AGENT<br />IS READY
          </h2>
          <p className={`${s.ctaSub} ${r(s.d1)}`}>
            BTC-PERP. 24/7. Live Hyperliquid data before every trade.<br />
            One slider. Everything else runs without you.
          </p>
          <div className={`${s.ctaBtns} ${r(s.d2)}`}>
            <Link href="/agent" className={s.btnPrimary}>Open Agent →</Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className={s.footer}>
        <span className={s.footerBrand}>AOMI Trader · BTC-PERP · Powered by aomi-labs</span>
        <div className={s.footerLinks}>
          <Link href="/agent"     className={s.footerLink}>Agent</Link>
          <a href="https://github.com/Julian-dev28/aomi-trader" target="_blank" rel="noopener noreferrer" className={s.footerLink}>GitHub</a>
          <a href="https://aomi.dev" target="_blank" rel="noopener noreferrer" className={s.footerLink}>AOMI</a>
        </div>
      </footer>

    </div>
  )
}
