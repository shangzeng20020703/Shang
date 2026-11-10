<template>
  <nav ref="rootRef" class="mobile-breadcrumb-shell" aria-label="面包屑导航">
    <div class="mobile-breadcrumb-bar">
      <button class="mobile-breadcrumb-icon-button" type="button" aria-label="回到首页" @click="go('/app/tabs/home')">
        <ion-icon :icon="homeOutline" />
      </button>

      <ol class="mobile-breadcrumb-list">
        <li v-for="(crumb, index) in breadcrumbs" :key="`${crumb.label}-${index}`" class="mobile-breadcrumb-item">
          <ion-icon v-if="index > 0" class="mobile-breadcrumb-separator" :icon="chevronForwardOutline" />
          <button
            v-if="canNavigate(crumb)"
            class="mobile-breadcrumb-link"
            type="button"
            @click="goBreadcrumb(crumb)"
          >
            {{ crumb.label }}
          </button>
          <span v-else class="mobile-breadcrumb-current">{{ crumb.label }}</span>
        </li>
      </ol>

      <button
        class="mobile-breadcrumb-menu-button"
        :class="{ 'is-open': menuOpen }"
        type="button"
        aria-label="打开页面导航"
        :aria-expanded="menuOpen"
        @click.stop="menuOpen = !menuOpen"
      >
        <ion-icon :icon="gridOutline" />
      </button>
    </div>

    <transition name="mobile-breadcrumb-panel">
      <section v-if="menuOpen" class="mobile-breadcrumb-panel" aria-label="可访问页面">
        <header class="mobile-breadcrumb-panel__head">
          <strong>页面导航</strong>
          <button type="button" aria-label="关闭页面导航" @click="menuOpen = false">
            <ion-icon :icon="closeOutline" />
          </button>
        </header>

        <div class="mobile-breadcrumb-destination-grid">
          <button
            v-for="item in destinations"
            :key="item.path"
            class="mobile-breadcrumb-destination"
            :class="{ 'is-active': isActiveDestination(item.path) }"
            type="button"
            @click="go(item.path)"
          >
            <ion-icon :icon="item.icon" />
            <span>{{ item.label }}</span>
          </button>
        </div>
      </section>
    </transition>
  </nav>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { IonIcon } from '@ionic/vue'
import {
  chatbubblesOutline,
  chevronForwardOutline,
  closeOutline,
  documentTextOutline,
  gridOutline,
  homeOutline,
  notificationsOutline,
  personCircleOutline,
} from 'ionicons/icons'
import { roleWorkbench, type AppRole } from '@/config/access'
import { useAuthStore } from '@/stores/auth'

interface BreadcrumbItem {
  label: string
  to?: string
}

interface DestinationItem {
  label: string
  path: string
  icon: string
  roles: AppRole[]
}

const allMobileRoles: AppRole[] = ['employee', 'manager', 'hr', 'finance', 'asset_admin']

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const rootRef = ref<HTMLElement | null>(null)
const menuOpen = ref(false)

const breadcrumbs = computed<BreadcrumbItem[]>(() => {
  const raw = (route.meta as Record<string, unknown>).breadcrumb
  if (Array.isArray(raw)) {
    const items = raw
      .map((item) => normalizeBreadcrumbItem(item))
      .filter((item): item is BreadcrumbItem => Boolean(item))
    if (items.length) return items
  }

  return [{ label: currentDestination.value?.label || '当前页面' }]
})

const destinations = computed<DestinationItem[]>(() => {
  const base: DestinationItem[] = [
    { label: '首页', path: '/app/tabs/home', icon: homeOutline, roles: allMobileRoles },
    { label: '工作台', path: '/app/tabs/workbench', icon: gridOutline, roles: allMobileRoles },
    { label: '审批中心', path: '/app/tabs/approvals', icon: documentTextOutline, roles: allMobileRoles },
    { label: '消息', path: '/app/messages', icon: chatbubblesOutline, roles: allMobileRoles },
    { label: '通知待办', path: '/app/notifications', icon: notificationsOutline, roles: allMobileRoles },
    { label: '我的', path: '/app/tabs/profile', icon: personCircleOutline, roles: allMobileRoles },
  ]

  const workbenchDestinations = roleWorkbench(auth.roles).map((item) => ({
    label: item.title,
    path: item.path,
    icon: item.icon || gridOutline,
    roles: item.roles,
  }))

  return dedupeDestinations([...base, ...workbenchDestinations]).filter((item) =>
    item.roles.some((role) => auth.roles.includes(role)),
  )
})

const currentDestination = computed(() => destinations.value.find((item) => matchesDestinationPath(item.path)))
const activeDestinationPath = computed(() => {
  if (currentDestination.value) return currentDestination.value.path

  const destinationPaths = new Set(destinations.value.map((item) => item.path))
  const parentCrumb = [...breadcrumbs.value].reverse().find((crumb) => crumb.to && destinationPaths.has(crumb.to))
  return parentCrumb?.to || ''
})

function normalizeBreadcrumbItem(item: unknown): BreadcrumbItem | null {
  if (!item || typeof item !== 'object') return null
  const source = item as Record<string, unknown>
  const label = typeof source.label === 'string' ? source.label.trim() : ''
  if (!label) return null
  const to = typeof source.to === 'string' && source.to.trim() ? source.to.trim() : undefined
  return { label, to }
}

function dedupeDestinations(items: DestinationItem[]) {
  const seen = new Set<string>()
  return items.filter((item) => {
    if (seen.has(item.path)) return false
    seen.add(item.path)
    return true
  })
}

function canNavigate(crumb: BreadcrumbItem) {
  return Boolean(crumb.to && crumb.to !== route.path)
}

function isActiveDestination(path: string) {
  return activeDestinationPath.value === path
}

function matchesDestinationPath(path: string) {
  if (route.path === path) return true
  return !path.startsWith('/app/tabs/') && route.path.startsWith(`${path}/`)
}

function go(path: string) {
  menuOpen.value = false
  if (route.path === path) return
  void router.push(path)
}

function goBreadcrumb(crumb: BreadcrumbItem) {
  if (!crumb.to) return
  go(crumb.to)
}

function handleDocumentClick(event: MouseEvent) {
  if (!menuOpen.value) return
  const target = event.target
  if (target instanceof Node && rootRef.value?.contains(target)) return
  menuOpen.value = false
}

watch(
  () => route.fullPath,
  () => {
    menuOpen.value = false
  },
)

onMounted(() => {
  document.body.classList.add('mobile-breadcrumb-active')
  document.addEventListener('click', handleDocumentClick)
})

onBeforeUnmount(() => {
  document.body.classList.remove('mobile-breadcrumb-active')
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<style scoped>
.mobile-breadcrumb-shell {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 45;
  padding: calc(env(safe-area-inset-top) + 6px) 12px 6px;
  pointer-events: none;
}

.mobile-breadcrumb-bar,
.mobile-breadcrumb-panel {
  box-sizing: border-box;
  width: min(100%, var(--mobile-window-width));
  margin: 0 auto;
  pointer-events: auto;
}

.mobile-breadcrumb-bar {
  min-height: 42px;
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) 38px;
  align-items: center;
  gap: 6px;
  padding: 4px;
  border: 1px solid rgba(203, 213, 225, 0.92);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.1);
  backdrop-filter: blur(16px);
}

.mobile-breadcrumb-icon-button,
.mobile-breadcrumb-menu-button,
.mobile-breadcrumb-panel__head button {
  width: 34px;
  height: 34px;
  padding: 0;
  border: 0;
  border-radius: 8px;
  display: inline-grid;
  place-items: center;
  background: #f1f5f9;
  color: #0f172a;
}

.mobile-breadcrumb-icon-button ion-icon,
.mobile-breadcrumb-menu-button ion-icon,
.mobile-breadcrumb-panel__head button ion-icon {
  width: 19px;
  height: 19px;
}

.mobile-breadcrumb-menu-button.is-open {
  background: #dbeafe;
  color: #1d4ed8;
}

.mobile-breadcrumb-list {
  min-width: 0;
  margin: 0;
  padding: 0 2px;
  display: flex;
  align-items: center;
  gap: 2px;
  overflow-x: auto;
  list-style: none;
  scrollbar-width: none;
}

.mobile-breadcrumb-list::-webkit-scrollbar {
  display: none;
}

.mobile-breadcrumb-item {
  min-width: max-content;
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.mobile-breadcrumb-separator {
  width: 14px;
  height: 14px;
  color: #94a3b8;
  flex: 0 0 auto;
}

.mobile-breadcrumb-link,
.mobile-breadcrumb-current {
  max-width: 116px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 700;
  line-height: 1;
}

.mobile-breadcrumb-link {
  padding: 8px 6px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #2563eb;
}

.mobile-breadcrumb-current {
  padding: 8px 4px;
  color: #0f172a;
}

.mobile-breadcrumb-panel {
  margin-top: 8px;
  border: 1px solid rgba(203, 213, 225, 0.94);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 18px 38px rgba(15, 23, 42, 0.16);
  overflow: hidden;
}

.mobile-breadcrumb-panel__head {
  height: 48px;
  padding: 0 12px 0 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e2e8f0;
}

.mobile-breadcrumb-panel__head strong {
  font-size: 15px;
  color: #0f172a;
}

.mobile-breadcrumb-destination-grid {
  max-height: min(54vh, 420px);
  padding: 12px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  overflow-y: auto;
}

.mobile-breadcrumb-destination {
  min-height: 44px;
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  background: #ffffff;
  color: #0f172a;
  text-align: left;
}

.mobile-breadcrumb-destination ion-icon {
  width: 20px;
  height: 20px;
  color: #2563eb;
}

.mobile-breadcrumb-destination span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 700;
}

.mobile-breadcrumb-destination.is-active {
  border-color: #93c5fd;
  background: #eff6ff;
}

.mobile-breadcrumb-panel-enter-active,
.mobile-breadcrumb-panel-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.mobile-breadcrumb-panel-enter-from,
.mobile-breadcrumb-panel-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

@media (max-width: 360px) {
  .mobile-breadcrumb-link,
  .mobile-breadcrumb-current {
    max-width: 88px;
  }

  .mobile-breadcrumb-destination-grid {
    grid-template-columns: 1fr;
  }
}
</style>
