import { cloneElement, useEffect, useId, useRef, useState } from 'react'
import { Info, TriangleAlert } from 'lucide-react'
import { useT } from '../i18n/index.js'
import { Button, ErrorMessage, Field, Spinner, inputClass } from './ui.jsx'

export function Modal({ role = 'dialog', labelledBy, describedBy, onClose, closable = true, className = '', children }) {
  const ref = useRef(null)
  useEffect(() => {
    const dialog = ref.current
    if (!dialog.open) dialog.showModal()
  }, [])

  const cancel = (event) => {
    event.preventDefault()
    if (closable) onClose()
  }
  const closed = () => {
    if (closable) onClose()
    else ref.current?.showModal()
  }
  const backdrop = (event) => {
    if (event.target === event.currentTarget && closable) onClose()
  }
  const keydown = (event) => {
    if (event.key !== 'Escape') return
    event.preventDefault()
    if (closable) onClose()
  }

  return (
    <dialog
      ref={ref}
      role={role}
      aria-modal="true"
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      onCancel={cancel}
      onClose={closed}
      onClick={backdrop}
      onKeyDown={keydown}
      className={`m-auto max-h-[calc(100vh-32px)] w-[min(440px,calc(100vw-32px))] overflow-auto rounded-panel border border-border bg-surface-strong p-0 text-text shadow-2xl backdrop-blur-2xl backdrop:bg-[rgba(3,7,16,0.48)] backdrop:backdrop-blur-sm ${className}`.trim()}
    >
      <div className="p-6">{children}</div>
    </dialog>
  )
}

export function ConfirmDialog({
  trigger,
  title,
  description,
  confirmLabel,
  onConfirm,
  destructive = false,
  confirmationValue,
  confirmationLabel,
  children,
}) {
  const t = useT()
  const id = useId()
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)

  const reset = () => {
    setOpen(false)
    setValue('')
    setError(null)
  }
  const close = () => {
    if (!pending) reset()
  }
  const confirm = async () => {
    setPending(true)
    setError(null)
    try {
      await onConfirm()
      reset()
    } catch (confirmError) {
      setError(confirmError.message)
    } finally {
      setPending(false)
    }
  }
  const confirmed = confirmationValue === undefined || value === confirmationValue

  return (
    <>
      {cloneElement(trigger, { onClick: () => setOpen(true) })}
      {open && (
        <Modal role="alertdialog" labelledBy={`${id}-title`} describedBy={`${id}-description`} onClose={close} closable={!pending}>
          <div
            aria-hidden="true"
            className={`mb-4 grid size-10 place-items-center rounded-card ${destructive ? 'bg-danger-soft text-danger' : 'bg-accent-soft text-accent'}`}
          >
            {destructive ? <TriangleAlert size={20} /> : <Info size={20} />}
          </div>
          <h2 id={`${id}-title`} className="m-0 text-lg font-semibold tracking-tight text-text-strong">{title}</h2>
          <p id={`${id}-description`} className="mt-2 mb-0 text-xs leading-relaxed text-muted">{description}</p>
          {children && <div className="mt-4 flex flex-col gap-3">{children}</div>}
          {confirmationValue !== undefined && (
            <Field className="mt-4" label={confirmationLabel ?? t('common.confirm_type', { value: confirmationValue })}>
              <input className={inputClass} value={value} onChange={(event) => setValue(event.target.value)}
                autoComplete="off" autoFocus disabled={pending} />
            </Field>
          )}
          {error && <div className="mt-4"><ErrorMessage compact>{error}</ErrorMessage></div>}
          <div className="mt-6 flex flex-wrap justify-end gap-2">
            <Button onClick={close} disabled={pending}>{t('common.cancel')}</Button>
            <Button variant={destructive ? 'danger' : 'primary'} disabled={!confirmed || pending} onClick={confirm}>
              {pending && <Spinner size={14} />}
              {confirmLabel}
            </Button>
          </div>
        </Modal>
      )}
    </>
  )
}
