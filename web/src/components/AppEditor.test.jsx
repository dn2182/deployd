import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import AppEditor from './AppEditor.jsx'
import { translate } from '../i18n.js'

const t = (key, values) => translate('en', key, values)
const existing = {
  releases_dir: '/srv/deployd/site/releases', current_link: '/srv/deployd/site/current',
  keep_previous: 4, auto_cleanup: false,
  artifact: { allowed_url_prefix: 'https://example.com/artifacts/', max_download_bytes: 1234 },
  migrate: { command: ['/usr/bin/migrate', '--apply'] },
  restart: { command: ['/usr/bin/restart', 'arg with spaces', 'two\nlines'] },
  health: { url: 'https://example.com/health', retries: 12, interval_seconds: 5 },
  secret: { configured: true, env_override: false, fingerprint: '123456' },
  github: { configured: true, source: 'app', env_override: false },
}

function editor(spec = existing) {
  const call = vi.fn().mockResolvedValue({ status: 'saved', app: 'site', secret: null })
  const onSaved = vi.fn()
  render(<AppEditor name="site" initialSpec={spec} call={call} onSaved={onSaved} onCancel={vi.fn()} t={t} />)
  return { call, onSaved }
}

describe('AppEditor', () => {
  it('preserves existing configuration and credentials when edited without changes', async () => {
    const { call } = editor()
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(call).toHaveBeenCalledTimes(1))
    const sent = JSON.parse(call.mock.calls[0][1].body)
    expect(sent.spec).toMatchObject({
      artifact: existing.artifact, migrate: existing.migrate, restart: existing.restart,
      health: existing.health, keep_previous: 4, auto_cleanup: false, release_layout: 'symlink',
    })
    expect(sent.spec.secret).toBeUndefined()
    expect(sent.spec.github).toBeUndefined()
    expect(sent.credentials).toEqual({ generate_signing_secret: false, remove_github_token: false })
    expect(sent.create_only).toBe(false)
  })

  it('keeps credential values out of advanced JSON and sends them separately', async () => {
    const { call } = editor()
    fireEvent.change(screen.getByLabelText('GitHub repository'), { target: { value: 'acme/site' } })
    fireEvent.change(screen.getByLabelText('GitHub access token (optional)'), { target: { value: 'private-pat-value' } })
    fireEvent.change(screen.getByLabelText('Deployment-signing secret'), { target: { value: 'custom' } })
    fireEvent.change(screen.getByLabelText('Signing secret value'), { target: { value: 's'.repeat(32) } })
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
    fireEvent.click(screen.getByLabelText(/I understand that existing CI/))
    fireEvent.click(screen.getByRole('button', { name: 'Advanced JSON' }))
    const json = screen.getByLabelText('New application configuration').value
    expect(json).not.toContain('private-pat-value')
    expect(json).not.toContain('s'.repeat(32))
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(call).toHaveBeenCalledTimes(1))
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

  it('keeps invalid advanced JSON editable without breaking the form', () => {
    editor()
    fireEvent.click(screen.getByRole('button', { name: 'Advanced JSON' }))
    fireEvent.change(screen.getByLabelText('New application configuration'), { target: { value: 'null' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guided form' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Configuration must be a JSON object')
    fireEvent.change(screen.getByLabelText('New application configuration'), { target: { value: JSON.stringify(existing) } })
    fireEvent.click(screen.getByRole('button', { name: 'Guided form' }))
    expect(screen.getByLabelText('Release directory')).toHaveValue(existing.releases_dir)
  })

  it('shows request errors and permits retrying', async () => {
    const { call } = editor()
    call.mockRejectedValueOnce(new Error('application already exists; edit it instead'))
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('application already exists')
    expect(screen.getByRole('button', { name: 'Save changes' })).not.toBeDisabled()
  })

  it('has translated guided fields in Spanish', () => {
    render(<AppEditor name="site" initialSpec={existing} call={vi.fn()} onSaved={vi.fn()} onCancel={vi.fn()}
      t={(key, values) => translate('es', key, values)} />)
    expect(screen.getByLabelText('Repositorio de GitHub')).toBeInTheDocument()
    expect(screen.getByLabelText('Secreto de firma de despliegues')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'JSON avanzado' })).toBeInTheDocument()
  })
})
