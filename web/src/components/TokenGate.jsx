import { useState } from 'react'
import { KeyRound, ShieldCheck } from 'lucide-react'
import { useT } from '../i18n/index.js'
import { Button, ErrorMessage, Panel } from './ui.jsx'

export default function TokenGate({ rejected, onSubmit }) {
  const t = useT()
  const [value, setValue] = useState('')
  const submit = (event) => {
    event.preventDefault()
    const token = value.trim()
    if (!token) return
    onSubmit(token)
    setValue('')
  }
  return (
    <Panel as="section" className="flex flex-col gap-5 p-6 sm:flex-row sm:items-start sm:gap-6 sm:p-7" aria-labelledby="token-gate-title">
      <div className="grid size-12 shrink-0 place-items-center rounded-card bg-accent-soft text-accent" aria-hidden="true">
        <KeyRound size={23} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <div>
          <h2 id="token-gate-title" className="m-0 text-lg font-semibold tracking-tight text-text-strong">
            {t(rejected ? 'token.rejected_title' : 'locked.title')}
          </h2>
          <p className="mt-1 mb-0 text-xs leading-relaxed text-muted">{t(rejected ? 'token.rejected_description' : 'locked.description')}</p>
        </div>
        <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <label className="flex min-w-0 flex-1 items-center gap-2 rounded-card border border-border-subtle bg-input px-3 text-muted focus-within:border-accent/45 focus-within:ring-2 focus-within:ring-accent/25">
            <ShieldCheck size={15} aria-hidden="true" />
            <span className="sr-only">{t('topbar.admin_token')}</span>
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder={t('topbar.admin_token')}
              value={value}
              aria-invalid={rejected || undefined}
              onChange={(event) => setValue(event.target.value)}
              className="h-9 w-full min-w-0 border-0 bg-transparent text-xs text-text outline-none placeholder:text-muted-soft"
            />
          </label>
          <Button type="submit" variant="primary" disabled={!value.trim()}>{t('token.connect')}</Button>
        </form>
        {rejected && <ErrorMessage compact>{t('token.rejected')}</ErrorMessage>}
      </div>
    </Panel>
  )
}
