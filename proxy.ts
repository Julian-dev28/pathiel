/**
 * Next.js proxy/middleware: validates Appwrite session on API routes,
 * injects x-appwrite-session header for downstream route handlers.
 *
 * Public routes pass through without auth.
 * When APPWRITE_PROJECT_ID is not set, all requests pass through (demo mode).
 */

import { NextRequest, NextResponse } from 'next/server'

const PUBLIC_API_PREFIXES = [
  '/api/markets',
  '/api/btc-price',
  '/api/python-health',
  '/api/auth/',
  '/api/orderbook',
  '/api/market-quote',
  '/api/settings/',
]

export function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl

  // Basic Auth — protects all routes when BASIC_AUTH_PASSWORD is set
  const basicAuthPw = process.env.BASIC_AUTH_PASSWORD
  if (basicAuthPw) {
    const auth = req.headers.get('authorization')
    let authed = false
    if (auth?.startsWith('Basic ')) {
      const decoded = Buffer.from(auth.slice(6), 'base64').toString('utf-8')
      const colonIdx = decoded.indexOf(':')
      const pass = colonIdx >= 0 ? decoded.slice(colonIdx + 1) : decoded
      authed = pass === basicAuthPw
    }
    if (!authed) {
      return new NextResponse('Unauthorized', {
        status: 401,
        headers: { 'WWW-Authenticate': 'Basic realm="Sentient"' },
      })
    }
  }

  // Only guard /api/ routes for Appwrite auth
  if (!pathname.startsWith('/api/')) {
    return NextResponse.next()
  }

  // Demo mode: Appwrite not configured — skip auth entirely
  if (!process.env.APPWRITE_PROJECT_ID) {
    return NextResponse.next()
  }

  // Public routes pass through
  if (PUBLIC_API_PREFIXES.some(p => pathname.startsWith(p))) {
    return NextResponse.next()
  }

  // Extract session from cookie or Authorization header
  const sessionCookie = req.cookies.get('appwrite-session')?.value
  const authHeader    = req.headers.get('authorization')
  const bearerToken   = authHeader?.startsWith('Bearer ') ? authHeader.slice(7) : null
  const token         = sessionCookie ?? bearerToken

  if (!token) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  // Forward session token to route handler via header
  const headers = new Headers(req.headers)
  headers.set('x-appwrite-session', token)
  return NextResponse.next({ request: { headers } })
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
}

// Alias so Next.js picks this up as middleware
export { proxy as middleware }
