import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import {
  Activity,
  AppWindow,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CloudCog,
  Copy,
  KeyRound,
  LoaderCircle,
  Moon,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  ShieldCheck,
  Sun,
  Trash2,
  X,
} from 'lucide-react'
import { Button, ConfirmDialog, TooltipButton } from './components/ui.jsx'
import { detectLanguage, translate } from './i18n.js'
import ReleasePanel from './components/ReleasePanel.jsx'
import AppEditor from './components/AppEditor.jsx'
import SetupSummary from './components/SetupSummary.jsx'
import AppPaths from './components/AppPaths.jsx'
import WebsiteConnection from './components/WebsiteConnection.jsx'
import GitHubSetup from './components/GitHubSetup.jsx'

const STEP_ICON = {
  succeeded: <Check size={13} />,
  failed: <X size={13} />,
  running: <LoaderCircle className="spin" size={13} />,
  skipped: <span>–</span>,
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
  if (!resp.ok) {
    const detail = payload?.detail
    throw new Error(Array.isArray(detail)
      ? detail.map((item) => `${item.loc?.filter((part) => part !== 'body').join('.')}: ${item.msg}`).join('; ')
      : detail ?? `HTTP ${resp.status}`)
  }
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

function AppCard({ name, spec, call, onChanged, revision, busy, t }) {
  const [editing, setEditing] = useState(false)
  const [tab, setTab] = useState(spec.site_path ? 'website' : 'releases')
  const tabs = [...(spec.site_path ? ['website'] : []), 'releases', 'github']
  const activeTab = tabs.includes(tab) ? tab : 'releases'
  const tabId = useId()
  const tabLabels = { website: 'website.title', releases: 'releases.manage', github: 'github.title' }
  const [removeWebsite, setRemoveWebsite] = useState('restore')
  const [removing, setRemoving] = useState(false)
  const [freshSecret, setFreshSecret] = useState(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const startEdit = () => {
    setEditing(true)
    setError(null)
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
    setRemoving(true)
    try {
      const result = await call(`/admin/apps/${name}`, {
        method: 'DELETE',
        ...(spec.site_path && { body: JSON.stringify({ confirm: name, website: removeWebsite }) }),
      })
      if (result.status === 'queued') setNotice(t('app.removal_queued'))
      onChanged()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setRemoving(false)
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
          <TooltipButton label={t('app.edit', { name })} onClick={startEdit} disabled={busy || removing}>
            <Pencil size={16} />
          </TooltipButton>
          <ConfirmDialog
            trigger={
              <TooltipButton label={t('app.rotate', { name })} disabled={busy || removing}>
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
              <TooltipButton label={t('app.remove', { name })} className="icon-button-danger" disabled={busy || removing}>
                <Trash2 size={16} />
              </TooltipButton>
            }
            title={t('app.remove_title', { name })}
            description={t(spec.site_path ? 'website.remove_description' : 'app.remove_description')}
            confirmLabel={t('app.remove_confirm')}
            confirmationValue={name}
            confirmationLabel={t('common.confirm_type', { value: name })}
            cancelLabel={t('common.cancel')}
            destructive
            onConfirm={remove}
          >
            {spec.site_path && <label className="field-label">
              {t('website.remove_choice')}
              <select className="text-input" value={removeWebsite} onChange={(event) => setRemoveWebsite(event.target.value)}>
                <option value="restore">{t('website.remove_restore')}</option>
                <option value="keep">{t('website.remove_keep')}</option>
              </select>
              <span className="release-help">{t('website.remove_help')}</span>
            </label>}
          </ConfirmDialog>
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
      </div>
      {notice && <p role="status">{notice}</p>}
      {busy && <p className="release-help">{t('activity.pending')}</p>}
      <details className="app-folders">
        <summary>{t('app.folders')}</summary>
        <AppPaths spec={spec} t={t} />
      </details>
      {!editing && <><div className="app-tabs" role="tablist" aria-label={t('app.sections', { name })}>
        {tabs.map((value, index) => <button key={value} type="button" role="tab"
          id={`${tabId}-${value}`} aria-controls={`${tabId}-panel`}
          aria-selected={activeTab === value} tabIndex={activeTab === value ? 0 : -1}
          onClick={() => setTab(value)} onKeyDown={(event) => {
            const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length
              : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
                : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null
            if (next === null) return
            event.preventDefault()
            setTab(tabs[next])
            event.currentTarget.parentElement.children[next].focus()
          }}>{t(tabLabels[value])}</button>)}
      </div>
      <div role="tabpanel" id={`${tabId}-panel`} aria-labelledby={`${tabId}-${activeTab}`} tabIndex={0}>
        {activeTab === 'website' && <WebsiteConnection name={name} call={call} onChanged={onChanged} revision={revision} t={t} />}
        {activeTab === 'releases' && <ReleasePanel name={name} spec={spec} call={call} onChanged={onChanged} revision={revision} t={t} />}
        {activeTab === 'github' && <GitHubSetup name={name} spec={spec} call={call} onChanged={onChanged} t={t} />}
      </div></>}

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
          <AppEditor name={name} initialSpec={spec} call={call} t={t}
            onCancel={() => setEditing(false)} onSaved={(result) => {
              setEditing(false)
              if (result.secret) setFreshSecret(result)
              onChanged()
            }} />
        </div>
      )}
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
    </article>
  )
}

function NewAppCard({ call, onChanged, t }) {
  const [open, setOpen] = useState(false)
  const [setup, setSetup] = useState(null)
  if (setup) return <SetupSummary {...setup} call={call} onChanged={onChanged} t={t} onDismiss={() => setSetup(null)} />
  if (!open) return (
    <button className="add-app-card" type="button" onClick={() => setOpen(true)}>
      <span><Plus size={19} /></span>
      <strong>{t('new.add')}</strong>
      <small>{t('new.add_description')}</small>
    </button>
  )
  return <article className="glass-panel app-card new-app-card">
    <h3>{t('new.title')}</h3>
    <AppEditor call={call} t={t} onCancel={() => setOpen(false)}
      onSaved={(result, spec) => {
        setSetup({ result, spec })
        setOpen(false)
        onChanged()
      }} />
  </article>
}

function AppRegistry({ apps, deploys, call, refresh, revision, t }) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(null)
  const [page, setPage] = useState(0)
  const entries = Object.entries(apps).sort(([a], [b]) => a.localeCompare(b))
    .filter(([name, spec]) => [name, spec.github_repository, spec.site_path]
      .some((value) => value?.toLowerCase().includes(query.trim().toLowerCase())))
  const pageCount = Math.max(1, Math.ceil(entries.length / 10))
  const currentPage = Math.min(page, pageCount - 1)
  const visible = entries.slice(currentPage * 10, currentPage * 10 + 10)
  const active = entries.find(([name]) => name === selected) ?? visible[0]
  const isBusy = (name) => deploys.some((deploy) => deploy.app === name && ['queued', 'running'].includes(deploy.status))

  return <>
    <NewAppCard call={call} onChanged={refresh} t={t} />
    {Object.keys(apps).length > 0 && <div className="project-workspace">
      <aside className="glass-panel project-browser" aria-label={t('registry.title')}>
        <label className="project-search">
          <Search size={16} aria-hidden="true" />
          <input type="search" aria-label={t('registry.search')} placeholder={t('registry.search')}
            value={query} onChange={(event) => { setQuery(event.target.value); setPage(0) }} />
        </label>
        <p className="project-count">{t('registry.matches', { count: entries.length })}</p>
        <nav aria-label={t('registry.select')} className="project-list">
          {visible.map(([name, spec]) => <button type="button" key={name}
            aria-current={active?.[0] === name ? 'true' : undefined} onClick={() => setSelected(name)}>
            <span><Server size={15} aria-hidden="true" /><strong>{name}</strong>
              {isBusy(name) && <LoaderCircle size={14} className="spin" aria-label={t('status.running')} />}</span>
            <small>{spec.github_repository || spec.site_path || t('registry.no_repository')}</small>
          </button>)}
        </nav>
        {pageCount > 1 && <div className="project-pagination">
          <Button size="small" disabled={currentPage === 0} onClick={() => { setPage(currentPage - 1); setSelected(null) }} aria-label={t('registry.previous')}>←</Button>
          <span>{currentPage + 1} / {pageCount}</span>
          <Button size="small" disabled={currentPage + 1 === pageCount} onClick={() => { setPage(currentPage + 1); setSelected(null) }} aria-label={t('registry.next')}>→</Button>
        </div>}
      </aside>
      {active ? <AppCard key={active[0]} name={active[0]} spec={active[1]} call={call}
        onChanged={refresh} revision={revision} busy={isBusy(active[0])} t={t} />
        : <div className="glass-panel empty-state"><Search size={22} /><p>{t('registry.no_matches')}</p></div>}
    </div>}
  </>
}

function DeployRow({ deploy, call, onChanged, revision, t }) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)
  const localOperation = deploy.artifact_url === 'local-website://connect' ? 'connect'
    : deploy.artifact_url === 'local-website://remove' ? 'remove'
      : deploy.artifact_url?.startsWith('local-release://') ? 'activate' : null

  useEffect(() => {
    if (!expanded) return
    call(`/deploys/${deploy.deploy_id}`)
      .then(setDetail)
      .catch((requestError) => setError(requestError.message))
  }, [expanded, deploy.status, call, deploy.deploy_id, revision])

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
            <code>{localOperation ? t(`deploy.operation_${localOperation}`) : deploy.commit_sha.slice(0, 12)}</code>
          </span>
        </button>
        <div className="deploy-meta">
          <time>{deploy.created_at}</time>
          <StatusBadge status={deploy.status} t={t} />
          {!localOperation && <ConfirmDialog
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
          />}
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
  const [refreshing, setRefreshing] = useState(false)
  const [revision, setRevision] = useState(0)
  const requestId = useRef(0)
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
    const id = ++requestId.current
    setRefreshing(true)
    const healthRequest = fetch('/api/healthz')
      .then((response) => { if (!response.ok) throw new Error('health'); return response.json() })
      .then((data) => { if (id === requestId.current) setHealth(data.status) })
      .catch(() => { if (id === requestId.current) setHealth('unreachable') })
    try {
      if (!token) { setApps(null); setDeploys([]); return }
      const [nextApps, nextDeploys] = await Promise.all([
        call('/admin/apps'),
        call('/admin/deploys?limit=20'),
      ])
      if (id !== requestId.current) return
      setApps(nextApps)
      setDeploys(nextDeploys)
      setError(null)
      setRevision((value) => value + 1)
    } catch (requestError) {
      if (id !== requestId.current) return
      setError(requestError.message)
    } finally {
      await healthRequest
      if (id === requestId.current) setRefreshing(false)
    }
  }, [call, token])

  useEffect(() => {
    const timer = setTimeout(refresh, 0)
    return () => clearTimeout(timer)
  }, [refresh])

  const hasActive = deploys.some((deploy) => deploy.status === 'queued' || deploy.status === 'running')
  const saveToken = (value) => {
    requestId.current += 1
    setApps(null)
    setDeploys([])
    setError(null)
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
            <TooltipButton label={t('activity.refresh')} disabled={refreshing} onClick={refresh}>
              <RefreshCw size={17} className={refreshing ? 'spin' : undefined} />
            </TooltipButton>
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
            <AppRegistry apps={apps} deploys={deploys} call={call} refresh={refresh} revision={revision} t={t} />
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
                <span className="release-help">{t(hasActive ? 'activity.pending' : 'activity.manual')}</span>
                <Button size="small" disabled={refreshing} onClick={refresh}><RefreshCw size={14} className={refreshing ? 'spin' : undefined} /> {t('activity.refresh')}</Button>
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
                      revision={revision}
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
