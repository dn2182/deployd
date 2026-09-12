import { useCallback, useEffect, useRef, useState } from 'react'
import { useT } from '../i18n/index.js'
import { ConfirmDialog } from './ConfirmDialog.jsx'
import { Button, ErrorMessage, Help, Notice, Skeleton } from './ui.jsx'

export default function WebsiteConnection({ name, call, onChanged, revision = 0 }) {
  const t = useT()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)
  const [loading, setLoading] = useState(false)
  const requestId = useRef(0)
  const base = `/admin/apps/${encodeURIComponent(name)}/website`

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
    const timer = setTimeout(load, 0)
    return () => clearTimeout(timer)
  }, [load, revision])

  const connect = async () => {
    setPending(true)
    try {
      await call(`${base}/connect`, { method: 'POST', body: JSON.stringify({ confirm: name }) })
      setData({ status: 'busy' })
      setError(null)
      onChanged()
    } finally {
      setPending(false)
    }
  }

  return (
    <section className="flex flex-col gap-3" aria-label={t('website.title')}>
      <Help>{t('website.help')}</Help>
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
      {!data && !error && <Skeleton lines={2} label={t('releases.loading')} />}
      {data && <Notice>{t(`website.${data.status}`)}</Notice>}
      {data?.status === 'connected' && !data.backup && <Help>{t('website.existing_link')}</Help>}
      <div className="flex flex-wrap gap-2">
        {['ready', 'recovery', 'reconnect'].includes(data?.status) && <ConfirmDialog
          trigger={<Button variant="primary" disabled={pending}>{t('website.connect')}</Button>}
          title={t('website.confirm_title')}
          description={t(data.status === 'reconnect' ? 'website.reconnect_description' : 'website.confirm_description')}
          confirmationValue={name}
          confirmationLabel={t('website.confirm_name', { name })}
          confirmLabel={t('website.connect')}
          onConfirm={connect}
        />}
        <Button disabled={pending || loading} onClick={load}>{t('website.refresh')}</Button>
      </div>
    </section>
  )
}
