import { useState } from 'react'
import { useT } from '../i18n/index.js'
import { Button, Checkbox, ErrorMessage, Field, Help, Notice, inputClass } from './ui.jsx'

function downloadBundle(result) {
  const bytes = Uint8Array.from(atob(result.content_base64), (character) => character.charCodeAt(0))
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/zip' }))
  const link = document.createElement('a')
  link.href = url
  link.download = result.filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const link = 'text-xs font-semibold text-accent underline-offset-2 hover:underline'
const code = 'rounded bg-surface-soft px-1 py-0.5 font-mono text-[11px] text-text-strong'

export default function GitHubSetup({ name, spec, call, onChanged }) {
  const t = useT()
  const [form, setForm] = useState(() => ({
    kind: 'pnpm', project_dir: '.', output_dir: 'dist', build_command: 'pnpm run build',
    node_version: '22', pnpm_version: '10.33.0', branch: 'main', automatic: false,
    ...spec.github_actions,
  }))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(false)
  const change = (key, value) => {
    setSaved(false)
    setForm((old) => ({ ...old, [key]: value }))
  }
  const changeKind = (kind) => {
    setSaved(false)
    setForm((old) => ({ ...old, kind, build_command:
      old.build_command === 'pnpm run build' || old.build_command === 'npm run build'
        ? kind === 'npm' ? 'npm run build' : kind === 'custom' ? '' : 'pnpm run build'
        : old.build_command,
    }))
  }
  const save = async (event) => {
    event.preventDefault()
    setPending(true)
    setError(null)
    setSaved(false)
    try {
      const result = await call(`/admin/apps/${encodeURIComponent(name)}/github-actions`, {
        method: 'POST', body: JSON.stringify(form),
      })
      downloadBundle(result)
      setSaved(true)
      onChanged?.()
    } catch (err) { setError(err.message) } finally { setPending(false) }
  }
  return <section className="flex flex-col gap-4" aria-label={t('github.title')}>
    <div>
      <h4 className="m-0 text-sm font-semibold text-text-strong">{t('github.title')}</h4>
      <Help className="mt-1">{t('github.intro')}</Help>
    </div>
    <form onSubmit={save}>
      <fieldset disabled={pending} className="m-0 flex min-w-0 flex-col gap-3 border-0 p-0">
        <Field label={t('github.kind')}>
          <select className={inputClass} value={form.kind} onChange={(event) => changeKind(event.target.value)}>
            <option value="pnpm">Node / pnpm</option>
            <option value="npm">Node / npm</option>
            <option value="static">{t('github.static')}</option>
            <option value="custom">{t('github.custom')}</option>
          </select>
        </Field>
        <Field label={t(form.kind === 'static' ? 'github.source' : 'github.project')} help={t('github.paths_help')}>
          <input className={inputClass} required maxLength="200" value={form.project_dir}
            placeholder="frontend" onChange={(event) => change('project_dir', event.target.value)} />
        </Field>
        {form.kind !== 'static' && <>
          <Field label={t('github.command')} help={t(form.kind === 'custom' ? 'github.custom_help' : 'github.node_help')}>
            <textarea className={`${inputClass} min-h-20 font-mono`} required maxLength="4000" value={form.build_command}
              onChange={(event) => change('build_command', event.target.value)} />
          </Field>
          <Field label={t('github.output')}>
            <input className={inputClass} required maxLength="200" value={form.output_dir}
              onChange={(event) => change('output_dir', event.target.value)} />
          </Field>
        </>}
        {['pnpm', 'npm'].includes(form.kind) && <Field label={t('github.node')}>
          <select className={inputClass} value={form.node_version} onChange={(event) => change('node_version', event.target.value)}>
            <option value="22">22</option><option value="24">24</option>
          </select>
        </Field>}
        {form.kind === 'pnpm' && <Field label={t('github.pnpm')}>
          <input className={inputClass} required pattern="[0-9]{1,2}\.[0-9]{1,3}\.[0-9]{1,3}" value={form.pnpm_version}
            onChange={(event) => change('pnpm_version', event.target.value)} />
        </Field>}
        <Field label={t('github.branch')}>
          <input className={inputClass} required maxLength="100" value={form.branch}
            onChange={(event) => change('branch', event.target.value)} />
        </Field>
        <Checkbox label={t('github.automatic')} checked={form.automatic}
          onChange={(event) => change('automatic', event.target.checked)} />
        <Help>{t(form.automatic ? 'github.push_warning' : 'github.manual_help')}</Help>
        <Help>{t('github.artifact_help')}</Help>
        {error && <ErrorMessage compact>{error}</ErrorMessage>}
        <div><Button type="submit" variant="primary">{t(pending ? 'github.preparing' : 'github.download')}</Button></div>
      </fieldset>
    </form>
    {saved && <Notice>{t('github.saved')}</Notice>}
    <ol className="m-0 flex list-decimal flex-col gap-2 pl-5 text-xs leading-relaxed text-text">
      <li>{t('github.copy_files')} <code className={code}>.github/workflows/deploy.yml</code> + <code className={code}>scripts/notify_deploy.py</code>. {t('github.existing')}</li>
      <li>{t('github.variable')} <code className={code}>DEPLOYD_URL</code>: <code className={code}>{spec.deploy_url || t('setup.set_url')}</code>.</li>
      <li>{t('github.secret')} <code className={code}>DEPLOYD_SECRET</code>: {t('github.secret_help')}</li>
      <li>{t('github.run', { name })}</li>
    </ol>
    {spec.github_repository && <div className="flex flex-wrap gap-4">
      <a className={link} href={`https://github.com/${spec.github_repository}/settings/variables/actions`} target="_blank" rel="noopener noreferrer">{t('github.variables')}</a>
      <a className={link} href={`https://github.com/${spec.github_repository}/settings/secrets/actions`} target="_blank" rel="noopener noreferrer">{t('setup.open_github')}</a>
    </div>}
    <Help>{t('github.private_help')}</Help>
  </section>
}
