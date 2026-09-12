import { useLanguage } from '../i18n/index.js'
import { formatWhen } from '../lib/time.js'

export default function When({ value, className = '' }) {
  const { language } = useLanguage()
  const when = formatWhen(value, language)
  if (!when) return null
  return (
    <time dateTime={when.iso} title={when.iso} className={`inline-flex flex-wrap gap-x-1.5 tabular-nums ${className}`.trim()}>
      <span>{when.absolute}</span>
      <span className="text-muted-soft">({when.relative})</span>
    </time>
  )
}
