import { useT } from '../i18n/index.js'
import { Help } from './ui.jsx'

const code = 'break-all font-mono text-xs text-text-strong'

export default function AppPaths({ spec }) {
  const t = useT()
  return (
    <div className="flex flex-col gap-3">
      <dl className="m-0 grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[auto_minmax(0,1fr)]">
        {spec.site_path && <>
          <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-soft sm:pt-0.5">{t('setup.site_path')}</dt>
          <dd className="m-0"><code className={code}>{spec.site_path}</code></dd>
        </>}
        <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-soft sm:pt-0.5">{t('app.release_directory')}</dt>
        <dd className="m-0"><code className={code}>{spec.releases_dir}</code></dd>
        <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-soft sm:pt-0.5">{t('setup.current')}</dt>
        <dd className="m-0"><code className={code}>{spec.current_link}</code></dd>
      </dl>
      <Help>{t('setup.managed_paths')}</Help>
      {spec.site_path && <Help>{t('setup.site_connection')}</Help>}
    </div>
  )
}
