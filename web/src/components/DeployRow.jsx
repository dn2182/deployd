import { useEffect, useState } from 'react'
import { Ban, Check, ChevronDown, ChevronRight, ExternalLink, GitCompare, RotateCcw, Undo2, X } from 'lucide-react'
import { useT } from '../i18n/index.js'
import { deployKind } from '../lib/deploys.js'
import { formatDuration } from '../lib/time.js'
import { ConfirmDialog } from './ConfirmDialog.jsx'
import { useToast } from '../hooks/useToast.js'
import When from './When.jsx'
import { ErrorMessage, IconButton, MiniBadge, Skeleton, Spinner, StatusBadge } from './ui.jsx'

const STEP_ICON = {
  succeeded: <Check size={13} />,
  failed: <X size={13} />,
  running: <Spinner size={13} />,
  skipped: <span aria-hidden="true">-</span>,
}
const STEP_COLOR = { succeeded: 'text-success', failed: 'text-danger', running: 'text-accent' }

export default function DeployRow({ deploy, repository, previousSha, api, onChanged, revision }) {
  const t = useT()
  const toast = useToast()
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)
  const kind = deployKind(deploy)
  const sha = deploy.commit_sha.slice(0, 12)
  const duration = formatDuration(deploy.created_at, deploy.finished_at)

  useEffect(() => {
    if (!expanded) return
    let active = true
    api.deploy(deploy.deploy_id)
      .then((result) => {
        if (!active) return
        setDetail(result)
        setError(null)
      })
      .catch((requestError) => {
        if (active) setError(requestError.message)
      })
    return () => { active = false }
  }, [expanded, api, deploy.deploy_id, deploy.status, revision])

  const toggle = () => {
    if (expanded) {
      setDetail(null)
      setError(null)
    }
    setExpanded(!expanded)
  }
  const loading = expanded && !detail && !error

  const run = async (request, message) => {
    await request()
    toast({ kind: 'info', message })
    onChanged()
  }
  const redeploy = () => run(() => api.redeploy(deploy.deploy_id), t('deploy.redeploy_toast', { name: deploy.app }))
  const cancel = () => run(() => api.cancelDeploy(deploy.deploy_id), t('deploy.cancel_toast', { name: deploy.app }))
  const rollback = () => run(() => api.activateRelease(deploy.app, 'previous'), t('deploy.rollback_toast', { name: deploy.app }))

  const commitUrl = repository && kind === 'artifact' ? `https://github.com/${repository}/commit/${deploy.commit_sha}` : null
  const compareUrl = repository && previousSha ? `https://github.com/${repository}/compare/${previousSha}...${deploy.commit_sha}` : null
  const canRollBack = kind === 'artifact' && ['failed', 'rolled_back'].includes(deploy.status)

  return (
    <li className={`flex flex-col gap-3 px-3 py-3 sm:px-4 ${expanded ? 'bg-surface-soft' : ''}`}>
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <button type="button" aria-expanded={expanded} onClick={toggle}
          className="flex min-w-0 items-center gap-2.5 rounded-card text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40">
          <span className="shrink-0 text-muted" aria-hidden="true">
            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          </span>
          <span className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
            <strong className="text-[13px] font-semibold text-text-strong">{deploy.app}</strong>
            {kind === 'artifact' && <code className="font-mono text-xs text-muted">{sha}</code>}
            <MiniBadge>{t(`deploy.kind_${kind}`)}</MiniBadge>
          </span>
        </button>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pl-6 text-[11px] text-muted md:pl-0">
          <When value={deploy.created_at} />
          {duration && <span className="tabular-nums" title={t('deploy.duration')}>{duration}</span>}
          <StatusBadge status={deploy.status} />
          <span className="flex items-center gap-1">
            {commitUrl && (
              <a href={commitUrl} target="_blank" rel="noopener noreferrer" title={t('deploy.open_commit')} aria-label={t('deploy.open_commit')}
                className="inline-grid size-9 place-items-center rounded-card text-muted hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40">
                <ExternalLink size={15} />
              </a>
            )}
            {compareUrl && (
              <a href={compareUrl} target="_blank" rel="noopener noreferrer" title={t('deploy.compare')} aria-label={t('deploy.compare')}
                className="inline-grid size-9 place-items-center rounded-card text-muted hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40">
                <GitCompare size={15} />
              </a>
            )}
            {deploy.status === 'queued' && (
              <ConfirmDialog
                trigger={<IconButton label={t('deploy.cancel', { name: deploy.app })} danger><Ban size={15} /></IconButton>}
                title={t('deploy.cancel_title', { name: deploy.app })}
                description={t('deploy.cancel_description')}
                confirmLabel={t('deploy.cancel_confirm')}
                destructive
                onConfirm={cancel}
              />
            )}
            {kind === 'artifact' && (
              <ConfirmDialog
                trigger={<IconButton label={t('deploy.redeploy', { name: deploy.app })}><RotateCcw size={15} /></IconButton>}
                title={t('deploy.redeploy_title', { name: deploy.app })}
                description={t('deploy.redeploy_description', { commit: sha })}
                confirmLabel={t('deploy.redeploy_confirm')}
                onConfirm={redeploy}
              />
            )}
            {canRollBack && (
              <ConfirmDialog
                trigger={<IconButton label={t('deploy.rollback', { name: deploy.app })}><Undo2 size={15} /></IconButton>}
                title={t('deploy.rollback_title', { name: deploy.app })}
                description={t('deploy.rollback_description')}
                confirmLabel={t('deploy.rollback_confirm')}
                destructive
                onConfirm={rollback}
              />
            )}
          </span>
        </div>
      </div>

      {expanded && (
        <div className="flex flex-col gap-2 rounded-card border border-border-subtle bg-surface p-3 sm:ml-6">
          {loading && <Skeleton lines={3} label={t('deploy.loading_steps')} />}
          {detail?.steps.map((step, index) => (
            <div className="flex gap-2.5" key={`${step.step}-${index}`}>
              <span className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-surface-soft ${STEP_COLOR[step.status] ?? 'text-muted'}`}>
                {STEP_ICON[step.status] ?? <span aria-hidden="true">·</span>}
              </span>
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <strong className="text-xs font-semibold text-text-strong">{step.step}</strong>
                {step.output && (
                  <pre className="m-0 max-h-64 overflow-auto rounded bg-input p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words text-muted">{step.output}</pre>
                )}
              </div>
            </div>
          ))}
          {detail && <p className="m-0 text-[11px] text-muted-soft">{t('deploy.triggered_by', { name: deploy.triggered_by })}</p>}
          {error && <ErrorMessage compact>{error}</ErrorMessage>}
        </div>
      )}
    </li>
  )
}
