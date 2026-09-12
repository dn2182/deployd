import { useCallback, useEffect, useState } from 'react'
import { Button, ConfirmDialog } from './ui.jsx'

export default function ReleasePanel({ name, spec, call, onChanged, t }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)
  const [notice, setNotice] = useState('')
  const [draft, setDraft] = useState(null)
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
    try {
      setData(await call(base))
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }, [base, call])

  useEffect(() => {
    const first = setTimeout(load, 0)
    const poll = setInterval(load, 5000)
    return () => { clearTimeout(first); clearInterval(poll) }
  }, [load])

  const operate = async (operation, release) => {
    setPending(true)
    setNotice('')
    try {
      await call(`${base}/${operation}`, { method: 'POST', body: JSON.stringify({ release }) })
      setNotice(t(operation === 'activate' ? 'releases.queued' : 'releases.removed'))
      onChanged()
      await load()
    } catch (err) {
      setError(err.message)
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
    } finally {
      setPending(false)
    }
  }

  const busy = pending || data?.busy
  return (
    <section className="release-panel" aria-label={t('releases.label', { name })}>
      {error && <p className="error-message" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {!data && !error && <p>{t('releases.loading')}</p>}
      {data && <>
        <div className="detail-item">
          <span className="detail-label">{t('releases.active')}</span>
          <code className="path-value">{data.active_path || t('releases.none')}</code>
        </div>
        {data.busy && <p role="status">{t('releases.busy')}</p>}
        <ul className="release-list">
          {data.releases.map((release) => {
            const commit = release.commit_sha?.slice(0, 12) || t('releases.imported')
            return <li key={release.name} className="release-item">
              <div className="release-identity">
                <code title={release.name}>{commit}</code>
                {release.commit_sha && <small>{(release.release_id || release.name).slice(41, 49)}</small>}
                {release.created_at && <time dateTime={new Date(release.created_at * 1000).toISOString()}>
                  {new Date(release.created_at * 1000).toLocaleString(document.documentElement.lang || 'en')}
                </time>}
                {release.active && <span className="mini-badge">{t('releases.active')}</span>}
                {release.previous && <span className="mini-badge">{t('releases.previous')}</span>}
              </div>
              <div className="release-actions">
                <ConfirmDialog
                  trigger={<Button size="small" disabled={busy || !release.can_activate}>{t('releases.activate')}</Button>}
                  title={t('releases.activate_title', { commit })}
                  description={t('releases.activate_description')}
                  confirmLabel={t('releases.activate')}
                  cancelLabel={t('common.cancel')}
                  onConfirm={() => operate('activate', release.name)}
                />
                {!release.protected && <ConfirmDialog
                  trigger={<Button size="small" disabled={busy}>{t('releases.remove')}</Button>}
                  title={t('releases.remove_title', { commit })}
                  description={t('releases.remove_description')}
                  confirmLabel={t('releases.remove')}
                  cancelLabel={t('common.cancel')}
                  destructive
                  onConfirm={() => operate('cleanup', release.name)}
                />}
              </div>
            </li>
          })}
        </ul>
        {!data.releases.length && <p>{t('releases.empty')}</p>}
      </>}
      <form className="release-retention" onSubmit={(event) => { event.preventDefault(); if (retain) saveRetention() }}>
        <label className="release-checkbox">
          <input type="checkbox" checked={retain} disabled={busy}
            onChange={(event) => updatePolicy({ retain: event.target.checked })} />
          {t('releases.retain')}
        </label>
        <label className="field-label">
          {t('releases.keep')}
          <input className="text-input" type="number" min="1" max="99" required
            value={keep} onChange={(event) => updatePolicy({ keep: event.target.value })} disabled={busy || !retain} />
        </label>
        <label className="release-checkbox">
          <input type="checkbox" checked={automatic} disabled={busy}
            onChange={(event) => updatePolicy({ automatic: event.target.checked })} />
          {t('releases.automatic')}
        </label>
        <p className="release-help">{t('releases.protection')}</p>
        {retain ? <Button type="submit" disabled={busy}>{t('app.save_changes')}</Button> : <ConfirmDialog
          trigger={<Button disabled={busy}>{t('app.save_changes')}</Button>}
          title={t('releases.disable_title')}
          description={t('releases.disable_description')}
          confirmLabel={t('app.save_changes')}
          cancelLabel={t('common.cancel')}
          destructive
          onConfirm={saveRetention}
        />}
      </form>
    </section>
  )
}
