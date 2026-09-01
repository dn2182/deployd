import { useCallback, useEffect, useLayoutEffect, useState } from 'react'
import {
  Activity,
  AppWindow,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CloudCog,
  Code2,
  Copy,
  KeyRound,
  LoaderCircle,
  Moon,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Server,
  ShieldCheck,
  Sun,
  Trash2,
  X,
} from 'lucide-react'
import { Button, ConfirmDialog, TooltipButton } from './components/ui.jsx'

const STATUS_LABELS = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  rolled_back: 'Rolled back',
}

const STEP_ICON = {
  succeeded: <Check size={13} />,
  failed: <X size={13} />,
  running: <LoaderCircle className="spin" size={13} />,
  skipped: <span>–</span>,
}

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

function initialTheme() {
  try {
    const saved = localStorage.getItem('deployd-theme')
    if (saved === 'light' || saved === 'dark') return saved
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

function StatusBadge({ status }) {
  return (
    <span className={`status-badge status-${status}`}>
      <span className="status-dot" />
      {STATUS_LABELS[status] ?? status}
    </span>
  )
}

function ErrorMessage({ children, compact = false }) {
  return (
    <div className={`error-message ${compact ? 'error-message-compact' : ''}`} role="alert">
      <CircleAlert size={16} />
      <span>{children}</span>
    </div>
  )
}

function AppCard({ name, spec, call, onChanged }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [freshSecret, setFreshSecret] = useState(null)
  const [copied, setCopied] = useState(false)
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
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  const rotate = async () => {
    try {
      const out = await call(`/admin/apps/${name}/rotate-secret`, { method: 'POST' })
      setFreshSecret(out)
      onChanged()
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  const remove = async () => {
    try {
      await call(`/admin/apps/${name}`, { method: 'DELETE' })
      onChanged()
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  const copySecret = async () => {
    if (!freshSecret?.secret) return
    try {
      await navigator.clipboard.writeText(freshSecret.secret)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      setCopied(false)
    }
  }

  return (
    <article className="glass-panel app-card">
      <div className="app-card-header">
        <div className="app-identity">
          <div className="app-icon" aria-hidden="true">
            <Server size={19} />
          </div>
          <div>
            <h3>{name}</h3>
            <span>{spec.health?.url ?? 'No health endpoint'}</span>
          </div>
        </div>
        <div className="app-actions">
          <TooltipButton label={`Edit ${name}`} onClick={startEdit}>
            <Pencil size={16} />
          </TooltipButton>
          <ConfirmDialog
            trigger={
              <TooltipButton label={`Rotate secret for ${name}`}>
                <KeyRound size={16} />
              </TooltipButton>
            }
            title={`Rotate ${name} secret?`}
            description="The current HMAC secret will stop working. Update your CI with the new value immediately."
            confirmLabel="Rotate secret"
            onConfirm={rotate}
          />
          <ConfirmDialog
            trigger={
              <TooltipButton label={`Remove ${name}`} className="icon-button-danger">
                <Trash2 size={16} />
              </TooltipButton>
            }
            title={`Remove ${name}?`}
            description="The app will be removed from the registry. Existing releases on disk are not touched."
            confirmLabel="Remove app"
            confirmationValue={name}
            destructive
            onConfirm={remove}
          />
        </div>
      </div>

      <div className="app-details">
        <div className="detail-item">
          <span className="detail-label">Signing secret</span>
          {spec.secret?.configured ? (
            <span className="secret-value">
              <ShieldCheck size={14} />
              <code>{spec.secret.fingerprint}</code>
              {spec.secret.env_override && <span className="mini-badge">ENV</span>}
            </span>
          ) : (
            <span className="warning-value">Not configured</span>
          )}
        </div>
        <div className="detail-item detail-item-wide">
          <span className="detail-label">Release directory</span>
          <code className="path-value">{spec.releases_dir}</code>
        </div>
      </div>

      {freshSecret && (
        <div className="secret-reveal">
          <div className="secret-reveal-head">
            <div>
              <strong>{freshSecret.secret ? 'New secret generated' : 'Secret not returned'}</strong>
              <span>{freshSecret.secret ? 'Shown once. Copy it to CI now.' : freshSecret.warning}</span>
            </div>
            <TooltipButton label="Dismiss" onClick={() => setFreshSecret(null)}>
              <X size={15} />
            </TooltipButton>
          </div>
          {freshSecret.secret && (
            <button className="secret-copy" type="button" onClick={copySecret}>
              <code>{freshSecret.secret}</code>
              {copied ? <Check size={15} /> : <Copy size={15} />}
              <span>{copied ? 'Copied' : 'Copy'}</span>
            </button>
          )}
        </div>
      )}

      {editing && (
        <div className="editor-panel">
          <div className="editor-title">
            <Code2 size={15} />
            Configuration
          </div>
          <textarea
            className="code-editor"
            aria-label={`${name} configuration`}
            value={draft}
            spellCheck="false"
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="form-actions">
            <Button variant="primary" onClick={save}>Save changes</Button>
            <Button onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </div>
      )}
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
    </article>
  )
}

function NewAppCard({ call, onChanged }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [draft, setDraft] = useState(JSON.stringify(APP_TEMPLATE, null, 2))
  const [error, setError] = useState(null)

  const close = () => {
    setOpen(false)
    setError(null)
  }

  const save = async () => {
    if (!/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(name)) {
      setError('Name must use lowercase letters, digits, and interior dashes.')
      return
    }
    try {
      await call(`/admin/apps/${name}`, { method: 'PUT', body: draft })
      setOpen(false)
      setName('')
      setDraft(JSON.stringify(APP_TEMPLATE, null, 2))
      setError(null)
      onChanged()
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  if (!open) {
    return (
      <button className="add-app-card" type="button" onClick={() => setOpen(true)}>
        <span><Plus size={19} /></span>
        <strong>Add application</strong>
        <small>Register another deployment target</small>
      </button>
    )
  }

  return (
    <article className="glass-panel app-card new-app-card">
      <div className="app-card-header">
        <div className="app-identity">
          <div className="app-icon app-icon-new" aria-hidden="true"><Plus size={19} /></div>
          <div>
            <h3>New application</h3>
            <span>Define its deployment contract</span>
          </div>
        </div>
      </div>
      <label className="field-label">
        Application name
        <input
          className="text-input"
          placeholder="app name (e.g. my-api)"
          value={name}
          autoComplete="off"
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className="field-label">
        Configuration
        <textarea
          className="code-editor"
          aria-label="New application configuration"
          value={draft}
          spellCheck="false"
          onChange={(event) => setDraft(event.target.value)}
        />
      </label>
      <div className="form-actions">
        <Button variant="primary" onClick={save}>Create application</Button>
        <Button onClick={close}>Cancel</Button>
      </div>
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
    </article>
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
      .catch((requestError) => setError(requestError.message))
  }, [expanded, deploy.status, call, deploy.deploy_id])

  const redeploy = async () => {
    try {
      await call(`/admin/deploys/${deploy.deploy_id}/redeploy`, { method: 'POST' })
      onChanged()
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  return (
    <li className={`deploy-row ${expanded ? 'deploy-row-expanded' : ''}`}>
      <div className="deploy-summary">
        <button
          className="deploy-expand"
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          <span className="chevron" aria-hidden="true">
            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          </span>
          <span className="deploy-main">
            <strong>{deploy.app}</strong>
            <code>{deploy.commit_sha.slice(0, 12)}</code>
          </span>
        </button>
        <div className="deploy-meta">
          <time>{deploy.created_at}</time>
          <StatusBadge status={deploy.status} />
          <ConfirmDialog
            trigger={
              <TooltipButton label={`Redeploy ${deploy.app}`}>
                <RotateCcw size={15} />
              </TooltipButton>
            }
            title={`Redeploy ${deploy.app}?`}
            description={`Commit ${deploy.commit_sha.slice(0, 12)} will be queued using the original artifact.`}
            confirmLabel="Redeploy"
            onConfirm={redeploy}
          />
        </div>
      </div>

      {expanded && (
        <div className="deploy-detail">
          {!detail && !error && (
            <div className="detail-loading"><LoaderCircle className="spin" size={15} /> Loading steps</div>
          )}
          {detail?.steps.map((step, index) => (
            <div className={`deploy-step step-${step.status}`} key={`${step.step}-${index}`}>
              <span className="step-icon">{STEP_ICON[step.status] ?? <span>·</span>}</span>
              <div>
                <strong>{step.step}</strong>
                {step.output && <p>{step.output}</p>}
              </div>
            </div>
          ))}
          {detail && <p className="triggered-by">Triggered by {deploy.triggered_by}</p>}
        </div>
      )}
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
    </li>
  )
}

function LoadingPanel() {
  return (
    <div className="glass-panel loading-panel" aria-label="Loading applications">
      <LoaderCircle className="spin" size={20} />
      <span>Loading control plane…</span>
    </div>
  )
}

export default function App() {
  const [theme, setTheme] = useState(initialTheme)
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

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem('deployd-theme', theme)
    } catch {
      return
    }
  }, [theme])

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
    } catch (requestError) {
      setApps(null)
      setError(requestError.message)
    }
  }, [call, token])

  useEffect(() => {
    fetch('/api/healthz')
      .then((response) => response.json())
      .then((data) => setHealth(data.status))
      .catch(() => setHealth('unreachable'))
  }, [])

  useEffect(() => {
    const timer = setTimeout(refresh, 0)
    return () => clearTimeout(timer)
  }, [refresh])

  const hasActive = deploys.some((deploy) => deploy.status === 'queued' || deploy.status === 'running')
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
      return
    }
  }

  const appCount = apps ? Object.keys(apps).length : 0
  const successfulCount = deploys.filter((deploy) => deploy.status === 'succeeded').length

  return (
    <div className="app-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <div className="brand-mark" aria-hidden="true"><CloudCog size={21} /></div>
            <div>
              <strong>deployd</strong>
              <span>Control plane</span>
            </div>
          </div>
          <div className="topbar-actions">
            <label className="token-field">
              <ShieldCheck size={15} />
              <span className="sr-only">Admin token</span>
              <input
                type="password"
                placeholder="Admin token"
                value={token}
                autoComplete="current-password"
                onChange={(event) => saveToken(event.target.value)}
              />
            </label>
            <span className={`health-pill ${health === 'ok' ? 'health-ok' : 'health-error'}`}>
              <span /> API {health ?? 'checking'}
            </span>
            <TooltipButton
              label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            >
              {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
            </TooltipButton>
          </div>
        </div>
      </header>

      <main className="main-content">
        <section className="hero-section">
          <div>
            <span className="eyebrow"><Activity size={14} /> Deployment control</span>
            <h1>Ship clearly.<br /><span>Recover confidently.</span></h1>
            <p>Manage application contracts, signing secrets, and release activity from one calm control plane.</p>
          </div>
          <div className="metrics glass-panel">
            <div><strong>{appCount}</strong><span>Applications</span></div>
            <div><strong>{deploys.length}</strong><span>Recent deploys</span></div>
            <div><strong>{successfulCount}</strong><span>Succeeded</span></div>
          </div>
        </section>

        {error && <ErrorMessage>{error}</ErrorMessage>}

        {!token && (
          <section className="glass-panel locked-panel">
            <div className="locked-icon"><KeyRound size={23} /></div>
            <div>
              <h2>Connect to the control plane</h2>
              <p>Enter the admin token above to load applications and deployment history.</p>
            </div>
          </section>
        )}

        {token && !apps && !error && <LoadingPanel />}

        {apps && (
          <section className="content-section">
            <div className="section-heading">
              <div>
                <span className="section-kicker"><AppWindow size={14} /> Registry</span>
                <h2>Applications</h2>
              </div>
              <span>{appCount} configured</span>
            </div>
            <div className="app-grid">
              {Object.entries(apps).map(([name, spec]) => (
                <AppCard key={name} name={name} spec={spec} call={call} onChanged={refresh} />
              ))}
              <NewAppCard call={call} onChanged={refresh} />
            </div>
          </section>
        )}

        {apps && (
          <section className="content-section">
            <div className="section-heading">
              <div>
                <span className="section-kicker"><Activity size={14} /> Activity</span>
                <h2>Recent deploys</h2>
              </div>
              <div className="section-actions">
                {hasActive && <span className="live-indicator"><span /> Live</span>}
                <Button size="small" onClick={refresh}><RefreshCw size={14} /> Refresh</Button>
              </div>
            </div>
            <div className="glass-panel deploy-panel">
              {deploys.length === 0 ? (
                <div className="empty-state">
                  <Activity size={22} />
                  <p>No deployments yet.</p>
                </div>
              ) : (
                <ul className="deploy-list">
                  {deploys.map((deploy) => (
                    <DeployRow
                      key={deploy.deploy_id}
                      deploy={deploy}
                      call={call}
                      onChanged={refresh}
                    />
                  ))}
                </ul>
              )}
            </div>
          </section>
        )}
      </main>

      <footer>
        <span>deployd</span>
        <span>Private deployment control plane</span>
      </footer>
    </div>
  )
}
