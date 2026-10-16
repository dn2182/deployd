import { useEffect, useState } from 'react'
import { useT } from '../i18n/index.js'
import { Button, Checkbox, ErrorMessage, Field, Help, inputClass } from './ui.jsx'

const DEFAULT_TIMEOUT = 600

function repositoryName(value) {
  const repository = value.trim().replace(/^https:\/\/github\.com\//i, '').replace(/\/$/, '').replace(/\.git$/, '')
  if (repository && !/^[a-zA-Z0-9][a-zA-Z0-9-]{0,38}\/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$/.test(repository)) {
    throw new Error('repository')
  }
  return repository
}

const commandFields = (command) => ({ executable: command?.[0] || '', arguments: (command || []).slice(1).join('\n') })

function commandFrom(executable, args) {
  const program = executable.trim()
  if (!program) return null
  return [program, ...args.split('\n').filter((argument) => argument !== '')]
}

function fields(spec = {}) {
  const restart = spec.restart?.command || []
  return {
    repository: spec.github_repository || spec.artifact?.allowed_url_prefix?.match(/^https:\/\/api\.github\.com\/repos\/([^/]+\/[^/]+)\/releases\/assets\/$/)?.[1] || '',
    deployUrl: spec.deploy_url || '',
    sitePath: spec.site_path || '',
    kind: !restart.length || (restart.length === 3 && restart[0] === '/usr/bin/test' && restart[1] === '-s' && restart[2] === `${spec.current_link}/index.html`) ? 'static' : 'service',
    ...commandFields(restart),
    restartTimeout: spec.restart?.timeout_seconds ?? DEFAULT_TIMEOUT,
    migrateTimeout: spec.migrate?.timeout_seconds ?? DEFAULT_TIMEOUT,
    healthUrl: spec.health?.url || '',
    expectBody: spec.health?.expect_body || '',
    expectHeader: spec.health?.expect_header || '',
    beforeCutover: commandFields(spec.hooks?.before_cutover),
    afterHealth: commandFields(spec.hooks?.after_health),
    hooksTimeout: spec.hooks?.timeout_seconds ?? DEFAULT_TIMEOUT,
    envPassthrough: (spec.env_passthrough || []).join('\n'),
    keep: spec.keep_previous ?? (spec.keep_releases ? spec.keep_releases - 1 : 1),
    automatic: spec.auto_cleanup ?? true,
  }
}

function Section({ title, help, children }) {
  return (
    <section className="flex flex-col gap-3 border-t border-border-subtle pt-4">
      <div>
        <h4 className="m-0 text-sm font-semibold text-text-strong">{title}</h4>
        {help && <Help className="mt-1">{help}</Help>}
      </div>
      {children}
    </section>
  )
}

function CommandFields({ value, onChange, executableLabel, argumentsLabel, placeholder, required = false }) {
  const t = useT()
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <Field label={executableLabel}>
        <input className={inputClass} required={required} value={value.executable} placeholder={placeholder}
          onChange={(event) => onChange({ ...value, executable: event.target.value })} />
      </Field>
      <Field label={argumentsLabel ?? t('setup.arguments')}>
        <textarea className={`${inputClass} min-h-20 font-mono`} value={value.arguments} placeholder={'restart\nmy-app'}
          onChange={(event) => onChange({ ...value, arguments: event.target.value })} />
      </Field>
    </div>
  )
}

export default function AppEditor({ name: existingName, initialSpec, call, onSaved, onCancel }) {
  const t = useT()
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
    const restartUnchanged = form.executable === baseFields.executable && form.arguments === baseFields.arguments && base.restart
    return {
      ...(base.github_actions !== undefined && { github_actions: base.github_actions }),
      github_repository: repository || null,
      deploy_url: form.deployUrl.trim() || null,
      site_path: form.sitePath.trim() || null,
      release_layout: base.release_layout || (existingName ? 'symlink' : 'directory'),
      releases_dir: root,
      current_link: current,
      keep_previous: Number(form.keep),
      auto_cleanup: form.automatic,
      frozen: base.frozen ?? false,
      artifact: repository ? { ...base.artifact,
        allowed_url_prefix: `https://api.github.com/repos/${repository}/releases/assets/`,
        allowed_redirect_hosts: repository === baseFields.repository
          ? base.artifact?.allowed_redirect_hosts || ['release-assets.githubusercontent.com']
          : ['release-assets.githubusercontent.com'],
      } : base.artifact || { allowed_url_prefix: '' },
      migrate: { command: base.migrate?.command ?? null, timeout_seconds: Number(form.migrateTimeout) },
      restart: {
        command: form.kind === 'static' ? ['/usr/bin/test', '-s', `${current}/index.html`]
          : restartUnchanged ? base.restart.command : commandFrom(form.executable, form.arguments),
        timeout_seconds: Number(form.restartTimeout),
      },
      health: {
        url: form.healthUrl.trim() || null,
        retries: base.health?.retries ?? 5,
        interval_seconds: base.health?.interval_seconds ?? 3,
        expect_body: form.expectBody.trim() || null,
        expect_header: form.expectHeader.trim() || null,
      },
      hooks: {
        before_cutover: commandFrom(form.beforeCutover.executable, form.beforeCutover.arguments),
        after_health: commandFrom(form.afterHealth.executable, form.afterHealth.arguments),
        timeout_seconds: Number(form.hooksTimeout),
      },
      env_passthrough: form.envPassthrough.split('\n').map((name) => name.trim()).filter(Boolean),
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

  return <form onSubmit={save}>
    <fieldset disabled={pending} className="m-0 flex min-w-0 flex-col gap-3 border-0 p-0">
      <Field label={t('new.name')}>
        <input className={inputClass} value={name} disabled={Boolean(existingName)} autoComplete="off"
          placeholder={t('new.placeholder')} onChange={(event) => setName(event.target.value)} />
      </Field>
      <Field label={t('setup.repository')}>
        <input className={inputClass} value={form.repository} required={!existingName}
          placeholder="https://github.com/owner/repo" onChange={(event) => change('repository', event.target.value)} />
      </Field>
      <Field label={t('setup.deploy_url')} help={t('setup.deploy_url_help')}>
        <input className={inputClass} type="url" value={form.deployUrl} required={!existingName}
          placeholder="https://deployd.example.com" onChange={(event) => change('deployUrl', event.target.value)} />
      </Field>
      <Field label={t('setup.kind')}>
        <select className={inputClass} value={form.kind} onChange={(event) => change('kind', event.target.value)}>
          <option value="static">{t('setup.static')}</option><option value="service">{t('setup.service')}</option>
        </select>
      </Field>
      {(form.kind === 'static' || base.site_path) && (
        <Field label={t('setup.site_path')} help={t('setup.site_path_help')}>
          <input className={inputClass} value={form.sitePath} required={form.kind === 'static'}
            disabled={Boolean(base.site_path)} placeholder="/var/www/example.com"
            onChange={(event) => change('sitePath', event.target.value)} />
        </Field>
      )}
      {form.kind === 'service' ? <>
        <CommandFields value={{ executable: form.executable, arguments: form.arguments }} required placeholder="/usr/bin/systemctl"
          executableLabel={t('setup.executable')}
          onChange={(value) => setForm((old) => ({ ...old, ...value }))} />
        <Help>{t('setup.command_help')}</Help>
      </> : <Help>{t('setup.static_help')}</Help>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={t('setup.restart_timeout')}>
          <input className={inputClass} type="number" min="1" max="3600" required value={form.restartTimeout}
            onChange={(event) => change('restartTimeout', event.target.value)} />
        </Field>
        <Field label={t('setup.migrate_timeout')}>
          <input className={inputClass} type="number" min="1" max="3600" required value={form.migrateTimeout}
            onChange={(event) => change('migrateTimeout', event.target.value)} />
        </Field>
      </div>
      <Help>{t('setup.timeout_help')}</Help>
      <Field label={t('releases.keep')} help={t('setup.retention_help')} className="sm:max-w-64">
        <input className={inputClass} type="number" min="0" max="99" required value={form.keep}
          onChange={(event) => change('keep', event.target.value)} />
      </Field>
      <Checkbox label={t('releases.automatic')} checked={form.automatic}
        onChange={(event) => change('automatic', event.target.checked)} />

      <Section title={t('setup.health_section')} help={t('setup.health_help')}>
        <Field label={t('setup.health_url')}>
          <input className={inputClass} type="url" value={form.healthUrl}
            placeholder="https://example.com/healthz" onChange={(event) => change('healthUrl', event.target.value)} />
        </Field>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={t('setup.expect_body')} help={t('setup.expect_body_help')}>
            <input className={inputClass} value={form.expectBody} placeholder="{commit_sha}"
              onChange={(event) => change('expectBody', event.target.value)} />
          </Field>
          <Field label={t('setup.expect_header')} help={t('setup.expect_header_help')}>
            <input className={inputClass} value={form.expectHeader} placeholder="X-Release: {commit_sha}"
              onChange={(event) => change('expectHeader', event.target.value)} />
          </Field>
        </div>
      </Section>

      <Section title={t('setup.hooks_section')} help={t('setup.hooks_help')}>
        <CommandFields value={form.beforeCutover} placeholder="/usr/local/bin/prepare"
          executableLabel={t('setup.before_cutover')} argumentsLabel={t('setup.before_cutover_arguments')}
          onChange={(value) => change('beforeCutover', value)} />
        <CommandFields value={form.afterHealth} placeholder="/usr/local/bin/warm-cache"
          executableLabel={t('setup.after_health')} argumentsLabel={t('setup.after_health_arguments')}
          onChange={(value) => change('afterHealth', value)} />
        <Field label={t('setup.hooks_timeout')} className="sm:max-w-64">
          <input className={inputClass} type="number" min="1" max="3600" required value={form.hooksTimeout}
            onChange={(event) => change('hooksTimeout', event.target.value)} />
        </Field>
        <Field label={t('setup.env_passthrough')} help={t('setup.env_passthrough_help')}>
          <textarea className={inputClass} rows={2} value={form.envPassthrough} placeholder="DEPLOYD_MIGRATE_DSN"
            spellCheck={false} onChange={(event) => change('envPassthrough', event.target.value)} />
        </Field>
      </Section>

      <Section title={t('setup.credentials')} help={t('setup.secure_transport')}>
        <Field label={t('setup.signing')} help={t('setup.signing_help')}>
          <select className={inputClass} value={signingMode} disabled={initialSpec?.secret?.env_override}
            onChange={(event) => { setSigningMode(event.target.value); setConfirmed(false) }}>
            {existingName && <option value="keep">{t('setup.keep_secret')}</option>}
            <option value="generate">{t('setup.generate_secret')}</option>
            <option value="custom">{t('setup.custom_secret')}</option>
          </select>
        </Field>
        {signingMode === 'custom' && <Field label={t('setup.secret_value')}>
          <input className={inputClass} type="password" autoComplete="new-password" required minLength="32" maxLength="4096"
            value={signingSecret} onChange={(event) => setSigningSecret(event.target.value)} />
        </Field>}
        {needsConfirmation && <Checkbox label={t('setup.replace_confirmation')} checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)} />}
        <Field label={t('setup.github_token')} help={t('setup.github_help')}>
          <input className={inputClass} type="password" autoComplete="new-password" maxLength="4096"
            disabled={removeToken || initialSpec?.github?.env_override} value={githubToken}
            onChange={(event) => setGithubToken(event.target.value)} />
        </Field>
        {serverToken && <Help>{t('setup.server_token')}</Help>}
        {initialSpec?.github?.configured && <Help>{t('setup.github_configured', { source: t(`setup.source_${initialSpec.github.source}`) })}</Help>}
        {initialSpec?.github?.source === 'app' && <Checkbox label={t('setup.remove_token')} checked={removeToken}
          onChange={(event) => setRemoveToken(event.target.checked)} />}
      </Section>

      <Help>{t('setup.remaining')}</Help>
      {error && <ErrorMessage compact>{error}</ErrorMessage>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="primary" disabled={needsConfirmation && !confirmed}>
          {t(existingName ? 'app.save_changes' : 'new.create')}
        </Button>
        <Button onClick={onCancel}>{t('common.cancel')}</Button>
      </div>
    </fieldset>
  </form>
}
