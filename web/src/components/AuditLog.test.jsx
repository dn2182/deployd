import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AuditLog from './AuditLog.jsx'
import { renderWith } from '../test-utils.jsx'

const entry = (id) => ({ id, action: `action-${id}`, target: 'app', actor: 'dan', at: '2026-11-18T10:00:00Z', detail: null })
const rows = (count, from = 0) => Array.from({ length: count }, (_, index) => entry(from + index))

afterEach(() => vi.useRealTimers())

describe('audit log background refresh', () => {
  it('reloads the rows already on screen in place, without collapsing the list or showing progress', async () => {
    const audit = vi.fn(async ({ limit, offset }) => (offset === 0 ? rows(limit) : rows(3, offset)))
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    renderWith(<AuditLog api={{ audit }} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Load more' }))
    expect(await screen.findByText('action-12')).toBeInTheDocument()
    audit.mockImplementation(async ({ limit }) => [entry(99), ...rows(limit - 1)])
    await act(() => vi.advanceTimersByTimeAsync(60000))
    await waitFor(() => expect(screen.getByText('action-99')).toBeInTheDocument())
    expect(audit).toHaveBeenLastCalledWith({ limit: 13, offset: 0 })
    expect(screen.getByText('action-11')).toBeInTheDocument()
    expect(document.querySelector('.animate-spin, [aria-busy="true"]')).toBeNull()
  })
})
