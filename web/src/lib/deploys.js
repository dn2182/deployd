export const DEPLOY_STATUSES = ['queued', 'running', 'succeeded', 'failed', 'rolled_back', 'superseded', 'cancelled']
export const NOTIFY_EVENTS = ['succeeded', 'failed', 'rolled_back']
export const NOTIFY_FORMATS = ['generic', 'slack', 'discord']

// Older backends only carry the operation in artifact_url.
export function deployKind(deploy) {
  if (deploy.kind) return deploy.kind
  if (deploy.artifact_url === 'local-website://connect') return 'connect'
  if (deploy.artifact_url === 'local-website://remove') return 'remove'
  if (deploy.artifact_url?.startsWith('local-release://')) return 'activate'
  return 'artifact'
}
