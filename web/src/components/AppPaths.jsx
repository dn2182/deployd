export default function AppPaths({ spec, t }) {
  return <div className="app-paths">
    <dl>
      {spec.site_path && <><dt>{t('setup.site_path')}</dt><dd><code>{spec.site_path}</code></dd></>}
      <dt>{t('app.release_directory')}</dt><dd><code>{spec.releases_dir}</code></dd>
      <dt>{t('setup.current')}</dt><dd><code>{spec.current_link}</code></dd>
    </dl>
    <p className="release-help">{t('setup.managed_paths')}</p>
    {spec.site_path && <p className="release-help">{t('setup.site_connection')}</p>}
  </div>
}
