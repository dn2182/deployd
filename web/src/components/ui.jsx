import { useState } from 'react'
import * as AlertDialog from '@radix-ui/react-alert-dialog'
import * as Tooltip from '@radix-ui/react-tooltip'

export function Button({ className = '', variant = 'secondary', size = 'default', ...props }) {
  return (
    <button
      type="button"
      className={`button button-${variant} button-${size} ${className}`.trim()}
      {...props}
    />
  )
}

export function TooltipButton({ label, className = '', children, ...props }) {
  return (
    <Tooltip.Provider delayDuration={300}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <button
            type="button"
            aria-label={label}
            className={`icon-button ${className}`.trim()}
            {...props}
          >
            {children}
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content className="tooltip-content" sideOffset={8}>
            {label}
            <Tooltip.Arrow className="tooltip-arrow" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
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
}) {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')

  const confirmed = confirmationValue === undefined || value === confirmationValue
  const changeOpen = (nextOpen) => {
    setOpen(nextOpen)
    if (!nextOpen) setValue('')
  }

  return (
    <AlertDialog.Root open={open} onOpenChange={changeOpen}>
      <AlertDialog.Trigger asChild>{trigger}</AlertDialog.Trigger>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="dialog-overlay" />
        <AlertDialog.Content className="dialog-content">
          <div className={`dialog-symbol ${destructive ? 'dialog-symbol-danger' : ''}`} aria-hidden="true">
            !
          </div>
          <AlertDialog.Title className="dialog-title">{title}</AlertDialog.Title>
          <AlertDialog.Description className="dialog-description">
            {description}
          </AlertDialog.Description>
          {confirmationValue !== undefined && (
            <label className="field-label dialog-confirmation">
              Type <strong>{confirmationValue}</strong> to confirm
              <input
                className="text-input"
                value={value}
                onChange={(event) => setValue(event.target.value)}
                autoComplete="off"
                autoFocus
              />
            </label>
          )}
          <div className="dialog-actions">
            <AlertDialog.Cancel asChild>
              <Button>Cancel</Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action asChild>
              <Button
                variant={destructive ? 'danger' : 'primary'}
                disabled={!confirmed}
                onClick={onConfirm}
              >
                {confirmLabel}
              </Button>
            </AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  )
}
