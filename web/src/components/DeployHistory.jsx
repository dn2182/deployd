import { useState } from 'react'
import { Activity, Search } from 'lucide-react'
import { useT } from '../i18n/index.js'
import { Button, EmptyState, Field, Panel, Skeleton, Spinner, inputClass } from './ui.jsx'
import { DEPLOY_STATUSES, deployKind } from '../lib/deploys.js'
import DeployRow from './DeployRow.jsx'

// Rows are newest first, so the previous successful artifact deploy of the
// same app is the next matching row further down the loaded list.
function previousShaOf(deploys, index) {
  const current = deploys[index]
  for (let cursor = index + 1; cursor < deploys.length; cursor += 1) {
    const candidate = deploys[cursor]
    if (candidate.app === current.app && candidate.status === 'succeeded' && deployKind(candidate) === 'artifact'
      && candidate.commit_sha !== current.commit_sha) return candidate.commit_sha
  }
  return null
}

export default function DeployHistory({ deploys, apps, filters, onFilters, hasMore, loadMore, loadingMore, loading, api, onChanged, revision }) {
  const t = useT()
  const [search, setSearch] = useState('')
  const needle = search.trim().toLowerCase()
  const appNames = Object.keys(apps ?? {}).sort()
  const rows = deploys
    .map((deploy, index) => ({ deploy, previousSha: previousShaOf(deploys, index) }))
    .filter(({ deploy }) => !needle || deploy.commit_sha.toLowerCase().startsWith(needle))

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Field label={t('history.filter_app')}>
          <select className={inputClass} value={filters.app} onChange={(event) => onFilters({ app: event.target.value })}>
            <option value="">{t('history.all_apps')}</option>
            {appNames.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </Field>
        <Field label={t('history.filter_status')}>
          <select className={inputClass} value={filters.status} onChange={(event) => onFilters({ status: event.target.value })}>
            <option value="">{t('history.all_statuses')}</option>
            {DEPLOY_STATUSES.map((status) => <option key={status} value={status}>{t(`status.${status}`)}</option>)}
          </select>
        </Field>
        <Field label={t('history.search_sha')}>
          <input className={`${inputClass} font-mono`} type="search" value={search} autoComplete="off" spellCheck={false}
            placeholder="a1b2c3" onChange={(event) => setSearch(event.target.value)} />
        </Field>
      </div>
      <Panel className="overflow-hidden">
        {loading ? (
          <div className="p-4"><Skeleton lines={4} label={t('history.loading')} /></div>
        ) : rows.length === 0 ? (
          <EmptyState icon={needle ? <Search size={22} /> : <Activity size={22} />}>
            {t(needle || filters.app || filters.status ? 'history.no_matches' : 'activity.empty')}
          </EmptyState>
        ) : (
          <ul className="m-0 flex list-none flex-col divide-y divide-border-subtle p-0">
            {rows.map(({ deploy, previousSha }) => (
              <DeployRow key={deploy.deploy_id} deploy={deploy} previousSha={previousSha}
                repository={apps?.[deploy.app]?.github_repository ?? null}
                api={api} onChanged={onChanged} revision={revision} />
            ))}
          </ul>
        )}
        {hasMore && !loading && (
          <div className="flex justify-center border-t border-border-subtle p-3">
            <Button size="small" disabled={loadingMore} onClick={loadMore}>
              {loadingMore && <Spinner size={13} />}{t('history.load_more')}
            </Button>
          </div>
        )}
      </Panel>
    </div>
  )
}
