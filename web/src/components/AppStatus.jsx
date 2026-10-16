import { useEffect, useState } from 'react'
import { Snowflake, Sun } from 'lucide-react'
import { useT } from '../i18n/index.js'
import { ConfirmDialog } from './ConfirmDialog.jsx'
import { useToast } from '../hooks/useToast.js'
import When from './When.jsx'
import { Button, DetailItem, ErrorMessage, MiniBadge, Skeleton, StatusBadge } from './ui.jsx'

export default function AppStatus({ name, api, revision, onChanged }) {
  const t = useT()
  const toast = useToast()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)

  useEffect(() => {
    let active = true
    api.appStatus(name)
      .then((result) => {
        if (!active) return
        setData(result)
        setError(null)
      })
      .catch((requestError) => {
        if (active) setError(requestError.message)
      })
    return () => { active = false }
  }, [api, name, revision])

  const setFrozen = async (frozen) => {
    setPending(true)
    try {
      const result = await api.freeze(name, frozen)
      setData((old) => ({ ...old, frozen: result.frozen }))
      toast({ kind: 'success', message: t(result.frozen ? 'status_panel.frozen_toast' : 'status_panel.unfrozen_toast', { name }) })
      onChanged()
    } finally {
      setPending(false)
    }
  }

  return (
    <section className="flex flex-col gap-3 rounded-card border border-border-subtle bg-surface-soft p-3.5" aria-label={t('status_panel.title', { name })}>
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
      {!data && !error && <Skeleton lines={2} label={t('status_panel.loading')} />}
      {data && (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <DetailItem label={t('status_panel.current_release')}>
              <code className="font-mono">{data.current_release ? data.current_release.slice(0, 12) : t('releases.none')}</code>
            </DetailItem>
            <DetailItem label={t('status_panel.queued')}>
              {data.queued}{data.busy && <span className="ml-2 text-muted">{t('status_panel.busy')}</span>}
            </DetailItem>
            <DetailItem label={t('status_panel.last_deploy')}>
              {data.last_deploy ? (
                <span className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={data.last_deploy.status} />
                  <When value={data.last_deploy.finished_at ?? data.last_deploy.created_at} className="text-muted" />
                </span>
              ) : t('status_panel.never')}
            </DetailItem>
            <DetailItem label={t('status_panel.last_health')}>
              {data.last_health ? (
                <span className="flex flex-col gap-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <MiniBadge>{data.last_health.status}</MiniBadge>
                    <When value={data.last_health.started_at} className="text-muted" />
                  </span>
                  {data.last_health.output && (
                    <pre className="m-0 max-h-24 overflow-auto rounded bg-input p-2 font-mono text-[11px] whitespace-pre-wrap text-muted">{data.last_health.output}</pre>
                  )}
                </span>
              ) : t('status_panel.never')}
            </DetailItem>
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
            <span className={`inline-flex items-center gap-1.5 text-xs font-semibold ${data.frozen ? 'text-accent' : 'text-muted'}`}>
              {data.frozen ? <Snowflake size={14} /> : <Sun size={14} />}
              {t(data.frozen ? 'status_panel.frozen' : 'status_panel.not_frozen')}
            </span>
            {data.frozen ? (
              <Button size="small" disabled={pending} onClick={() => setFrozen(false).catch((err) => setError(err.message))}>
                {t('status_panel.unfreeze')}
              </Button>
            ) : (
              <ConfirmDialog
                trigger={<Button size="small" disabled={pending}>{t('status_panel.freeze')}</Button>}
                title={t('status_panel.freeze_title', { name })}
                description={t('status_panel.freeze_description')}
                confirmLabel={t('status_panel.freeze')}
                onConfirm={() => setFrozen(true)}
              />
            )}
          </div>
        </>
      )}
    </section>
  )
}
