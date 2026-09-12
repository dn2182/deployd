import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ReleasePanel from './ReleasePanel.jsx'
import { renderWith, wrap } from '../test-utils.jsx'

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
  renderWith(<ReleasePanel name="site" spec={spec} call={call} onChanged={changed} />, { language })
  return call
}

describe('ReleasePanel', () => {
  it('preserves the existing layout when saving retention for an empty app', async () => {
    const call = setup({ releases_dir: '/srv/site/releases', current_link: '/srv/site/current',
      release_layout: 'symlink', keep_previous: 1 }, 'en', false, [], null)
    await screen.findByText('No retained versions yet.')
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
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  })

  it('protects active and previous releases and confirms manual deletion', async () => {
    const call = setup({ keep_previous: 3 })
    await screen.findByText('aaaaaaaaaaaa')
    expect(screen.getAllByRole('button', { name: 'Delete files' })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Activate' })[0]).toBeDisabled()
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

  it('keeps the dialog open and shows the error when an operation fails', async () => {
    const call = setup({ keep_previous: 3 })
    await screen.findByText('cccccccccccc')
    call.mockRejectedValueOnce(new Error('app has queued or running deployments'))
    fireEvent.click(screen.getByRole('button', { name: 'Delete files' }))
    const dialog = screen.getByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete files' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('queued or running')
    expect(dialog).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Delete files' })).toBeEnabled()
  })

  it('ignores stale responses when a newer load finishes first', async () => {
    let resolveFirst
    const call = vi.fn()
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValue({ releases: versions, active_path: '/srv/current', busy: false })
    const panel = (revision) => wrap(<ReleasePanel name="site" spec={{ keep_previous: 1 }} call={call} onChanged={vi.fn()} revision={revision} />)
    const view = render(panel(0))
    await waitFor(() => expect(call).toHaveBeenCalledTimes(1))
    view.rerender(panel(1))
    await screen.findByText('aaaaaaaaaaaa')
    resolveFirst({ releases: [], active_path: null, busy: false })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.getByText('aaaaaaaaaaaa')).toBeInTheDocument()
  })

  it('localizes controls and locks mutations during deployment', async () => {
    setup({ keep_previous: 2 }, 'es', true)
    expect(await screen.findByText(/Despliegue en curso/)).toBeInTheDocument()
    expect(screen.getByLabelText('Cantidad de versiones anteriores')).toHaveValue(2)
    expect(screen.getByRole('button', { name: 'Actualizar' })).toBeEnabled()
    for (const button of screen.getAllByRole('button').filter((item) => item.textContent !== 'Actualizar')) expect(button).toBeDisabled()
  })
})
