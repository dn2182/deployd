import { useState } from 'react'
import { Button } from './ui.jsx'

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

export default function GitHubSetup({ name, spec, call, onChanged, t }) {
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
  return <section className="github-setup" aria-label={t('github.title')}>
    <h4>{t('github.title')}</h4>
    <p className="release-help">{t('github.intro')}</p>
    <form className="app-editor" onSubmit={save}>
      <fieldset disabled={pending}>
        <label className="field-label">{t('github.kind')}
          <select className="text-input" value={form.kind} onChange={(event) => changeKind(event.target.value)}>
            <option value="pnpm">Node / pnpm</option>
            <option value="npm">Node / npm</option>
            <option value="static">{t('github.static')}</option>
            <option value="custom">{t('github.custom')}</option>
          </select>
        </label>
        <label className="field-label">{t(form.kind === 'static' ? 'github.source' : 'github.project')}
          <input className="text-input" required maxLength="200" value={form.project_dir}
            placeholder="frontend" onChange={(event) => change('project_dir', event.target.value)} />
        </label>
        <p className="release-help">{t('github.paths_help')}</p>
        {form.kind !== 'static' && <>
          <label className="field-label">{t('github.command')}
            <textarea className="text-input" required maxLength="4000" value={form.build_command}
              onChange={(event) => change('build_command', event.target.value)} />
          </label>
          <p className="release-help">{t(form.kind === 'custom' ? 'github.custom_help' : 'github.node_help')}</p>
          <label className="field-label">{t('github.output')}
            <input className="text-input" required maxLength="200" value={form.output_dir}
              onChange={(event) => change('output_dir', event.target.value)} />
          </label>
        </>}
        {['pnpm', 'npm'].includes(form.kind) && <label className="field-label">{t('github.node')}
          <select className="text-input" value={form.node_version} onChange={(event) => change('node_version', event.target.value)}>
            <option value="22">22</option><option value="24">24</option>
          </select>
        </label>}
        {form.kind === 'pnpm' && <label className="field-label">{t('github.pnpm')}
          <input className="text-input" required pattern="[0-9]{1,2}\.[0-9]{1,3}\.[0-9]{1,3}" value={form.pnpm_version}
            onChange={(event) => change('pnpm_version', event.target.value)} />
        </label>}
        <label className="field-label">{t('github.branch')}
          <input className="text-input" required maxLength="100" value={form.branch}
            onChange={(event) => change('branch', event.target.value)} />
        </label>
        <label className="release-checkbox"><input type="checkbox" checked={form.automatic}
          onChange={(event) => change('automatic', event.target.checked)} />{t('github.automatic')}</label>
        <p className="release-help">{t(form.automatic ? 'github.push_warning' : 'github.manual_help')}</p>
        <p className="release-help">{t('github.artifact_help')}</p>
        {error && <p className="error-message" role="alert">{error}</p>}
        <Button type="submit" variant="primary">{t(pending ? 'github.preparing' : 'github.download')}</Button>
      </fieldset>
    </form>
    {saved && <p role="status">{t('github.saved')}</p>}
    <ol className="github-instructions">
      <li>{t('github.copy_files')} <code>.github/workflows/deploy.yml</code> + <code>scripts/notify_deploy.py</code>. {t('github.existing')}</li>
      <li>{t('github.variable')} <code>DEPLOYD_URL</code>: <code>{spec.deploy_url || t('setup.set_url')}</code>.</li>
      <li>{t('github.secret')} <code>DEPLOYD_SECRET</code>: {t('github.secret_help')}</li>
      <li>{t('github.run', { name })}</li>
    </ol>
    {spec.github_repository && <div className="form-actions">
      <a href={`https://github.com/${spec.github_repository}/settings/variables/actions`} target="_blank" rel="noopener noreferrer">{t('github.variables')}</a>
      <a href={`https://github.com/${spec.github_repository}/settings/secrets/actions`} target="_blank" rel="noopener noreferrer">{t('setup.open_github')}</a>
    </div>}
    <p className="release-help">{t('github.private_help')}</p>
  </section>
}
