import { useT } from '../i18n/index.js'
import { Button, Help, Panel } from './ui.jsx'
import AppPaths from './AppPaths.jsx'
import GitHubSetup from './GitHubSetup.jsx'

export default function SetupSummary({ result, spec, call, onChanged, onDismiss }) {
  const t = useT()
  return (
    <Panel as="section" className="flex flex-col gap-5 p-5 sm:p-6" aria-label={t('setup.saved')}>
      <h3 className="m-0 text-base font-semibold tracking-tight text-text-strong">{t('setup.saved')}</h3>
      <AppPaths spec={spec} />
      <GitHubSetup name={result.app} spec={spec} call={call} onChanged={onChanged} />
      <Help>{t('setup.remaining')}</Help>
      <div><Button onClick={onDismiss}>{t('app.dismiss')}</Button></div>
    </Panel>
  )
}
