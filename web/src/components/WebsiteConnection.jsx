import { useCallback, useEffect, useState } from 'react'
import { Button, ConfirmDialog } from './ui.jsx'

export default function WebsiteConnection({ name, call, onChanged, revision = 0, t }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)
  const [loading, setLoading] = useState(false)
  const base = `/admin/apps/${encodeURIComponent(name)}/website`
  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await call(base))
      setError(null)
    } catch (err) {
      setData(null)
      setError(err.message)
    } finally {
      setLoading(false)
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
    } catch (err) {
      setError(err.message)
    } finally {
      setPending(false)
    }
  }
  return <section className="release-panel" aria-label={t('website.title')}>
    <p className="release-help">{t('website.help')}</p>
    {error && <p className="error-message" role="alert">{error}</p>}
    {!data && !error && <p>{t('releases.loading')}</p>}
    {data && <p role="status">{t(`website.${data.status}`)}</p>}
    {data?.status === 'connected' && !data.backup && <p>{t('website.existing_link')}</p>}
    {['ready', 'recovery', 'reconnect'].includes(data?.status) && <ConfirmDialog
      trigger={<Button disabled={pending}>{t('website.connect')}</Button>}
      title={t('website.confirm_title')}
      description={t(data.status === 'reconnect' ? 'website.reconnect_description' : 'website.confirm_description')}
      confirmationValue={name}
      confirmationLabel={t('website.confirm_name', { name })}
      confirmLabel={t('website.connect')}
      cancelLabel={t('common.cancel')}
      onConfirm={connect}
    />}
    <Button disabled={pending || loading} onClick={load}>{t('website.refresh')}</Button>
  </section>
}
