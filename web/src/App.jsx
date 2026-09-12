import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { Activity, AppWindow, CloudCog, LogOut, Moon, RefreshCw, ScrollText, Sun } from 'lucide-react'
import { LanguageProvider, detectLanguage, useT } from './i18n/index.js'
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts.js'
import { useRegistry } from './hooks/useRegistry.js'
import { useSession } from './hooks/useSession.js'
import { useTheme } from './hooks/useTheme.js'
import { ToastProvider } from './components/Toasts.jsx'
import { useToast } from './hooks/useToast.js'
import { Button, ErrorMessage, IconButton, Kicker, Panel } from './components/ui.jsx'
import AppRegistry, { RegistrySkeleton } from './components/AppRegistry.jsx'
import AuditLog from './components/AuditLog.jsx'
import DeployHistory from './components/DeployHistory.jsx'
import SecretModal from './components/SecretModal.jsx'
import TokenGate from './components/TokenGate.jsx'

const TERMINAL_TOAST = { succeeded: 'success', failed: 'error', rolled_back: 'error', cancelled: 'info' }

function HealthPill({ health }) {
  const t = useT()
  const status = health?.status ?? 'checking'
  const tone = status === 'ok' ? 'bg-success-soft text-success' : status === 'checking' ? 'bg-surface-soft text-muted' : 'bg-danger-soft text-danger'
  const details = health && status !== 'unreachable' ? `db: ${health.db ?? '?'}, worker: ${health.worker ?? '?'}` : undefined
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ${tone}`} title={details}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
      {t('topbar.api', { status: t(`health.${status}`) })}
    </span>
  )
}

function SectionHeading({ icon, kicker, title, aside, children }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <Kicker icon={icon}>{kicker}</Kicker>
        <h2 className="m-0 mt-1 text-2xl font-semibold tracking-tight text-text-strong">{title}</h2>
      </div>
      {aside && <span className="text-xs text-muted">{aside}</span>}
      {children}
    </div>
  )
}

function Console({ language, setLanguage }) {
  const t = useT()
  const toast = useToast()
  const { theme, toggleTheme } = useTheme()
  const session = useSession()
  const [secret, setSecret] = useState(null)
  const [query, setQuery] = useState('')
  const [view, setView] = useState('deploys')
  const searchRef = useRef(null)

  const onTerminal = useCallback((deploy) => {
    toast({
      kind: TERMINAL_TOAST[deploy.status] ?? 'info',
      message: t('deploy.finished_toast', { name: deploy.app, status: t(`status.${deploy.status}`) }),
    })
  }, [toast, t])

  const registry = useRegistry({ token: session.token, api: session.api, onTerminal })
  const { apps, deploys, refresh, refreshing } = registry
  const doRefresh = useCallback(() => { if (session.token && !session.rejected) refresh() }, [refresh, session.token, session.rejected])
  const focusSearch = useCallback(() => searchRef.current?.focus(), [])
  const clearSearch = useCallback(() => setQuery(''), [])
  useKeyboardShortcuts({ onSearch: focusSearch, onRefresh: doRefresh, onClear: clearSearch })

  const locked = !session.token || session.rejected
  const appCount = apps ? Object.keys(apps).length : 0
  const successfulCount = deploys.filter((deploy) => deploy.status === 'succeeded').length

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[radial-gradient(circle_at_50%_-15%,rgba(118,137,255,0.14),transparent_36rem),linear-gradient(155deg,var(--background),var(--background-deep))]">
      <header className="sticky top-0 z-30 border-b border-border-subtle bg-bg/70 backdrop-blur-2xl backdrop-saturate-150">
        <div className="mx-auto flex w-[min(1120px,calc(100%-32px))] flex-wrap items-center justify-between gap-x-6 gap-y-2 py-3">
          <div className="flex items-center gap-3">
            <div className="grid size-10 place-items-center rounded-card bg-linear-to-br from-[#7488ff] via-[#405bef] to-[#3947c4] text-white shadow-soft" aria-hidden="true"><CloudCog size={21} /></div>
            <div className="flex flex-col">
              <strong className="text-[15px] leading-tight tracking-tight text-text-strong">deployd</strong>
              <span className="text-[10px] uppercase tracking-[0.09em] text-muted">{t('brand.control_plane')}</span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <HealthPill health={registry.health} />
            <IconButton label={t('activity.refresh')} disabled={refreshing || locked} onClick={doRefresh}>
              <RefreshCw size={17} className={refreshing ? 'animate-spin' : undefined} />
            </IconButton>
            <IconButton label={t('theme.switch', { theme: t(`theme.${theme === 'dark' ? 'light' : 'dark'}`) })} onClick={toggleTheme}>
              {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
            </IconButton>
            <IconButton label={t('language.switch')} onClick={() => setLanguage(language === 'es' ? 'en' : 'es')}>
              <span className="text-[11px] font-bold tracking-wider">{language === 'es' ? 'ES' : 'EN'}</span>
            </IconButton>
            {session.token && (
              <Button size="small" onClick={session.logout}><LogOut size={14} /> {t('token.logout')}</Button>
            )}
          </div>
        </div>
      </header>

      <main className="relative z-10 mx-auto flex w-[min(1120px,calc(100%-32px))] flex-col gap-8 py-7 sm:py-9">
        <section className="grid grid-cols-1 items-center gap-5 md:grid-cols-[minmax(0,1fr)_auto]">
          <div>
            <Kicker icon={<Activity size={14} />}>{t('hero.kicker')}</Kicker>
            <h1 className="mt-2 mb-3 max-w-2xl text-[clamp(28px,3vw,38px)] leading-[1.1] font-semibold tracking-[-0.04em] text-text-strong">{t('hero.title')}</h1>
            <p className="m-0 max-w-xl text-[13px] leading-relaxed text-muted">{t('hero.description')}</p>
          </div>
          <Panel className="grid min-w-0 grid-cols-3 divide-x divide-border-subtle p-1.5 md:min-w-72">
            {[[appCount, 'metrics.applications'], [deploys.length, 'metrics.recent'], [successfulCount, 'metrics.succeeded']].map(([value, key]) => (
              <div key={key} className="flex flex-col gap-0.5 px-3 py-2.5 sm:px-4">
                <strong className="text-[22px] font-semibold tracking-tight text-text-strong tabular-nums">{value}</strong>
                <span className="text-[11px] text-muted">{t(key)}</span>
              </div>
            ))}
          </Panel>
        </section>

        {registry.error && !session.rejected && <ErrorMessage>{registry.error}</ErrorMessage>}

        {locked && <TokenGate rejected={session.rejected} onSubmit={session.login} />}

        {!locked && (
          <section>
            <SectionHeading icon={<AppWindow size={14} />} kicker={t('registry.kicker')} title={t('registry.title')}
              aside={apps ? t('registry.configured', { count: appCount }) : null} />
            {apps ? (
              <AppRegistry apps={apps} call={session.call} api={session.api} refresh={refresh} revision={registry.revision}
                isBusy={registry.isBusy} onSecret={setSecret} query={query} onQueryChange={setQuery} searchRef={searchRef} />
            ) : !registry.error && <RegistrySkeleton />}
          </section>
        )}

        {!locked && (
          <section>
            <SectionHeading icon={view === 'audit' ? <ScrollText size={14} /> : <Activity size={14} />}
              kicker={t('activity.kicker')} title={t(view === 'audit' ? 'audit.title' : 'activity.title')}>
              <div className="flex flex-wrap items-center gap-2">
                <div role="tablist" aria-label={t('activity.views')} className="flex gap-1 rounded-full bg-surface-soft p-1">
                  {['deploys', 'audit'].map((value) => (
                    <button key={value} type="button" role="tab" aria-selected={view === value} onClick={() => setView(value)}
                      className={`rounded-full px-3 py-1 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${view === value ? 'bg-accent text-white' : 'text-muted hover:text-text-strong'}`}>
                      {t(value === 'audit' ? 'audit.title' : 'activity.title')}
                    </button>
                  ))}
                </div>
                {view === 'deploys' && (
                  <>
                    <span className="text-[11px] text-muted">{t(registry.hasActive ? 'activity.live' : 'activity.manual')}</span>
                    <Button size="small" disabled={refreshing} onClick={doRefresh}>
                      <RefreshCw size={14} className={refreshing ? 'animate-spin' : undefined} /> {t('activity.refresh')}
                    </Button>
                  </>
                )}
              </div>
            </SectionHeading>
            {view === 'audit' ? <AuditLog api={session.api} /> : (
              <DeployHistory deploys={deploys} apps={apps} filters={registry.filters} onFilters={registry.setFilters}
                hasMore={registry.hasMore} loadMore={registry.loadMore} loadingMore={registry.loadingMore}
                loading={!apps && !registry.error} api={session.api} onChanged={refresh} revision={registry.revision} />
            )}
          </section>
        )}
      </main>

      <footer className="relative z-10 mx-auto flex w-[min(1120px,calc(100%-32px))] flex-wrap justify-between gap-2 pb-7 text-[9px] tracking-wider text-muted-soft uppercase">
        <span>deployd</span>
        <span>{t('footer.description')}</span>
      </footer>

      {secret && <SecretModal secret={secret} onDismiss={() => setSecret(null)} />}
    </div>
  )
}

export default function App() {
  const [language, setLanguage] = useState(detectLanguage)
  useLayoutEffect(() => {
    document.documentElement.lang = language
  }, [language])
  return (
    <LanguageProvider language={language} setLanguage={setLanguage}>
      <ToastProvider>
        <Console language={language} setLanguage={setLanguage} />
      </ToastProvider>
    </LanguageProvider>
  )
}
