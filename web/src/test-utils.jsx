import { render } from '@testing-library/react'
import { vi } from 'vitest'
import { LanguageProvider } from './i18n/index.js'
import { ToastProvider } from './components/Toasts.jsx'

export function wrap(ui, language = 'en') {
  return (
    <LanguageProvider language={language} setLanguage={() => {}}>
      <ToastProvider>{ui}</ToastProvider>
    </LanguageProvider>
  )
}

export function renderWith(ui, { language = 'en' } = {}) {
  return render(wrap(ui, language))
}

export const STATUS = {
  app: 'my-api', frozen: false, busy: false, queued: 0, current_release: null, last_deploy: null, last_health: null,
}

// Routes are read on every call, so tests can mutate the map to simulate change.
export function mockFetch(routes) {
  return vi.fn(async (url, opts = {}) => {
    const key = `${opts.method ?? 'GET'} ${url}`
    const path = key.split('?')[0]
    const defaults = {}
    if (/^GET \/api\/admin\/apps\/[^/]+\/releases$/.test(path)) defaults[path] = { releases: [], active_path: null, busy: false }
    if (/^GET \/api\/admin\/apps\/[^/]+\/status$/.test(path)) defaults[path] = { ...STATUS, app: path.split('/')[4] }
    if (path === 'GET /api/admin/audit') defaults[path] = []
    const hit = Object.entries({ ...defaults, ...routes }).sort(([a], [b]) => b.length - a.length)
      .find(([k]) => key === k || key.startsWith(`${k}?`))
    const payload = hit ? hit[1] : { detail: `no route: ${key}` }
    return {
      ok: Boolean(hit),
      status: hit ? 200 : 404,
      json: async () => payload,
      text: async () => JSON.stringify(payload),
    }
  })
}
