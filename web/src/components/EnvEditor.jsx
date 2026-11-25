import { useCallback, useEffect, useRef, useState } from 'react'
import { Eye, EyeOff, Plus, Trash2, TriangleAlert } from 'lucide-react'
import { useT } from '../i18n/index.js'
import { useToast } from '../hooks/useToast.js'
import { ConfirmDialog } from './ConfirmDialog.jsx'
import { Button, Checkbox, ErrorMessage, Help, IconButton, Notice, Skeleton, inputClass } from './ui.jsx'

const ASSIGNMENT = /^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_.-]*)(\s*=)(.*)$/
let nextRowId = 0

function KeyList({ label, keys, tone = 'text-text' }) {
  if (!keys?.length) return null
  return (
    <div className="flex flex-col gap-1">
      <span className={`text-xs font-medium ${tone}`}>{label}</span>
      <code className="max-h-28 overflow-auto break-all rounded-card border border-border bg-surface px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-text">
        {keys.join(', ')}
      </code>
    </div>
  )
}

function Caution({ children }) {
  return (
    <p role="alert" className="m-0 flex items-start gap-2 rounded-card border border-danger/25 bg-danger-soft px-3 py-2 text-xs text-danger">
      <TriangleAlert size={15} className="mt-px shrink-0" />
      <span className="min-w-0 break-words">{children}</span>
    </p>
  )
}

// What the server says a save would do. Key names only: the server never returns values here.
function Review({ review, t }) {
  const touched = new Set([...review.added, ...review.changed])
  const unquoted = review.unquoted.filter((key) => touched.has(key))
  const untouched = review.unquoted.length - unquoted.length
  const quiet = !review.added.length && !review.changed.length && !review.removed.length
  return (
    <>
      <p className="m-0 text-xs text-text">{t('env.review_count', { before: review.keys_before, after: review.keys_after })}</p>
      {review.removed.length > 0 && <Caution>{t('env.review_removed_warning', { count: review.removed.length })}</Caution>}
      <KeyList label={t('env.review_removed')} keys={review.removed} tone="text-danger" />
      <KeyList label={t('env.review_changed')} keys={review.changed} />
      <KeyList label={t('env.review_added')} keys={review.added} />
      {quiet && <Help>{t('env.review_quiet')}</Help>}
      {unquoted.length > 0 && <Caution>{t('env.review_unquoted', { keys: unquoted.join(', ') })}</Caution>}
      {untouched > 0 && <Help>{t('env.review_unquoted_existing', { count: untouched })}</Help>}
      {review.duplicates.length > 0 && <Caution>{t('env.review_duplicates', { keys: review.duplicates.join(', ') })}</Caution>}
    </>
  )
}

// Lines that are not assignments (comments, blanks, continuation lines) are kept verbatim.
function toRows(text) {
  const lines = text.split('\n')
  if (lines.at(-1) === '') lines.pop()
  return lines.map((raw) => {
    const match = raw.trimStart().startsWith('#') ? null : raw.match(ASSIGNMENT)
    const id = ++nextRowId
    return match
      ? { id, pair: true, prefix: match[1], key: match[2], separator: match[3], value: match[4] }
      : { id, pair: false, raw }
  })
}

function toText(rows) {
  const lines = rows.map((row) => (row.pair ? `${row.prefix}${row.key}${row.separator}${row.value}` : row.raw))
  return lines.length ? `${lines.join('\n')}\n` : ''
}

export default function EnvEditor({ name, spec, call, onChanged, revision = 0 }) {
  const t = useT()
  const toast = useToast()
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [file, setFile] = useState(null)
  const [rows, setRows] = useState([])
  const [text, setText] = useState(null)
  const [revealed, setRevealed] = useState(() => new Set())
  const static_ = spec?.restart?.command?.[0] === '/usr/bin/test'
  const [restart, setRestart] = useState(!static_)
  const [pending, setPending] = useState(false)
  const [review, setReview] = useState(null)
  const requestId = useRef(0)
  const base = `/admin/apps/${encodeURIComponent(name)}/env`

  const loadStatus = useCallback(async () => {
    const id = ++requestId.current
    try {
      const result = await call(base)
      if (id !== requestId.current) return
      setStatus(result)
      setError(null)
    } catch (err) {
      if (id !== requestId.current) return
      setStatus(null)
      setError(err.message)
    }
  }, [base, call])

  useEffect(() => {
    const timer = setTimeout(loadStatus, 0)
    return () => clearTimeout(timer)
  }, [loadStatus, revision])

  // Values leave memory as soon as the editor closes.
  const close = () => {
    setFile(null)
    setRows([])
    setText(null)
    setRevealed(new Set())
  }

  const open = async () => {
    setPending(true)
    setError(null)
    try {
      const result = await call(`${base}/content`)
      setFile({ sha256: result.sha256, original: result.content })
      setRows(toRows(result.content))
      setText(null)
      setRevealed(new Set())
    } catch (err) {
      setError(err.message)
    } finally {
      setPending(false)
    }
  }

  const current = text ?? toText(rows)
  const dirty = Boolean(file) && current !== file.original
  const update = (id, change) => setRows((old) => old.map((row) => (row.id === id ? { ...row, ...change } : row)))
  const toggle = (id) => setRevealed((old) => {
    const next = new Set(old)
    if (!next.delete(id)) next.add(id)
    return next
  })
  const add = () => {
    const id = ++nextRowId
    setRows((old) => [...old, { id, pair: true, prefix: '', key: '', separator: '=', value: '' }])
    setRevealed((old) => new Set(old).add(id))
  }

  const finish = async (result, message) => {
    close()
    toast({ kind: 'success', message: t(result.saved ? (result.deploy_id ? 'env.saved_restart' : message) : 'env.unchanged') })
    await loadStatus()
    if (result.deploy_id) onChanged()
  }
  // The server works out what would change, so the list shown is the one it enforces.
  const preview = async () => {
    setPending(true)
    setError(null)
    try {
      setReview(await call(`${base}/preview`, {
        method: 'POST', body: JSON.stringify({ content: current, expected_sha256: file.sha256 }),
      }))
    } catch (err) {
      setError(err.message)
      return false
    } finally {
      setPending(false)
    }
  }
  const save = async () => {
    const result = await call(base, {
      method: 'PUT',
      body: JSON.stringify({ content: current, expected_sha256: file.sha256, confirm_removed: review.removed, restart }),
    })
    await finish(result, 'env.saved')
  }
  const removing = (review?.removed.length ?? 0) > 0
  const restore = async () => {
    const result = await call(`${base}/restore`, { method: 'POST', body: JSON.stringify({ confirm: name, restart }) })
    await finish(result, 'env.restored')
  }

  const restartBox = (
    <div className="flex flex-col gap-1">
      <Checkbox label={t('env.restart')} checked={restart} onChange={(event) => setRestart(event.target.checked)} />
      {restart && !static_ && <Help>{t(spec?.health?.url ? 'env.restart_help' : 'env.restart_no_health')}</Help>}
    </div>
  )

  return (
    <section className="flex flex-col gap-3" aria-label={t('env.title')}>
      <Help>{t('env.help')}</Help>
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
      {!status && !error && <Skeleton lines={2} label={t('releases.loading')} />}
      {status && (
        <div className="flex flex-col gap-1 text-xs text-text">
          <code className="break-all font-mono text-text-strong">{status.path}</code>
          {status.exists && (
            <span className="text-muted">
              {t('env.details', { owner: status.owner, group: status.group, mode: status.mode })}
              {status.backups > 0 && ` ${t('env.backups', { count: status.backups })}`}
            </span>
          )}
        </div>
      )}
      {status && !status.available && <Notice>{t('env.unavailable')}</Notice>}
      {status?.available && !status.exists && <Notice>{t('env.missing')}</Notice>}

      {status?.exists && !file && (
        <>
          {status.backups > 0 && restartBox}
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" disabled={pending} onClick={open}>{t('env.load')}</Button>
            {status.backups > 0 && <ConfirmDialog
              trigger={<Button disabled={pending}>{t('env.restore')}</Button>}
              title={t('env.restore_title')}
              description={t('env.restore_description')}
              confirmationValue={name}
              confirmationLabel={t('website.confirm_name', { name })}
              confirmLabel={t('env.restore')}
              onConfirm={restore}
            />}
          </div>
        </>
      )}

      {file && (
        <>
          {text === null ? (
            <ul className="m-0 flex list-none flex-col gap-2 p-0">
              {rows.map((row) => (row.pair ? (
                <li key={row.id} className="grid grid-cols-1 items-center gap-2 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_auto]">
                  <input className={`${inputClass} font-mono`} value={row.key} aria-label={t('env.key')}
                    spellCheck={false} autoComplete="off" placeholder="NAME"
                    onChange={(event) => update(row.id, { key: event.target.value })} />
                  <input className={`${inputClass} font-mono`} value={row.value}
                    type={revealed.has(row.id) ? 'text' : 'password'} autoComplete="new-password" spellCheck={false}
                    aria-label={t('env.value_of', { key: row.key || t('env.key') })}
                    onChange={(event) => update(row.id, { value: event.target.value })} />
                  <div className="flex gap-1.5">
                    <IconButton label={t(revealed.has(row.id) ? 'env.hide' : 'env.show', { key: row.key })} onClick={() => toggle(row.id)}>
                      {revealed.has(row.id) ? <EyeOff size={16} /> : <Eye size={16} />}
                    </IconButton>
                    <IconButton label={t('env.remove', { key: row.key })} danger
                      onClick={() => setRows((old) => old.filter((item) => item.id !== row.id))}>
                      <Trash2 size={16} />
                    </IconButton>
                  </div>
                </li>
              ) : row.raw.trim() && (
                <li key={row.id} className="break-all font-mono text-[11px] text-muted">{row.raw}</li>
              )))}
            </ul>
          ) : (
            <textarea className={`${inputClass} min-h-64 font-mono`} value={text} spellCheck={false}
              aria-label={t('env.text_label')} onChange={(event) => setText(event.target.value)} />
          )}
          <div className="flex flex-wrap gap-2">
            {text === null && <Button onClick={add}><Plus size={14} />{t('env.add')}</Button>}
            {text === null
              ? <Button onClick={() => setText(toText(rows))}>{t('env.as_text')}</Button>
              : <Button onClick={() => { setRows(toRows(text)); setText(null); setRevealed(new Set()) }}>{t('env.as_fields')}</Button>}
          </div>
          {restartBox}
          <div className="flex flex-wrap gap-2">
            <ConfirmDialog
              trigger={<Button variant="primary" disabled={!dirty || pending}>{t('env.save')}</Button>}
              title={t('env.save_title')}
              description={t('env.save_description')}
              confirmLabel={t(removing ? 'env.save_removing' : 'env.save')}
              destructive={removing}
              confirmationValue={removing ? name : undefined}
              confirmationLabel={removing ? t('env.review_confirm', { name }) : undefined}
              onOpen={preview}
              onConfirm={save}
            >
              {review && <Review review={review} t={t} />}
            </ConfirmDialog>
            <Button onClick={close}>{t('env.close')}</Button>
          </div>
        </>
      )}
    </section>
  )
}
