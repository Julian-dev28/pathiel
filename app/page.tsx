export const runtime = 'nodejs'
export async function GET() {
  return new Response(JSON.stringify({ status: 'hermes-trader API', endpoints: '/api/agent/*, /api/hl/*' }), {
    headers: { 'Content-Type': 'application/json' },
  })
}
