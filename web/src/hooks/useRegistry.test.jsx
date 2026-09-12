import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useRegistry } from './useRegistry.js'

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const rows = (prefix, app = 'app-a', status = 'succeeded') => Array.from({ length: 50 }, (_, i) => ({
  deploy_id: `${prefix}-${i}`, app, status,
}))

async function setup() {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok' }) }))
  const api = { apps: vi.fn().mockResolvedValue({}), deploys: vi.fn().mockResolvedValue(rows('first')) }
  const onTerminal = vi.fn()
  const hook = renderHook(({ token }) => useRegistry({ token, api, onTerminal }), { initialProps: { token: 'token-a' } })
  await waitFor(() => expect(hook.result.current.deploys).toHaveLength(50))
  await waitFor(() => expect(hook.result.current.refreshing).toBe(false))
  return { ...hook, api, onTerminal }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('history request isolation', () => {
  it.each([{ app: 'app-b' }, { status: 'failed' }])('discards pages from an old filter: %j', async (filter) => {
    const { result, api, onTerminal } = await setup()
    const oldPage = deferred()
    api.deploys.mockReturnValueOnce(oldPage.promise)
    let pending
    act(() => { pending = result.current.loadMore() })
    const filtered = rows('filtered', filter.app || 'app-a', filter.status || 'succeeded')
    api.deploys.mockResolvedValue(filtered)
    act(() => { result.current.setFilters(filter) })
    await waitFor(() => expect(result.current.deploys).toEqual(filtered))
    await act(async () => {
      oldPage.resolve([{ deploy_id: 'old-page', app: 'app-a', status: 'succeeded' }])
      await pending
    })
    expect(result.current.deploys).toEqual(filtered)
    expect(result.current.hasMore).toBe(true)
    expect(result.current.error).toBeNull()
    expect(onTerminal).not.toHaveBeenCalled()
    api.deploys.mockResolvedValue([])
    await act(async () => { await result.current.loadMore() })
    expect(api.deploys).toHaveBeenLastCalledWith(expect.objectContaining({ ...filter, offset: 50 }))
  })

  it('ignores an old error without clearing the newer page loading state', async () => {
    const { result, api } = await setup()
    const oldPage = deferred(), newPage = deferred()
    api.deploys.mockReturnValueOnce(oldPage.promise)
    let oldPending, newPending
    act(() => { oldPending = result.current.loadMore() })
    const filtered = rows('filtered', 'app-b')
    api.deploys.mockResolvedValue(filtered)
    act(() => { result.current.setFilters({ app: 'app-b' }) })
    await waitFor(() => expect(result.current.refreshing).toBe(false))
    api.deploys.mockReturnValueOnce(newPage.promise)
    act(() => { newPending = result.current.loadMore() })
    await act(async () => { oldPage.reject(new Error('stale error')); await oldPending })
    expect(result.current.error).toBeNull()
    expect(result.current.loadingMore).toBe(true)
    await act(async () => { newPage.resolve([]); await newPending })
    expect(result.current.loadingMore).toBe(false)
    expect(result.current.deploys).toEqual(filtered)
  })

  it('invalidates a pending page when refreshing the current filter', async () => {
    const { result, api } = await setup()
    const oldPage = deferred()
    api.deploys.mockReturnValueOnce(oldPage.promise)
    let pending
    act(() => { pending = result.current.loadMore() })
    const fresh = rows('fresh')
    api.deploys.mockResolvedValue(fresh)
    await act(async () => { await result.current.refresh() })
    await act(async () => { oldPage.resolve(rows('old')); await pending })
    expect(result.current.deploys).toEqual(fresh)
    expect(result.current.loadingMore).toBe(false)
    api.deploys.mockResolvedValue([])
    await act(async () => { await result.current.loadMore() })
    expect(api.deploys).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 }))
  })

  it.each(['token-b', ''])('discards pending pages when the token changes to %j', async (token) => {
    const { result, rerender, api, onTerminal } = await setup()
    const oldPage = deferred()
    api.deploys.mockReturnValueOnce(oldPage.promise)
    let pending
    act(() => { pending = result.current.loadMore() })
    const fresh = token ? rows('new-session') : []
    api.deploys.mockResolvedValue(fresh)
    rerender({ token })
    await waitFor(() => expect(result.current.deploys).toEqual(fresh))
    await act(async () => { oldPage.resolve(rows('old-session')); await pending })
    expect(result.current.deploys).toEqual(fresh)
    expect(onTerminal).not.toHaveBeenCalled()
  })

  it('only allows one page request at a time', async () => {
    const { result, api } = await setup()
    const page = deferred()
    api.deploys.mockReturnValueOnce(page.promise)
    let first, second
    act(() => { first = result.current.loadMore(); second = result.current.loadMore() })
    expect(api.deploys).toHaveBeenCalledTimes(2)
    await act(async () => { page.resolve(rows('second')); await Promise.all([first, second]) })
    expect(result.current.deploys).toHaveLength(100)
    api.deploys.mockResolvedValue([])
    await act(async () => { await result.current.loadMore() })
    expect(api.deploys).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 100 }))
  })

  it('does not announce a late result after unmounting', async () => {
    const { result, api, unmount, onTerminal } = await setup()
    api.deploys.mockResolvedValue(rows('running', 'app-a', 'running'))
    await act(async () => { await result.current.refresh() })
    const page = deferred()
    api.deploys.mockReturnValueOnce(page.promise)
    let pending
    act(() => { pending = result.current.loadMore() })
    unmount()
    await act(async () => { page.resolve(rows('running')); await pending })
    expect(onTerminal).not.toHaveBeenCalled()
  })
})
