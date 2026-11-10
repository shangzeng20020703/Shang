import { computed, reactive } from 'vue'
import { get, resolveApiUrl } from '@/utils/request'
import { getMobileAuthToken } from '@/utils/authSession'

export const MOBILE_CHAT_EVENT = 'mobile-chat-event'

const sharedState = reactive({
  unreadCount: 0,
})

let realtimeSource: EventSource | null = null
let reconnectTimer: number | null = null

export function useMobileChat() {
  let timer: number | null = null
  const state = sharedState

  const badgeText = computed(() => {
    if (state.unreadCount <= 0) return ''
    return state.unreadCount > 99 ? '99+' : `${state.unreadCount}`
  })

  async function loadUnreadCount() {
    try {
      const result = await get<{ count: number }>('/chat/unread-count')
      state.unreadCount = Number(result?.count || 0)
    } catch {
      state.unreadCount = 0
    }
  }

  function startAutoRefresh(intervalMs = 15000) {
    if (timer) return
    timer = window.setInterval(() => {
      if (document.hidden) return
      void loadUnreadCount()
    }, intervalMs)
  }

  function stopAutoRefresh() {
    if (!timer) return
    window.clearInterval(timer)
    timer = null
  }

  function emitChatEvent(payload: Record<string, any>) {
    window.dispatchEvent(new CustomEvent(MOBILE_CHAT_EVENT, { detail: payload }))
  }

  function startRealtime() {
    if (realtimeSource || typeof EventSource === 'undefined') return
    const token = getMobileAuthToken()
    if (!token) return
    const url = new URL(resolveApiUrl('/chat/stream'), window.location.origin)
    url.searchParams.set('token', token)
    realtimeSource = new EventSource(url.toString())
    realtimeSource.addEventListener('chat', (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data || '{}')
        if (typeof payload.unread_count === 'number') {
          state.unreadCount = payload.unread_count
        }
        emitChatEvent(payload)
      } catch {
        emitChatEvent({ type: 'snapshot' })
      }
    })
    realtimeSource.onerror = () => {
      realtimeSource?.close()
      realtimeSource = null
      if (reconnectTimer) return
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null
        startRealtime()
      }, 3000)
    }
  }

  function stopRealtime() {
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (realtimeSource) {
      realtimeSource.close()
      realtimeSource = null
    }
  }

  return {
    state,
    badgeText,
    loadUnreadCount,
    startAutoRefresh,
    stopAutoRefresh,
    startRealtime,
    stopRealtime,
  }
}
