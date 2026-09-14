import { useEffect, useId, useState } from 'react'
import { useT } from '../i18n/index.js'
import { Modal } from './ConfirmDialog.jsx'
import { Button, ErrorMessage, Field, Help, Spinner, inputClass } from './ui.jsx'

export default function PasswordDialog({ api, onClose }) {
  const t = useT()
  const id = useId()
  const [account, setAccount] = useState(null)
  const [current, setCurrent] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [changed, setChanged] = useState(false)

  useEffect(() => {
    let active = true
    api.account().then((value) => { if (active) setAccount(value) })
      .catch((failure) => { if (active) setError(failure.message) })
    return () => { active = false }
  }, [api])

  const submit = async (event) => {
    event.preventDefault()
    if (pending) return
    const bytes = new TextEncoder().encode(password).length
    if ([...password].length < 12 || bytes > 72) { setError(t('password.length')); return }
    if (password !== confirmation) { setError(t('password.mismatch')); return }
    if (password === current) { setError(t('password.different')); return }
    setPending(true)
    setError(null)
    try {
      await api.changePassword({ current_password: current, new_password: password })
      setChanged(true)
    } catch (failure) {
      setError(failure.message)
    } finally {
      setCurrent('')
      setPassword('')
      setConfirmation('')
      setPending(false)
    }
  }

  return (
    <Modal labelledBy={`${id}-title`} describedBy={`${id}-description`} onClose={onClose} closable={!pending}>
      <h2 id={`${id}-title`} className="m-0 text-lg font-semibold text-text-strong">{t('password.title')}</h2>
      <p id={`${id}-description`} className="mt-2 text-xs leading-relaxed text-muted">{t('password.description')}</p>
      {changed ? (
        <>
          <p role="status" className="text-sm text-success">{t('password.success')}</p>
          <Button className="mt-4" onClick={onClose}>{t('app.dismiss')}</Button>
        </>
      ) : (
        <form onSubmit={submit} className="mt-4 flex flex-col gap-4">
          {error && <ErrorMessage>{error}</ErrorMessage>}
          {!account && !error && <Spinner />}
          {account && !account.password_change_available && <Help>{t('password.unavailable')}</Help>}
          {account?.password_change_available && (
            <>
              <Field label={t('password.username')}>
                <input className={inputClass} autoComplete="username" value={account.username} readOnly />
              </Field>
              <Field label={t('password.current')}>
                <input className={inputClass} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required disabled={pending} />
              </Field>
              <Field label={t('password.new')} help={t('password.length')}>
                <input className={inputClass} type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required disabled={pending} />
              </Field>
              <Field label={t('password.confirm')}>
                <input className={inputClass} type="password" autoComplete="new-password" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} required disabled={pending} />
              </Field>
              <Help>{t('password.transport')}</Help>
            </>
          )}
          <div className="flex justify-end gap-2">
            <Button onClick={onClose} disabled={pending}>{t('app.dismiss')}</Button>
            {account?.password_change_available && <Button type="submit" variant="primary" disabled={pending}>{pending && <Spinner />}{t('password.save')}</Button>}
          </div>
        </form>
      )}
    </Modal>
  )
}
