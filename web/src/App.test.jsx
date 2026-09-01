import { render, screen, fireEvent, waitFor } from '@testing-library/react'
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
    const hit = Object.entries(routes).find(([k]) => key.startsWith(k))
    if (!hit) return { ok: false, json: async () => ({ detail: `no route: ${key}` }) }
    return { ok: true, json: async () => hit[1] }
  })
}

beforeEach(() => {
  sessionStorage.setItem('deployd-admin-token', 't0ken')
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})

describe('App', () => {
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

  it('validates the new-app name', async () => {
    global.fetch = mockFetch({
      'GET /api/healthz': { status: 'ok' },
      'GET /api/admin/apps': APPS,
      'GET /api/admin/deploys': DEPLOYS,
    })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /Add application/ }))
    fireEvent.change(screen.getByPlaceholderText(/app name/), { target: { value: 'BAD NAME' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create application' }))
    expect(await screen.findByText(/lowercase/)).toBeInTheDocument()
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
})
