import { useEffect, useState } from 'react'

const STATUS_STYLES = {
  queued: 'bg-slate-200 text-slate-700',
  running: 'bg-blue-100 text-blue-700',
  succeeded: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  rolled_back: 'bg-amber-100 text-amber-700',
}

function StatusBadge({ status }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_STYLES[status] ?? 'bg-slate-100'}`}>
      {status}
    </span>
  )
}

export default function App() {
  const [health, setHealth] = useState(null)

  useEffect(() => {
    fetch('/api/healthz')
      .then((r) => r.json())
      .then((d) => setHealth(d.status))
      .catch(() => setHealth('unreachable'))
  }, [])

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b bg-white px-6 py-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">deployd</h1>
        <div className="text-sm">
          API:{' '}
          <span className={health === 'ok' ? 'text-green-600' : 'text-red-600'}>
            {health ?? '…'}
          </span>
        </div>
      </header>

      <main className="max-w-3xl mx-auto p-6 space-y-6">
        <section className="bg-white rounded-lg border p-5">
          <h2 className="font-medium mb-3">Apps</h2>
          <p className="text-sm text-slate-500">
            App registry viewer/editor — needs config endpoints on the API (M6).
          </p>
        </section>

        <section className="bg-white rounded-lg border p-5">
          <h2 className="font-medium mb-3">Recent deploys</h2>
          <p className="text-sm text-slate-500">
            Deploy history list — needs GET /deploys index endpoint (M6).
          </p>
          <div className="mt-3 flex gap-2">
            {Object.keys(STATUS_STYLES).map((s) => (
              <StatusBadge key={s} status={s} />
            ))}
          </div>
        </section>
      </main>
    </div>
  )
}
