import type { AxiosError } from 'axios'

type MonitorPayload = {
  app: 'frontend-admin'
  type: 'axios-http-error'
  level: 'error'
  message: string
  route: string
  pageUrl: string
  timestamp: string
  extras: {
    status?: number
    method?: string
    requestUrl?: string
    responseData?: unknown
    code?: string
  }
}

const DEFAULT_COLLECTOR_URL = 'http://127.0.0.1:8787/errors'
const MAX_BODY_SIZE = 4000

export function reportAxiosHttpError(error: AxiosError): void {
  if (!import.meta.env.DEV) {
    return
  }

  const collectorUrl = import.meta.env.VITE_CONSOLE_COLLECTOR_URL || DEFAULT_COLLECTOR_URL
  const status = error.response?.status
  if (status === 401) {
    return
  }

  const method = error.config?.method?.toUpperCase()
  const requestUrl = error.config?.url || ''
  const message = `HTTP ${status || 'ERR'} ${method || 'UNKNOWN'} ${requestUrl || '(unknown-url)'}`
  const route = `${window.location.pathname}${window.location.search}${window.location.hash}`

  const payload: MonitorPayload = {
    app: 'frontend-admin',
    type: 'axios-http-error',
    level: 'error',
    message: message.slice(0, 500),
    route,
    pageUrl: window.location.href,
    timestamp: new Date().toISOString(),
    extras: {
      status,
      method,
      requestUrl,
      responseData: safeJson(error.response?.data),
      code: typeof error.code === 'string' ? error.code : undefined,
    },
  }

  postPayload(collectorUrl, payload)
}

function postPayload(url: string, payload: MonitorPayload): void {
  const body = JSON.stringify(payload).slice(0, MAX_BODY_SIZE)

  if (typeof navigator.sendBeacon === 'function' && shouldUseBeacon(url)) {
    const ok = navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }))
    if (ok) {
      return
    }
  }

  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
    mode: 'cors',
    credentials: 'omit',
  }).catch(() => {
    // Swallow monitor reporting failures; business request flow should not be impacted.
  })
}

function shouldUseBeacon(url: string): boolean {
  try {
    return new URL(url, window.location.href).origin === window.location.origin
  } catch {
    return false
  }
}

function safeJson(data: unknown): unknown {
  try {
    return JSON.parse(JSON.stringify(data))
  } catch {
    if (typeof data === 'string') {
      return data.slice(0, 1000)
    }
    return String(data).slice(0, 1000)
  }
}
