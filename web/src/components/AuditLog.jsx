import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, ScrollText } from 'lucide-react'
import { useT } from '../i18n/index.js'
import When from './When.jsx'
import { Button, EmptyState, ErrorMessage, Panel, Skeleton, Spinner } from './ui.jsx'

const PAGE = 50

export default function AuditLog({ api }) {
  const t = useT()
  const [entries, setEntries] = useState(null)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const requestId = useRef(0)

  const load = useCallback(async (offset = 0) => {
    const id = ++requestId.current
    setLoading(true)
    try {
      const page = await api.audit({ limit: PAGE, offset })
      if (id !== requestId.current) return
      setEntries((old) => (offset === 0 ? page : [...(old ?? []), ...page]))
      setHasMore(page.length >= PAGE)
      setError(null)
    } catch (requestError) {
      if (id === requestId.current) setError(requestError.message)
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }, [api])

  useEffect(() => {
    const timer = setTimeout(() => load(0), 0)
    return () => clearTimeout(timer)
  }, [load])

  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <Button size="small" disabled={loading} onClick={() => load(0)}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : undefined} /> {t('activity.refresh')}
        </Button>
      </div>
      {error && <ErrorMessage>{error}</ErrorMessage>}
      <Panel className="overflow-hidden">
        {!entries && !error ? (
          <div className="p-4"><Skeleton lines={4} label={t('audit.loading')} /></div>
        ) : entries?.length === 0 ? (
          <EmptyState icon={<ScrollText size={22} />}>{t('audit.empty')}</EmptyState>
        ) : entries && (
          <ul className="m-0 flex list-none flex-col divide-y divide-border-subtle p-0">
            {entries.map((entry) => (
              <li key={entry.id} className="flex flex-col gap-1 px-3 py-3 text-xs sm:px-4">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <strong className="font-semibold text-text-strong">{entry.action}</strong>
                  {entry.target && <code className="font-mono text-muted">{entry.target}</code>}
                  <span className="text-muted">{t('audit.by', { actor: entry.actor })}</span>
                  <When value={entry.at} className="text-[11px] text-muted" />
                </div>
                {entry.detail && (
                  <pre className="m-0 max-h-40 overflow-auto rounded bg-input p-2 font-mono text-[11px] whitespace-pre-wrap break-words text-muted">
                    {typeof entry.detail === 'string' ? entry.detail : JSON.stringify(entry.detail, null, 2)}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        )}
        {hasMore && entries && (
          <div className="flex justify-center border-t border-border-subtle p-3">
            <Button size="small" disabled={loading} onClick={() => load(entries.length)}>
              {loading && <Spinner size={13} />}{t('history.load_more')}
            </Button>
          </div>
        )}
      </Panel>
    </div>
  )
}
