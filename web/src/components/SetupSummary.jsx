import { useState } from 'react'
import { Button } from './ui.jsx'
import AppPaths from './AppPaths.jsx'
import GitHubSetup from './GitHubSetup.jsx'

export default function SetupSummary({ result, spec, call, onChanged, onDismiss, t }) {
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
    <GitHubSetup name={result.app} spec={spec} call={call} onChanged={onChanged} t={t} />
    <p className="release-help">{t('setup.remaining')}</p>
    <Button onClick={onDismiss}>{t('app.dismiss')}</Button>
  </section>
}
