import { useEffect, useState } from 'react'
import { Button } from './ui.jsx'

function repositoryName(value) {
  const repository = value.trim().replace(/^https:\/\/github\.com\//i, '').replace(/\/$/, '').replace(/\.git$/, '')
  if (repository && !/^[a-zA-Z0-9][a-zA-Z0-9-]{0,38}\/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$/.test(repository)) {
    throw new Error('repository')
  }
  return repository
}

function fields(spec = {}) {
  const restart = spec.restart?.command || []
  return {
    repository: spec.github_repository || spec.artifact?.allowed_url_prefix?.match(/^https:\/\/api\.github\.com\/repos\/([^/]+\/[^/]+)\/releases\/assets\/$/)?.[1] || '',
    deployUrl: spec.deploy_url || '',
    sitePath: spec.site_path || '',
    kind: !restart.length || (restart.length === 3 && restart[0] === '/usr/bin/test' && restart[1] === '-s' && restart[2] === `${spec.current_link}/index.html`) ? 'static' : 'service',
    executable: restart[0] || '',
    arguments: restart.slice(1).join('\n'),
    healthUrl: spec.health?.url || '',
    keep: spec.keep_previous ?? (spec.keep_releases ? spec.keep_releases - 1 : 1),
    automatic: spec.auto_cleanup ?? true,
  }
}

export default function AppEditor({ name: existingName, initialSpec, call, onSaved, onCancel, t }) {
  const [name, setName] = useState(existingName || '')
  const [base] = useState(() => {
    const { secret: _secret, github: _github, ...spec } = initialSpec || {}
    return spec
  })
  const [form, setForm] = useState(() => fields(initialSpec))
  const [signingMode, setSigningMode] = useState(existingName ? 'keep' : 'generate')
  const [signingSecret, setSigningSecret] = useState('')
  const [githubToken, setGithubToken] = useState('')
  const [removeToken, setRemoveToken] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [serverToken, setServerToken] = useState(false)
  useEffect(() => {
    let active = true
    if (!existingName) call('/admin/setup').then((result) => {
      if (active) setServerToken(result.github_server_token_configured)
    }).catch(() => {})
    return () => { active = false }
  }, [call, existingName])
  const change = (key, value) => setForm((old) => ({ ...old, [key]: value }))
  const root = base.releases_dir || `/srv/deployd/${name || 'myapp'}/releases`
  const current = base.current_link || `${root}/current`
  const needsConfirmation = Boolean(initialSpec?.secret?.configured) && signingMode !== 'keep'
  const baseFields = fields(base)

  const makeSpec = () => {
    let repository
    try { repository = repositoryName(form.repository) } catch { throw new Error(t('setup.repository_error')) }
    return {
      ...base,
      github_repository: repository || null,
      deploy_url: form.deployUrl.trim() || null,
      site_path: form.sitePath.trim() || null,
      release_layout: base.release_layout || (existingName ? 'symlink' : 'directory'),
      releases_dir: root,
      current_link: current,
      keep_previous: Number(form.keep),
      auto_cleanup: form.automatic,
      artifact: repository ? { ...base.artifact,
        allowed_url_prefix: `https://api.github.com/repos/${repository}/releases/assets/`,
        allowed_redirect_hosts: repository === baseFields.repository
          ? base.artifact?.allowed_redirect_hosts || ['release-assets.githubusercontent.com']
          : ['release-assets.githubusercontent.com'],
      } : base.artifact || { allowed_url_prefix: '' },
      migrate: base.migrate || { command: null },
      restart: { command: form.kind === 'static' ? ['/usr/bin/test', '-s', `${current}/index.html`]
        : form.executable === baseFields.executable && form.arguments === baseFields.arguments && base.restart
          ? base.restart.command
          : [form.executable.trim(), ...form.arguments.split('\n').filter((argument) => argument !== '')] },
      health: { ...base.health, url: form.healthUrl.trim() || null, retries: base.health?.retries ?? 5,
        interval_seconds: base.health?.interval_seconds ?? 3 },
    }
  }

  const save = async (event) => {
    event.preventDefault()
    if (!/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(name)) {
      setError(t('new.validation'))
      return
    }
    if (needsConfirmation && !confirmed) return
    setPending(true)
    setError(null)
    try {
      const spec = makeSpec()
      const credentials = {
        generate_signing_secret: signingMode === 'generate',
        ...(signingMode === 'custom' ? { signing_secret: signingSecret } : {}),
        ...(githubToken && !removeToken ? { github_token: githubToken } : {}),
        remove_github_token: removeToken,
      }
      const result = await call(`/admin/apps/${encodeURIComponent(name)}/setup`, {
        method: 'POST', body: JSON.stringify({ spec, credentials, create_only: !existingName }),
      })
      setSigningSecret('')
      setGithubToken('')
      onSaved(result, result.config || spec)
    } catch (err) { setError(err.message) } finally { setPending(false) }
  }

  return <form className="app-editor" onSubmit={save}>
    <fieldset disabled={pending}>
      <label className="field-label">{t('new.name')}
        <input className="text-input" value={name} disabled={Boolean(existingName)} autoComplete="off"
          placeholder={t('new.placeholder')} onChange={(event) => setName(event.target.value)} />
      </label>
        <label className="field-label">{t('setup.repository')}
          <input className="text-input" value={form.repository} required={!existingName}
            placeholder="https://github.com/owner/repo" onChange={(event) => change('repository', event.target.value)} />
        </label>
        <label className="field-label">{t('setup.deploy_url')}
          <input className="text-input" type="url" value={form.deployUrl} required={!existingName}
            placeholder="https://deployd.example.com" onChange={(event) => change('deployUrl', event.target.value)} />
        </label>
        <p className="release-help">{t('setup.deploy_url_help')}</p>
        <label className="field-label">{t('setup.kind')}
          <select className="text-input" value={form.kind} onChange={(event) => change('kind', event.target.value)}>
            <option value="static">{t('setup.static')}</option><option value="service">{t('setup.service')}</option>
          </select>
        </label>
        {(form.kind === 'static' || base.site_path) && <>
          <label className="field-label">{t('setup.site_path')}
            <input className="text-input" value={form.sitePath} required={form.kind === 'static'}
              disabled={Boolean(base.site_path)} placeholder="/var/www/example.com"
              onChange={(event) => change('sitePath', event.target.value)} />
          </label>
          <p className="release-help">{t('setup.site_path_help')}</p>
        </>}
        {form.kind === 'service' ? <>
          <label className="field-label">{t('setup.executable')}
            <input className="text-input" required value={form.executable} placeholder="/usr/bin/systemctl"
              onChange={(event) => change('executable', event.target.value)} />
          </label>
          <label className="field-label">{t('setup.arguments')}
            <textarea className="text-input" value={form.arguments} placeholder={'restart\nmy-app'}
              onChange={(event) => change('arguments', event.target.value)} />
          </label>
          <p className="release-help">{t('setup.command_help')}</p>
        </> : <p className="release-help">{t('setup.static_help')}</p>}
        <label className="field-label">{t('setup.health_url')}
          <input className="text-input" type="url" value={form.healthUrl}
            placeholder="https://example.com/healthz" onChange={(event) => change('healthUrl', event.target.value)} />
        </label>
        <p className="release-help">{t('setup.health_help')}</p>
        <label className="field-label">{t('releases.keep')}
          <input className="text-input" type="number" min="0" max="99" required value={form.keep}
            onChange={(event) => change('keep', event.target.value)} />
        </label>
        <p className="release-help">{t('setup.retention_help')}</p>
        <label className="release-checkbox"><input type="checkbox" checked={form.automatic}
          onChange={(event) => change('automatic', event.target.checked)} />{t('releases.automatic')}</label>
      <section className="setup-credentials">
        <h4>{t('setup.credentials')}</h4>
        <p className="release-help">{t('setup.secure_transport')}</p>
        <label className="field-label">{t('setup.signing')}
          <select className="text-input" value={signingMode} disabled={initialSpec?.secret?.env_override}
            onChange={(event) => { setSigningMode(event.target.value); setConfirmed(false) }}>
            {existingName && <option value="keep">{t('setup.keep_secret')}</option>}
            <option value="generate">{t('setup.generate_secret')}</option>
            <option value="custom">{t('setup.custom_secret')}</option>
          </select>
        </label>
        <p className="release-help">{t('setup.signing_help')}</p>
        {signingMode === 'custom' && <label className="field-label">{t('setup.secret_value')}
          <input className="text-input" type="password" autoComplete="new-password" required minLength="32" maxLength="4096"
            value={signingSecret} onChange={(event) => setSigningSecret(event.target.value)} />
        </label>}
        {needsConfirmation && <label className="release-checkbox"><input type="checkbox" checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)} />{t('setup.replace_confirmation')}</label>}
        <label className="field-label">{t('setup.github_token')}
          <input className="text-input" type="password" autoComplete="new-password" maxLength="4096"
            disabled={removeToken || initialSpec?.github?.env_override} value={githubToken}
            onChange={(event) => setGithubToken(event.target.value)} />
        </label>
        <p className="release-help">{t('setup.github_help')}</p>
        {serverToken && <p className="release-help">{t('setup.server_token')}</p>}
        {initialSpec?.github?.configured && <p className="release-help">{t('setup.github_configured', { source: t(`setup.source_${initialSpec.github.source}`) })}</p>}
        {initialSpec?.github?.source === 'app' && <label className="release-checkbox"><input type="checkbox"
          checked={removeToken} onChange={(event) => setRemoveToken(event.target.checked)} />{t('setup.remove_token')}</label>}
      </section>
      <p className="release-help">{t('setup.remaining')}</p>
      {error && <p className="error-message" role="alert">{error}</p>}
      <div className="form-actions">
        <Button type="submit" variant="primary" disabled={needsConfirmation && !confirmed}>
          {t(existingName ? 'app.save_changes' : 'new.create')}
        </Button>
        <Button onClick={onCancel}>{t('common.cancel')}</Button>
      </div>
    </fieldset>
  </form>
}
