import { describe, expect, it } from 'vitest'
import { formatDuration, formatRelative, formatWhen, toDate } from './time.js'

describe('time helpers', () => {
  it('parses naive server timestamps as UTC and epoch seconds as dates', () => {
    expect(toDate('2026-08-28 04:00:00').toISOString()).toBe('2026-08-28T04:00:00.000Z')
    expect(toDate(1_700_000_000).toISOString()).toBe('2023-11-14T22:13:20.000Z')
    expect(toDate(null)).toBeNull()
    expect(toDate('not a date')).toBeNull()
  })

  it('renders a relative label in the requested language', () => {
    const now = Date.UTC(2026, 7, 28, 4, 10, 0)
    expect(formatRelative(new Date('2026-08-28T04:07:00Z'), 'en', now)).toBe('3 minutes ago')
    expect(formatRelative(new Date('2026-08-28T04:07:00Z'), 'es', now)).toBe('hace 3 minutos')
    expect(formatRelative(new Date('2026-08-26T04:10:00Z'), 'en', now)).toBe('2 days ago')
  })

  it('formats iso, absolute, and relative parts together', () => {
    const when = formatWhen('2026-08-28 04:00:00', 'en', Date.UTC(2026, 7, 28, 5, 0, 0))
    expect(when.iso).toBe('2026-08-28T04:00:00.000Z')
    expect(when.relative).toBe('1 hour ago')
    expect(when.absolute).toContain('2026')
    expect(formatWhen(undefined, 'en')).toBeNull()
  })

  it('formats deploy durations compactly', () => {
    expect(formatDuration('2026-08-28 04:00:00', '2026-08-28 04:00:30')).toBe('30s')
    expect(formatDuration('2026-08-28 04:00:00', '2026-08-28 04:02:05')).toBe('2m 5s')
    expect(formatDuration('2026-08-28 04:00:00', '2026-08-28 05:30:00')).toBe('1h 30m')
    expect(formatDuration('2026-08-28 04:00:00', null)).toBeNull()
  })
})
