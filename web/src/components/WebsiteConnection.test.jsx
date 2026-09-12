import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import WebsiteConnection from './WebsiteConnection.jsx'
import { renderWith } from '../test-utils.jsx'

describe('website connection', () => {
  it('requires typing the app name before queuing a live switch', async () => {
    const call = vi.fn().mockResolvedValue({ status: 'ready' })
    const onChanged = vi.fn()
    renderWith(<WebsiteConnection name="site" call={call} onChanged={onChanged} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Connect website' }))
    const dialog = screen.getByRole('alertdialog')
    const confirm = within(dialog).getByRole('button', { name: 'Connect website' })
    expect(confirm).toBeDisabled()
    expect(dialog).toHaveTextContent('b4deployd')
    fireEvent.change(within(dialog).getByLabelText('Type site to confirm'), { target: { value: 'site' } })
    fireEvent.click(confirm)
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect(call).toHaveBeenCalledWith('/admin/apps/site/website/connect', {
      method: 'POST', body: JSON.stringify({ confirm: 'site' }),
    })
    expect(screen.getByRole('status')).toHaveTextContent('queued or running')
  })

  it('does not offer to move an already linked website or invent a backup', async () => {
    const call = vi.fn().mockResolvedValue({ status: 'connected', backup: false })
    renderWith(<WebsiteConnection name="site" call={call} onChanged={vi.fn()} />)
    expect(await screen.findByText(/No b4deployd backup was created/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Connect website' })).not.toBeInTheDocument()
    expect(call).toHaveBeenCalledTimes(1)
  })

  it('shows preflight errors without offering a live switch', async () => {
    const call = vi.fn().mockRejectedValue(new Error('install the helper first'))
    renderWith(<WebsiteConnection name="site" call={call} onChanged={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('install the helper first')
    expect(screen.queryByRole('button', { name: 'Connect website' })).not.toBeInTheDocument()
  })

  it('allows a manual status check while a connection is busy and clears old errors', async () => {
    const call = vi.fn().mockRejectedValueOnce(new Error('helper missing')).mockResolvedValue({ status: 'connected', backup: true })
    renderWith(<WebsiteConnection name="site" call={call} onChanged={vi.fn()} />)
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }))
    expect(await screen.findByText(/Connected: the local site path/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(call).toHaveBeenCalledTimes(2)
  })

  it('explains retained restored files before reconnecting', async () => {
    const call = vi.fn().mockResolvedValue({ status: 'reconnect', backup: true })
    renderWith(<WebsiteConnection name="site" call={call} onChanged={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Connect website' }))
    expect(screen.getByRole('alertdialog')).toHaveTextContent('including any edits')
    expect(screen.getByRole('alertdialog')).toHaveTextContent('b4deployd backup is unchanged')
  })
})
