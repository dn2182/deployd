import { useId, useState } from 'react'
import { useT } from '../i18n/index.js'
import { Modal } from './ConfirmDialog.jsx'
import { Button, ErrorMessage, Field, Spinner, inputClass } from './ui.jsx'

export default function MemberPasswordDialog({ api, username, onClose }) {
  const t = useT()
  const id = useId()
  const [current, setCurrent] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [changed, setChanged] = useState(false)

  const submit = async (event) => {
    event.preventDefault()
    if (pending) return
    if ([...password].length < 12) { setError(t('password.length')); return }
    if (password !== confirmation) { setError(t('password.mismatch')); return }
    if (password === current) { setError(t('password.different')); return }
    setPending(true)
    setError(null)
    try {
      await api.changeOwnPassword({ current_password: current, new_password: password })
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
      <h2 id={`${id}-title`} className="m-0 text-lg font-semibold text-text-strong">{t('member_password.title')}</h2>
      <p id={`${id}-description`} className="mt-2 text-xs leading-relaxed text-muted">{t('member_password.description')}</p>
      {changed ? (
        <>
          <p role="status" className="text-sm text-success">{t('member_password.success')}</p>
          <Button className="mt-4" onClick={onClose}>{t('app.dismiss')}</Button>
        </>
      ) : (
        <form onSubmit={submit} className="mt-4 flex flex-col gap-4">
          {error && <ErrorMessage>{error}</ErrorMessage>}
          <input type="text" autoComplete="username" value={username} readOnly hidden />
          <Field label={t('password.current')}>
            <input className={inputClass} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required disabled={pending} />
          </Field>
          <Field label={t('password.new')} help={t('password.length')}>
            <input className={inputClass} type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required disabled={pending} />
          </Field>
          <Field label={t('password.confirm')}>
            <input className={inputClass} type="password" autoComplete="new-password" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} required disabled={pending} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button onClick={onClose} disabled={pending}>{t('app.dismiss')}</Button>
            <Button type="submit" variant="primary" disabled={pending}>{pending && <Spinner />}{t('password.save')}</Button>
          </div>
        </form>
      )}
    </Modal>
  )
}
