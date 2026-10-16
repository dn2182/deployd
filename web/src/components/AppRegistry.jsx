import { useState } from 'react'
import { ChevronLeft, ChevronRight, Plus, Search, Server } from 'lucide-react'
import { useT } from '../i18n/index.js'
import { Button, EmptyState, Panel, Skeleton, Spinner } from './ui.jsx'
import AppCard from './AppCard.jsx'
import AppEditor from './AppEditor.jsx'
import SetupSummary from './SetupSummary.jsx'

const PAGE = 10

function NewAppCard({ call, onChanged, onSecret }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [setup, setSetup] = useState(null)
  if (setup) return <SetupSummary {...setup} call={call} onChanged={onChanged} onDismiss={() => setSetup(null)} />
  if (!open) return (
    <button type="button" onClick={() => setOpen(true)}
      className="flex w-full items-center gap-3 rounded-panel border border-dashed border-border-subtle bg-surface-soft px-5 py-4 text-left transition hover:border-accent/40 hover:bg-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40">
      <span className="grid size-10 shrink-0 place-items-center rounded-card bg-accent-soft text-accent"><Plus size={19} /></span>
      <span className="flex min-w-0 flex-col">
        <strong className="text-sm text-text-strong">{t('new.add')}</strong>
        <small className="text-xs text-muted">{t('new.add_description')}</small>
      </span>
    </button>
  )
  return (
    <Panel as="article" className="flex flex-col gap-4 p-4 sm:p-6">
      <h3 className="m-0 text-base font-semibold tracking-tight text-text-strong">{t('new.title')}</h3>
      <AppEditor call={call} onCancel={() => setOpen(false)}
        onSaved={(result, spec) => {
          if (result.secret) onSecret({ ...result, app: result.app })
          setSetup({ result, spec })
          setOpen(false)
          onChanged()
        }} />
    </Panel>
  )
}

export function RegistrySkeleton() {
  const t = useT()
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(220px,280px)_minmax(0,1fr)]">
      <Panel className="p-4"><Skeleton lines={5} label={t('loading.applications')} /></Panel>
      <Panel className="p-6"><Skeleton lines={6} /></Panel>
    </div>
  )
}

export default function AppRegistry({ apps, call, api, refresh, revision, isBusy, onSecret, query, onQueryChange, searchRef }) {
  const t = useT()
  const [selected, setSelected] = useState(null)
  const [page, setPage] = useState(0)
  const needle = query.trim().toLowerCase()
  const entries = Object.entries(apps).sort(([a], [b]) => a.localeCompare(b))
    .filter(([name, spec]) => [name, spec.github_repository, spec.site_path]
      .some((value) => value?.toLowerCase().includes(needle)))
  const pageCount = Math.max(1, Math.ceil(entries.length / PAGE))
  const currentPage = Math.min(page, pageCount - 1)
  const visible = entries.slice(currentPage * PAGE, currentPage * PAGE + PAGE)
  const active = entries.find(([name]) => name === selected) ?? visible[0]

  return (
    <div className="flex flex-col gap-4">
      <NewAppCard call={call} onChanged={refresh} onSecret={onSecret} />
      {Object.keys(apps).length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(220px,280px)_minmax(0,1fr)]">
          <Panel as="aside" className="flex flex-col gap-3 self-start p-3.5" aria-label={t('registry.title')}>
            <label className="flex items-center gap-2 rounded-card border border-border-subtle bg-input px-3 text-muted focus-within:border-accent/45 focus-within:ring-2 focus-within:ring-accent/25">
              <Search size={16} aria-hidden="true" />
              <input ref={searchRef} type="search" aria-label={t('registry.search')} placeholder={t('registry.search')}
                value={query} autoComplete="off"
                onChange={(event) => { onQueryChange(event.target.value); setPage(0) }}
                onKeyDown={(event) => { if (event.key === 'Escape') { onQueryChange(''); event.currentTarget.blur() } }}
                className="h-9 w-full min-w-0 border-0 bg-transparent text-xs text-text outline-none placeholder:text-muted-soft" />
            </label>
            <p className="m-0 text-[11px] text-muted">{t('registry.matches', { count: entries.length })} <kbd className="ml-1 rounded border border-border-subtle px-1 font-mono text-[10px] text-muted-soft">/</kbd></p>
            <nav aria-label={t('registry.select')} className="flex flex-col gap-1">
              {visible.map(([name, spec]) => {
                const current = active?.[0] === name
                return (
                  <button type="button" key={name} aria-pressed={current} onClick={() => setSelected(name)}
                    className={`flex flex-col gap-0.5 rounded-card border px-3 py-2 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${current ? 'border-accent/40 bg-accent-soft' : 'border-transparent hover:bg-surface-soft'}`}>
                    <span className="flex items-center gap-2 text-xs font-semibold text-text-strong">
                      <Server size={15} aria-hidden="true" className="shrink-0 text-muted" />
                      <strong className="truncate">{name}</strong>
                      {isBusy(name) && <Spinner size={14} label={t('status.running')} />}
                    </span>
                    <small className="truncate text-[11px] text-muted">{spec.github_repository || spec.site_path || t('registry.no_repository')}</small>
                  </button>
                )
              })}
            </nav>
            {pageCount > 1 && (
              <div className="flex items-center justify-between gap-2 text-[11px] text-muted">
                <Button size="small" disabled={currentPage === 0} onClick={() => { setPage(currentPage - 1); setSelected(null) }} aria-label={t('registry.previous')}><ChevronLeft size={14} /></Button>
                <span>{currentPage + 1} / {pageCount}</span>
                <Button size="small" disabled={currentPage + 1 === pageCount} onClick={() => { setPage(currentPage + 1); setSelected(null) }} aria-label={t('registry.next')}><ChevronRight size={14} /></Button>
              </div>
            )}
          </Panel>
          {active ? (
            <AppCard key={active[0]} name={active[0]} spec={active[1]} call={call} api={api}
              onChanged={refresh} onSecret={onSecret} revision={revision} busy={isBusy(active[0])} />
          ) : (
            <Panel><EmptyState icon={<Search size={22} />}>{t('registry.no_matches')}</EmptyState></Panel>
          )}
        </div>
      )}
      {Object.keys(apps).length === 0 && (
        <Panel><EmptyState icon={<Server size={22} />}>{t('registry.empty')}</EmptyState></Panel>
      )}
    </div>
  )
}
