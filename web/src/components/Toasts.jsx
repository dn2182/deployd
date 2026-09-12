import { useCallback, useEffect, useRef, useState } from 'react'
import { CircleCheck, CircleX, Info, X } from 'lucide-react'
import { useT } from '../i18n/index.js'
import { ToastContext } from '../hooks/useToast.js'

const DURATION_MS = 6000
const MAX_VISIBLE = 5
let counter = 0

const STYLES = {
  success: ['border-success/30', <CircleCheck key="i" size={16} className="text-success" />],
  error: ['border-danger/30', <CircleX key="i" size={16} className="text-danger" />],
  info: ['border-accent/30', <Info key="i" size={16} className="text-accent" />],
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const timers = useRef(new Map())

  const dismiss = useCallback((id) => {
    clearTimeout(timers.current.get(id))
    timers.current.delete(id)
    setToasts((old) => old.filter((toast) => toast.id !== id))
  }, [])

  const push = useCallback(({ kind = 'info', message }) => {
    const id = ++counter
    setToasts((old) => [...old.slice(-(MAX_VISIBLE - 1)), { id, kind, message }])
    timers.current.set(id, setTimeout(() => dismiss(id), DURATION_MS))
  }, [dismiss])

  useEffect(() => {
    const pending = timers.current
    return () => {
      for (const timer of pending.values()) clearTimeout(timer)
    }
  }, [])

  return (
    <ToastContext.Provider value={push}>
      {children}
      <Toasts toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  )
}

export function Toasts({ toasts, onDismiss }) {
  const t = useT()
  return (
    <div className="pointer-events-none fixed inset-x-3 bottom-3 z-50 flex flex-col items-stretch gap-2 sm:inset-x-auto sm:right-5 sm:bottom-5 sm:w-[min(360px,calc(100vw-40px))]">
      {toasts.map((toast) => {
        const [border, icon] = STYLES[toast.kind] ?? STYLES.info
        return (
          <div
            key={toast.id}
            role="status"
            className={`pointer-events-auto flex items-start gap-2.5 rounded-card border bg-surface-strong px-3.5 py-3 text-xs text-text shadow-panel backdrop-blur-2xl ${border}`}
          >
            <span className="mt-px shrink-0">{icon}</span>
            <span className="min-w-0 flex-1 break-words">{toast.message}</span>
            <button
              type="button"
              aria-label={t('app.dismiss')}
              title={t('app.dismiss')}
              onClick={() => onDismiss(toast.id)}
              className="shrink-0 rounded-md p-0.5 text-muted hover:text-text-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
            >
              <X size={14} />
            </button>
          </div>
        )
      })}
    </div>
  )
}
