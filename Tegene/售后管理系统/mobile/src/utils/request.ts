import axios from 'axios'
import type { AxiosInstance, AxiosRequestConfig, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { Capacitor } from '@capacitor/core'
import { getMobileAuthToken, clearMobileAuthToken } from '@/utils/authSession'

/**
 * 移动端统一请求层。
 *
 * 关键差异：
 * - H5 版本默认走相对路径 `/api/v1`
 * - 原生容器里没有同源概念，所以需要一个显式 API 基址
 * - 正式 APP 必须通过 `VITE_API_BASE_URL` 指向 HTTPS API
 */

const isNative = Capacitor.getPlatform() !== 'web'
const configuredApiBaseUrl = (import.meta.env.VITE_API_BASE_URL || '').trim()

if (isNative && !configuredApiBaseUrl) {
  console.error('原生 APP 必须通过 VITE_API_BASE_URL 配置 HTTPS API 地址')
}

if (isNative && import.meta.env.PROD && /^http:\/\//i.test(configuredApiBaseUrl)) {
  console.error('正式 APP 不应使用 HTTP API，请改为 HTTPS 域名')
}

const API_BASE_URL = configuredApiBaseUrl || '/api/v1'

const service: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 20000,
})

service.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  // 移动端统一读取持久化 token，避免 H5 每次重新进入都要求登录。
  const token = getMobileAuthToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

service.interceptors.response.use(
  // 和 Web 保持一致：统一把 data 直接透给调用方。
  (response: AxiosResponse) => response.data,
  (error) => {
    if (error.response?.status === 403 && ['账号已被停用', '账号已锁定'].includes(error.response?.data?.detail)) {
      clearMobileAuthToken()
      window.location.replace(import.meta.env.BASE_URL + 'login')
    }
    return Promise.reject(error)
  },
)

export function get<T = any>(url: string, params?: Record<string, any>, config?: AxiosRequestConfig): Promise<T> {
  return service.get(url, { params, ...config })
}

export function post<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
  return service.post(url, data, config)
}

export function put<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
  return service.put(url, data, config)
}

export function del<T = any>(url: string, config?: AxiosRequestConfig): Promise<T> {
  return service.delete(url, config)
}

export function resolveApiUrl(url: string) {
  const path = url.startsWith('/') ? url : `/${url}`
  return `${API_BASE_URL.replace(/\/$/, '')}${path}`
}

function backendAssetOrigin() {
  if (/^https?:\/\//i.test(API_BASE_URL)) {
    return API_BASE_URL.replace(/\/api\/v1\/?$/i, '')
  }
  return window.location.origin
}

export function resolveBackendAssetUrl(url?: string | null) {
  if (!url) return ''
  const raw = String(url).trim()
  if (!raw) return ''
  if (/^https?:\/\//i.test(raw)) return raw
  const normalizedPath = raw.startsWith('/') ? raw : `/${raw}`
  return new URL(normalizedPath, backendAssetOrigin()).toString()
}

export function resolveAuthenticatedAssetUrl(url?: string | null) {
  const resolved = resolveBackendAssetUrl(url)
  if (!resolved) return ''
  const parsed = new URL(resolved)
  const token = getMobileAuthToken()
  if (token && parsed.origin === new URL(backendAssetOrigin()).origin && parsed.pathname.startsWith('/uploads/') && !parsed.searchParams.has('token')) {
    parsed.searchParams.set('token', token)
  }
  return parsed.toString()
}

export default service
