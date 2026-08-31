import { useCallback, useEffect, useState } from 'react'

const STATUS_STYLES = {
  queued: 'bg-slate-200 text-slate-700',
  running: 'bg-blue-100 text-blue-700',
  succeeded: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  rolled_back: 'bg-amber-100 text-amber-700',
}

const STEP_ICON = { succeeded: '✓', failed: '✗', running: '…', skipped: '−' }

const APP_TEMPLATE = {
  releases_dir: '/srv/myapp/releases',
  current_link: '/srv/myapp/current',
  keep_releases: 5,
  artifact: { allowed_url_prefix: 'https://github.com/your-org/' },
  migrate: { command: null },
  restart: { command: ['sudo', 'systemctl', 'restart', 'myapp'] },
  health: { url: 'http://127.0.0.1:8000/healthz', retries: 10, interval_seconds: 3 },
}

const api = async (token, path, opts = {}) => {
  const resp = await fetch(`/api${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      'X-Admin-Token': token,
      ...opts.headers,
    },
  })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `HTTP ${resp.status}`)
  return resp.json()
}

function StatusBadge({ status }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_STYLES[status] ?? 'bg-slate-100'}`}>
      {status}
    </span>
  )
}

function AppCard({ name, spec, call, onChanged }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [freshSecret, setFreshSecret] = useState(null)
  const [error, setError] = useState(null)

  const startEdit = () => {
    const { secret: _secret, ...rest } = spec
    setDraft(JSON.stringify(rest, null, 2))
    setEditing(true)
    setError(null)
  }

  const save = async () => {
    try {
      await call(`/admin/apps/${name}`, { method: 'PUT', body: draft })
      setEditing(false)
      onChanged()
    } catch (e) {
      setError(e.message)
    }
  }

  const rotate = async () => {
    if (!confirm(`Rotate the HMAC secret for ${name}? CI must be updated with the new value.`)) return
    try {
      const out = await call(`/admin/apps/${name}/rotate-secret`, { method: 'POST' })
      setFreshSecret(out)
      onChanged()
    } catch (e) {
      setError(e.message)
    }
  }

  const remove = async () => {
    const typed = prompt(
      `Remove ${name} from the registry? Deployed releases on disk are NOT touched.\n\nType the app name to confirm:`
    )
    if (typed !== name) return
    try {
      await call(`/admin/apps/${name}`, { method: 'DELETE' })
      onChanged()
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="bg-white rounded-lg border p-5 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="font-medium">{name}</h3>
        <div className="flex gap-2 text-sm">
          <button className="px-3 py-1 rounded border hover:bg-slate-50" onClick={startEdit}>
            Edit
          </button>
          <button className="px-3 py-1 rounded border hover:bg-slate-50" onClick={rotate}>
            Rotate secret
          </button>
          <button
            className="px-3 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50"
            onClick={remove}
          >
            Remove
          </button>
        </div>
      </div>

      <div className="text-sm text-slate-600">
        Secret:{' '}
        {spec.secret?.configured ? (
          <code className="text-xs bg-slate-100 px-1 rounded">{spec.secret.fingerprint}</code>
        ) : (
          <span className="text-amber-600">not configured</span>
        )}
        {spec.secret?.env_override && <span className="ml-2 text-xs text-slate-400">(env override)</span>}
        <span className="mx-2">·</span>
        Releases: <code className="text-xs">{spec.releases_dir}</code>
      </div>

      {freshSecret && (
        <div className="bg-amber-50 border border-amber-200 rounded p-3 text-sm">
          {freshSecret.secret ? (
            <>
              New secret (shown once — copy to your CI now):
              <code className="block mt-1 text-xs break-all select-all">{freshSecret.secret}</code>
            </>
          ) : (
            <span>{freshSecret.warning}</span>
          )}
          <button className="mt-2 text-xs underline" onClick={() => setFreshSecret(null)}>
            dismiss
          </button>
        </div>
      )}

      {editing && (
        <div className="space-y-2">
          <textarea
            className="w-full h-56 font-mono text-xs border rounded p-2"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="flex gap-2 text-sm">
            <button className="px-3 py-1 rounded bg-slate-900 text-white" onClick={save}>
              Save
            </button>
            <button className="px-3 py-1 rounded border" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && <p className="text-sm text-red-600">{error}</p>}
    </div>
  )
}

function NewAppCard({ call, onChanged }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [draft, setDraft] = useState(JSON.stringify(APP_TEMPLATE, null, 2))
  const [error, setError] = useState(null)

  const save = async () => {
    if (!/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(name)) {
      setError('name must use lowercase letters, digits, and interior dashes')
      return
    }
    try {
      await call(`/admin/apps/${name}`, { method: 'PUT', body: draft })
      setOpen(false)
      setName('')
      setDraft(JSON.stringify(APP_TEMPLATE, null, 2))
      setError(null)
      onChanged()
    } catch (e) {
      setError(e.message)
    }
  }

  if (!open) {
    return (
      <button
        className="w-full border-2 border-dashed rounded-lg p-4 text-sm text-slate-500 hover:bg-white"
        onClick={() => setOpen(true)}
      >
        + Add app
      </button>
    )
  }
  return (
    <div className="bg-white rounded-lg border p-5 space-y-2">
      <input
        className="border rounded px-2 py-1 text-sm w-full"
        placeholder="app name (e.g. my-api)"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <textarea
        className="w-full h-56 font-mono text-xs border rounded p-2"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      <div className="flex gap-2 text-sm">
        <button className="px-3 py-1 rounded bg-slate-900 text-white" onClick={save}>
          Create
        </button>
        <button className="px-3 py-1 rounded border" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </div>
  )
}

function DeployRow({ deploy, call, onChanged }) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!expanded) return
    call(`/deploys/${deploy.deploy_id}`)
      .then(setDetail)
      .catch((e) => setError(e.message))
  }, [expanded, deploy.status, call, deploy.deploy_id])

  const redeploy = async (e) => {
    e.stopPropagation()
    if (!confirm(`Redeploy ${deploy.app} @ ${deploy.commit_sha.slice(0, 12)}?`)) return
    try {
      await call(`/admin/deploys/${deploy.deploy_id}/redeploy`, { method: 'POST' })
      onChanged()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <li className="py-2 text-sm">
      <div
        className="flex items-center justify-between cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <span>
          <span className="text-slate-400 mr-1">{expanded ? '▾' : '▸'}</span>
          <span className="font-medium">{deploy.app}</span>{' '}
          <code className="text-xs text-slate-500">{deploy.commit_sha.slice(0, 12)}</code>
        </span>
        <span className="flex items-center gap-3">
          <button className="text-xs underline text-slate-500" onClick={redeploy}>
            redeploy
          </button>
          <span className="text-xs text-slate-400">{deploy.created_at}</span>
          <StatusBadge status={deploy.status} />
        </span>
      </div>

      {expanded && (
        <div className="mt-2 ml-5 border-l pl-3 space-y-1">
          {!detail && !error && <p className="text-xs text-slate-400">loading…</p>}
          {detail?.steps.map((s, i) => (
            <div key={i} className="text-xs">
              <span
                className={
                  s.status === 'failed'
                    ? 'text-red-600'
                    : s.status === 'succeeded'
                      ? 'text-green-600'
                      : 'text-slate-500'
                }
              >
                {STEP_ICON[s.status] ?? '·'} {s.step}
              </span>
              {s.output && <span className="text-slate-500 ml-1">— {s.output}</span>}
            </div>
          ))}
          <p className="text-xs text-slate-400">triggered by {detail?.steps ? deploy.triggered_by : ''}</p>
        </div>
      )}
      {error && <p className="text-xs text-red-600 ml-5">{error}</p>}
    </li>
  )
}

export default function App() {
  const [token, setToken] = useState(() => {
    try {
      return sessionStorage.getItem('deployd-admin-token') ?? ''
    } catch {
      return ''
    }
  })
  const [health, setHealth] = useState(null)
  const [apps, setApps] = useState(null)
  const [deploys, setDeploys] = useState([])
  const [error, setError] = useState(null)
  const call = useCallback((path, opts = {}) => api(token, path, opts), [token])

  const refresh = useCallback(async () => {
    if (!token) return
    try {
      const [nextApps, nextDeploys] = await Promise.all([
        call('/admin/apps'),
        call('/admin/deploys?limit=20'),
      ])
      setApps(nextApps)
      setDeploys(nextDeploys)
      setError(null)
    } catch (e) {
      setApps(null)
      setError(e.message)
    }
  }, [call, token])

  useEffect(() => {
    fetch('/api/healthz')
      .then((r) => r.json())
      .then((d) => setHealth(d.status))
      .catch(() => setHealth('unreachable'))
  }, [])

  useEffect(() => {
    const timer = setTimeout(refresh, 0)
    return () => clearTimeout(timer)
  }, [refresh])

  const hasActive = deploys.some((d) => d.status === 'queued' || d.status === 'running')
  useEffect(() => {
    if (!hasActive || !token) return
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  }, [hasActive, token, refresh])

  const saveToken = (value) => {
    setToken(value)
    try {
      sessionStorage.setItem('deployd-admin-token', value)
    } catch {
      /* storage unavailable — token lives for this page only */
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b bg-white px-6 py-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">deployd</h1>
        <div className="flex items-center gap-4 text-sm">
          <input
            type="password"
            placeholder="admin token"
            className="border rounded px-2 py-1 text-sm w-48"
            value={token}
            onChange={(e) => saveToken(e.target.value)}
          />
          <span>
            API:{' '}
            <span className={health === 'ok' ? 'text-green-600' : 'text-red-600'}>{health ?? '…'}</span>
          </span>
        </div>
      </header>

      <main className="max-w-3xl mx-auto p-6 space-y-6">
        {error && (
          <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">{error}</div>
        )}
        {!token && <p className="text-sm text-slate-500">Enter the admin token to load apps and deploys.</p>}

        {apps && (
          <section className="space-y-4">
            <h2 className="font-medium">Apps</h2>
            {Object.entries(apps).map(([name, spec]) => (
              <AppCard key={name} name={name} spec={spec} call={call} onChanged={refresh} />
            ))}
            <NewAppCard call={call} onChanged={refresh} />
          </section>
        )}

        {apps && (
          <section className="bg-white rounded-lg border p-5">
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-medium">
                Recent deploys
                {hasActive && <span className="ml-2 text-xs text-blue-600 animate-pulse">live</span>}
              </h2>
              <button className="text-sm underline" onClick={refresh}>
                refresh
              </button>
            </div>
            {deploys.length === 0 && <p className="text-sm text-slate-500">No deploys yet.</p>}
            <ul className="divide-y">
              {deploys.map((d) => (
                <DeployRow key={d.deploy_id} deploy={d} call={call} onChanged={refresh} />
              ))}
            </ul>
          </section>
        )}
      </main>
    </div>
  )
}
