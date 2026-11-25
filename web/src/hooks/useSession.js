import { useCallback, useMemo, useState } from 'react'
import { ApiError, createApi, request } from '../api.js'

// Both live in sessionStorage, so they end with the browser tab.
const ADMIN_KEY = 'deployd-admin-token'
const MEMBER_KEY = 'deployd-member-session'
const SIGNED_OUT = { role: null, secret: '', username: null }

function stored() {
  try {
    const member = JSON.parse(sessionStorage.getItem(MEMBER_KEY) ?? 'null')
    if (member?.token && member?.username) return { role: 'member', secret: member.token, username: member.username }
    const token = sessionStorage.getItem(ADMIN_KEY) ?? ''
    return token ? { role: 'admin', secret: token, username: null } : SIGNED_OUT
  } catch {
    return SIGNED_OUT
  }
}

function persist(state) {
  try {
    sessionStorage.removeItem(ADMIN_KEY)
    sessionStorage.removeItem(MEMBER_KEY)
    if (state.role === 'admin') sessionStorage.setItem(ADMIN_KEY, state.secret)
    if (state.role === 'member') sessionStorage.setItem(MEMBER_KEY, JSON.stringify({ token: state.secret, username: state.username }))
  } catch {
    return
  }
}

export function useSession() {
  const [state, setState] = useState(stored)
  const [rejected, setRejected] = useState(false)
  const change = useCallback((next) => {
    setRejected(false)
    setState(next)
    persist(next)
  }, [])

  const credentials = useMemo(
    () => (state.role === 'member' ? { session: state.secret } : state.secret),
    [state],
  )

  const call = useCallback(async (path, opts) => {
    try {
      return await request(credentials, path, opts)
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setRejected(true)
      throw error
    }
  }, [credentials])

  const login = useCallback((token) => change({ role: 'admin', secret: token, username: null }), [change])

  const loginMember = useCallback(async (username, password) => {
    const session = await request(null, '/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) })
    change({ role: 'member', secret: session.token, username: session.username })
  }, [change])

  const logout = useCallback(() => {
    // The server forgets a member session too; signing out locally never waits for it.
    if (state.role === 'member') request(credentials, '/admin/logout', { method: 'POST' }).catch(() => {})
    change(SIGNED_OUT)
  }, [state.role, credentials, change])

  const api = useMemo(() => createApi(call), [call])
  return {
    token: state.secret, role: state.role, username: state.username, admin: state.role !== 'member',
    rejected, call, api, login, loginMember, logout,
  }
}
