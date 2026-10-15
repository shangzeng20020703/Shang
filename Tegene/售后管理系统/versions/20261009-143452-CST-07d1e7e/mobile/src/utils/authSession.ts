/**
 * 移动端认证会话存储。
 *
 * 保持登录时使用 localStorage；临时登录时只放 sessionStorage。
 * 明文密码不进入本地存储，通过后端 remember_me 长效 token 避免员工频繁重复输入密码。
 */

const MOBILE_TOKEN_KEY = 'mobile_token'

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

export function getMobileAuthToken(): string {
  return getLocalStore()?.getItem(MOBILE_TOKEN_KEY) || getSessionStore()?.getItem(MOBILE_TOKEN_KEY) || ''
}

export function setMobileAuthToken(token: string, persist = true) {
  const localStore = getLocalStore()
  const sessionStore = getSessionStore()
  if (persist) {
    localStore?.setItem(MOBILE_TOKEN_KEY, token)
    sessionStore?.removeItem(MOBILE_TOKEN_KEY)
    return
  }
  sessionStore?.setItem(MOBILE_TOKEN_KEY, token)
  localStore?.removeItem(MOBILE_TOKEN_KEY)
}

export function clearMobileAuthToken() {
  getSessionStore()?.removeItem(MOBILE_TOKEN_KEY)
  getLocalStore()?.removeItem(MOBILE_TOKEN_KEY)
}
