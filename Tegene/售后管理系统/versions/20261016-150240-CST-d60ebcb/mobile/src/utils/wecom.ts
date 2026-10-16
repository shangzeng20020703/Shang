import { get, post } from '@/utils/request'

export interface LoginPolicy {
  mode: 'password' | 'hybrid' | 'wecom_only'
  password_enabled: boolean
  wecom_enabled: boolean
  configuration_ready: boolean
}
export const loadLoginPolicy = () => get<LoginPolicy>('/auth/policy')
const key = (client: string) => `aftersales_wecom_${client}`

export async function startWecom(client: 'web' | 'mobile') {
  const verifier = Array.from(crypto.getRandomValues(new Uint8Array(32)), n => n.toString(16).padStart(2, '0')).join('')
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  const challenge = Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, '0')).join('')
  const data = await post<{ state: string; authorize_url: string }>('/auth/wecom/start', {
    client, challenge, environment: /wxwork/i.test(navigator.userAgent) ? 'in_app' : 'web',
  })
  sessionStorage.setItem(key(client), JSON.stringify({ state: data.state, verifier, at: Date.now() }))
  location.assign(data.authorize_url)
}

// Remove the one-time authorization code from history before issuing other page requests.
export function takeWecomCallback() {
  const params = new URLSearchParams(location.search)
  if (!params.has('code') && !params.has('state')) return null
  const result = { code: params.get('code') || '', state: params.get('state') || '' }
  history.replaceState(history.state, '', location.pathname)
  return result
}

export async function finishWecom(client: 'web' | 'mobile', callback: { code: string; state: string }) {
  const raw = sessionStorage.getItem(key(client))
  sessionStorage.removeItem(key(client))
  const saved = raw ? JSON.parse(raw) : null
  if (!saved || saved.state !== callback.state || Date.now() - saved.at > 300000) {
    throw new Error('登录请求不属于当前浏览器或已过期，请重新点击企业微信登录')
  }
  return post<{ access_token: string }>('/auth/wecom/complete', { ...callback, client, verifier: saved.verifier })
}

export function rememberLoginDestination(client: 'web' | 'mobile', path: string) {
  if (path.startsWith('/') && !path.startsWith('//') && !path.startsWith('/login')) {
    sessionStorage.setItem(`aftersales_return_${client}`, path)
  }
}
export function consumeLoginDestination(client: 'web' | 'mobile', fallback: string) {
  const key = `aftersales_return_${client}`
  const path = sessionStorage.getItem(key) || ''
  sessionStorage.removeItem(key)
  if (client === 'mobile' && path.split(/[?#]/)[0] === '/app/tabs/workbench') return '/app/tabs/home'
  return path.startsWith(client === 'mobile' ? '/app/' : '/') && !path.startsWith('//') && !path.startsWith('/login') ? path : fallback
}
