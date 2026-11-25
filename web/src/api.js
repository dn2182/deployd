export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function describe(payload, status) {
  const detail = payload?.detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => `${item.loc?.filter((part) => part !== 'body').join('.')}: ${item.msg}`)
      .join('; ')
  }
  return detail ?? `HTTP ${status}`
}

// The owner sends the admin token; a team member sends the session from sign-in.
function credentialHeaders(credentials) {
  if (typeof credentials === 'string') return credentials ? { 'X-Admin-Token': credentials } : {}
  return credentials?.session ? { 'X-Deployd-Session': credentials.session } : {}
}

export async function request(credentials, path, opts = {}) {
  const resp = await fetch(`/api${path}`, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...credentialHeaders(credentials), ...opts.headers },
  })
  const body = await resp.text()
  let payload
  try {
    payload = body ? JSON.parse(body) : null
  } catch {
    const message = body.trim().slice(0, 200)
    throw new ApiError(message || `HTTP ${resp.status}: invalid server response`, resp.status)
  }
  if (!resp.ok) throw new ApiError(describe(payload, resp.status), resp.status)
  return payload
}

export async function fetchHealth() {
  try {
    const resp = await fetch('/api/healthz')
    if (!resp.ok) throw new Error('health')
    const data = await resp.json()
    return { status: data.status ?? 'degraded', db: data.db ?? null, worker: data.worker ?? null }
  } catch {
    return { status: 'unreachable', db: null, worker: null }
  }
}

const query = (params) => {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

const post = (body) => ({ method: 'POST', body: JSON.stringify(body) })
const app = (name) => `/admin/apps/${encodeURIComponent(name)}`

export function createApi(call) {
  return {
    account: () => call('/admin/account'),
    changeOwnPassword: (passwords) => call('/admin/me/password', post(passwords)),
    members: () => call('/admin/users'),
    createMember: (member) => call('/admin/users', post(member)),
    updateMember: (username, changes) =>
      call(`/admin/users/${encodeURIComponent(username)}`, { method: 'PATCH', body: JSON.stringify(changes) }),
    deleteMember: (username) => call(`/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' }),
    changePassword: (credentials) => call('/admin/account/password', post(credentials)),
    apps: () => call('/admin/apps'),
    deploys: ({ limit = 50, offset = 0, app: appName, status } = {}) =>
      call(`/admin/deploys${query({ limit, offset, app: appName, status })}`),
    deploy: (id) => call(`/deploys/${encodeURIComponent(id)}`),
    cancelDeploy: (id) => call(`/admin/deploys/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
    redeploy: (id) => call(`/admin/deploys/${encodeURIComponent(id)}/redeploy`, { method: 'POST' }),
    audit: ({ limit = 50, offset = 0 } = {}) => call(`/admin/audit${query({ limit, offset })}`),
    appStatus: (name) => call(`${app(name)}/status`),
    freeze: (name, frozen) => call(`${app(name)}/freeze`, post({ frozen })),
    rotateSecret: (name) => call(`${app(name)}/rotate-secret`, { method: 'POST' }),
    removeApp: (name, selection) =>
      call(app(name), { method: 'DELETE', ...(selection && { body: JSON.stringify(selection) }) }),
  }
}
