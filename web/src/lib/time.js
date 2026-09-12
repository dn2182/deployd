const UTC_PATTERN = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?$/

// Server timestamps are naive UTC strings; releases carry epoch seconds.
export function toDate(value) {
  if (value === null || value === undefined || value === '') return null
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
  if (typeof value === 'number') return new Date(value * 1000)
  const match = UTC_PATTERN.exec(value)
  if (match) {
    const [, year, month, day, hour, minute, second] = match.map(Number)
    return new Date(Date.UTC(year, month - 1, day, hour, minute, second))
  }
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

const UNITS = [
  ['year', 31536000],
  ['month', 2592000],
  ['week', 604800],
  ['day', 86400],
  ['hour', 3600],
  ['minute', 60],
]

export function formatRelative(date, language, now = Date.now()) {
  const seconds = Math.round((date.getTime() - now) / 1000)
  const formatter = new Intl.RelativeTimeFormat(language, { numeric: 'auto' })
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return formatter.format(Math.trunc(seconds / size), unit)
  }
  return formatter.format(seconds, 'second')
}

export function formatWhen(value, language, now = Date.now()) {
  const date = toDate(value)
  if (!date) return null
  return {
    iso: date.toISOString(),
    absolute: date.toLocaleString(language, { dateStyle: 'medium', timeStyle: 'short' }),
    relative: formatRelative(date, language, now),
  }
}

export function formatDuration(start, end) {
  const from = toDate(start)
  const to = toDate(end)
  if (!from || !to) return null
  const total = Math.max(0, Math.round((to.getTime() - from.getTime()) / 1000))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
  return minutes ? `${minutes}m ${seconds}s` : `${seconds}s`
}
