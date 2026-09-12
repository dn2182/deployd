import { useCallback, useMemo, useState } from 'react'
import { ApiError, createApi, request } from '../api.js'

const STORAGE_KEY = 'deployd-admin-token'

function storedToken() {
  try {
    return sessionStorage.getItem(STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

function persist(value) {
  try {
    if (value) sessionStorage.setItem(STORAGE_KEY, value)
    else sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    return
  }
}

export function useSession() {
  const [token, setToken] = useState(storedToken)
  const [rejected, setRejected] = useState(false)

  const call = useCallback(async (path, opts) => {
    try {
      return await request(token, path, opts)
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setRejected(true)
      throw error
    }
  }, [token])

  const login = useCallback((value) => {
    setRejected(false)
    setToken(value)
    persist(value)
  }, [])

  const logout = useCallback(() => {
    setRejected(false)
    setToken('')
    persist('')
  }, [])

  const api = useMemo(() => createApi(call), [call])
  return { token, rejected, call, api, login, logout }
}
