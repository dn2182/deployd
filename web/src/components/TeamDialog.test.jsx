import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import TeamDialog from './TeamDialog.jsx'
import { renderWith } from '../test-utils.jsx'

const APPS = {
  'orders-api': { github_repository: 'acme/orders-api' },
  'shop-web': { github_repository: 'acme/shop-web' },
  'status-page': { github_repository: null },
}
const MARIA = { username: 'maria', apps: ['orders-api'], disabled: false, last_login_at: null, sessions: 0 }

function setup(overrides = {}) {
  const api = {
    members: vi.fn().mockResolvedValue([MARIA]),
    createMember: vi.fn().mockResolvedValue({ username: 'luis', apps: [], password: 'generated-pass-123456' }),
    updateMember: vi.fn().mockResolvedValue({ username: 'maria', password: null }),
    deleteMember: vi.fn().mockResolvedValue({ deleted: 'maria' }),
    ...overrides,
  }
  const onClose = vi.fn()
  renderWith(<TeamDialog api={api} apps={APPS} onClose={onClose} />)
  return { api, onClose }
}

describe('team access', () => {
  it('lists members with their applications and sign-in state', async () => {
    setup()
    const row = (await screen.findByText('maria')).closest('li')
    expect(row).toHaveTextContent('orders-api')
    expect(row).toHaveTextContent('Has not signed in yet')
    expect(screen.getByRole('dialog', { name: 'Team access' })).toBeInTheDocument()
  })

  it('invites to add the first member when there are none', async () => {
    setup({ members: vi.fn().mockResolvedValue([]) })
    expect(await screen.findByText(/No team members yet/)).toBeInTheDocument()
  })

  it('creates a member with switches per application and shows the generated password once', async () => {
    const { api } = setup()
    fireEvent.click(await screen.findByRole('button', { name: 'Add member' }))
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: ' Luis ' } })
    const orders = screen.getByRole('switch', { name: 'Access to orders-api' })
    expect(orders).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(orders)
    fireEvent.click(screen.getByRole('switch', { name: 'Access to status-page' }))
    expect(orders).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText('2 of 3 selected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Create member' }))
    expect(await screen.findByText('generated-pass-123456')).toBeInTheDocument()
    expect(api.createMember).toHaveBeenCalledWith({ username: 'luis', apps: ['orders-api', 'status-page'], generate_password: true })
    expect(screen.getByText('This password is shown once.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByText('generated-pass-123456')).not.toBeInTheDocument()
    expect(api.members).toHaveBeenCalledTimes(2)
  })

  it('lets the administrator type a password instead, and validates before sending', async () => {
    const { api } = setup()
    fireEvent.click(await screen.findByRole('button', { name: 'Add member' }))
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'Bad Name' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create member' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Lowercase letters')
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'luis' } })
    fireEvent.click(screen.getByRole('switch', { name: 'Generate a password' }))
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'short' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create member' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('12')
    expect(api.createMember).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'long-enough-password' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create member' }))
    await waitFor(() => expect(api.createMember).toHaveBeenCalledWith({ username: 'luis', apps: [], password: 'long-enough-password' }))
  })

  it('edits access with select all, and can block sign-in', async () => {
    const { api } = setup()
    fireEvent.click(await screen.findByRole('button', { name: 'Edit maria' }))
    expect(screen.getByRole('switch', { name: 'Access to orders-api' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Select all' }))
    expect(screen.getByText('3 of 3 selected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('switch', { name: 'Can sign in' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(api.updateMember).toHaveBeenCalledWith('maria', {
      apps: ['orders-api', 'shop-web', 'status-page'], disabled: true,
    }))
    expect(await screen.findByText('maria was updated.')).toBeInTheDocument()
  })

  it('resets a password after confirmation and reveals the new one', async () => {
    const { api } = setup({ updateMember: vi.fn().mockResolvedValue({ username: 'maria', password: 'fresh-password-987654' }) })
    fireEvent.click(await screen.findByRole('button', { name: 'Edit maria' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reset password' }))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Reset password' }))
    expect(await screen.findByText('fresh-password-987654')).toBeInTheDocument()
    expect(api.updateMember).toHaveBeenCalledWith('maria', { generate_password: true })
  })

  it('removes a member after confirmation and keeps the form open on a server error', async () => {
    const { api } = setup({ createMember: vi.fn().mockRejectedValue(new Error('a member with that username already exists')) })
    fireEvent.click(await screen.findByRole('button', { name: 'Remove maria' }))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Remove member' }))
    await waitFor(() => expect(api.deleteMember).toHaveBeenCalledWith('maria'))
    fireEvent.click(screen.getByRole('button', { name: 'Add member' }))
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'maria' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create member' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('already exists')
    expect(screen.getByLabelText('Username')).toHaveValue('maria')
  })
})
