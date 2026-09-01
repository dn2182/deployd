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
import { detectLanguage, translate } from './i18n.js'

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
  const body = await resp.text()
  let payload
  try {
    payload = body ? JSON.parse(body) : null
  } catch {
    const message = body.trim().slice(0, 200)
    throw new Error(message || `HTTP ${resp.status}: invalid server response`)
  }
  if (!resp.ok) throw new Error(payload?.detail ?? `HTTP ${resp.status}`)
  return payload
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

function StatusBadge({ status, t }) {
  return (
    <span className={`status-badge status-${status}`}>
      <span className="status-dot" />
      {t(`status.${status}`)}
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

function AppCard({ name, spec, call, onChanged, t }) {
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
            <span>{spec.health?.url ?? t('app.no_health')}</span>
          </div>
        </div>
        <div className="app-actions">
          <TooltipButton label={t('app.edit', { name })} onClick={startEdit}>
            <Pencil size={16} />
          </TooltipButton>
          <ConfirmDialog
            trigger={
              <TooltipButton label={t('app.rotate', { name })}>
                <KeyRound size={16} />
              </TooltipButton>
            }
            title={t('app.rotate_title', { name })}
            description={t('app.rotate_description')}
            confirmLabel={t('app.rotate_confirm')}
            cancelLabel={t('common.cancel')}
            onConfirm={rotate}
          />
          <ConfirmDialog
            trigger={
              <TooltipButton label={t('app.remove', { name })} className="icon-button-danger">
                <Trash2 size={16} />
              </TooltipButton>
            }
            title={t('app.remove_title', { name })}
            description={t('app.remove_description')}
            confirmLabel={t('app.remove_confirm')}
            confirmationValue={name}
            confirmationLabel={t('common.confirm_type', { value: name })}
            cancelLabel={t('common.cancel')}
            destructive
            onConfirm={remove}
          />
        </div>
      </div>

      <div className="app-details">
        <div className="detail-item">
          <span className="detail-label">{t('app.signing_secret')}</span>
          {spec.secret?.configured ? (
            <span className="secret-value">
              <ShieldCheck size={14} />
              <code>{spec.secret.fingerprint}</code>
              {spec.secret.env_override && <span className="mini-badge">ENV</span>}
            </span>
          ) : (
            <span className="warning-value">{t('app.not_configured')}</span>
          )}
        </div>
        <div className="detail-item detail-item-wide">
          <span className="detail-label">{t('app.release_directory')}</span>
          <code className="path-value">{spec.releases_dir}</code>
        </div>
      </div>

      {freshSecret && (
        <div className="secret-reveal">
          <div className="secret-reveal-head">
            <div>
              <strong>{freshSecret.secret ? t('app.secret_generated') : t('app.secret_missing')}</strong>
              <span>{freshSecret.secret ? t('app.secret_once') : freshSecret.warning}</span>
            </div>
            <TooltipButton label={t('app.dismiss')} onClick={() => setFreshSecret(null)}>
              <X size={15} />
            </TooltipButton>
          </div>
          {freshSecret.secret && (
            <button className="secret-copy" type="button" onClick={copySecret}>
              <code>{freshSecret.secret}</code>
              {copied ? <Check size={15} /> : <Copy size={15} />}
              <span>{copied ? t('app.copied') : t('app.copy')}</span>
            </button>
          )}
        </div>
      )}

      {editing && (
        <div className="editor-panel">
          <div className="editor-title">
            <Code2 size={15} />
            {t('app.configuration')}
          </div>
          <textarea
            className="code-editor"
            aria-label={t('app.configuration_label', { name })}
            value={draft}
            spellCheck="false"
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="form-actions">
            <Button variant="primary" onClick={save}>{t('app.save_changes')}</Button>
            <Button onClick={() => setEditing(false)}>{t('common.cancel')}</Button>
          </div>
        </div>
      )}
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
    </article>
  )
}

function NewAppCard({ call, onChanged, t }) {
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
      setError(t('new.validation'))
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
        <strong>{t('new.add')}</strong>
        <small>{t('new.add_description')}</small>
      </button>
    )
  }

  return (
    <article className="glass-panel app-card new-app-card">
      <div className="app-card-header">
        <div className="app-identity">
          <div className="app-icon app-icon-new" aria-hidden="true"><Plus size={19} /></div>
          <div>
            <h3>{t('new.title')}</h3>
            <span>{t('new.description')}</span>
          </div>
        </div>
      </div>
      <label className="field-label">
        {t('new.name')}
        <input
          className="text-input"
          placeholder={t('new.placeholder')}
          value={name}
          autoComplete="off"
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className="field-label">
        {t('app.configuration')}
        <textarea
          className="code-editor"
          aria-label={t('new.configuration_label')}
          value={draft}
          spellCheck="false"
          onChange={(event) => setDraft(event.target.value)}
        />
      </label>
      <div className="form-actions">
        <Button variant="primary" onClick={save}>{t('new.create')}</Button>
        <Button onClick={close}>{t('common.cancel')}</Button>
      </div>
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
    </article>
  )
}

function DeployRow({ deploy, call, onChanged, t }) {
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
          <StatusBadge status={deploy.status} t={t} />
          <ConfirmDialog
            trigger={
              <TooltipButton label={t('deploy.redeploy', { name: deploy.app })}>
                <RotateCcw size={15} />
              </TooltipButton>
            }
            title={t('deploy.redeploy_title', { name: deploy.app })}
            description={t('deploy.redeploy_description', { commit: deploy.commit_sha.slice(0, 12) })}
            confirmLabel={t('deploy.redeploy_confirm')}
            cancelLabel={t('common.cancel')}
            onConfirm={redeploy}
          />
        </div>
      </div>

      {expanded && (
        <div className="deploy-detail">
          {!detail && !error && (
            <div className="detail-loading"><LoaderCircle className="spin" size={15} /> {t('deploy.loading_steps')}</div>
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
          {detail && <p className="triggered-by">{t('deploy.triggered_by', { name: deploy.triggered_by })}</p>}
        </div>
      )}
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
    </li>
  )
}

function LoadingPanel({ t }) {
  return (
    <div className="glass-panel loading-panel" aria-label={t('loading.applications')}>
      <LoaderCircle className="spin" size={20} />
      <span>{t('loading.control_plane')}</span>
    </div>
  )
}

export default function App() {
  const [theme, setTheme] = useState(initialTheme)
  const [language, setLanguage] = useState(detectLanguage)
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
  const t = useCallback((key, values) => translate(language, key, values), [language])
  const call = useCallback((path, opts = {}) => api(token, path, opts), [token])

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem('deployd-theme', theme)
    } catch {
      return
    }
  }, [theme])

  useLayoutEffect(() => {
    document.documentElement.lang = language
  }, [language])

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
              <span>{t('brand.control_plane')}</span>
            </div>
          </div>
          <div className="topbar-actions">
            <label className="token-field">
              <ShieldCheck size={15} />
              <span className="sr-only">{t('topbar.admin_token')}</span>
              <input
                type="password"
                placeholder={t('topbar.admin_token')}
                value={token}
                autoComplete="current-password"
                onChange={(event) => saveToken(event.target.value)}
              />
            </label>
            <span className={`health-pill ${health === 'ok' ? 'health-ok' : 'health-error'}`}>
              <span /> {t('topbar.api', {
                status: health === 'unreachable' ? t('health.unreachable') : health ?? t('health.checking'),
              })}
            </span>
            <TooltipButton
              label={t('theme.switch', { theme: t(`theme.${theme === 'dark' ? 'light' : 'dark'}`) })}
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            >
              {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
            </TooltipButton>
            <TooltipButton
              label={t('language.switch')}
              onClick={() => setLanguage(language === 'es' ? 'en' : 'es')}
            >
              <span className="language-code">{language === 'es' ? 'ES' : 'EN'}</span>
            </TooltipButton>
          </div>
        </div>
      </header>

      <main className="main-content">
        <section className="hero-section">
          <div>
            <span className="eyebrow"><Activity size={14} /> {t('hero.kicker')}</span>
            <h1>{t('hero.title')}</h1>
            <p>{t('hero.description')}</p>
          </div>
          <div className="metrics glass-panel">
            <div><strong>{appCount}</strong><span>{t('metrics.applications')}</span></div>
            <div><strong>{deploys.length}</strong><span>{t('metrics.recent')}</span></div>
            <div><strong>{successfulCount}</strong><span>{t('metrics.succeeded')}</span></div>
          </div>
        </section>

        {error && <ErrorMessage>{error}</ErrorMessage>}

        {!token && (
          <section className="glass-panel locked-panel">
            <div className="locked-icon"><KeyRound size={23} /></div>
            <div>
              <h2>{t('locked.title')}</h2>
              <p>{t('locked.description')}</p>
            </div>
          </section>
        )}

        {token && !apps && !error && <LoadingPanel t={t} />}

        {apps && (
          <section className="content-section">
            <div className="section-heading">
              <div>
                <span className="section-kicker"><AppWindow size={14} /> {t('registry.kicker')}</span>
                <h2>{t('registry.title')}</h2>
              </div>
              <span>{t('registry.configured', { count: appCount })}</span>
            </div>
            <div className="app-grid">
              {Object.entries(apps).map(([name, spec]) => (
                <AppCard key={name} name={name} spec={spec} call={call} onChanged={refresh} t={t} />
              ))}
              <NewAppCard call={call} onChanged={refresh} t={t} />
            </div>
          </section>
        )}

        {apps && (
          <section className="content-section">
            <div className="section-heading">
              <div>
                <span className="section-kicker"><Activity size={14} /> {t('activity.kicker')}</span>
                <h2>{t('activity.title')}</h2>
              </div>
              <div className="section-actions">
                {hasActive && <span className="live-indicator"><span /> {t('activity.live')}</span>}
                <Button size="small" onClick={refresh}><RefreshCw size={14} /> {t('activity.refresh')}</Button>
              </div>
            </div>
            <div className="glass-panel deploy-panel">
              {deploys.length === 0 ? (
                <div className="empty-state">
                  <Activity size={22} />
                  <p>{t('activity.empty')}</p>
                </div>
              ) : (
                <ul className="deploy-list">
                  {deploys.map((deploy) => (
                    <DeployRow
                      key={deploy.deploy_id}
                      deploy={deploy}
                      call={call}
                      onChanged={refresh}
                      t={t}
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
        <span>{t('footer.description')}</span>
      </footer>
    </div>
  )
}
