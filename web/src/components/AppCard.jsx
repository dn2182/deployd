import { useId, useState } from 'react'
import { KeyRound, Pencil, Server, ShieldCheck, Snowflake, Trash2 } from 'lucide-react'
import { useT } from '../i18n/index.js'
import { ConfirmDialog } from './ConfirmDialog.jsx'
import { useToast } from '../hooks/useToast.js'
import { Field, Help, IconButton, MiniBadge, Notice, Panel, inputClass } from './ui.jsx'
import AppEditor from './AppEditor.jsx'
import AppPaths from './AppPaths.jsx'
import AppStatus from './AppStatus.jsx'
import GitHubSetup from './GitHubSetup.jsx'
import ReleasePanel from './ReleasePanel.jsx'
import WebsiteConnection from './WebsiteConnection.jsx'

const TAB_LABELS = { website: 'website.title', releases: 'releases.manage', github: 'github.title' }

export default function AppCard({ name, spec, call, api, onChanged, onSecret, revision, busy }) {
  const t = useT()
  const toast = useToast()
  const tabId = useId()
  const [editing, setEditing] = useState(false)
  const [tab, setTab] = useState(spec.site_path ? 'website' : 'releases')
  const tabs = [...(spec.site_path ? ['website'] : []), 'releases', 'github']
  const activeTab = tabs.includes(tab) ? tab : 'releases'
  const [removeWebsite, setRemoveWebsite] = useState('restore')
  const [removing, setRemoving] = useState(false)
  const [notice, setNotice] = useState(null)

  const rotate = async () => {
    const out = await api.rotateSecret(name)
    onSecret({ ...out, app: name })
    onChanged()
  }

  const remove = async () => {
    setRemoving(true)
    try {
      const result = await api.removeApp(name, spec.site_path ? { confirm: name, website: removeWebsite } : null)
      if (result.status === 'queued') setNotice(t('app.removal_queued'))
      else toast({ kind: 'success', message: t('app.removed_toast', { name }) })
      onChanged()
    } finally {
      setRemoving(false)
    }
  }

  const moveTab = (event, index) => {
    const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length
      : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
        : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null
    if (next === null) return
    event.preventDefault()
    setTab(tabs[next])
    event.currentTarget.parentElement.children[next].focus()
  }

  return (
    <Panel as="article" className="flex min-w-0 flex-col gap-4 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="grid size-11 shrink-0 place-items-center rounded-card bg-accent-soft text-accent" aria-hidden="true">
            <Server size={19} />
          </div>
          <div className="min-w-0">
            <h3 className="m-0 flex flex-wrap items-center gap-2 text-base font-semibold tracking-tight text-text-strong">
              <span className="break-all">{name}</span>
              {spec.frozen && <MiniBadge><Snowflake size={10} className="mr-1 inline" />{t('status_panel.frozen')}</MiniBadge>}
            </h3>
            <span className="block break-all text-xs text-muted">{spec.health?.url ?? t('app.no_health')}</span>
          </div>
        </div>
        <div className="flex shrink-0 gap-1.5">
          <IconButton label={t('app.edit', { name })} onClick={() => setEditing(true)} disabled={busy || removing}>
            <Pencil size={16} />
          </IconButton>
          <ConfirmDialog
            trigger={<IconButton label={t('app.rotate', { name })} disabled={busy || removing}><KeyRound size={16} /></IconButton>}
            title={t('app.rotate_title', { name })}
            description={t('app.rotate_description')}
            confirmLabel={t('app.rotate_confirm')}
            onConfirm={rotate}
          />
          <ConfirmDialog
            trigger={<IconButton label={t('app.remove', { name })} danger disabled={busy || removing}><Trash2 size={16} /></IconButton>}
            title={t('app.remove_title', { name })}
            description={t(spec.site_path ? 'website.remove_description' : 'app.remove_description')}
            confirmLabel={t('app.remove_confirm')}
            confirmationValue={name}
            confirmationLabel={t('common.confirm_type', { value: name })}
            destructive
            onConfirm={remove}
          >
            {spec.site_path && (
              <Field label={t('website.remove_choice')} help={t('website.remove_help')}>
                <select className={inputClass} value={removeWebsite} onChange={(event) => setRemoveWebsite(event.target.value)}>
                  <option value="restore">{t('website.remove_restore')}</option>
                  <option value="keep">{t('website.remove_keep')}</option>
                </select>
              </Field>
            )}
          </ConfirmDialog>
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <span className="text-[10px] font-bold uppercase tracking-wider text-muted-soft">{t('app.signing_secret')}</span>
        {spec.secret?.configured ? (
          <span className="inline-flex flex-wrap items-center gap-2 text-xs text-text">
            <ShieldCheck size={14} className="text-success" aria-hidden="true" />
            <code className="font-mono text-text-strong">{spec.secret.fingerprint}</code>
            {spec.secret.env_override && <MiniBadge>{t('app.env_override')}</MiniBadge>}
          </span>
        ) : (
          <span className="text-xs font-semibold text-warning">{t('app.not_configured')}</span>
        )}
      </div>

      <AppStatus name={name} api={api} revision={revision} onChanged={onChanged} />

      {notice && <Notice>{notice}</Notice>}
      {busy && <Help>{t('activity.pending')}</Help>}
      <details className="group rounded-card border border-border-subtle bg-surface-soft px-3.5 py-2.5 text-xs">
        <summary className="cursor-pointer font-semibold text-text-strong">{t('app.folders')}</summary>
        <div className="pt-3"><AppPaths spec={spec} /></div>
      </details>

      {editing ? (
        <div className="rounded-card border border-border-subtle bg-surface-soft p-4">
          <AppEditor name={name} initialSpec={spec} call={call}
            onCancel={() => setEditing(false)} onSaved={(result) => {
              setEditing(false)
              if (result.secret) onSecret({ ...result, app: name })
              onChanged()
            }} />
        </div>
      ) : (
        <>
          <div role="tablist" aria-label={t('app.sections', { name })} className="flex flex-wrap gap-1.5 border-b border-border-subtle pb-3">
            {tabs.map((value, index) => (
              <button key={value} type="button" role="tab"
                id={`${tabId}-${value}`} aria-controls={`${tabId}-panel`}
                aria-selected={activeTab === value} tabIndex={activeTab === value ? 0 : -1}
                onClick={() => setTab(value)} onKeyDown={(event) => moveTab(event, index)}
                className={`rounded-full px-3.5 py-1.5 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${activeTab === value ? 'bg-accent text-white' : 'bg-surface-soft text-muted hover:text-text-strong'}`}>
                {t(TAB_LABELS[value])}
              </button>
            ))}
          </div>
          <div role="tabpanel" id={`${tabId}-panel`} aria-labelledby={`${tabId}-${activeTab}`} tabIndex={0} className="min-w-0 focus-visible:outline-none">
            {activeTab === 'website' && <WebsiteConnection name={name} call={call} onChanged={onChanged} revision={revision} />}
            {activeTab === 'releases' && <ReleasePanel name={name} spec={spec} call={call} onChanged={onChanged} revision={revision} />}
            {activeTab === 'github' && <GitHubSetup name={name} spec={spec} call={call} onChanged={onChanged} />}
          </div>
        </>
      )}
    </Panel>
  )
}
