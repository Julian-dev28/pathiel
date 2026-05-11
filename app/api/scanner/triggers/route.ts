import { triggerStore } from '@/lib/scanner/store'

export const runtime = 'nodejs'

export async function GET(req: Request) {
  const url = new URL(req.url)
  const historyLimit = parseInt(url.searchParams.get('history') ?? '50', 10)

  const active = triggerStore.getActiveTriggers()
  const history = triggerStore.getHistory(historyLimit)

  return Response.json({
    active,
    history,
    stats: {
      totalTriggers: active.length,
    },
  })
}
