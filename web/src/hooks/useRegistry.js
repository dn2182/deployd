import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchHealth } from '../api.js'

const PAGE_SIZE = 50
const ACTIVE_STATUSES = new Set(['queued', 'running'])
const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'rolled_back', 'superseded', 'cancelled'])

export function useRegistry({ token, api, onTerminal }) {
  const [health, setHealth] = useState(null)
  const [apps, setApps] = useState(null)
  const [deploys, setDeploys] = useState([])
  const [hasMore, setHasMore] = useState(false)
  const [filters, setFiltersState] = useState({ app: '', status: '' })
  const [error, setError] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [revision, setRevision] = useState(0)
  const requestId = useRef(0)
  const pageRequest = useRef(null)
  const loaded = useRef(0)
  const known = useRef(new Map())
  const lastToken = useRef(token)
  const terminalRef = useRef(onTerminal)
  useEffect(() => {
    terminalRef.current = onTerminal
  }, [onTerminal])

  const announce = useCallback((rows) => {
    for (const deploy of rows) {
      const previous = known.current.get(deploy.deploy_id)
      if (previous && ACTIVE_STATUSES.has(previous) && TERMINAL_STATUSES.has(deploy.status)) {
        terminalRef.current?.(deploy)
      }
      known.current.set(deploy.deploy_id, deploy.status)
    }
  }, [])

  const refresh = useCallback(async () => {
    const id = ++requestId.current
    pageRequest.current = null
    setLoadingMore(false)
    if (lastToken.current !== token) {
      lastToken.current = token
      loaded.current = 0
      known.current.clear()
      setApps(null)
      setDeploys([])
      setHasMore(false)
      setError(null)
    }
    setRefreshing(true)
    const healthRequest = fetchHealth().then((data) => {
      if (id === requestId.current) setHealth(data)
    })
    try {
      if (!token) return
      const limit = Math.min(200, Math.max(PAGE_SIZE, loaded.current))
      const [nextApps, nextDeploys] = await Promise.all([
        api.apps(),
        api.deploys({ limit, offset: 0, app: filters.app, status: filters.status }),
      ])
      if (id !== requestId.current) return
      loaded.current = nextDeploys.length
      setApps(nextApps)
      setDeploys(nextDeploys)
      setHasMore(nextDeploys.length >= limit)
      setError(null)
      setRevision((value) => value + 1)
      announce(nextDeploys)
    } catch (requestError) {
      if (id === requestId.current) setError(requestError.message)
    } finally {
      await healthRequest
      if (id === requestId.current) setRefreshing(false)
    }
  }, [api, token, filters, announce])

  const loadMore = useCallback(async () => {
    if (!token || refreshing || pageRequest.current !== null) return
    const id = requestId.current
    pageRequest.current = id
    setLoadingMore(true)
    try {
      const page = await api.deploys({
        limit: PAGE_SIZE, offset: loaded.current, app: filters.app, status: filters.status,
      })
      if (id !== requestId.current) return
      setDeploys((old) => {
        if (id !== requestId.current) return old
        const seen = new Set(old.map((deploy) => deploy.deploy_id))
        return [...old, ...page.filter((deploy) => !seen.has(deploy.deploy_id))]
      })
      loaded.current += page.length
      setHasMore(page.length >= PAGE_SIZE)
      announce(page)
    } catch (requestError) {
      if (id === requestId.current) setError(requestError.message)
    } finally {
      if (id === requestId.current) {
        pageRequest.current = null
        setLoadingMore(false)
      }
    }
  }, [api, filters, announce, token, refreshing])

  const setFilters = useCallback((next) => {
    // Invalidate old pages before the filter's refresh effect starts.
    requestId.current += 1
    pageRequest.current = null
    setLoadingMore(false)
    setRefreshing(true)
    loaded.current = 0
    setFiltersState((old) => ({ ...old, ...next }))
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => refresh(), 0)
    return () => {
      clearTimeout(timer)
      requestId.current += 1
      pageRequest.current = null
    }
  }, [refresh])

  const isBusy = useCallback(
    (name) => deploys.some((deploy) => deploy.app === name && ACTIVE_STATUSES.has(deploy.status)),
    [deploys],
  )

  return {
    health, apps, deploys, hasMore, filters, setFilters, error, refreshing, loadingMore,
    revision, refresh, loadMore, isBusy,
  }
}
