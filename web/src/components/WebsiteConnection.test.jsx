import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { translate } from '../i18n.js'
import WebsiteConnection from './WebsiteConnection.jsx'

const t = (key, values) => translate('en', key, values)

describe('website connection', () => {
  it('requires typing the app name before queuing a live switch', async () => {
    const call = vi.fn().mockResolvedValue({ status: 'ready' })
    const onChanged = vi.fn()
    render(<WebsiteConnection name="site" call={call} onChanged={onChanged} t={t} />)
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
    render(<WebsiteConnection name="site" call={call} onChanged={vi.fn()} t={t} />)
    expect(await screen.findByText(/No b4deployd backup was created/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Connect website' })).not.toBeInTheDocument()
    expect(call).toHaveBeenCalledTimes(1)
  })

  it('shows preflight errors without offering a live switch', async () => {
    const call = vi.fn().mockRejectedValue(new Error('install the helper first'))
    render(<WebsiteConnection name="site" call={call} onChanged={vi.fn()} t={t} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('install the helper first')
    expect(screen.queryByRole('button', { name: 'Connect website' })).not.toBeInTheDocument()
  })
})
