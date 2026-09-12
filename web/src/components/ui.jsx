import { CircleAlert, LoaderCircle } from 'lucide-react'
import { useT } from '../i18n/index.js'

const FOCUS = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40'

const BUTTON_VARIANTS = {
  secondary: 'border-border-subtle bg-surface-soft text-text hover:enabled:bg-surface-strong hover:enabled:text-text-strong',
  primary: 'border-transparent bg-accent text-white shadow-soft hover:enabled:bg-accent-strong',
  danger: 'border-transparent bg-danger text-white hover:enabled:brightness-110',
}

const BUTTON_SIZES = {
  default: 'h-9 px-3.5 text-xs',
  small: 'h-8 px-3 text-[11px]',
}

export function Button({ className = '', variant = 'secondary', size = 'default', ...props }) {
  return (
    <button
      type="button"
      className={`inline-flex shrink-0 items-center justify-center gap-1.5 rounded-card border font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS} ${BUTTON_VARIANTS[variant]} ${BUTTON_SIZES[size]} ${className}`.trim()}
      {...props}
    />
  )
}

export function IconButton({ label, className = '', danger = false, children, ...props }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`inline-grid size-9 shrink-0 place-items-center rounded-card border border-border-subtle bg-surface-soft text-muted transition hover:enabled:bg-surface-strong disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS} ${danger ? 'hover:enabled:text-danger' : 'hover:enabled:text-text-strong'} ${className}`.trim()}
      {...props}
    >
      {children}
    </button>
  )
}

export function Panel({ as: Tag = 'div', className = '', ...props }) {
  return (
    <Tag
      className={`rounded-panel border border-border bg-linear-to-br from-surface-strong to-surface shadow-panel backdrop-blur-2xl backdrop-saturate-150 ${className}`.trim()}
      {...props}
    />
  )
}

export const inputClass = `w-full min-w-0 rounded-card border border-border-subtle bg-input px-3 py-2 font-sans text-[13px] font-normal text-text placeholder:text-muted-soft transition disabled:cursor-not-allowed disabled:opacity-60 ${FOCUS}`

export function Field({ label, help, children, className = '' }) {
  return (
    <div className={`flex min-w-0 flex-col gap-1.5 ${className}`.trim()}>
      <label className="flex flex-col gap-1.5 text-xs font-semibold text-text-strong">
        <span>{label}</span>
        {children}
      </label>
      {help && <Help>{help}</Help>}
    </div>
  )
}

export function Checkbox({ label, className = '', ...props }) {
  return (
    <label className={`flex items-start gap-2 text-xs text-text ${className}`.trim()}>
      <input type="checkbox" className="mt-0.5 size-4 shrink-0 accent-accent" {...props} />
      <span>{label}</span>
    </label>
  )
}

export function Help({ children, className = '' }) {
  return <p className={`text-[11px] leading-relaxed text-muted ${className}`.trim()}>{children}</p>
}

export function Notice({ children, className = '' }) {
  return (
    <p role="status" className={`rounded-card border border-success/25 bg-success-soft px-3 py-2 text-xs text-text ${className}`.trim()}>
      {children}
    </p>
  )
}

export function ErrorMessage({ children, compact = false }) {
  return (
    <div
      role="alert"
      className={`flex items-start gap-2 rounded-card border border-danger/25 bg-danger-soft text-danger ${compact ? 'px-3 py-2 text-xs' : 'px-4 py-3 text-[13px]'}`}
    >
      <CircleAlert size={16} className="mt-px shrink-0" />
      <span className="min-w-0 break-words">{children}</span>
    </div>
  )
}

export function Spinner({ size = 15, label }) {
  return <LoaderCircle size={size} className="animate-spin" aria-label={label} aria-hidden={label ? undefined : true} />
}

const STATUS_STYLES = {
  queued: 'bg-warning-soft text-warning',
  running: 'bg-accent-soft text-accent',
  succeeded: 'bg-success-soft text-success',
  failed: 'bg-danger-soft text-danger',
  rolled_back: 'bg-warning-soft text-warning',
  superseded: 'bg-surface-soft text-muted',
  cancelled: 'bg-surface-soft text-muted',
}

export function StatusBadge({ status }) {
  const t = useT()
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ${STATUS_STYLES[status] ?? STATUS_STYLES.superseded}`}>
      <span className={`size-1.5 rounded-full bg-current ${status === 'running' ? 'animate-pulse' : ''}`} aria-hidden="true" />
      {t(`status.${status}`)}
    </span>
  )
}

export function MiniBadge({ children }) {
  return (
    <span className="inline-flex items-center rounded-full bg-accent-soft px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-accent">
      {children}
    </span>
  )
}

export function Skeleton({ lines = 3, label }) {
  return (
    <div className="flex flex-col gap-2.5" role="status" aria-label={label} aria-busy="true">
      {Array.from({ length: lines }, (_, index) => (
        <span
          key={index}
          className="block h-3.5 animate-pulse rounded-full bg-border-subtle"
          style={{ width: `${88 - (index % 3) * 18}%` }}
        />
      ))}
    </div>
  )
}

export function EmptyState({ icon, children, className = '' }) {
  return (
    <div className={`flex flex-col items-center gap-2 py-9 text-center text-[13px] text-muted ${className}`.trim()}>
      <span className="text-muted-soft">{icon}</span>
      <p className="m-0">{children}</p>
    </div>
  )
}

export function Kicker({ icon, children }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.11em] text-accent">
      {icon} {children}
    </span>
  )
}

export function DetailItem({ label, children }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-[10px] font-bold uppercase tracking-wider text-muted-soft">{label}</span>
      <span className="min-w-0 break-all text-xs text-text">{children}</span>
    </div>
  )
}
