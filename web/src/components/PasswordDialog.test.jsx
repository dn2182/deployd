import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../i18n/index.js'
import PasswordDialog from './PasswordDialog.jsx'

function setup(api = {}, language = 'en') {
  const client = { account: vi.fn().mockResolvedValue({ username: 'dan', password_change_available: true }),
    changePassword: vi.fn().mockResolvedValue({ changed: true }), ...api }
  const close = vi.fn()
  render(<LanguageProvider language={language}><PasswordDialog api={client} onClose={close} /></LanguageProvider>)
  return { client, close }
}

async function fill(confirmation = 'replacement-password') {
  fireEvent.change(await screen.findByLabelText('Current password'), { target: { value: 'original-password' } })
  fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'replacement-password' } })
  fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: confirmation } })
  fireEvent.click(screen.getByRole('button', { name: 'Save password' }))
}

describe('PasswordDialog', () => {
  it('changes only the password and clears fields after success', async () => {
    const { client } = setup()
    await fill()
    expect(await screen.findByRole('status')).toHaveTextContent('Password changed')
    expect(client.changePassword).toHaveBeenCalledWith({ current_password: 'original-password', new_password: 'replacement-password' })
    expect(screen.queryByLabelText('New password')).not.toBeInTheDocument()
    expect(client.account).toHaveBeenCalledTimes(1)
  })

  it('rejects a mismatching confirmation without sending passwords', async () => {
    const { client } = setup()
    await fill('something-else')
    expect(screen.getByText('The new passwords do not match.')).toBeInTheDocument()
    expect(client.changePassword).not.toHaveBeenCalled()
  })

  it('disables close and duplicate submit during a password change', async () => {
    let finish
    const { client } = setup({ changePassword: vi.fn().mockImplementation(() => new Promise((resolve) => { finish = resolve })) })
    await fill()
    expect(screen.getByRole('button', { name: 'Save password' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeDisabled()
    finish({ changed: true })
    await screen.findByRole('status')
    expect(client.changePassword).toHaveBeenCalledTimes(1)
  })

  it('clears all passwords after a failed request and keeps the form usable', async () => {
    setup({ changePassword: vi.fn().mockRejectedValue(new Error('Current password is incorrect.')) })
    await fill()
    await screen.findByText('Current password is incorrect.')
    expect(screen.getByLabelText('Current password')).toHaveValue('')
    expect(screen.getByLabelText('New password')).toHaveValue('')
    expect(screen.getByLabelText('Confirm new password')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Save password' })).toBeEnabled()
  })

  it('explains missing installer support without offering a broken form', async () => {
    setup({ account: vi.fn().mockResolvedValue({ username: 'dan', password_change_available: false }) })
    await screen.findByText(/Run the updated Ubuntu installer/)
    expect(screen.queryByLabelText('Current password')).not.toBeInTheDocument()
  })

  it('renders Spanish without polling', async () => {
    const { client } = setup({}, 'es')
    await screen.findByLabelText('Contraseña actual')
    await waitFor(() => expect(client.account).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button', { name: 'Guardar contraseña' })).toBeInTheDocument()
  })
})
