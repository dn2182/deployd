import { useState } from 'react'
import { CloudCog, KeyRound, ShieldCheck, UserRound } from 'lucide-react'
import { useT } from '../i18n/index.js'
import { Button, ErrorMessage, Field, Panel, Spinner, inputClass } from './ui.jsx'

const MODE_KEY = 'deployd-sign-in-mode'

function rememberedMode() {
  try {
    return localStorage.getItem(MODE_KEY) === 'member' ? 'member' : 'admin'
  } catch {
    return 'admin'
  }
}

export default function SignIn({ rejected, expired, onAdmin, onMember }) {
  const t = useT()
  const [mode, setModeState] = useState(() => (expired ? 'member' : rememberedMode()))
  const [token, setToken] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)

  const setMode = (next) => {
    setModeState(next)
    setError(null)
    try {
      localStorage.setItem(MODE_KEY, next)
    } catch {
      return
    }
  }

  const submitAdmin = (event) => {
    event.preventDefault()
    const value = token.trim()
    if (!value) return
    onAdmin(value)
    setToken('')
  }

  const submitMember = async (event) => {
    event.preventDefault()
    if (pending || !username.trim() || !password) return
    setPending(true)
    setError(null)
    try {
      await onMember(username.trim().toLowerCase(), password)
    } catch (failure) {
      setError(failure.message)
      setPassword('')
    } finally {
      setPending(false)
    }
  }

  const adminRejected = rejected && !expired
  return (
    <Panel as="section" className="mx-auto flex w-full max-w-md flex-col gap-5 p-6 sm:p-8" aria-labelledby="sign-in-title">
      <div className="flex flex-col items-center gap-3 text-center">
        <div className="grid size-12 place-items-center rounded-card bg-linear-to-br from-[#7488ff] via-[#405bef] to-[#3947c4] text-white shadow-soft" aria-hidden="true">
          <CloudCog size={24} />
        </div>
        <div>
          <h2 id="sign-in-title" className="m-0 text-xl font-semibold tracking-tight text-text-strong">
            {t(adminRejected ? 'token.rejected_title' : 'signin.title')}
          </h2>
          <p className="mt-1 mb-0 text-xs leading-relaxed text-muted">
            {t(adminRejected ? 'token.rejected_description' : expired ? 'signin.expired' : 'signin.description')}
          </p>
        </div>
      </div>

      <div role="tablist" aria-label={t('signin.as')} className="grid grid-cols-2 gap-1 rounded-card bg-surface-soft p-1">
        {[['member', 'signin.member', UserRound], ['admin', 'signin.admin', ShieldCheck]].map(([value, label, Icon]) => (
          <button key={value} type="button" role="tab" aria-selected={mode === value} onClick={() => setMode(value)}
            className={`inline-flex h-9 items-center justify-center gap-1.5 rounded-[9px] text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${mode === value ? 'bg-accent text-white shadow-soft' : 'text-muted hover:text-text-strong'}`}>
            <Icon size={14} aria-hidden="true" /> {t(label)}
          </button>
        ))}
      </div>

      {mode === 'admin' ? (
        <form onSubmit={submitAdmin} className="flex flex-col gap-3">
          <Field label={t('topbar.admin_token')} help={t('locked.description')}>
            <span className="relative block">
              <KeyRound size={15} aria-hidden="true" className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted" />
              <input
                type="password" autoComplete="off" spellCheck={false} value={token}
                aria-invalid={adminRejected || undefined}
                onChange={(event) => setToken(event.target.value)}
                className={`${inputClass} h-10 pl-9`}
              />
            </span>
          </Field>
          {adminRejected && <ErrorMessage compact>{t('token.rejected')}</ErrorMessage>}
          <Button type="submit" variant="primary" className="h-10" disabled={!token.trim()}>{t('token.connect')}</Button>
        </form>
      ) : (
        <form onSubmit={submitMember} className="flex flex-col gap-3">
          <Field label={t('signin.username')}>
            <input className={`${inputClass} h-10`} autoComplete="username" autoCapitalize="none" spellCheck={false}
              value={username} disabled={pending} onChange={(event) => setUsername(event.target.value)} />
          </Field>
          <Field label={t('signin.password')}>
            <input className={`${inputClass} h-10`} type="password" autoComplete="current-password"
              value={password} disabled={pending} onChange={(event) => setPassword(event.target.value)} />
          </Field>
          {error && <ErrorMessage compact>{error}</ErrorMessage>}
          <Button type="submit" variant="primary" className="h-10" disabled={pending || !username.trim() || !password}>
            {pending && <Spinner size={14} />}{t('signin.submit')}
          </Button>
        </form>
      )}
    </Panel>
  )
}
