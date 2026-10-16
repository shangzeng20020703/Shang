<template>
  <ion-page>
    <ion-content fullscreen class="app-gradient-page message-enterprise-page">
      <div class="mobile-shell message-shell">
        <section class="message-command-card" aria-label="通知待办">
          <div class="message-command-card__head">
            <div>
              <span class="message-kicker">通知待办</span>
              <h1>移动通知中心</h1>
              <p>聚合审批动态、考勤异常与系统通知。</p>
            </div>
            <span class="message-bell" aria-hidden="true">
              <ion-icon :icon="notificationsOutline" />
            </span>
          </div>

          <label class="message-search" aria-label="搜索消息">
            <ion-icon :icon="searchOutline" />
            <input v-model.trim="messageQuery" type="search" placeholder="搜索审批、考勤、通知内容" />
          </label>

          <div class="message-summary-grid">
            <div>
              <span>总待处理</span>
              <strong>{{ inbox.totalActionCount }}</strong>
            </div>
            <div>
              <span>通知未读</span>
              <strong>{{ inbox.state.unreadNotifications }}</strong>
            </div>
            <div>
              <span>审批动态</span>
              <strong>{{ inbox.approvalDisplayCount }}</strong>
            </div>
          </div>
        </section>

        <section class="section-block message-segment-card">
          <ion-segment v-model="segment">
            <ion-segment-button value="all"><ion-label>全部</ion-label></ion-segment-button>
            <ion-segment-button value="approval"><ion-label>审批</ion-label></ion-segment-button>
            <ion-segment-button value="notification"><ion-label>通知</ion-label></ion-segment-button>
          </ion-segment>
        </section>

        <section
          v-if="showSection('approval')"
          class="section-block solid-card"
          style="padding: 16px 18px"
        >
          <div class="section-head">
            <h2 class="page-section-title" style="margin: 0">审批待办与进展</h2>
            <button
              class="inline-link-button"
              type="button"
              @click="router.push(canUseApproval ? '/app/tabs/approvals' : '/app/tabs/approvals?segment=submitted')"
            >
              查看全部
            </button>
          </div>
          <div v-if="filteredApprovalCards.length" class="stack-list" style="margin-top: 12px">
            <button
              v-for="item in filteredApprovalCards"
              :key="`approval-${item.mode}-${item.id}`"
              type="button"
              class="quick-link-card"
              @click="openApproval(item.id, item.mode)"
            >
              <div>
                <div class="quick-link-card__title">{{ item.title }}</div>
                <div class="quick-link-card__caption">{{ item.caption }}</div>
              </div>
              <span class="compact-chip">{{ item.badge }}</span>
            </button>
          </div>
          <div v-else class="muted-text" style="margin-top: 12px">当前没有审批待办或新的审批进展。</div>
        </section>


        <section
          v-if="showSection('notification')"
          class="section-block solid-card"
          style="padding: 16px 18px"
        >
          <div class="section-head">
            <h2 class="page-section-title" style="margin: 0">系统通知</h2>
            <button
              v-if="notifications.some((item) => !item.is_read)"
              class="inline-link-button"
              type="button"
              @click="markAllRead"
            >
              全部已读
            </button>
          </div>
          <div class="filter-row" style="margin-top: 12px">
            <button
              type="button"
              class="filter-pill"
              :class="{ 'is-active': !unreadOnly }"
              @click="unreadOnly = false"
            >
              全部
            </button>
            <button
              type="button"
              class="filter-pill"
              :class="{ 'is-active': unreadOnly }"
              @click="unreadOnly = true"
            >
              仅未读
            </button>
          </div>
          <div v-if="filteredNotifications.length" class="stack-list" style="margin-top: 12px">
            <button
              v-for="item in filteredNotifications"
              :key="`notif-${item.id}`"
              type="button"
              class="notice-card"
              :class="{ 'notice-card--unread': !item.is_read }"
              @click="openNotification(item)"
            >
              <div class="sheet-row">
                <strong class="notice-card__title">{{ item.title }}</strong>
                <span class="compact-chip">{{ notificationTypeText(item.notif_type) }}</span>
              </div>
              <div class="notice-card__meta">{{ item.content || '系统消息' }}</div>
              <div class="notice-card__meta">{{ formatDateTime(item.created_at) }}</div>
            </button>
          </div>
          <div v-else class="muted-text" style="margin-top: 12px">当前没有通知消息。</div>
        </section>
      </div>
    </ion-content>
  </ion-page>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { IonContent, IonIcon, IonLabel, IonPage, IonSegment, IonSegmentButton, onIonViewDidEnter, onIonViewDidLeave, onIonViewWillEnter } from '@ionic/vue'
import { notificationsOutline, searchOutline } from 'ionicons/icons'
import { useAuthStore } from '@/stores/auth'
import { get, post } from '@/utils/request'
import { chinaNowYearMonth, formatDateTime, formatStatus } from '@/utils/format'
import { useMobileInbox } from '@/composables/useMobileInbox'

const auth = useAuthStore()
const router = useRouter()
const inbox = useMobileInbox(
  () => auth.displayRole !== 'employee',
  () => auth.roles.includes('hr') || auth.roles.includes('finance'),
)

const segment = ref<'all' | 'approval' | 'payslip' | 'notification'>('all')
const unreadOnly = ref(false)
const messageQuery = ref('')
const approvalPending = ref<any[]>([])
const submittedHighlights = ref<any[]>([])
const paySlipMessages = ref<any[]>([])
const notifications = ref<any[]>([])

const canUseApproval = computed(() => auth.displayRole !== 'employee')
let refreshTimer: number | null = null
let autoMarkingRead = false

const approvalCards = computed(() => {
  const pending = approvalPending.value.slice(0, 4).map((item) => ({
    id: item.id,
    mode: 'pending' as const,
    title: item.title || item.business_name || `审批单 #${item.id}`,
    caption: `${item.module || item.business_type || '审批'} · ${formatDateTime(item.created_at)}`,
    badge: formatStatus(item.status),
  }))
  const submitted = submittedHighlights.value.slice(0, 4).map((item) => ({
    id: item.id,
    mode: 'submitted' as const,
    title: item.title || item.business_name || `审批单 #${item.id}`,
    caption: `${item.module || item.business_type || '审批'} · ${submittedStatusText(item.status)}`,
    badge: formatStatus(item.status),
  }))
  return [...pending, ...submitted].slice(0, 6)
})

const paySlipCards = computed(() => {
  return paySlipMessages.value.slice(0, 6)
})

const filteredNotifications = computed(() => {
  return notifications.value
    .filter((item) => {
      return unreadOnly.value ? !item.is_read : true
    })
    .filter((item) => matchesMessageQuery([
      item.title,
      item.content,
      notificationTypeText(item.notif_type),
      formatDateTime(item.created_at),
    ]))
})
const filteredApprovalCards = computed(() => {
  return approvalCards.value.filter((item) => matchesMessageQuery([item.title, item.caption, item.badge]))
})
const filteredPaySlipCards = computed(() => {
  return paySlipCards.value.filter((item) => matchesMessageQuery([
    `${item.year}-${String(item.month).padStart(2, '0')} 工资条`,
    paySlipStatusText(item.query_status),
    paySlipBadgeText(item.query_status),
  ]))
})

function matchesMessageQuery(values: Array<string | number | undefined | null>) {
  const keyword = messageQuery.value.trim().toLowerCase()
  if (!keyword) return true
  return values
    .map((value) => String(value || '').toLowerCase())
    .some((value) => value.includes(keyword))
}

function showSection(section: 'approval' | 'payslip' | 'notification') {
  return segment.value === 'all' || segment.value === section
}

function paySlipStatusText(status?: string) {
  const map: Record<string, string> = {
    queried: '你已提交工资疑问，等待 HR 回复',
    replied: 'HR 已回复，等待你确认',
    escalated: '工资疑问已升级处理',
    confirmed: '工资条沟通已确认结束',
  }
  return map[String(status || '').toLowerCase()] || '工资条已发送'
}

function paySlipBadgeText(status?: string) {
  const map: Record<string, string> = {
    queried: '待回复',
    replied: '已回复',
    escalated: '已升级',
    confirmed: '已确认',
  }
  return map[String(status || '').toLowerCase()] || '已发送'
}

function submittedStatusText(status?: string) {
  if (String(status || '').toLowerCase() === 'pending') return '仍在审批中'
  if (String(status || '').toLowerCase() === 'approved') return '审批已通过'
  if (String(status || '').toLowerCase() === 'rejected') return '审批已拒绝'
  if (String(status || '').toLowerCase() === 'withdrawn') return '申请已撤回'
  return '审批状态已更新'
}

function notificationTypeText(type?: string) {
  const map: Record<string, string> = {
    approval: '审批消息',
    system: '系统通知',
    leave: '请假提醒',
    contract: '合同提醒',
    performance: '绩效提醒',
  }
  return map[String(type || '').toLowerCase()] || '通知'
}

function openApproval(id: number, mode: 'pending' | 'submitted') {
  router.push({
    path: `/app/approval/${id}`,
    query: { mode },
  })
}

function openPayslip(item: any) {
  if (auth.roles.includes('hr') || auth.roles.includes('finance')) {
    router.push({
      path: `/app/payroll-center/pay-slips/${item.id}`,
    })
    return
  }
  router.push({
    path: `/app/payslips/${item.salary_record_id}`,
    query: {
      year: item.year,
      month: item.month,
    },
  })
}

async function openNotification(item: any) {
  if (!item.is_read) {
    await post(`/notifications/${item.id}/read`)
    item.is_read = true
    await inbox.loadInboxCounts()
  }
  if (String(item.ref_type || '').toLowerCase() === 'approval_instance' && item.ref_id) {
    const titleText = String(item.title || '')
    const contentText = String(item.content || '')
    const isApprovalTodo = String(item.notif_type || '').toLowerCase() === 'approval'
      && /催办|待处理|待审批|请及时处理/.test(`${titleText}${contentText}`)
    router.push({
      path: `/app/approval/${item.ref_id}`,
      query: { mode: isApprovalTodo ? 'pending' : 'submitted' },
    })
  }
}

async function markAllRead() {
  await inbox.markAllNotificationsRead()
  notifications.value = notifications.value.map((item) => ({ ...item, is_read: true }))
}

async function autoMarkViewedNotificationsRead() {
  if (autoMarkingRead) return
  if (!notifications.value.some((item) => !item.is_read)) return
  autoMarkingRead = true
  try {
    await inbox.markAllNotificationsRead()
    notifications.value = notifications.value.map((item) => ({ ...item, is_read: true }))
  } finally {
    autoMarkingRead = false
  }
}

async function loadMessages() {
  const { year, month } = chinaNowYearMonth()
  const tasks = await Promise.allSettled([
    canUseApproval.value ? get<any>('/approval/my-pending', { skip: 0, limit: 6 }) : Promise.resolve({ items: [] }),
    get<any>('/approval/my-submitted', { skip: 0, limit: 10 }),
    Promise.resolve([] as any[]),
    get<any>('/notifications', { page: 1, page_size: 20 }),
  ])

  const [pendingRes, submittedRes, paySlipsRes, notificationsRes] = tasks

  const pendingItems = pendingRes.status === 'fulfilled'
    ? (Array.isArray(pendingRes.value?.items) ? pendingRes.value.items : [])
    : []
  approvalPending.value = pendingItems

  const submittedItems = submittedRes.status === 'fulfilled'
    ? (Array.isArray(submittedRes.value?.items) ? submittedRes.value.items : [])
    : []
  submittedHighlights.value = submittedItems.filter((item: any) => String(item.status || '').toLowerCase() !== 'pending')

  const slips = paySlipsRes.status === 'fulfilled' && Array.isArray(paySlipsRes.value) ? paySlipsRes.value : []
  paySlipMessages.value = slips.filter((item) => ['queried', 'replied', 'escalated', 'confirmed'].includes(String(item.query_status || '').toLowerCase()))

  notifications.value = notificationsRes.status === 'fulfilled' && Array.isArray(notificationsRes.value?.items)
    ? notificationsRes.value.items
    : []

  await inbox.loadInboxCounts()
  await autoMarkViewedNotificationsRead()
}

onIonViewWillEnter(loadMessages)

onIonViewDidEnter(() => {
  inbox.startAutoRefresh(30000)
  refreshTimer = window.setInterval(() => {
    if (document.hidden) return
    void loadMessages()
  }, 30000)
})

onIonViewDidLeave(() => {
  inbox.stopAutoRefresh()
  if (refreshTimer) {
    window.clearInterval(refreshTimer)
    refreshTimer = null
  }
})
</script>

<style scoped>
.message-enterprise-page {
  --background: #f7f9fc;
}

.message-shell {
  padding: 16px 14px calc(24px + env(safe-area-inset-bottom));
}

.message-command-card,
.message-segment-card,
.message-shell :deep(.solid-card) {
  border: 1px solid rgba(214, 223, 235, 0.92);
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 10px 26px rgba(15, 35, 63, 0.06);
}

.message-command-card {
  padding: 18px 16px 16px;
}

.message-command-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.message-kicker {
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.message-command-card h1 {
  margin: 6px 0 0;
  color: #111827;
  font-size: 22px;
  font-weight: 800;
  line-height: 1.18;
}

.message-command-card p {
  margin: 8px 0 0;
  color: #64748b;
  font-size: 13px;
  line-height: 1.45;
}

.message-bell {
  flex: 0 0 auto;
  width: 44px;
  height: 44px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: #eef6ff;
  color: #0b63f6;
  font-size: 22px;
}

.message-search {
  min-height: 44px;
  margin-top: 16px;
  padding: 0 12px;
  border: 1px solid #dbe4ef;
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 9px;
  background: #f8fafc;
  color: #64748b;
}

.message-search ion-icon {
  font-size: 18px;
}

.message-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: #111827;
  font: inherit;
  font-size: 14px;
}

.message-summary-grid {
  margin-top: 14px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.message-summary-grid div {
  min-height: 62px;
  border: 1px solid #e5edf6;
  border-radius: 8px;
  display: grid;
  gap: 6px;
  place-items: center;
  background: #ffffff;
}

.message-summary-grid span {
  color: #64748b;
  font-size: 11px;
  font-weight: 800;
}

.message-summary-grid strong {
  color: #111827;
  font-size: 18px;
  font-weight: 900;
}

.message-segment-card {
  padding: 7px;
}

.message-segment-card ion-segment {
  --background: #f1f5f9;
  border-radius: 8px;
}

.message-segment-card ion-segment-button {
  --border-radius: 8px;
  --indicator-color: #ffffff;
  --color: #64748b;
  --color-checked: #0b63f6;
  min-height: 36px;
  font-size: 12px;
  font-weight: 800;
}

.message-shell :deep(.page-section-title) {
  color: #111827;
  font-size: 17px;
  font-weight: 800;
}

.message-shell :deep(.quick-link-card),
.message-shell :deep(.notice-card) {
  border-radius: 8px;
  border-color: #e5edf6;
  background: #ffffff;
  box-shadow: none;
}

.message-shell :deep(.compact-chip),
.message-shell :deep(.filter-pill.is-active) {
  background: #eef6ff;
  color: #0b63f6;
}

.message-shell :deep(.inline-link-button) {
  color: #0b63f6;
  font-weight: 800;
}

@media (max-width: 360px) {
  .message-shell {
    padding-inline: 10px;
  }

  .message-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
