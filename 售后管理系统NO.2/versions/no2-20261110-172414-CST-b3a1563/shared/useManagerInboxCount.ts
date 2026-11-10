import { ref, watch, onBeforeUnmount } from 'vue'

// The open list publishes its exact total immediately after decisions.
export function useManagerInboxCount(
  enabled: () => boolean,
  get: (path: string, params: Record<string, any>) => Promise<any>,
) {
  const count = ref<number | null>(null)
  let version = 0
  async function refresh() {
    const id = ++version
    if (!enabled()) {
      count.value = null
      return
    }
    if (document.hidden) return
    try {
      const data = await get('/monthly/manager-inbox', { count_only: true })
      if (id === version && enabled()) count.value = data.total
    } catch {
      if (id === version) count.value = null
    }
  }
  function sync(e: Event) {
    if (enabled()) {
      version++
      count.value = (e as CustomEvent<number>).detail
    }
  }
  watch(enabled, refresh, { immediate: true })
  const timer = setInterval(refresh, 30000)
  window.addEventListener('manager-inbox-count', sync)
  window.addEventListener('focus', refresh)
  document.addEventListener('visibilitychange', refresh)
  onBeforeUnmount(() => {
    clearInterval(timer)
    version++
    window.removeEventListener('manager-inbox-count', sync)
    window.removeEventListener('focus', refresh)
    document.removeEventListener('visibilitychange', refresh)
  })
  return count
}
