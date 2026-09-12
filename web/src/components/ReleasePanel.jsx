import { useCallback, useEffect, useRef, useState } from 'react'
import { Layers } from 'lucide-react'
import { useT } from '../i18n/index.js'
import { ConfirmDialog } from './ConfirmDialog.jsx'
import When from './When.jsx'
import { Button, Checkbox, DetailItem, EmptyState, ErrorMessage, Field, Help, MiniBadge, Notice, Skeleton, inputClass } from './ui.jsx'

export default function ReleasePanel({ name, spec, call, onChanged, revision = 0 }) {
  const t = useT()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState('')
  const [draft, setDraft] = useState(null)
  const requestId = useRef(0)
  const policyKey = JSON.stringify([spec.keep_previous, spec.auto_cleanup])
  const policy = draft?.key === policyKey ? draft : {
    keep: spec.keep_previous || 1,
    retain: spec.keep_previous !== 0,
    automatic: spec.auto_cleanup ?? true,
  }
  const { keep, retain, automatic } = policy
  const updatePolicy = (values) => setDraft({ ...policy, key: policyKey, ...values })
  const base = `/admin/apps/${encodeURIComponent(name)}/releases`

  const load = useCallback(async () => {
    const id = ++requestId.current
    setLoading(true)
    try {
      const result = await call(base)
      if (id !== requestId.current) return
      setData(result)
      setError(null)
    } catch (err) {
      if (id !== requestId.current) return
      setData(null)
      setError(err.message)
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }, [base, call])

  useEffect(() => {
    const first = setTimeout(load, 0)
    return () => clearTimeout(first)
  }, [load, revision])

  const operate = async (operation, release) => {
    setPending(true)
    setNotice('')
    try {
      await call(`${base}/${operation}`, { method: 'POST', body: JSON.stringify({ release }) })
      setNotice(t(operation === 'activate' ? 'releases.queued' : 'releases.cleanup_queued'))
      setError(null)
      onChanged()
      await load()
    } finally {
      setPending(false)
    }
  }

  const saveRetention = async () => {
    setPending(true)
    try {
      const { secret: _secret, github: _github, ...configuration } = spec
      await call(`/admin/apps/${encodeURIComponent(name)}`, {
        method: 'PUT',
        body: JSON.stringify({ ...configuration, keep_previous: retain ? Number(keep) : 0, auto_cleanup: automatic }),
      })
      setNotice(t('releases.saved'))
      setError(null)
      onChanged()
    } catch (err) {
      setError(err.message)
      throw err
    } finally {
      setPending(false)
    }
  }

  const busy = pending || loading || !data || data.busy
  return (
    <section className="flex flex-col gap-4" aria-label={t('releases.label', { name })}>
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
      {notice && <Notice>{notice}</Notice>}
      {!data && !error && <Skeleton lines={3} label={t('releases.loading')} />}
      <div><Button size="small" disabled={pending || loading} onClick={load}>{t('activity.refresh')}</Button></div>
      {data && <>
        <DetailItem label={t('releases.active')}>
          <code className="font-mono">{data.active_path || t('releases.none')}</code>
        </DetailItem>
        {data.busy && <Notice>{t('releases.busy')}</Notice>}
        {data.releases.length > 0 ? (
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {data.releases.map((release) => {
              const commit = release.release_id === 'b4deployd' ? 'b4deployd'
                : release.commit_sha?.slice(0, 12) || t('releases.imported')
              return <li key={release.name} className="flex flex-col gap-2 rounded-card border border-border-subtle bg-surface-soft p-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 text-xs">
                  <code className="font-mono font-semibold text-text-strong" title={release.name}>{commit}</code>
                  {release.commit_sha && <small className="font-mono text-muted-soft">{(release.release_id || release.name).slice(41, 49)}</small>}
                  {release.created_at && <When value={release.created_at} className="text-muted" />}
                  {release.active && <MiniBadge>{t('releases.active')}</MiniBadge>}
                  {release.previous && <MiniBadge>{t('releases.previous')}</MiniBadge>}
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  <ConfirmDialog
                    trigger={<Button size="small" disabled={busy || !release.can_activate}>{t('releases.activate')}</Button>}
                    title={t('releases.activate_title', { commit })}
                    description={t('releases.activate_description')}
                    confirmLabel={t('releases.activate')}
                    onConfirm={() => operate('activate', release.name)}
                  />
                  {!release.protected && <ConfirmDialog
                    trigger={<Button size="small" disabled={busy}>{t('releases.remove')}</Button>}
                    title={t('releases.remove_title', { commit })}
                    description={t('releases.remove_description')}
                    confirmLabel={t('releases.remove')}
                    destructive
                    onConfirm={() => operate('cleanup', release.name)}
                  />}
                </div>
              </li>
            })}
          </ul>
        ) : <EmptyState icon={<Layers size={22} />} className="py-5">{t('releases.empty')}</EmptyState>}
      </>}
      <form className="flex flex-col gap-3 border-t border-border-subtle pt-4"
        onSubmit={(event) => { event.preventDefault(); if (retain) saveRetention().catch(() => {}) }}>
        <Checkbox label={t('releases.retain')} checked={retain} disabled={busy}
          onChange={(event) => updatePolicy({ retain: event.target.checked })} />
        <Field label={t('releases.keep')} className="sm:max-w-48">
          <input className={inputClass} type="number" min="1" max="99" required
            value={keep} onChange={(event) => updatePolicy({ keep: event.target.value })} disabled={busy || !retain} />
        </Field>
        <Checkbox label={t('releases.automatic')} checked={automatic} disabled={busy}
          onChange={(event) => updatePolicy({ automatic: event.target.checked })} />
        <Help>{t('releases.protection')}</Help>
        <div>
          {retain ? <Button type="submit" disabled={busy}>{t('app.save_changes')}</Button> : <ConfirmDialog
            trigger={<Button disabled={busy}>{t('app.save_changes')}</Button>}
            title={t('releases.disable_title')}
            description={t('releases.disable_description')}
            confirmLabel={t('app.save_changes')}
            destructive
            onConfirm={saveRetention}
          />}
        </div>
      </form>
    </section>
  )
}
