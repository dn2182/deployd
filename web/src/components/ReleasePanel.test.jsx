import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { translate } from '../i18n.js'
import ReleasePanel from './ReleasePanel.jsx'

const release = (char, extra = {}) => ({
  name: `${char.repeat(40)}-${char.repeat(32)}`,
  commit_sha: char.repeat(40), active: false, previous: false, protected: false, can_activate: true,
  ...extra,
})
const versions = [release('a', { active: true, protected: true, can_activate: false }),
  release('b', { previous: true, protected: true }), release('c')]

function setup(spec = {}, language = 'en', busy = false, releases = versions, activePath = '/srv/current') {
  const call = vi.fn(async () => ({ releases, active_path: activePath, busy }))
  const changed = vi.fn()
  render(<ReleasePanel name="site" spec={spec} call={call} onChanged={changed}
    t={(key, values) => translate(language, key, values)} />)
  return call
}

describe('ReleasePanel', () => {
  it('preserves the existing layout when saving retention for an empty app', async () => {
    const call = setup({ releases_dir: '/srv/site/releases', current_link: '/srv/site/current',
      release_layout: 'symlink', keep_previous: 1 }, 'en', false, [], null)
    await screen.findByText('No retained versions yet.')
    expect(screen.queryByLabelText('Release layout')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(call).toHaveBeenCalledWith('/admin/apps/site', {
      method: 'PUT', body: JSON.stringify({ releases_dir: '/srv/site/releases',
        current_link: '/srv/site/current', release_layout: 'symlink', keep_previous: 1, auto_cleanup: true }),
    }))
  })

  it('defaults to one previous version and lets the user change the count', async () => {
    const call = setup({ keep_previous: null, secret: { configured: true } })
    await screen.findByText('aaaaaaaaaaaa')
    expect(screen.getByRole('spinbutton')).toHaveValue(1)
    expect(screen.getByLabelText('Keep previous versions')).toBeChecked()
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(call).toHaveBeenCalledWith('/admin/apps/site', {
      method: 'PUT', body: JSON.stringify({ keep_previous: 3, auto_cleanup: true }),
    }))
  })

  it('requires confirmation to disable retention', async () => {
    const call = setup({ keep_previous: 3 })
    await screen.findByText('aaaaaaaaaaaa')
    fireEvent.click(screen.getByLabelText('Keep previous versions'))
    expect(screen.getByRole('spinbutton')).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    const dialog = screen.getByRole('alertdialog')
    expect(within(dialog).getByText(/local rollback is unavailable/)).toBeInTheDocument()
    expect(call).not.toHaveBeenCalledWith('/admin/apps/site', expect.anything())
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(call).toHaveBeenCalledWith('/admin/apps/site', {
      method: 'PUT', body: JSON.stringify({ keep_previous: 0, auto_cleanup: true }),
    }))
  })

  it('protects active and previous releases and confirms manual deletion', async () => {
    const call = setup({ keep_previous: 3 })
    await screen.findByText('aaaaaaaaaaaa')
    expect(screen.getAllByRole('button', { name: 'Delete files' })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Activate' })[0]).toBeDisabled()
    expect(screen.queryByLabelText('Release layout')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Delete files' }))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete files' }))
    await waitFor(() => expect(call).toHaveBeenCalledWith('/admin/apps/site/releases/cleanup', {
      method: 'POST', body: JSON.stringify({ release: versions[2].name }),
    }))
  })

  it('queues activation after confirmation', async () => {
    const call = setup({ keep_previous: 3 })
    await screen.findByText('bbbbbbbbbbbb')
    fireEvent.click(screen.getAllByRole('button', { name: 'Activate' })[1])
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Activate' }))
    await waitFor(() => expect(call).toHaveBeenCalledWith('/admin/apps/site/releases/activate', {
      method: 'POST', body: JSON.stringify({ release: versions[1].name }),
    }))
  })

  it('localizes controls and locks mutations during deployment', async () => {
    setup({ keep_previous: 2 }, 'es', true)
    expect(await screen.findByText(/Despliegue en curso/)).toBeInTheDocument()
    expect(screen.getByLabelText('Cantidad de versiones anteriores')).toHaveValue(2)
    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled()
  })
})
