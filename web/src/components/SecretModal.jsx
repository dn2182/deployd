import { useEffect, useId, useRef, useState } from 'react'
import { Check, Copy, KeyRound } from 'lucide-react'
import { useT } from '../i18n/index.js'
import { Modal } from './ConfirmDialog.jsx'
import { Button, Help } from './ui.jsx'

export default function SecretModal({ secret, onDismiss }) {
  const t = useT()
  const id = useId()
  const [copied, setCopied] = useState(false)
  const timer = useRef(null)
  useEffect(() => () => clearTimeout(timer.current), [])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret.secret)
      setCopied(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 1800)
    } catch {
      setCopied(false)
    }
  }

  return (
    <Modal labelledBy={`${id}-title`} describedBy={`${id}-description`} onClose={onDismiss} closable={!secret.secret}>
      <div aria-hidden="true" className="mb-4 grid size-10 place-items-center rounded-card bg-warning-soft text-warning">
        <KeyRound size={20} />
      </div>
      <h2 id={`${id}-title`} className="m-0 text-lg font-semibold tracking-tight text-text-strong">
        {secret.secret ? t('app.secret_generated') : t('app.secret_missing')}
      </h2>
      <p id={`${id}-description`} className="mt-2 mb-0 text-xs leading-relaxed text-muted">
        {secret.secret ? t('app.secret_once_for', { name: secret.app }) : secret.warning}
      </p>
      {secret.secret && (
        <>
          <code className="mt-4 block max-h-40 overflow-auto rounded-card border border-border-subtle bg-input p-3 font-mono text-xs break-all text-text-strong select-all">
            {secret.secret}
          </code>
          <Help className="mt-3">{t('app.secret_copy_help')}</Help>
        </>
      )}
      <div className="mt-6 flex flex-wrap justify-end gap-2">
        {secret.secret && (
          <Button onClick={copy}>
            {copied ? <Check size={15} /> : <Copy size={15} />}
            {t(copied ? 'app.copied' : 'app.copy')}
          </Button>
        )}
        <Button variant="primary" onClick={onDismiss}>{t(secret.secret ? 'app.secret_dismiss' : 'app.dismiss')}</Button>
      </div>
    </Modal>
  )
}
