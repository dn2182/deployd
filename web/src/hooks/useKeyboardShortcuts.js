import { useEffect } from 'react'

const EDITABLE = new Set(['INPUT', 'TEXTAREA', 'SELECT'])

function isTyping(target) {
  return target instanceof HTMLElement && (EDITABLE.has(target.tagName) || target.isContentEditable)
}

export function useKeyboardShortcuts({ onSearch, onRefresh, onClear }) {
  useEffect(() => {
    const handle = (event) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return
      if (isTyping(event.target)) return
      if (event.key === '/') {
        event.preventDefault()
        onSearch?.()
      } else if (event.key === 'r') {
        onRefresh?.()
      } else if (event.key === 'Escape') {
        onClear?.()
      }
    }
    document.addEventListener('keydown', handle)
    return () => document.removeEventListener('keydown', handle)
  }, [onSearch, onRefresh, onClear])
}
