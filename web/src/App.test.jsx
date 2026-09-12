import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.jsx'

const APPS = {
  'my-api': {
    releases_dir: '/srv/my-api/releases',
    current_link: '/srv/my-api/current',
    keep_releases: 5,
    artifact: { allowed_url_prefix: 'https://github.com/x/' },
    migrate: { command: null },
    restart: { command: ['true'] },
    health: { url: 'http://127.0.0.1:1/hz', retries: 1, interval_seconds: 0 },
    secret: { configured: true, fingerprint: 'abc123def456', env_override: false },
  },
}

const DEPLOYS = [
  {
    deploy_id: 'd1',
    app: 'my-api',
    commit_sha: 'a'.repeat(40),
    status: 'succeeded',
    created_at: '2026-08-28 04:00:00',
    triggered_by: 'test',
  },
]

const DETAIL = {
  deploy_id: 'd1',
  app: 'my-api',
  commit_sha: 'a'.repeat(40),
  status: 'succeeded',
  created_at: '2026-08-28 04:00:00',
  finished_at: '2026-08-28 04:00:30',
  steps: [
    { step: 'download', status: 'succeeded', started_at: '', output: '123 bytes' },
    { step: 'health', status: 'succeeded', started_at: '', output: 'healthy after 1 attempt(s)' },
  ],
}

function mockFetch(routes) {
  return vi.fn(async (url, opts = {}) => {
    const key = `${opts.method ?? 'GET'} ${url}`
    const defaults = /GET \/api\/admin\/apps\/[^/]+\/releases$/.test(key)
      ? { [key]: { releases: [], active_path: null, busy: false } } : {}
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

beforeEach(() => {
  Object.defineProperty(window.navigator, 'language', { value: 'en-US', configurable: true })
  sessionStorage.setItem('deployd-admin-token', 't0ken')
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})

describe('App', () => {
  it('labels website operations and does not offer artifact redeploy for them', async () => {
    global.fetch = mockFetch({ 'GET /api/healthz': { status: 'ok' }, 'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': [{ ...DEPLOYS[0], commit_sha: '0'.repeat(40), artifact_url: 'local-website://remove' }] })
    render(<App />)
    expect(await screen.findByText('Restore website and remove app')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Redeploy my-api' })).not.toBeInTheDocument()
    expect(screen.queryByText('000000000000')).not.toBeInTheDocument()
  })

  it('paginates and searches many applications while showing only one detail card', async () => {
    const apps = Object.fromEntries(Array.from({ length: 25 }, (_, i) => [`project-${String(i).padStart(2, '0')}`, {
      ...APPS['my-api'], github_repository: `owner/repo-${i}`,
    }]))
    global.fetch = mockFetch({ 'GET /api/healthz': { status: 'ok' }, 'GET /api/admin/apps': apps, 'GET /api/admin/deploys': [] })
    render(<App />)
    const navigation = await screen.findByRole('navigation', { name: 'Select an application' })
    expect(within(navigation).getAllByRole('button')).toHaveLength(10)
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Next applications' }))
    expect(screen.getByRole('heading', { name: 'project-10' })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'repo-24' } })
    expect(screen.getByRole('heading', { name: 'project-24' })).toBeInTheDocument()
    expect(within(navigation).getAllByRole('button')).toHaveLength(1)
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'no-match' } })
    expect(screen.getByText('No applications match your search.')).toBeInTheDocument()
  })

  it('shows just one application tab and supports keyboard navigation', async () => {
    global.fetch = mockFetch({ 'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': { site: { ...APPS['my-api'], site_path: '/var/www/site', github_repository: 'owner/site' } },
      'GET /api/admin/apps/site/website': { status: 'connected', backup: true },
      'GET /api/admin/deploys': [] })
    render(<App />)
    const website = await screen.findByRole('tab', { name: 'Website connection' })
    expect(website).toHaveAttribute('aria-selected', 'true')
    expect(screen.getAllByRole('tabpanel')).toHaveLength(1)
    fireEvent.keyDown(website, { key: 'ArrowRight' })
    const versions = screen.getByRole('tab', { name: 'Manage versions' })
    expect(versions).toHaveFocus()
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Manage versions')
    expect(screen.queryByRole('button', { name: 'Check connection' })).not.toBeInTheDocument()
    fireEvent.keyDown(versions, { key: 'End' })
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName('GitHub Actions setup')
    expect(screen.queryByRole('spinbutton', { name: /previous/ })).not.toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole('tab', { name: 'GitHub Actions setup' }), { key: 'Home' })
    expect(website).toHaveFocus()
  })

  it('does not poll running deployments and refreshes health, activity, and the active panel on demand', async () => {
    const fetcher = mockFetch({ 'GET /api/healthz': { status: 'ok' }, 'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': [{ ...DEPLOYS[0], status: 'running' }] })
    global.fetch = fetcher
    render(<App />)
    await screen.findByText('No retained versions yet.')
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '4' } })
    const before = fetcher.mock.calls.length
    vi.useFakeTimers()
    try {
      await act(() => vi.advanceTimersByTimeAsync(125000))
      expect(fetcher).toHaveBeenCalledTimes(before)
    } finally { vi.useRealTimers() }
    const healthCalls = fetcher.mock.calls.filter(([url]) => url === '/api/healthz').length
    fireEvent.click(screen.getAllByRole('button', { name: 'Refresh' })[0])
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url === '/api/healthz')).toHaveLength(healthCalls + 1))
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/releases'))).toHaveLength(2))
    expect(screen.getByRole('spinbutton')).toHaveValue(4)
  })

  it('creates a real-current app with paths derived from its name', async () => {
    const fetcher = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': {},
      'GET /api/admin/deploys': [],
      'GET /api/admin/setup': { github_server_token_configured: true },
      'POST /api/admin/apps/bluedatos/setup': { status: 'saved', app: 'bluedatos', secret: 's'.repeat(64) },
    })
    global.fetch = fetcher
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /Add application/ }))
    expect(screen.queryByLabelText('Release layout')).not.toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText(/app name/), { target: { value: 'bluedatos' } })
    fireEvent.change(screen.getByLabelText('GitHub repository'), { target: { value: 'https://github.com/dn2182/BlueDatos.com.git' } })
    fireEvent.change(screen.getByLabelText('Public deployd URL'), { target: { value: 'https://deployd.example.com' } })
    expect(screen.getByLabelText('Application health URL (optional)')).not.toBeRequired()
    fireEvent.change(screen.getByLabelText('Local site path'), { target: { value: '/var/www/bluedatos.com' } })
    expect(screen.queryByLabelText('Release directory')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Active path')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Create application' }))
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/admin/apps/bluedatos/setup', expect.objectContaining({ method: 'POST' })))
    const sent = fetcher.mock.calls.find(([url, options]) => url === '/api/admin/apps/bluedatos/setup' && options.method === 'POST')
    expect(JSON.parse(sent[1].body)).toMatchObject({ create_only: true,
      credentials: { generate_signing_secret: true }, spec: { release_layout: 'directory', keep_previous: 1,
        health: { url: null },
        site_path: '/var/www/bluedatos.com',
        github_repository: 'dn2182/BlueDatos.com', current_link: '/srv/deployd/bluedatos/releases/current' } })
    expect(await screen.findByText('s'.repeat(64))).toBeInTheDocument()
    expect(screen.getByText('/var/www/bluedatos.com')).toBeInTheDocument()
    expect(screen.getByText('/srv/deployd/bluedatos/releases/current')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText('s'.repeat(64))).not.toBeInTheDocument()
  })

  it('renders apps with secret fingerprint and deploys with status', async () => {
    global.fetch = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': DEPLOYS,
    })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'my-api' })).toBeInTheDocument()
    expect(screen.getByText('abc123def456')).toBeInTheDocument()
    expect(screen.getAllByText('Succeeded')).toHaveLength(2)
    expect(screen.getByText('aaaaaaaaaaaa')).toBeInTheDocument()
  })

  it('expands a deploy row into its step log', async () => {
    global.fetch = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': DEPLOYS,
      'GET /api/deploys/d1': DETAIL,
    })
    render(<App />)
    fireEvent.click(await screen.findByText('aaaaaaaaaaaa'))
    expect(await screen.findByText(/download/)).toBeInTheDocument()
    expect(screen.getByText(/healthy after 1 attempt/)).toBeInTheDocument()
  })

  it('redeploy posts to the admin endpoint after confirmation', async () => {
    const fetcher = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': DEPLOYS,
      'POST /api/admin/deploys/d1/redeploy': { deploy_id: 'd2', status: 'queued' },
    })
    global.fetch = fetcher
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Redeploy my-api' }))
    fireEvent.click(screen.getByRole('button', { name: 'Redeploy' }))
    await waitFor(() =>
      expect(fetcher).toHaveBeenCalledWith(
        '/api/admin/deploys/d1/redeploy',
        expect.objectContaining({ method: 'POST' })
      )
    )
  })

  it('shows the rotated secret exactly once', async () => {
    global.fetch = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': DEPLOYS,
      'POST /api/admin/apps/my-api/rotate-secret': {
        secret: 'f'.repeat(64),
        fingerprint: 'newfp',
        env_override: false,
        warning: null,
      },
    })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Rotate secret for my-api' }))
    fireEvent.click(screen.getByRole('button', { name: 'Rotate secret' }))
    expect(await screen.findByText('f'.repeat(64))).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText('f'.repeat(64))).not.toBeInTheDocument()
  })

  it('remove requires typing the app name and calls DELETE', async () => {
    const fetcher = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': DEPLOYS,
      'DELETE /api/admin/apps/my-api': { status: 'deleted', app: 'my-api' },
    })
    global.fetch = fetcher
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Remove my-api' }))
    const confirmation = screen.getByLabelText(/Type my-api to confirm/)
    const removeButton = screen.getByRole('button', { name: 'Remove app' })
    fireEvent.change(confirmation, { target: { value: 'wrong-name' } })
    expect(removeButton).toBeDisabled()
    expect(fetcher).not.toHaveBeenCalledWith(
      '/api/admin/apps/my-api',
      expect.objectContaining({ method: 'DELETE' })
    )

    fireEvent.change(confirmation, { target: { value: 'my-api' } })
    fireEvent.click(removeButton)
    await waitFor(() =>
      expect(fetcher).toHaveBeenCalledWith(
        '/api/admin/apps/my-api',
        expect.objectContaining({ method: 'DELETE' })
      )
    )
  })

  it.each(['restore', 'keep'])('asks what happens to the website when removing it: %s', async (choice) => {
    const fetcher = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': { 'my-api': { ...APPS['my-api'], site_path: '/var/www/example.com' } },
      'GET /api/admin/deploys': DEPLOYS,
      'DELETE /api/admin/apps/my-api': { status: 'queued', app: 'my-api' },
    })
    global.fetch = fetcher
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Remove my-api' }))
    expect(screen.getByText(/without rolling back to b4deployd/)).toBeInTheDocument()
    fireEvent.change(screen.getByRole('combobox'), { target: { value: choice } })
    fireEvent.change(screen.getByLabelText(/Type my-api to confirm/), { target: { value: 'my-api' } })
    fireEvent.click(screen.getByRole('button', { name: 'Remove app' }))
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/admin/apps/my-api',
      expect.objectContaining({ method: 'DELETE', body: JSON.stringify({ confirm: 'my-api', website: choice }) })))
  })

  it('validates the new-app name', async () => {
    global.fetch = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': DEPLOYS,
    })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /Add application/ }))
    fireEvent.change(screen.getByPlaceholderText(/app name/), { target: { value: 'BAD NAME' } })
    fireEvent.submit(screen.getByRole('button', { name: 'Create application' }).closest('form'))
    expect(await screen.findByText(/lowercase/)).toBeInTheDocument()
  })

  it('shows a useful error when the server returns non-JSON', async () => {
    global.fetch = vi.fn(async (url) => {
      if (url === '/api/healthz') {
        return { ok: true, json: async () => ({ status: 'ok' }) }
      }
      return { ok: false, status: 502, text: async () => 'Bad Gateway' }
    })

    render(<App />)

    expect(await screen.findByText('Bad Gateway')).toBeInTheDocument()
  })

  it('formats structured validation errors from application setup', async () => {
    const fallback = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': [],
    })
    global.fetch = vi.fn(async (url, opts) => {
      if (url === '/api/admin/apps/my-api/setup') return {
        ok: false, status: 422,
        text: async () => JSON.stringify({ detail: [{ loc: ['body', 'credentials', 'github_token'], msg: 'credential is invalid', type: 'value_error' }] }),
      }
      return fallback(url, opts)
    })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Edit my-api' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('credentials.github_token: credential is invalid')
  })

  it('persists the selected color theme', async () => {
    global.fetch = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': DEPLOYS,
    })
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }))
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe('dark'))
    expect(localStorage.getItem('deployd-theme')).toBe('dark')
    expect(screen.getByRole('button', { name: 'Switch to light theme' })).toBeInTheDocument()
  })

  it('uses Spanish for a Spanish browser and allows switching to English', async () => {
    Object.defineProperty(window.navigator, 'language', { value: 'es-CR', configurable: true })
    global.fetch = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': DEPLOYS,
    })

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Resumen de despliegues' })).toBeInTheDocument()
    expect(document.documentElement.lang).toBe('es')
    expect(await screen.findByText('Completado')).toBeInTheDocument()
    expect(screen.getByText('ES')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Switch to English' }))
    expect(screen.getByRole('heading', { name: 'Deployment overview' })).toBeInTheDocument()
    expect(document.documentElement.lang).toBe('en')
    expect(screen.getByText('EN')).toBeInTheDocument()
  })

  it('defaults non-Spanish browsers to English', async () => {
    Object.defineProperty(window.navigator, 'language', { value: 'fr-FR', configurable: true })
    global.fetch = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': DEPLOYS,
    })

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Deployment overview' })).toBeInTheDocument()
    expect(document.documentElement.lang).toBe('en')
  })
})
