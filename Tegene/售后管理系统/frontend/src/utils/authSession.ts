/**
 * Web 管理端认证会话存储。
 *
 * 认证态按浏览器标签页隔离，允许同一个浏览器用多个标签页分别登录不同员工账号。
 * 首次读取时会把旧版 localStorage 登录态迁移到当前标签页，并清理旧 key，避免后续标签页继续共享同一账号。
 */

const WEB_AUTH_KEYS = ['token', 'role', 'roles', 'is_superuser'] as const
export const WEB_MANAGEMENT_ROLES = ['admin', 'hr', 'manager'] as const

type WebAuthKey = typeof WEB_AUTH_KEYS[number]

function getSessionStore(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function getLocalStore(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function migrateLegacyAuthToSession() {
  const sessionStore = getSessionStore()
  const localStore = getLocalStore()
  if (!localStore) return

  const shouldImportLegacyAuth = Boolean(sessionStore && !sessionStore.getItem('token'))

  for (const key of WEB_AUTH_KEYS) {
    const legacyValue = localStore.getItem(key)
    if (legacyValue !== null && shouldImportLegacyAuth) {
      sessionStore?.setItem(key, legacyValue)
    }
    localStore.removeItem(key)
  }
}

export function getWebAuthValue(key: WebAuthKey): string {
  migrateLegacyAuthToSession()
  return getSessionStore()?.getItem(key) || ''
}

export function setWebAuthValue(key: WebAuthKey, value: string) {
  getSessionStore()?.setItem(key, value)
  getLocalStore()?.removeItem(key)
}

export function clearWebAuthSession() {
  const sessionStore = getSessionStore()
  const localStore = getLocalStore()

  for (const key of WEB_AUTH_KEYS) {
    sessionStore?.removeItem(key)
    localStore?.removeItem(key)
  }
}

export function getWebAuthToken(): string {
  return getWebAuthValue('token')
}

export function setWebAuthToken(token: string) {
  setWebAuthValue('token', token)
}

export function setWebAuthRoleSnapshot(role: string, roles: string[], isSuperuser: boolean) {
  setWebAuthValue('role', role)
  setWebAuthValue('roles', JSON.stringify(roles))
  setWebAuthValue('is_superuser', isSuperuser ? 'true' : 'false')
}

export function getWebAuthRoles(): string[] {
  try {
    const roles = JSON.parse(getWebAuthValue('roles') || '[]')
    if (Array.isArray(roles)) {
      return roles.map((item) => String(item || '').trim().toLowerCase()).filter(Boolean)
    }
  } catch {}

  const fallback = getWebAuthValue('role').trim().toLowerCase()
  return fallback ? [fallback] : []
}

export function hasWebManagementAccess(roles: string[], isSuperuser: boolean): boolean {
  if (isSuperuser) return true
  const roleSet = new Set(roles.map((item) => String(item || '').trim().toLowerCase()).filter(Boolean))
  return WEB_MANAGEMENT_ROLES.some((role) => roleSet.has(role))
}

export function hasWebManagementRoleSnapshot(): boolean {
  return hasWebManagementAccess(getWebAuthRoles(), isWebAuthSuperuser())
}

export function getWebManagementHomePath(roles: string[], isSuperuser: boolean): string {
  const roleSet = new Set(roles.map((item) => String(item || '').trim().toLowerCase()).filter(Boolean))
  if (isSuperuser || roleSet.has('admin') || roleSet.has('hr')) return '/dashboard'
  if (roleSet.has('manager')) return '/dashboard'
  if (roleSet.has('asset_admin')) return '/fixed-assets'
  if (roleSet.has('finance')) return '/payroll'
  return '/login'
}

export function getWebManagementSnapshotHomePath(): string {
  return getWebManagementHomePath(getWebAuthRoles(), isWebAuthSuperuser())
}

export function isWebAuthSuperuser(): boolean {
  return getWebAuthValue('is_superuser') === 'true'
}

export function hasUsableWebAuthToken(): boolean {
  const token = getWebAuthToken().trim()
  if (!token || token === 'undefined' || token === 'null') return false

  const tokenParts = token.split('.')
  if (tokenParts.length !== 3) return false

  try {
    if (typeof atob === 'undefined') return false
    const payloadRaw = tokenParts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = payloadRaw.padEnd(Math.ceil(payloadRaw.length / 4) * 4, '=')
    const payload = JSON.parse(atob(padded))
    return typeof payload?.exp !== 'number' || Date.now() < payload.exp * 1000
  } catch {
    return false
  }
}
