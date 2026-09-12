import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.jsx'
import { STATUS, mockFetch } from './test-utils.jsx'

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
    kind: 'artifact',
    created_at: '2026-08-28 04:00:00',
    finished_at: '2026-08-28 04:00:30',
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
    { step: 'health', status: 'succeeded', started_at: '', output: 'healthy after 1 attempt(s)\nline two' },
  ],
}

const BASE = { 'GET /api/healthz': { status: 'ok', db: 'ok', worker: 'ok' }, 'GET /api/admin/apps': APPS, 'GET /api/admin/deploys': DEPLOYS }
const calls = (fetcher, prefix) => fetcher.mock.calls.filter(([url]) => url.startsWith(prefix))

beforeEach(() => {
  Object.defineProperty(window.navigator, 'language', { value: 'en-US', configurable: true })
  sessionStorage.setItem('deployd-admin-token', 't0ken')
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})

afterEach(() => vi.useRealTimers())

describe('App', () => {
  it('labels website operations and does not offer artifact redeploy for them', async () => {
    global.fetch = mockFetch({ ...BASE,
      'GET /api/admin/deploys': [{ ...DEPLOYS[0], kind: undefined, commit_sha: '0'.repeat(40), artifact_url: 'local-website://remove' }] })
    render(<App />)
    expect(await screen.findByText('Restore website and remove app')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Redeploy my-api' })).not.toBeInTheDocument()
    expect(screen.queryByText('000000000000')).not.toBeInTheDocument()
  })

  it('paginates and searches many applications while showing only one detail card', async () => {
    const apps = Object.fromEntries(Array.from({ length: 25 }, (_, i) => [`project-${String(i).padStart(2, '0')}`, {
      ...APPS['my-api'], github_repository: `owner/repo-${i}`,
    }]))
    global.fetch = mockFetch({ ...BASE, 'GET /api/admin/apps': apps, 'GET /api/admin/deploys': [] })
    render(<App />)
    const navigation = await screen.findByRole('navigation', { name: 'Select an application' })
    expect(within(navigation).getAllByRole('button')).toHaveLength(10)
    expect(within(navigation).getByRole('button', { pressed: true })).toHaveTextContent('project-00')
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Next applications' }))
    expect(screen.getByRole('heading', { name: 'project-10' })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search applications' }), { target: { value: 'repo-24' } })
    expect(screen.getByRole('heading', { name: 'project-24' })).toBeInTheDocument()
    expect(within(navigation).getAllByRole('button')).toHaveLength(1)
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search applications' }), { target: { value: 'no-match' } })
    expect(screen.getByText('No applications match your search.')).toBeInTheDocument()
  })

  it('shows just one application tab and supports keyboard navigation', async () => {
    global.fetch = mockFetch({ ...BASE,
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
    fireEvent.keyDown(screen.getByRole('tab', { name: 'GitHub Actions setup' }), { key: 'Home' })
    expect(website).toHaveFocus()
  })

  it.each(['running', 'succeeded'])('never polls automatically when a deploy is %s', async (status) => {
    const routes = { ...BASE, 'GET /api/admin/deploys': [{ ...DEPLOYS[0], status }] }
    const fetcher = mockFetch(routes)
    global.fetch = fetcher
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    render(<App />)
    await screen.findByText('No retained versions yet.')
    expect(screen.getByText('Refresh on demand')).toBeInTheDocument()
    const before = fetcher.mock.calls.length
    await act(() => vi.advanceTimersByTimeAsync(600000))
    expect(fetcher).toHaveBeenCalledTimes(before)
    routes['GET /api/admin/deploys'] = DEPLOYS
    fireEvent.click(screen.getAllByRole('button', { name: 'Refresh' })[0])
    await waitFor(() => expect(calls(fetcher, '/api/admin/deploys')).toHaveLength(2))
    if (status === 'running') expect(await screen.findByText('my-api: Succeeded')).toBeInTheDocument()
  })

  it('refreshes health, activity, and the active panel on demand and with the r shortcut', async () => {
    const fetcher = mockFetch(BASE)
    global.fetch = fetcher
    render(<App />)
    await screen.findByText('No retained versions yet.')
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '4' } })
    const healthCalls = calls(fetcher, '/api/healthz').length
    fireEvent.click(screen.getAllByRole('button', { name: 'Refresh' })[0])
    await waitFor(() => expect(calls(fetcher, '/api/healthz')).toHaveLength(healthCalls + 1))
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/releases'))).toHaveLength(2))
    expect(screen.getByRole('spinbutton')).toHaveValue(4)
    fireEvent.keyDown(document.body, { key: 'r' })
    await waitFor(() => expect(calls(fetcher, '/api/healthz')).toHaveLength(healthCalls + 2))
    fireEvent.keyDown(screen.getByRole('spinbutton'), { key: 'r' })
    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(calls(fetcher, '/api/healthz')).toHaveLength(healthCalls + 2)
  })

  it('focuses and clears the search with keyboard shortcuts', async () => {
    global.fetch = mockFetch(BASE)
    render(<App />)
    await screen.findByRole('heading', { name: 'my-api' })
    fireEvent.keyDown(document.body, { key: '/' })
    const search = screen.getByRole('searchbox', { name: 'Search applications' })
    expect(search).toHaveFocus()
    fireEvent.change(search, { target: { value: 'zzz' } })
    fireEvent.keyDown(search, { key: 'Escape' })
    expect(search).toHaveValue('')
    fireEvent.change(search, { target: { value: 'zzz' } })
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(search).toHaveValue('')
  })

  it('creates a real-current app with paths derived from its name', async () => {
    const fetcher = mockFetch({ ...BASE,
      'GET /api/admin/apps': {},
      'GET /api/admin/deploys': [],
      'GET /api/admin/setup': { github_server_token_configured: true },
      'POST /api/admin/apps/bluedatos/setup': { status: 'saved', app: 'bluedatos', secret: 's'.repeat(64) },
    })
    global.fetch = fetcher
    render(<App />)
    expect(await screen.findByText(/No applications registered yet/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Add application/ }))
    fireEvent.change(screen.getByPlaceholderText(/app name/), { target: { value: 'bluedatos' } })
    fireEvent.change(screen.getByLabelText('GitHub repository'), { target: { value: 'https://github.com/dn2182/BlueDatos.com.git' } })
    fireEvent.change(screen.getByLabelText('Public deployd URL'), { target: { value: 'https://deployd.example.com' } })
    expect(screen.getByLabelText('Application health URL (optional)')).not.toBeRequired()
    fireEvent.change(screen.getByLabelText('Local site path'), { target: { value: '/var/www/bluedatos.com' } })
    expect(screen.queryByLabelText('Release directory')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Create application' }))
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/admin/apps/bluedatos/setup', expect.objectContaining({ method: 'POST' })))
    const sent = fetcher.mock.calls.find(([url, options]) => url === '/api/admin/apps/bluedatos/setup' && options.method === 'POST')
    expect(JSON.parse(sent[1].body)).toMatchObject({ create_only: true,
      credentials: { generate_signing_secret: true }, spec: { release_layout: 'directory', keep_previous: 1,
        health: { url: null }, frozen: false,
        site_path: '/var/www/bluedatos.com',
        github_repository: 'dn2182/BlueDatos.com', current_link: '/srv/deployd/bluedatos/releases/current' } })
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('s'.repeat(64))).toBeInTheDocument()
    expect(screen.getByText('/var/www/bluedatos.com')).toBeInTheDocument()
    expect(screen.getByText('/srv/deployd/bluedatos/releases/current')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'I copied it' }))
    expect(screen.queryByText('s'.repeat(64))).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText('/var/www/bluedatos.com')).not.toBeInTheDocument()
  })

  it('renders apps with secret fingerprint, deploys with status, duration, and local times', async () => {
    global.fetch = mockFetch(BASE)
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'my-api' })).toBeInTheDocument()
    expect(screen.getByText('abc123def456')).toBeInTheDocument()
    expect(screen.getByText('aaaaaaaaaaaa')).toBeInTheDocument()
    expect(screen.getByText('30s')).toBeInTheDocument()
    const time = screen.getByText('30s').parentElement.querySelector('time')
    expect(time).toHaveAttribute('datetime', '2026-08-28T04:00:00.000Z')
    expect(time.textContent).toMatch(/ago\)$/)
    expect(screen.getByTitle('db: ok, worker: ok')).toHaveTextContent('API ok')
  })

  it('expands a deploy row into its step log, keeps whitespace, and resets on collapse', async () => {
    const fetcher = mockFetch({ ...BASE, 'GET /api/deploys/d1': DETAIL })
    global.fetch = fetcher
    render(<App />)
    const toggle = (await screen.findByText('aaaaaaaaaaaa')).closest('button')
    fireEvent.click(toggle)
    expect(screen.getByRole('status', { name: 'Loading steps' })).toBeInTheDocument()
    expect(await screen.findByText(/download/)).toBeInTheDocument()
    const output = screen.getByText(/healthy after 1 attempt/)
    expect(output.tagName).toBe('PRE')
    expect(output.textContent).toContain('\nline two')
    expect(fetcher.mock.calls.filter(([url]) => url === '/api/deploys/d1')[0][1].headers['X-Admin-Token']).toBe('t0ken')
    fireEvent.click(toggle)
    expect(screen.queryByText(/download/)).not.toBeInTheDocument()
    fireEvent.click(toggle)
    expect(screen.getByRole('status', { name: 'Loading steps' })).toBeInTheDocument()
  })

  it('redeploy posts to the admin endpoint after confirmation', async () => {
    const fetcher = mockFetch({ ...BASE, 'POST /api/admin/deploys/d1/redeploy': { deploy_id: 'd2', status: 'queued' } })
    global.fetch = fetcher
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Redeploy my-api' }))
    fireEvent.click(screen.getByRole('button', { name: 'Redeploy' }))
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/admin/deploys/d1/redeploy', expect.objectContaining({ method: 'POST' })))
    expect(await screen.findByText('Redeploy of my-api queued.')).toBeInTheDocument()
  })

  it('cancels queued deploys without ambiguous rollback shortcuts in history', async () => {
    const fetcher = mockFetch({ ...BASE,
      'GET /api/admin/apps': { 'my-api': { ...APPS['my-api'], github_repository: 'acme/api' } },
      'GET /api/admin/deploys': [
        { ...DEPLOYS[0], deploy_id: 'q1', status: 'queued', commit_sha: 'c'.repeat(40), finished_at: null },
        { ...DEPLOYS[0], deploy_id: 'f1', status: 'failed', commit_sha: 'b'.repeat(40) },
        DEPLOYS[0],
      ],
      'POST /api/admin/deploys/q1/cancel': { deploy_id: 'q1', status: 'cancelled' },
    })
    global.fetch = fetcher
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel queued deploy of my-api' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel deploy' }))
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/admin/deploys/q1/cancel', expect.objectContaining({ method: 'POST' })))
    expect(screen.queryByRole('button', { name: /Roll back/ })).not.toBeInTheDocument()
    expect(calls(fetcher, '/api/admin/apps/my-api/releases/activate')).toHaveLength(0)
    const links = screen.getAllByRole('link', { name: 'Open commit on GitHub' })
    expect(links[0]).toHaveAttribute('href', `https://github.com/acme/api/commit/${'c'.repeat(40)}`)
    const compare = screen.getAllByRole('link', { name: 'Compare with previous deploy on GitHub' })
    expect(compare).toHaveLength(2)
    expect(compare[0]).toHaveAttribute('href', `https://github.com/acme/api/compare/${'a'.repeat(40)}...${'c'.repeat(40)}`)
  })

  it('filters history on the server, searches by sha prefix, and loads more with an offset', async () => {
    const page = (id, sha) => ({ ...DEPLOYS[0], deploy_id: id, commit_sha: sha.repeat(40) })
    const first = Array.from({ length: 50 }, (_, i) => page(`p${i}`, i === 0 ? 'e' : 'a'))
    const fetcher = mockFetch({ ...BASE,
      'GET /api/admin/deploys?limit=50&offset=0': first,
      'GET /api/admin/deploys?limit=50&offset=50': [page('x1', 'f')],
      'GET /api/admin/deploys?limit=50&offset=0&app=my-api&status=failed': [page('z1', 'd')],
    })
    global.fetch = fetcher
    render(<App />)
    await screen.findByRole('heading', { name: 'my-api' })
    expect(screen.getAllByText('aaaaaaaaaaaa')).toHaveLength(49)
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await screen.findByText('ffffffffffff')).toBeInTheDocument()
    expect(fetcher).toHaveBeenCalledWith('/api/admin/deploys?limit=50&offset=50', expect.anything())
    fireEvent.change(screen.getByLabelText('Commit prefix'), { target: { value: 'EEE' } })
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('eeeeeeeeeeee')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Commit prefix'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Application'), { target: { value: 'my-api' } })
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'failed' } })
    expect(await screen.findByText('dddddddddddd')).toBeInTheDocument()
    expect(screen.queryByText('ffffffffffff')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('counts succeeded deploys from the loaded history', async () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({ ...DEPLOYS[0], deploy_id: `s${i}`, status: i % 2 ? 'succeeded' : 'failed' }))
    global.fetch = mockFetch({ ...BASE, 'GET /api/admin/deploys': rows })
    render(<App />)
    await screen.findByRole('heading', { name: 'my-api' })
    const metrics = screen.getByText('Loaded deploys').parentElement.parentElement
    expect(within(metrics).getByText('30')).toBeInTheDocument()
    expect(within(metrics).getByText('15')).toBeInTheDocument()
  })

  it('shows the per-app status panel and freezes after confirmation', async () => {
    const routes = { ...BASE,
      'GET /api/admin/apps/my-api/status': { ...STATUS, queued: 2, current_release: 'a'.repeat(40),
        last_deploy: DEPLOYS[0], last_health: { status: 'ok', output: '200 OK', started_at: '2026-08-28 04:00:20' } },
      'POST /api/admin/apps/my-api/freeze': { app: 'my-api', frozen: true },
    }
    const fetcher = mockFetch(routes)
    global.fetch = fetcher
    render(<App />)
    const panel = await screen.findByRole('region', { name: 'my-api status' })
    expect(await within(panel).findByText('aaaaaaaaaaaa')).toBeInTheDocument()
    expect(within(panel).getByText('2')).toBeInTheDocument()
    expect(within(panel).getByText('200 OK')).toBeInTheDocument()
    expect(within(panel).getByText('Accepting deploys')).toBeInTheDocument()
    fireEvent.click(within(panel).getByRole('button', { name: 'Freeze' }))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Freeze' }))
    routes['GET /api/admin/apps/my-api/status'] = { ...routes['GET /api/admin/apps/my-api/status'], frozen: true }
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/admin/apps/my-api/freeze',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ frozen: true }) })))
    expect(await within(panel).findByRole('button', { name: 'Unfreeze' })).toBeInTheDocument()
    expect(within(panel).getByText('Frozen')).toBeInTheDocument()
  })

  it('shows the audit log with load more', async () => {
    const entry = (id) => ({ id, at: '2026-08-28 04:00:00', actor: 'dan', action: 'freeze', target: 'my-api', detail: { frozen: true } })
    const fetcher = mockFetch({ ...BASE,
      'GET /api/admin/audit?limit=50&offset=0': Array.from({ length: 50 }, (_, i) => entry(`a${i}`)),
      'GET /api/admin/audit?limit=50&offset=50': [entry('b1')],
    })
    global.fetch = fetcher
    render(<App />)
    await screen.findByRole('heading', { name: 'my-api' })
    fireEvent.click(screen.getByRole('tab', { name: 'Audit log' }))
    expect((await screen.findAllByText('freeze'))).toHaveLength(50)
    expect(screen.getAllByText('by dan')).toHaveLength(50)
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await waitFor(() => expect(screen.getAllByText('freeze')).toHaveLength(51))
    expect(fetcher).toHaveBeenCalledWith('/api/admin/audit?limit=50&offset=50', expect.anything())
  })

  it('shows the rotated secret in a modal that survives switching apps until confirmed', async () => {
    global.fetch = mockFetch({ ...BASE,
      'GET /api/admin/apps': { ...APPS, other: APPS['my-api'] },
      'POST /api/admin/apps/my-api/rotate-secret': { secret: 'f'.repeat(64), fingerprint: 'newfp', env_override: false, warning: null },
    })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Rotate secret for my-api' }))
    fireEvent.click(screen.getByRole('button', { name: 'Rotate secret' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('f'.repeat(64))).toBeInTheDocument()
    fireEvent.click(within(screen.getByRole('navigation')).getByRole('button', { name: /other/ }))
    expect(screen.getByRole('heading', { name: 'other' })).toBeInTheDocument()
    expect(screen.getByText('f'.repeat(64))).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'I copied it' }))
    expect(screen.queryByText('f'.repeat(64))).not.toBeInTheDocument()
  })

  it('remove requires typing the app name and calls DELETE', async () => {
    const fetcher = mockFetch({ ...BASE, 'DELETE /api/admin/apps/my-api': { status: 'deleted', app: 'my-api' } })
    global.fetch = fetcher
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Remove my-api' }))
    const confirmation = screen.getByLabelText(/Type my-api to confirm/)
    const removeButton = screen.getByRole('button', { name: 'Remove app' })
    fireEvent.change(confirmation, { target: { value: 'wrong-name' } })
    expect(removeButton).toBeDisabled()
    expect(fetcher).not.toHaveBeenCalledWith('/api/admin/apps/my-api', expect.objectContaining({ method: 'DELETE' }))
    fireEvent.change(confirmation, { target: { value: 'my-api' } })
    fireEvent.click(removeButton)
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/admin/apps/my-api', expect.objectContaining({ method: 'DELETE' })))
  })

  it.each(['restore', 'keep'])('asks what happens to the website when removing it: %s', async (choice) => {
    const fetcher = mockFetch({ ...BASE,
      'GET /api/admin/apps': { 'my-api': { ...APPS['my-api'], site_path: '/var/www/example.com' } },
      'GET /api/admin/apps/my-api/website': { status: 'connected', backup: true },
      'DELETE /api/admin/apps/my-api': { status: 'queued', app: 'my-api' },
    })
    global.fetch = fetcher
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Remove my-api' }))
    const dialog = screen.getByRole('alertdialog')
    expect(within(dialog).getByText(/without rolling back to b4deployd/)).toBeInTheDocument()
    fireEvent.change(within(dialog).getByRole('combobox'), { target: { value: choice } })
    fireEvent.change(within(dialog).getByLabelText(/Type my-api to confirm/), { target: { value: 'my-api' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove app' }))
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/admin/apps/my-api',
      expect.objectContaining({ method: 'DELETE', body: JSON.stringify({ confirm: 'my-api', website: choice }) })))
    expect(await screen.findByText(/Removal queued/)).toBeInTheDocument()
  })

  it('validates the new-app name', async () => {
    global.fetch = mockFetch(BASE)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /Add application/ }))
    fireEvent.change(screen.getByPlaceholderText(/app name/), { target: { value: 'BAD NAME' } })
    fireEvent.submit(screen.getByRole('button', { name: 'Create application' }).closest('form'))
    expect(await screen.findByText(/lowercase/)).toBeInTheDocument()
  })

  it('shows a useful error when the server returns non-JSON', async () => {
    global.fetch = vi.fn(async (url) => {
      if (url === '/api/healthz') return { ok: true, json: async () => ({ status: 'ok' }) }
      return { ok: false, status: 502, text: async () => 'Bad Gateway' }
    })
    render(<App />)
    expect(await screen.findByText('Bad Gateway')).toBeInTheDocument()
  })

  it('formats structured validation errors from application setup', async () => {
    const fallback = mockFetch({ ...BASE, 'GET /api/admin/deploys': [] })
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

  it('asks for a token, submits it once with Enter, and logs out', async () => {
    sessionStorage.clear()
    const fetcher = mockFetch(BASE)
    global.fetch = fetcher
    render(<App />)
    const input = screen.getByLabelText('Admin token')
    expect(input).toHaveAttribute('autocomplete', 'off')
    fireEvent.change(input, { target: { value: 'sec' } })
    fireEvent.change(input, { target: { value: 'secret' } })
    expect(calls(fetcher, '/api/admin')).toHaveLength(0)
    fireEvent.submit(input.closest('form'))
    expect(await screen.findByRole('heading', { name: 'my-api' })).toBeInTheDocument()
    expect(sessionStorage.getItem('deployd-admin-token')).toBe('secret')
    expect(calls(fetcher, '/api/admin/apps')[0][1].headers['X-Admin-Token']).toBe('secret')
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }))
    expect(sessionStorage.getItem('deployd-admin-token')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'my-api' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Admin token')).toBeInTheDocument()
  })

  it('shows a distinct state when the token is rejected', async () => {
    global.fetch = vi.fn(async (url) => {
      if (url === '/api/healthz') return { ok: true, json: async () => ({ status: 'ok' }) }
      return { ok: false, status: 401, text: async () => JSON.stringify({ detail: 'bad admin token' }) }
    })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Admin token rejected' })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('rejected this admin token')
    expect(screen.queryByText('bad admin token')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Admin token')).toHaveAttribute('aria-invalid', 'true')
  })

  it('persists the selected color theme', async () => {
    global.fetch = mockFetch(BASE)
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }))
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe('dark'))
    expect(localStorage.getItem('deployd-theme')).toBe('dark')
    expect(screen.getByRole('button', { name: 'Switch to light theme' })).toBeInTheDocument()
  })

  it('uses Spanish for a Spanish browser and allows switching to English', async () => {
    Object.defineProperty(window.navigator, 'language', { value: 'es-CR', configurable: true })
    global.fetch = mockFetch(BASE)
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Resumen de despliegues' })).toBeInTheDocument()
    expect(document.documentElement.lang).toBe('es')
    expect((await screen.findAllByText('Completado')).length).toBeGreaterThan(0)
    expect(screen.getByText('ES')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Switch to English' }))
    expect(screen.getByRole('heading', { name: 'Deployment overview' })).toBeInTheDocument()
    expect(document.documentElement.lang).toBe('en')
    expect(screen.getByText('EN')).toBeInTheDocument()
  })

  it('defaults non-Spanish browsers to English', async () => {
    Object.defineProperty(window.navigator, 'language', { value: 'fr-FR', configurable: true })
    global.fetch = mockFetch(BASE)
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Deployment overview' })).toBeInTheDocument()
    expect(document.documentElement.lang).toBe('en')
  })
})
