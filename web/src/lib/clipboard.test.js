import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyText } from './clipboard.js'

describe('copyText', () => {
  afterEach(() => {
    delete navigator.clipboard
    delete document.execCommand
  })

  it('uses the async clipboard when available', async () => {
    const writeText = vi.fn().mockResolvedValue()
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    expect(await copyText('secret-value')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('secret-value')
  })

  it('falls back to execCommand on plain HTTP and copies the whole text', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
    let selected = null
    document.execCommand = vi.fn(() => {
      const area = document.activeElement
      selected = area.value.slice(area.selectionStart, area.selectionEnd)
      return true
    })
    const long = 'line\n'.repeat(500)
    expect(await copyText(long)).toBe(true)
    expect(document.execCommand).toHaveBeenCalledWith('copy')
    expect(selected).toBe(long)
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('reports failure when neither path works', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    })
    document.execCommand = vi.fn(() => false)
    expect(await copyText('x')).toBe(false)
  })
})
