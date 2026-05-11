# AOMI Hyperliquid Trader

> Autonomous BTC-PERP momentum trader on Hyperliquid, powered by [`@aomi-labs/client`](https://github.com/aomi-labs/aomi-sdk).

**What it does:** Watches live BTC-PERP price action and order book on Hyperliquid, searches the web for market sentiment, decides LONG / SHORT / CLOSE / PASS, and executes the trade — with no human in the loop.

---

## The problem it solves

BTC momentum windows open and close constantly — 5-minute bursts, 1-hour trends, 4-hour swings. Most traders catch a handful per day. Most edge comes from acting fast when a signal is clear and getting out before it reverses.

This agent runs a 60-second analysis loop, flips direction when momentum shifts, and closes positions before they turn into losses. It's not waiting for "$79K breakout" macro levels — it trades whatever structure the current candles show.

---

## What AOMI does here

Every analysis cycle, AOMI:

1. **Queries live Hyperliquid data** — `get_all_mids` for current price, `get_l2_book` for bid/ask pressure, `get_clearinghouse_state` for open position and account equity.
2. **Searches the live web** via `brave_search` — BTC news, sentiment, momentum reports, right now.
3. **Returns a verdict** — `LONG`, `SHORT`, `CLOSE`, or `PASS` with a confidence percentage.
4. **Auto-executes** if confidence ≥ 60%: opens the position, or closes and resets for immediate re-entry.
5. **Runs again in 60 seconds** — perpetual loop, no windows to wait for.

A single `Session` from `@aomi-labs/client` manages the agent lifecycle. No custom model calls, no prompt chains.

---

## Setup (< 5 minutes)

### 1. Clone and install

```bash
git clone https://github.com/YOUR_HANDLE/aomi-kalshi-trader
cd aomi-kalshi-trader
npm install
```

### 2. Configure environment

```bash
cp .env.local.example .env.local
```

Fill in `.env.local`:

```bash
# AOMI
AOMI_BASE_URL=https://api.aomi.dev
AOMI_APP=hyperliquid
AOMI_API_KEY=your-aomi-api-key

# Hyperliquid — API wallet is the signing agent; master holds all funds
HYPERLIQUID_WALLET_ADDRESS=0xYOUR_API_WALLET
HYPERLIQUID_MASTER_ADDRESS=0xYOUR_MASTER_ACCOUNT   # omit if single-account
HYPERLIQUID_PRIVATE_KEY=0xYOUR_PRIVATE_KEY

NEXT_PUBLIC_HL_WALLET=0xYOUR_API_WALLET
NEXT_PUBLIC_HL_MASTER=0xYOUR_MASTER_ACCOUNT
```

### 3. Run

```bash
npm run dev
# → http://localhost:3000/agent    ← autonomous trading
# → http://localhost:3000/dashboard ← live chart + manual analysis
```

---

## How the AOMI integration works

```typescript
import { Session } from '@aomi-labs/client'

const session = new Session(
  { baseUrl: process.env.AOMI_BASE_URL, apiKey: process.env.AOMI_API_KEY },
  {
    app:       'hyperliquid',
    sessionId: sessionId,
    publicKey: masterAddress,   // query master account for real balances
    userState: { address: masterAddress, is_connected: true, chain_id: 1337 },
  },
)

// Inject live market context, then let AOMI use native HL tools + web search
const prompt = `
  BTC-PERP mid: $94,150 | LONG 0.0020 BTC @ $93,800 · unrealized PnL: +$0.70

  Check live price and order book on Hyperliquid. Check my current position.
  Search for the latest BTC price action and momentum.
  Give me a direct LONG / SHORT / CLOSE / PASS verdict.
`

// Stream the response
for await (const event of session.stream(prompt)) {
  if (event.type === 'message') console.log(event.text)
}
// → "LONG — momentum holding above $94K, bid side heavier 2:1, PnL positive ..."
```

AOMI handles `get_all_mids`, `get_l2_book`, `get_clearinghouse_state`, and `brave_search` natively. Your code just sends a prompt and streams the result.

---

## Architecture

```
Browser (Next.js App Router)
│
├── /agent          ← Autonomous loop: 60s cycle, auto-execute, chat
├── /dashboard      ← Live candlestick chart, market card, positions
│
└── /api/
    ├── aomi/chat        ← Server-side AOMI session, SSE stream to browser
    ├── hl/price         ← Live BTC-PERP mid price
    ├── hl/candles       ← OHLC candlestick data (1m / 5m intervals)
    ├── hl/account       ← Equity, spot USDC, open position
    ├── hl/orderbook     ← L2 bid/ask snapshot
    ├── hl/place-order   ← EIP-712 signed IOC limit orders
    └── hl/close-position← Market close of current BTC-PERP position
```

**Key files:**

| File | What it does |
|------|-------------|
| `lib/aomi-session.ts` | `Session` wrapper, system prompt, prompt builder |
| `lib/hyperliquid.ts` | HL API calls, EIP-712 signing, order placement |
| `app/api/aomi/chat/route.ts` | SSE-streaming AOMI responses to browser |
| `app/agent/page.tsx` | Autonomous loop, risk slider, verdict UI, auto-execute |
| `app/dashboard/page.tsx` | Candlestick chart, market card, manual analysis |
| `components/HLPriceChart.tsx` | Canvas-rendered OHLC candlestick chart with live partial candle |

---

## Auto Mode

The agent page runs a continuous 60-second loop:

```
analyze → LONG/SHORT (≥60%)? → open position → 2-min cooldown
                ↓
             CLOSE? → close position → immediate re-entry
                ↓
              PASS  → wait 60s → analyze again
```

**Verdicts:**
- `LONG` — buy BTC-PERP at mid × 1.05 (IOC limit, 5% slippage allowance)
- `SHORT` — sell BTC-PERP at mid × 0.95
- `CLOSE` — close the current position, reset cooldown, re-enter immediately
- `PASS` — no readable edge; wait for next cycle

State (auto on/off, last trade time, current session ID) persists in `sessionStorage` — survives page navigation, resets on new tab.

---

## Order signing

Hyperliquid requires EIP-712 typed data signatures for every order:

```
connectionId = keccak256(msgpack(action) + nonce_BE8 + 0x00)
sig = signTypedData({ domain: {name:'Exchange', version:'1', chainId:1337}, type: Agent, message: {source:'a', connectionId} })
```

The API wallet signs; if a master account is configured, orders are routed to the master account via the HL authorized-agent table. Balances and positions are always queried against the master account.

---

## Multi-Market Scanner + Auto-Trader

The `/scanner` dashboard watches 200+ Hyperliquid perp/spot markets continuously and fires AI analysis only when statistical triggers (price spikes, volume surges, breakouts, Bollinger squeezes) fire.

### Running the scanner daemon

```bash
# In one terminal
npm run dev

# In a second terminal — starts polling every 60s
node scripts/scanner-daemon.mjs

# Optional env vars:
#   SCANNER_API_URL=http://localhost:3000   (default)
#   SCANNER_INTERVAL_MS=60000               (default, 60s)
#   SCANNER_MIN_SCORE=1.0                   (min composite score 0-10)
```

### LIVE-mode flip procedure

1. Navigate to `/scanner` — the auto-trade toggle shows **OFF** or **DRY**.
2. Click through: OFF → DRY → LIVE. The LIVE transition requires typing `LIVE` to confirm.
3. **LIVE mode also requires `ALLOW_LIVE_TRADING=true` in `.env.local`** — without this env var, the API returns 403.
4. To instantly disable: press **Cmd+K** or click the toggle (sets mode to OFF).
5. Default safety caps: ≤3 concurrent positions, $200 per trade, -$100 daily loss kill switch, 60-min cooldown per market. Edit at `/api/scanner/auto-trade/config` or in `.scanner-config.json`.

### Press Cmd+K anywhere on /scanner to kill switch

Built on [`@aomi-labs/client`](https://github.com/aomi-labs/aomi-sdk) · Hyperliquid API · Next.js 15
## Autonomous Multi-Market Agent

The `/agent` page is now a multi-market autonomous trading firm: **scanner** (perception) → **research** (AI analyst) → **risk gates** (compliance) → **executor** (trader). It watches every market on Hyperliquid via `lib/hl-universe.ts`, fires statistical triggers (pctMoveSpike, volumeSpike, breakout, rangeCompression, trendStrength) in `lib/agent/triggers.ts`, and only runs AI analysis on triggered candidates via the heartbeat daemon.

### Running the heartbeat daemon

```bash
# Terminal 1: Next.js dev server
npm run dev

# Terminal 2: heartbeat daemon — polls the full HL universe every 60s
node scripts/agent-heartbeat.mjs
```

The heartbeat calls `/api/agent/scan` → ingests perceptions → triggers `/api/agent/research/{coin}` for high-score candidates (default threshold 75) → runs the research pipeline (multi-timeframe indicators + news + AI verdict) → executes through risk gates. All AI analysis happens server-side; the UI streams results every 5s.

### LIVE mode

1. Navigate to `/agent` — the top bar shows the agent mode (OFF by default).
2. Click **ACTIVATE LIVE** — a confirmation modal appears.
3. Type the word `LIVE` into the input to confirm. Mode flips to LIVE and real orders will execute.
4. **Cmd+K** from anywhere on the page instantly sets mode to OFF and logs a kill-switch message.
5. Default risk caps: ≤3 concurrent positions, $200/trade notional, -$100 daily loss kill switch, 60-min cooldown per market, news blackout on binary events. Configure at `/api/agent/config` or by editing `.agent-config.json`.

### Architecture

```
Heartbeat (node scripts/agent-heartbeat.mjs, every 60s)
  └→ POST /api/agent/scan      → scans all HL markets, returns triggered perceptions
  └→ POST /api/agent/ingest    → stores perceptions in agent memory
  └→ POST /api/agent/research/{coin} → deep analysis pipeline:
       1. Fetch 1h/4h/1d candles + indicators
       2. Fetch news via Brave Search
       3. Call AI through OpenRouter with full context
       4. Parse verdict, persist analysis
       5. POST /api/agent/execute → runs 10 risk gates → executes in LIVE mode

Browser (/agent page, polls /api/agent/state every 5s)
  └→ Watchlist: top-12 markets by composite trigger score
  └→ Decision stream: live AI verdicts with gate results
  └→ Chat: existing BTC-PERP analyst (single market, manual trigger)
```

