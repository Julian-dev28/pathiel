import type { KalshiMarket } from './types'

export const KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'
export const KALSHI_HOST = 'https://api.elections.kalshi.com'

/** Month abbreviations in Kalshi ticker format (ET-based) */
export const MONTHS_ET = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']

/** Parse Eastern Time date/time parts from the current moment */
export function getETParts(): Record<string, number> {
  const now = new Date()
  return Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      year: 'numeric', month: 'numeric', day: 'numeric',
      hour: 'numeric', minute: 'numeric', hour12: false,
    }).formatToParts(now)
      .filter(p => p.type !== 'literal')
      .map(p => [p.type, parseInt(p.value)])
  ) as Record<string, number>
}

/**
 * Compute the current active KXBTC15M event_ticker using ET timezone.
 * Format: KXBTC15M-{YY}{MON}{DD}{HHMM} — date/time in US Eastern Time.
 */
export function getCurrentEventTicker(): string {
  const { year, month, day, hour, minute } = getETParts()

  // Advance to the end of the current 15-min block
  let blockMin  = Math.ceil((minute + 1) / 15) * 15
  let blockHour = hour % 24  // hour12:false can yield 24 at midnight on some engines
  if (blockMin >= 60) { blockMin = 0; blockHour += 1 }

  const yy  = String(year).slice(-2)
  const mon = MONTHS_ET[month - 1]
  const dd  = String(day).padStart(2, '0')
  const hh  = String(blockHour).padStart(2, '0')
  const mm  = String(blockMin).padStart(2, '0')
  return `KXBTC15M-${yy}${mon}${dd}${hh}${mm}`
}

/**
 * Compute the KXBTCD hourly event_ticker for the current (or next) ET hour.
 * Format: KXBTCD-{YY}{MON}{DD}{HH} — closing hour in US Eastern Time.
 * offsetHours=1 gives the next hour's event (fallback when current is near/past expiry).
 */
export function getCurrentKXBTCDEventTicker(offsetHours = 0): string {
  const { year, month, day, hour } = getETParts()
  let closeHour  = (hour % 24) + 1 + offsetHours
  let closeDay   = day
  let closeMonth = month
  let closeYear  = year

  while (closeHour >= 24) {
    closeHour -= 24
    closeDay  += 1
    const daysInMonth = new Date(closeYear, closeMonth, 0).getDate()
    if (closeDay > daysInMonth) {
      closeDay = 1
      closeMonth += 1
      if (closeMonth > 12) { closeMonth = 1; closeYear += 1 }
    }
  }

  const yy  = String(closeYear).slice(-2)
  const mon = MONTHS_ET[closeMonth - 1]
  const dd  = String(closeDay).padStart(2, '0')
  const hh  = String(closeHour).padStart(2, '0')
  return `KXBTCD-${yy}${mon}${dd}${hh}`
}

/**
 * Parse the close-time UTC ms from a KXBTC15M event ticker.
 * Ticker format: KXBTC15M-{YY}{MON}{DD}{HHMM} where time is US Eastern.
 * Returns 0 if parsing fails.
 */
export function parseKXBTC15MCloseMs(ticker: string): number {
  const t = ticker.replace(/-\d+$/, '')  // strip -NN suffix if present
  const m = t.match(/(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(\d{2})(\d{2})$/)
  if (!m) return 0
  const year  = 2000 + parseInt(m[1])
  const month = MONTHS_ET.indexOf(m[2])  // 0-based
  if (month < 0) return 0
  const day  = parseInt(m[3])
  const hour = parseInt(m[4])
  const min  = parseInt(m[5])
  // ET offset: EDT (UTC-4) Mar–Nov, EST (UTC-5) Nov–Mar
  const etOffsetHours = (month >= 2 && month <= 10) ? -4 : -5
  return Date.UTC(year, month, day, hour - etOffsetHours, min)
}

/**
 * Parse the close-time UTC ms from a KXBTCD event ticker.
 * Ticker format: KXBTCD-{YY}{MON}{DD}{HH} where HH is the closing ET hour.
 * Returns 0 if parsing fails.
 */
export function parseKXBTCDCloseMs(ticker: string): number {
  const m = ticker.match(/(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(\d{2})$/)
  if (!m) return 0
  const year  = 2000 + parseInt(m[1])
  const month = MONTHS_ET.indexOf(m[2])  // 0-based
  if (month < 0) return 0
  const day  = parseInt(m[3])
  const hour = parseInt(m[4])
  const etOffsetHours = (month >= 2 && month <= 10) ? -4 : -5  // EDT Mar–Nov, EST otherwise
  return Date.UTC(year, month, day, hour - etOffsetHours, 0, 0, 0)
}

/** Find the nearest-expiry open market in the series */
export function findNearestMarket(markets: KalshiMarket[]): KalshiMarket | null {
  if (!markets.length) return null
  return markets.sort(
    (a, b) => new Date(a.expiration_time).getTime() - new Date(b.expiration_time).getTime()
  )[0]
}

/** Minutes until a market closes (uses close_time — the actual 15-min window end) */
export function minutesUntilExpiry(market: KalshiMarket): number {
  // close_time is the 15-minute window end; expiration_time can be days later
  const closeTime = market.close_time || market.expiration_time
  const ms = new Date(closeTime).getTime() - Date.now()
  return Math.max(0, ms / 60_000)
}

/** Seconds until a market closes */
export function secondsUntilExpiry(market: KalshiMarket): number {
  const closeTime = market.close_time || market.expiration_time
  const ms = new Date(closeTime).getTime() - Date.now()
  return Math.max(0, ms / 1_000)
}
