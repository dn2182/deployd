import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { ArrowLeft, Check, Copy, Pencil, Search, Server, Trash2, UserPlus, Users } from 'lucide-react'
import { copyText } from '../lib/clipboard.js'
import { useT } from '../i18n/index.js'
import { useToast } from '../hooks/useToast.js'
import { ConfirmDialog, Modal } from './ConfirmDialog.jsx'
import When from './When.jsx'
import { Avatar, Button, EmptyState, ErrorMessage, Field, Help, IconButton, MiniBadge, Skeleton, Spinner, Switch, inputClass } from './ui.jsx'

const USERNAME = /^[a-z0-9][a-z0-9._-]{1,31}$/
const SEARCH_FROM = 7

function AppSwitches({ apps, selected, onChange, disabled }) {
  const t = useT()
  const [query, setQuery] = useState('')
  const names = Object.keys(apps).sort((a, b) => a.localeCompare(b))
  const needle = query.trim().toLowerCase()
  const visible = names.filter((name) => [name, apps[name].github_repository].some((value) => value?.toLowerCase().includes(needle)))
  const toggle = (name, on) => onChange(on ? [...selected, name] : selected.filter((item) => item !== name))
  const all = names.length > 0 && selected.length === names.length

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-semibold text-text-strong">{t('team.apps')}</span>
        <span className="flex items-center gap-3 text-[11px] text-muted">
          {t('team.selected', { count: selected.length, total: names.length })}
          {names.length > 1 && (
            <button type="button" disabled={disabled} onClick={() => onChange(all ? [] : names)}
              className="font-semibold text-accent hover:underline focus-visible:outline-none focus-visible:underline disabled:opacity-50">
              {t(all ? 'team.select_none' : 'team.select_all')}
            </button>
          )}
        </span>
      </div>
      {names.length >= SEARCH_FROM && (
        <label className="flex items-center gap-2 rounded-card border border-border-subtle bg-input px-3 text-muted focus-within:border-accent/45 focus-within:ring-2 focus-within:ring-accent/25">
          <Search size={15} aria-hidden="true" />
          <input type="search" aria-label={t('team.search_apps')} placeholder={t('team.search_apps')} value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="h-9 w-full min-w-0 border-0 bg-transparent text-xs text-text outline-none placeholder:text-muted-soft" />
        </label>
      )}
      {names.length === 0 ? <Help>{t('team.no_apps')}</Help> : (
        <ul className="m-0 flex max-h-72 list-none flex-col divide-y divide-border-subtle overflow-auto rounded-card border border-border-subtle bg-surface-soft p-0">
          {visible.map((name) => {
            const on = selected.includes(name)
            return (
              <li key={name} className={`flex items-center gap-3 px-3 py-2.5 transition-colors ${on ? 'bg-accent-soft/60' : ''}`}>
                <Server size={16} aria-hidden="true" className={`shrink-0 ${on ? 'text-accent' : 'text-muted'}`} />
                <span className="flex min-w-0 flex-1 flex-col">
                  <strong className="truncate text-xs font-semibold text-text-strong">{name}</strong>
                  <small className="truncate text-[11px] text-muted">{apps[name].github_repository || t('registry.no_repository')}</small>
                </span>
                <Switch checked={on} disabled={disabled} label={t('team.access_to', { name })} onChange={(value) => toggle(name, value)} />
              </li>
            )
          })}
          {visible.length === 0 && <li className="px-3 py-4 text-center text-xs text-muted">{t('registry.no_matches')}</li>}
        </ul>
      )}
    </div>
  )
}

// The one moment a generated password is visible: copy it now or reset it later.
function PasswordReveal({ username, password, onDone }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  const timer = useRef(null)
  useEffect(() => () => clearTimeout(timer.current), [])
  const copy = async () => {
    const ok = await copyText(password)
    setCopied(ok)
    clearTimeout(timer.current)
    if (ok) timer.current = setTimeout(() => setCopied(false), 1800)
  }
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <Avatar name={username} />
        <div>
          <strong className="block text-sm text-text-strong">{username}</strong>
          <span className="text-xs text-muted">{t('team.password_once')}</span>
        </div>
      </div>
      <code className="block rounded-card border border-border-subtle bg-input p-3 font-mono text-sm break-all text-text-strong select-all">{password}</code>
      <Help>{t('team.password_share')}</Help>
      <div className="flex flex-wrap justify-end gap-2">
        <Button onClick={copy}>{copied ? <Check size={15} /> : <Copy size={15} />}{t(copied ? 'app.copied' : 'app.copy')}</Button>
        <Button variant="primary" onClick={onDone}>{t('team.done')}</Button>
      </div>
    </div>
  )
}

function MemberEditor({ member, apps, api, onBack, onSaved }) {
  const t = useT()
  const creating = !member
  const [username, setUsername] = useState('')
  const [generate, setGenerate] = useState(true)
  const [password, setPassword] = useState('')
  const [selected, setSelected] = useState(member?.apps ?? [])
  const [active, setActive] = useState(member ? !member.disabled : true)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)

  const save = async (event) => {
    event.preventDefault()
    if (pending) return
    const name = username.trim().toLowerCase()
    if (creating && !USERNAME.test(name)) { setError(t('team.username_rule')); return }
    if (creating && !generate && [...password].length < 12) { setError(t('password.length')); return }
    setPending(true)
    setError(null)
    try {
      const result = creating
        ? await api.createMember({ username: name, apps: selected, ...(generate ? { generate_password: true } : { password }) })
        : await api.updateMember(member.username, { apps: selected, disabled: !active })
      onSaved({ username: creating ? name : member.username, password: result.password ?? null, created: creating })
    } catch (failure) {
      setError(failure.message)
    } finally {
      setPending(false)
    }
  }

  const reset = async () => {
    setPending(true)
    setError(null)
    try {
      const result = await api.updateMember(member.username, { generate_password: true })
      onSaved({ username: member.username, password: result.password, created: false })
    } catch (failure) {
      setError(failure.message)
    } finally {
      setPending(false)
    }
  }

  return (
    <form onSubmit={save} className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <IconButton label={t('team.back')} onClick={onBack} disabled={pending}><ArrowLeft size={16} /></IconButton>
        <h3 className="m-0 text-base font-semibold text-text-strong">{creating ? t('team.add') : t('team.edit', { name: member.username })}</h3>
      </div>
      {creating ? (
        <>
          <Field label={t('signin.username')} help={t('team.username_rule')}>
            <input className={inputClass} value={username} autoComplete="off" autoCapitalize="none" spellCheck={false}
              placeholder="maria" disabled={pending} autoFocus onChange={(event) => setUsername(event.target.value)} />
          </Field>
          <div className="flex items-center justify-between gap-3 rounded-card border border-border-subtle bg-surface-soft px-3 py-2.5">
            <span className="flex flex-col">
              <strong className="text-xs font-semibold text-text-strong">{t('team.generate')}</strong>
              <small className="text-[11px] text-muted">{t('team.generate_help')}</small>
            </span>
            <Switch checked={generate} disabled={pending} label={t('team.generate')} onChange={setGenerate} />
          </div>
          {!generate && (
            <Field label={t('signin.password')} help={t('password.length')}>
              <input className={inputClass} type="password" autoComplete="new-password" value={password}
                disabled={pending} onChange={(event) => setPassword(event.target.value)} />
            </Field>
          )}
        </>
      ) : (
        <div className="flex items-center justify-between gap-3 rounded-card border border-border-subtle bg-surface-soft px-3 py-2.5">
          <span className="flex flex-col">
            <strong className="text-xs font-semibold text-text-strong">{t('team.can_sign_in')}</strong>
            <small className="text-[11px] text-muted">{t('team.can_sign_in_help')}</small>
          </span>
          <Switch checked={active} disabled={pending} label={t('team.can_sign_in')} onChange={setActive} />
        </div>
      )}
      <AppSwitches apps={apps} selected={selected} onChange={setSelected} disabled={pending} />
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
      <div className="flex flex-wrap items-center justify-between gap-2">
        {creating ? <span /> : (
          <ConfirmDialog
            trigger={<Button disabled={pending}>{t('team.reset_password')}</Button>}
            title={t('team.reset_title', { name: member.username })}
            description={t('team.reset_description')}
            confirmLabel={t('team.reset_password')}
            onConfirm={reset}
          />
        )}
        <span className="flex gap-2">
          <Button onClick={onBack} disabled={pending}>{t('common.cancel')}</Button>
          <Button type="submit" variant="primary" disabled={pending}>
            {pending && <Spinner size={14} />}{t(creating ? 'team.create' : 'app.save_changes')}
          </Button>
        </span>
      </div>
    </form>
  )
}

export default function TeamDialog({ api, apps, onClose }) {
  const t = useT()
  const toast = useToast()
  const id = useId()
  const [members, setMembers] = useState(null)
  const [error, setError] = useState(null)
  const [editing, setEditing] = useState(null)
  const [reveal, setReveal] = useState(null)

  const load = useCallback(async () => {
    try {
      setMembers(await api.members())
      setError(null)
    } catch (failure) {
      setError(failure.message)
    }
  }, [api])
  useEffect(() => {
    const timer = setTimeout(load, 0)
    return () => clearTimeout(timer)
  }, [load])

  const saved = async (result) => {
    setEditing(null)
    await load()
    if (result.password) setReveal(result)
    else toast({ kind: 'success', message: t(result.created ? 'team.created' : 'team.saved', { name: result.username }) })
  }
  const remove = async (username) => {
    await api.deleteMember(username)
    toast({ kind: 'success', message: t('team.removed', { name: username }) })
    await load()
  }

  return (
    <Modal size="wide" labelledBy={`${id}-title`} describedBy={`${id}-description`} onClose={onClose}>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div aria-hidden="true" className="grid size-10 shrink-0 place-items-center rounded-card bg-accent-soft text-accent"><Users size={20} /></div>
          <div>
            <h2 id={`${id}-title`} className="m-0 text-lg font-semibold tracking-tight text-text-strong">{t('team.title')}</h2>
            <p id={`${id}-description`} className="mt-1 mb-0 max-w-md text-xs leading-relaxed text-muted">{t('team.description')}</p>
          </div>
        </div>
        {!editing && !reveal && (
          <Button variant="primary" onClick={() => setEditing({ member: null })}><UserPlus size={15} />{t('team.add')}</Button>
        )}
      </div>

      {reveal ? (
        <PasswordReveal username={reveal.username} password={reveal.password} onDone={() => setReveal(null)} />
      ) : editing ? (
        <MemberEditor key={editing.member?.username ?? 'new'} member={editing.member} apps={apps ?? {}} api={api}
          onBack={() => setEditing(null)} onSaved={saved} />
      ) : (
        <>
          {error && <ErrorMessage compact>{error}</ErrorMessage>}
          {!members && !error && <Skeleton lines={3} label={t('team.loading')} />}
          {members?.length === 0 && <EmptyState icon={<Users size={24} />}>{t('team.empty')}</EmptyState>}
          {members?.length > 0 && (
            <ul className="m-0 flex list-none flex-col gap-2 p-0">
              {members.map((member) => (
                <li key={member.username} className="flex flex-wrap items-center gap-3 rounded-card border border-border-subtle bg-surface-soft p-3">
                  <Avatar name={member.username} muted={member.disabled} />
                  <div className="flex min-w-0 flex-1 flex-col gap-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <strong className="text-sm font-semibold text-text-strong">{member.username}</strong>
                      {member.disabled && <span className="rounded-full bg-danger-soft px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-danger">{t('team.disabled')}</span>}
                    </span>
                    <span className="flex flex-wrap items-center gap-1.5">
                      {member.apps.length === 0 && <span className="text-[11px] text-muted">{t('team.no_access')}</span>}
                      {member.apps.slice(0, 4).map((name) => <MiniBadge key={name}>{name}</MiniBadge>)}
                      {member.apps.length > 4 && <span className="text-[11px] text-muted">{t('team.more', { count: member.apps.length - 4 })}</span>}
                    </span>
                    <span className="text-[11px] text-muted">
                      {member.last_login_at ? <>{t('team.last_seen')} <When value={member.last_login_at} /></> : t('team.never_signed_in')}
                    </span>
                  </div>
                  <div className="flex shrink-0 gap-1.5">
                    <IconButton label={t('team.edit', { name: member.username })} onClick={() => setEditing({ member })}><Pencil size={16} /></IconButton>
                    <ConfirmDialog
                      trigger={<IconButton label={t('team.remove', { name: member.username })} danger><Trash2 size={16} /></IconButton>}
                      title={t('team.remove_title', { name: member.username })}
                      description={t('team.remove_description')}
                      confirmLabel={t('team.remove_confirm')}
                      destructive
                      onConfirm={() => remove(member.username)}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-5 flex justify-end">
            <Button onClick={onClose}>{t('help.close')}</Button>
          </div>
        </>
      )}
    </Modal>
  )
}
