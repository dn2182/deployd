import { useState } from 'react'
import { Button } from './ui.jsx'
import AppPaths from './AppPaths.jsx'

export default function SetupSummary({ result, spec, onDismiss, t }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(result.secret); setCopied(true) } catch { setCopied(false) }
  }
  return <section className="glass-panel app-card setup-summary" aria-label={t('setup.saved')}>
    <h3>{t('setup.saved')}</h3>
    <AppPaths spec={spec} t={t} />
    {result.secret && <div className="secret-reveal">
      <strong>{t('app.secret_generated')}</strong>
      <p>{t('app.secret_once')}</p>
      <code>{result.secret}</code>
      <Button onClick={copy}>{t(copied ? 'app.copied' : 'app.copy')}</Button>
    </div>}
    <p>{t('setup.ci_instructions')}</p>
    <dl>
      <dt>DEPLOYD_URL</dt><dd><code>{spec.deploy_url || t('setup.set_url')}</code></dd>
      <dt>DEPLOYD_SECRET</dt><dd>{t('setup.ci_secret')}</dd>
      <dt>{t('new.name')}</dt><dd><code>{result.app}</code></dd>
    </dl>
    {spec.github_repository && <a href={`https://github.com/${spec.github_repository}/settings/secrets/actions`}
      target="_blank" rel="noopener noreferrer">{t('setup.open_github')}</a>}
    <p>{t('setup.workflow_help', { name: result.app })}</p>
    <div className="form-actions">
      <a href="https://github.com/dn2182/deployd/blob/main/examples/github-actions-deploy.yml" target="_blank" rel="noopener noreferrer">{t('setup.workflow')}</a>
      <a href="https://github.com/dn2182/deployd/blob/main/examples/notify_deploy.py" target="_blank" rel="noopener noreferrer">{t('setup.notifier')}</a>
    </div>
    <p className="release-help">{t('setup.remaining')}</p>
    <Button onClick={onDismiss}>{t('app.dismiss')}</Button>
  </section>
}
