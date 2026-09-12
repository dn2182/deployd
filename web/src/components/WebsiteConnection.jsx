import { useCallback, useEffect, useState } from 'react'
import { Button, ConfirmDialog } from './ui.jsx'

export default function WebsiteConnection({ name, call, onChanged, t }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)
  const base = `/admin/apps/${encodeURIComponent(name)}/website`
  const load = useCallback(async () => {
    try {
      setData(await call(base))
      setError(null)
    } catch (err) {
      setData(null)
      setError(err.message)
    }
  }, [base, call])
  useEffect(() => {
    const timer = setTimeout(load, 0)
    return () => clearTimeout(timer)
  }, [load])
  useEffect(() => {
    if (data?.status !== 'busy') return
    const timer = setTimeout(load, 3000)
    return () => clearTimeout(timer)
  }, [data, load])

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
    {data && <p role="status">{t(`website.${data.status}`)}</p>}
    {data?.status === 'connected' && !data.backup && <p>{t('website.existing_link')}</p>}
    {(data?.status === 'ready' || data?.status === 'recovery') && <ConfirmDialog
      trigger={<Button disabled={pending}>{t('website.connect')}</Button>}
      title={t('website.confirm_title')}
      description={t('website.confirm_description')}
      confirmationValue={name}
      confirmationLabel={t('website.confirm_name', { name })}
      confirmLabel={t('website.connect')}
      cancelLabel={t('common.cancel')}
      onConfirm={connect}
    />}
    <Button disabled={pending || data?.status === 'busy'} onClick={load}>{t('website.refresh')}</Button>
  </section>
}
