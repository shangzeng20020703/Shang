<template>
  <ion-page>
    <ion-tabs>
      <ion-router-outlet />
      <ion-tab-bar v-if="!hideGlobalTabbar" slot="bottom" class="mobile-tabbar">
        <ion-tab-button tab="home" href="/app/tabs/home">
          <ion-icon :icon="homeOutline" />
          <ion-label>首页</ion-label>
        </ion-tab-button>
        <ion-tab-button tab="workbench" href="/app/tabs/workbench">
          <ion-icon :icon="gridOutline" />
          <ion-label>工作台</ion-label>
        </ion-tab-button>
        <ion-tab-button tab="approvals" href="/app/tabs/approvals">
          <span v-if="approvalBadge" class="tab-badge">{{ approvalBadge }}</span>
          <ion-icon :icon="documentTextOutline" />
          <ion-label>审批</ion-label>
        </ion-tab-button>
        <ion-tab-button tab="messages" href="/app/notifications">
          <span v-if="messageBadge" class="tab-badge">{{ messageBadge }}</span>
          <ion-icon :icon="chatbubblesOutline" />
          <ion-label>通知</ion-label>
        </ion-tab-button>
        <ion-tab-button tab="profile" href="/app/tabs/profile">
          <ion-icon :icon="personCircleOutline" />
          <ion-label>我的</ion-label>
        </ion-tab-button>
      </ion-tab-bar>
    </ion-tabs>
  </ion-page>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  IonIcon,
  IonLabel,
  IonPage,
  IonRouterOutlet,
  IonTabBar,
  IonTabButton,
  IonTabs,
  onIonViewDidEnter,
  onIonViewDidLeave,
  onIonViewWillEnter,
} from '@ionic/vue'
import { chatbubblesOutline, documentTextOutline, gridOutline, homeOutline, personCircleOutline } from 'ionicons/icons'
import { useAuthStore } from '@/stores/auth'
import { useMobileInbox } from '@/composables/useMobileInbox'

const auth = useAuthStore()
const route = useRoute()
const inbox = useMobileInbox(
  () => auth.displayRole !== 'employee',
  () => auth.roles.includes('hr') || auth.roles.includes('finance'),
)
const hideGlobalTabbar = computed(() => {
  return [
    '/app/attendance',
    '/app/leave',
    '/app/approval/start',
    '/app/payslips',
    '/app/assets',
    '/app/chat',
  ].some((prefix) => route.path === prefix || route.path.startsWith(`${prefix}/`))
})
const approvalBadge = computed(() => {
  const count = inbox.approvalDisplayCount.value
  if (count > 0) {
    return count > 99 ? '99+' : `${count}`
  }
  return ''
})
const messageBadge = computed(() => {
  const count = inbox.state.unreadNotifications
  return count ? (count > 99 ? '99+' : String(count)) : ''
})

async function refreshInboxCounts() {
  if (!auth.isLoggedIn || !auth.user) return
  await inbox.loadInboxCounts()
}

onIonViewWillEnter(refreshInboxCounts)

onIonViewDidEnter(() => {
  inbox.startAutoRefresh(30000)
})

watch(
  () => [auth.user?.id, auth.roles.join(','), route.fullPath],
  () => {
    void refreshInboxCounts()
  },
  { immediate: true },
)

onIonViewDidLeave(() => {
  inbox.stopAutoRefresh()
})

onBeforeUnmount(() => inbox.stopAutoRefresh())
</script>
