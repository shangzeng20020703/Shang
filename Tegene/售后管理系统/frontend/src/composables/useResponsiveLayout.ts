import { computed } from 'vue'
import { useWindowSize } from '@vueuse/core'

export const MOBILE_LAYOUT_BREAKPOINT = 768
export const COMPACT_LAYOUT_BREAKPOINT = 1100
export const LANDSCAPE_COMPACT_MAX_WIDTH = 1180
export const LANDSCAPE_COMPACT_MAX_HEIGHT = 560

export function useResponsiveLayout() {
  const { width, height } = useWindowSize()

  const isLandscape = computed(() => width.value > height.value)
  const isMobile = computed(() => width.value <= MOBILE_LAYOUT_BREAKPOINT && !isLandscape.value)
  const isCompact = computed(() => width.value <= COMPACT_LAYOUT_BREAKPOINT)
  const isLandscapeCompact = computed(() => {
    return isLandscape.value
      && width.value >= MOBILE_LAYOUT_BREAKPOINT
      && width.value <= LANDSCAPE_COMPACT_MAX_WIDTH
      && height.value <= LANDSCAPE_COMPACT_MAX_HEIGHT
  })

  return {
    width,
    height,
    isLandscape,
    isMobile,
    isCompact,
    isLandscapeCompact,
  }
}
