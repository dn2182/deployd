import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import AppEditor from './AppEditor.jsx'
import { renderWith } from '../test-utils.jsx'

const existing = {
  releases_dir: '/srv/deployd/site/releases', current_link: '/srv/deployd/site/current',
  keep_previous: 4, auto_cleanup: false, frozen: true,
  artifact: { allowed_url_prefix: 'https://example.com/artifacts/', max_download_bytes: 1234 },
  migrate: { command: ['/usr/bin/migrate', '--apply'], timeout_seconds: 120 },
  restart: { command: ['/usr/bin/restart', 'arg with spaces', 'two\nlines'], timeout_seconds: 600 },
  health: { url: 'https://example.com/health', retries: 12, interval_seconds: 5, expect_body: null, expect_header: null },
  hooks: { before_cutover: null, after_health: ['/usr/local/bin/warm', '--all'] },
  notify: { url: 'https://hooks.example.com/x', events: ['failed'], format: 'slack' },
  secret: { configured: true, env_override: false, fingerprint: '123456' },
  github: { configured: true, source: 'app', env_override: false },
}

function editor(spec = existing, language = 'en') {
  const call = vi.fn().mockResolvedValue({ status: 'saved', app: 'site', secret: null })
  const onSaved = vi.fn()
  renderWith(<AppEditor name="site" initialSpec={spec} call={call} onSaved={onSaved} onCancel={vi.fn()} />, { language })
  return { call, onSaved }
}

const sentSpec = (call) => JSON.parse(call.mock.calls[0][1].body).spec

describe('AppEditor', () => {
  it('allows clearing an existing HTTP health URL', async () => {
    const { call } = editor()
    fireEvent.change(screen.getByLabelText('Application health URL (optional)'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(call).toHaveBeenCalledTimes(1))
    expect(sentSpec(call).health).toEqual({
      url: null, retries: 12, interval_seconds: 5, expect_body: null, expect_header: null,
    })
  })

  it('preserves existing configuration and credentials when edited without changes', async () => {
    const { call } = editor()
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(call).toHaveBeenCalledTimes(1))
    const sent = JSON.parse(call.mock.calls[0][1].body)
    expect(sent.spec).toEqual({
      github_repository: null, deploy_url: null, site_path: null, release_layout: 'symlink',
      releases_dir: existing.releases_dir, current_link: existing.current_link,
      keep_previous: 4, auto_cleanup: false, frozen: true,
      artifact: existing.artifact, migrate: existing.migrate, restart: existing.restart,
      health: existing.health, hooks: existing.hooks, notify: existing.notify,
    })
    expect(sent.credentials).toEqual({ generate_signing_secret: false, remove_github_token: false })
    expect(sent.create_only).toBe(false)
  })

  it('sends health expectations, hooks, notifications, and timeouts from the form', async () => {
    const { call } = editor()
    fireEvent.change(screen.getByLabelText('Expected body text (optional)'), { target: { value: 'build {commit_sha}' } })
    fireEvent.change(screen.getByLabelText('Expected header (optional)'), { target: { value: 'X-Release: {commit_sha}' } })
    fireEvent.change(screen.getByLabelText('Before cutover executable'), { target: { value: '/usr/local/bin/prepare' } })
    fireEvent.change(screen.getByLabelText('Before cutover arguments (one per line)'), { target: { value: '--fast\n\nnow' } })
    fireEvent.change(screen.getByLabelText('After health executable'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Webhook URL (optional)'), { target: { value: 'https://hooks.example.com/y' } })
    fireEvent.change(screen.getByLabelText('Payload format'), { target: { value: 'discord' } })
    fireEvent.click(screen.getByLabelText('Succeeded'))
    fireEvent.click(screen.getByLabelText('Failed'))
    fireEvent.change(screen.getByLabelText('Restart timeout (seconds)'), { target: { value: '30' } })
    fireEvent.change(screen.getByLabelText('Migration timeout (seconds)'), { target: { value: '900' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(call).toHaveBeenCalledTimes(1))
    const spec = sentSpec(call)
    expect(spec.health).toMatchObject({ expect_body: 'build {commit_sha}', expect_header: 'X-Release: {commit_sha}' })
    expect(spec.hooks).toEqual({ before_cutover: ['/usr/local/bin/prepare', '--fast', 'now'], after_health: null })
    expect(spec.notify).toEqual({ url: 'https://hooks.example.com/y', events: ['succeeded'], format: 'discord' })
    expect(spec.restart.timeout_seconds).toBe(30)
    expect(spec.migrate).toEqual({ command: ['/usr/bin/migrate', '--apply'], timeout_seconds: 900 })
  })

  it('defaults new fields for apps without them', async () => {
    const { call } = editor({ ...existing, migrate: { command: null }, restart: { command: ['true'] },
      health: { url: null }, hooks: undefined, notify: undefined, frozen: undefined })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(call).toHaveBeenCalledTimes(1))
    expect(sentSpec(call)).toMatchObject({
      frozen: false,
      migrate: { command: null, timeout_seconds: 600 },
      restart: { timeout_seconds: 600 },
      hooks: { before_cutover: null, after_health: null },
      notify: { url: null, events: [], format: 'generic' },
    })
  })

  it('sends credentials separately from application settings', async () => {
    const { call } = editor()
    fireEvent.change(screen.getByLabelText('GitHub repository'), { target: { value: 'acme/site' } })
    fireEvent.change(screen.getByLabelText('GitHub access token (optional)'), { target: { value: 'private-pat-value' } })
    fireEvent.change(screen.getByLabelText('Deployment-signing secret'), { target: { value: 'custom' } })
    fireEvent.change(screen.getByLabelText('Signing secret value'), { target: { value: 's'.repeat(32) } })
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
    fireEvent.click(screen.getByLabelText(/I understand that existing CI/))
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(call).toHaveBeenCalledTimes(1))
    expect(JSON.stringify(sentSpec(call))).not.toContain('private-pat-value')
    expect(JSON.parse(call.mock.calls[0][1].body).credentials).toMatchObject({
      signing_secret: 's'.repeat(32), github_token: 'private-pat-value',
    })
    expect(screen.getByLabelText('GitHub access token (optional)')).toHaveValue('')
    expect(screen.getByLabelText('Signing secret value')).toHaveValue('')
  })

  it('does not allow changing credentials managed by the service environment', () => {
    editor({ ...existing, secret: { ...existing.secret, env_override: true },
      github: { configured: true, source: 'environment', env_override: true } })
    expect(screen.getByLabelText('Deployment-signing secret')).toBeDisabled()
    expect(screen.getByLabelText('GitHub access token (optional)')).toBeDisabled()
    expect(screen.queryByLabelText(/Remove the app token/)).not.toBeInTheDocument()
  })

  it('supports explicit token removal without accidentally submitting a replacement', async () => {
    const { call } = editor()
    fireEvent.change(screen.getByLabelText('GitHub access token (optional)'), { target: { value: 'discard-this' } })
    fireEvent.click(screen.getByLabelText(/Remove the app token/))
    expect(screen.getByLabelText('GitHub access token (optional)')).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(call).toHaveBeenCalledTimes(1))
    expect(JSON.parse(call.mock.calls[0][1].body).credentials).toEqual({
      generate_signing_secret: false, remove_github_token: true,
    })
  })

  it('hides internal controls and locks an existing local site path', () => {
    editor({ ...existing, site_path: '/var/www/example.com' })
    expect(screen.queryByLabelText('Release directory')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Active path')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Local site path')).toBeDisabled()
  })

  it('shows request errors and permits retrying', async () => {
    const { call } = editor()
    call.mockRejectedValueOnce(new Error('application already exists; edit it instead'))
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('application already exists')
    expect(screen.getByRole('button', { name: 'Save changes' })).not.toBeDisabled()
  })

  it('has translated guided fields in Spanish', () => {
    editor(existing, 'es')
    expect(screen.getByLabelText('Repositorio de GitHub')).toBeInTheDocument()
    expect(screen.getByLabelText('Secreto de firma de despliegues')).toBeInTheDocument()
    expect(screen.getByLabelText('URL del webhook (opcional)')).toBeInTheDocument()
  })
})
