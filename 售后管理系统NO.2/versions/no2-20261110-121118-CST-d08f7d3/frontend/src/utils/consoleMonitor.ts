interface MonitorEvent {
  app: 'frontend-admin'
  type: 'window-error' | 'unhandled-rejection' | 'console-error' | 'vue-error' | 'console-warn'
  level: 'error' | 'warn'
  message: string
  stack?: string
  source?: string
  line?: number
  column?: number
  route: string
  pageUrl: string
  userAgent: string
  sessionId: string
  timestamp: string
  fingerprint: string
  lastInteraction?: {
    type: 'click'
    target: string
    at: string
  }
  extras?: Record<string, unknown>
}

const FALLBACK_COLLECTOR = 'http://127.0.0.1:8787/errors'
const SESSION_ID =
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `session-${Date.now()}`

let monitorStarted = false

export function startConsoleMonitor(reportVueError?: (handler: (err: unknown, info: string) => void) => void): void {
  if (monitorStarted || !shouldEnableMonitor()) {
    return
  }

  monitorStarted = true

  const collectorUrl = import.meta.env.VITE_CONSOLE_COLLECTOR_URL || FALLBACK_COLLECTOR
  const includeWarn = import.meta.env.VITE_CONSOLE_MONITOR_INCLUDE_WARN === 'true'
  const perMinuteLimit = Number(import.meta.env.VITE_CONSOLE_MONITOR_MAX_PER_MINUTE || 120)
  const localDedupeMs = Number(import.meta.env.VITE_CONSOLE_MONITOR_LOCAL_DEDUPE_MS || 5000)

  const sentFingerprintAt = new Map<string, number>()
  let minuteWindowStart = Date.now()
  let minuteWindowCount = 0
  let reporting = false
  let lastInteraction: MonitorEvent['lastInteraction']

  const noteClick = (event: MouseEvent) => {
    const target = event.target
    if (!(target instanceof Element)) {
      return
    }

    lastInteraction = {
      type: 'click',
      target: describeElement(target),
      at: new Date().toISOString(),
    }
  }

  document.addEventListener('click', noteClick, { capture: true })

  const emit = (partial: Omit<MonitorEvent, 'app' | 'route' | 'pageUrl' | 'userAgent' | 'sessionId' | 'timestamp' | 'fingerprint'>) => {
    if (reporting) {
      return
    }

    const now = Date.now()
    if (now - minuteWindowStart >= 60000) {
      minuteWindowStart = now
      minuteWindowCount = 0
    }

    if (minuteWindowCount >= perMinuteLimit) {
      return
    }

    const route = `${window.location.pathname}${window.location.search}${window.location.hash}`
    const message = normalizeMessage(partial.message)
    if (shouldIgnoreSyntheticProbe(partial.type, message, route)) {
      return
    }
    const stack = typeof partial.stack === 'string' ? partial.stack.slice(0, 8000) : undefined

    const fingerprint = createFingerprint([
      partial.type,
      partial.level,
      message,
      stack || '',
      partial.source || '',
      String(partial.line || ''),
      String(partial.column || ''),
      route,
    ])

    const lastSentAt = sentFingerprintAt.get(fingerprint)
    if (lastSentAt && now - lastSentAt < localDedupeMs) {
      return
    }

    sentFingerprintAt.set(fingerprint, now)
    minuteWindowCount += 1

    const payload: MonitorEvent = {
      app: 'frontend-admin',
      type: partial.type,
      level: partial.level,
      message,
      stack,
      source: partial.source,
      line: partial.line,
      column: partial.column,
      route,
      pageUrl: window.location.href,
      userAgent: navigator.userAgent,
      sessionId: SESSION_ID,
      timestamp: new Date().toISOString(),
      fingerprint,
      lastInteraction,
      extras: partial.extras,
    }

    reporting = true
    sendPayload(collectorUrl, payload).finally(() => {
      reporting = false
    })
  }

  window.addEventListener('error', event => {
    const errorEvent = event as ErrorEvent
    emit({
      type: 'window-error',
      level: 'error',
      message: errorEvent.message || 'Unknown window error',
      stack: errorEvent.error instanceof Error ? errorEvent.error.stack : undefined,
      source: errorEvent.filename,
      line: errorEvent.lineno,
      column: errorEvent.colno,
    })
  })

  window.addEventListener('unhandledrejection', event => {
    const reason = event.reason
    emit({
      type: 'unhandled-rejection',
      level: 'error',
      message: extractReasonMessage(reason),
      stack: reason instanceof Error ? reason.stack : undefined,
    })
  })

  const rawError = console.error.bind(console)
  console.error = (...args: unknown[]) => {
    rawError(...args)
    emit({
      type: 'console-error',
      level: 'error',
      message: stringifyArgs(args),
      extras: { args: sanitizeArgs(args) },
    })
  }

  if (includeWarn) {
    const rawWarn = console.warn.bind(console)
    console.warn = (...args: unknown[]) => {
      rawWarn(...args)
      emit({
        type: 'console-warn',
        level: 'warn',
        message: stringifyArgs(args),
        extras: { args: sanitizeArgs(args) },
      })
    }
  }

  if (typeof reportVueError === 'function') {
    reportVueError((err, info) => {
      const isError = err instanceof Error
      emit({
        type: 'vue-error',
        level: 'error',
        message: isError ? err.message : normalizeMessage(String(err)),
        stack: isError ? err.stack : undefined,
        extras: { info },
      })
    })
  }
}

function shouldIgnoreSyntheticProbe(type: MonitorEvent['type'], message: string, route: string): boolean {
  // Chrome 在表格/标签页重排时会偶发抛出这类 ResizeObserver 噪音，
  // 通常没有可定位业务堆栈，不应进入自动修复队列。
  if (
    type === 'window-error'
    && /^ResizeObserver loop (completed with undelivered notifications\.|limit exceeded)$/.test(message)
  ) {
    return true
  }

  if (type === 'window-error' && route.startsWith('/e2e-validate')) {
    return /^e2e-trigger-\d{4}-\d{2}-\d{2}T/.test(message)
  }

  // Synthetic CORS probe logs used by console monitor validation.
  if (type === 'console-error' && message.startsWith('cors-')) {
    return true
  }

  return false
}

function shouldEnableMonitor(): boolean {
  if (!import.meta.env.DEV) {
    return false
  }

  return import.meta.env.VITE_ENABLE_CONSOLE_MONITOR !== 'false'
}

function sendPayload(url: string, payload: MonitorEvent): Promise<void> {
  const body = JSON.stringify(payload)

  if (typeof navigator.sendBeacon === 'function' && shouldUseBeacon(url)) {
    const ok = navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }))
    if (ok) {
      return Promise.resolve()
    }
  }

  return fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body,
    keepalive: true,
    mode: 'cors',
    credentials: 'omit',
  }).then(() => undefined)
}

function shouldUseBeacon(url: string): boolean {
  try {
    return new URL(url, window.location.href).origin === window.location.origin
  } catch {
    return false
  }
}

function createFingerprint(parts: string[]): string {
  const source = parts.join('|').toLowerCase()
  let hash = 0
  for (let i = 0; i < source.length; i += 1) {
    hash = (hash << 5) - hash + source.charCodeAt(i)
    hash |= 0
  }
  return `fp_${Math.abs(hash)}`
}

function normalizeMessage(message: string): string {
  return message.slice(0, 2000)
}

function extractReasonMessage(reason: unknown): string {
  if (reason instanceof Error) {
    return reason.message
  }

  if (typeof reason === 'string') {
    return reason
  }

  try {
    return JSON.stringify(reason)
  } catch {
    return String(reason)
  }
}

function stringifyArgs(args: unknown[]): string {
  return args
    .map(item => {
      if (item instanceof Error) {
        return item.stack || item.message
      }
      if (typeof item === 'string') {
        return item
      }
      try {
        return JSON.stringify(item)
      } catch {
        return String(item)
      }
    })
    .join(' | ')
    .slice(0, 2000)
}

function sanitizeArgs(args: unknown[]): unknown[] {
  return args.slice(0, 5).map(arg => {
    if (arg instanceof Error) {
      return {
        name: arg.name,
        message: arg.message,
        stack: arg.stack?.slice(0, 4000),
      }
    }

    if (typeof arg === 'string' || typeof arg === 'number' || typeof arg === 'boolean' || arg === null) {
      return arg
    }

    try {
      return JSON.parse(JSON.stringify(arg))
    } catch {
      return String(arg)
    }
  })
}

function describeElement(target: Element): string {
  const tag = target.tagName.toLowerCase()
  const id = target.id ? `#${target.id}` : ''
  const classes =
    typeof target.className === 'string' && target.className.trim().length > 0
      ? `.${target.className.trim().split(/\s+/).slice(0, 3).join('.')}`
      : ''
  const text = target.textContent?.trim().replace(/\s+/g, ' ').slice(0, 40)
  const textFragment = text ? `:${text}` : ''
  return `${tag}${id}${classes}${textFragment}`
}
