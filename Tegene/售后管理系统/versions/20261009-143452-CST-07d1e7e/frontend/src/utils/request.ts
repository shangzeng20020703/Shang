import axios from 'axios'
import type { AxiosError, AxiosInstance, AxiosRequestConfig, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'
import { reportAxiosHttpError } from '@/utils/httpErrorReporter'
import { clearWebAuthSession, getWebAuthToken } from '@/utils/authSession'

/**
 * Web 管理端统一请求层。
 *
 * 约束：
 * - 所有管理端 API 默认走 `/api/v1`
 * - token 从当前标签页会话读，统一挂到 Authorization
 * - 常见错误（401/403/500）在这里统一兜底
 *
 * 这样页面和 store 可以只关心“请求什么”，不必重复处理鉴权和通用报错。
 */

const service: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

type RequestConfig = AxiosRequestConfig & {
  skipErrorReport?: boolean
  skipNetworkErrorReport?: boolean
  skipDefaultErrorHandler?: boolean
}

service.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    // Web 端 token 按标签页隔离，支持同一浏览器多个员工账号并行测试。
    const token = getWebAuthToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  },
)

let isRedirecting = false

function clearStoredAuth() {
  clearWebAuthSession()
}

service.interceptors.response.use(
  (response: AxiosResponse) => {
    // 统一返回 response.data，调用方不需要每次再拆一层。
    return response.data
  },
  (error) => {
    const requestConfig = (error.response?.config || error.config || {}) as RequestConfig
    const shouldReportError = !requestConfig.skipErrorReport
    const shouldReportNetworkError = shouldReportError && !requestConfig.skipNetworkErrorReport
    const shouldHandleDefaultError = !requestConfig.skipDefaultErrorHandler

    if (error.response) {
      const { status, data, config } = error.response
      const url = config?.url || ''

      if (status === 401) {
        // 登录接口的401不做跳转，交给登录页自己处理
        if (url.includes('/auth/login') || url.includes('/auth/management-login') || url.includes('/auth/management-session')) {
          return Promise.reject(error)
        }
        // 防止多个并发请求同时 401 时出现重复弹窗和重复跳转。
        if (!isRedirecting) {
          isRedirecting = true
          clearStoredAuth()
          ElMessage.error('登录已过期，请重新登录')
          router.push('/login').finally(() => {
            setTimeout(() => { isRedirecting = false }, 1000)
          })
        }
      } else if (status === 403) {
        // Report non-auth HTTP errors to console monitor collector so real 4xx/5xx can enter auto-fix workflow.
        if (shouldReportError) {
          reportAxiosHttpError(error as AxiosError)
        }
        if (shouldHandleDefaultError) {
          ElMessage.error('没有权限访问')
        }
      } else if (status === 404) {
        // 404 静默处理：由各调用方在 catch 中自行决定是否提示
      } else if (status === 500) {
        if (shouldReportError) {
          reportAxiosHttpError(error as AxiosError)
        }
        if (shouldHandleDefaultError) {
          ElMessage.error('服务器内部错误')
        }
      } else {
        if (shouldReportError) {
          reportAxiosHttpError(error as AxiosError)
        }
        if (shouldHandleDefaultError) {
          ElMessage.error(data?.detail || '请求失败')
        }
      }
    } else {
      if (shouldReportNetworkError) {
        reportAxiosHttpError(error as AxiosError)
      }
      if (shouldHandleDefaultError) {
        ElMessage.error('网络连接失败')
      }
    }
    return Promise.reject(error)
  },
)

export function get<T = any>(url: string, params?: Record<string, any>, config?: RequestConfig): Promise<T> {
  return service.get(url, { params, ...config })
}

export function post<T = any>(url: string, data?: Record<string, any>, config?: RequestConfig): Promise<T> {
  return service.post(url, data, config)
}

export function put<T = any>(url: string, data?: Record<string, any>, config?: RequestConfig): Promise<T> {
  return service.put(url, data, config)
}

export function del<T = any>(url: string, config?: RequestConfig): Promise<T> {
  return service.delete(url, config)
}

export function buildProtectedFileUrl(url?: string): string {
  // 某些附件下载需要 token，前端通过 query string 补一个受保护的访问链接。
  if (!url) return ''
  if (!url.startsWith('/uploads/')) return url
  const token = getWebAuthToken()
  if (!token) return url
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}token=${encodeURIComponent(token)}`
}

export default service
