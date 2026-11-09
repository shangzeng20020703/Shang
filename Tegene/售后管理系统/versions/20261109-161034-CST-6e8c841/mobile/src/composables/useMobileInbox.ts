import { computed, reactive } from 'vue'
import { get, post } from '@/utils/request'
import { chinaNowYearMonth } from '@/utils/format'

const sharedState = reactive({
  unreadNotifications: 0,
  approvalReminderNotifications: 0,
  approvalReminderBadgeCount: 0,
  pendingApprovals: 0,
  submittedUpdates: 0,
  payslipPending: 0,
  payslipReplied: 0,
})

export function useMobileInbox(canUseApproval: () => boolean, canManagePayslips: () => boolean = () => false) {
  let timer: number | null = null
  const state = sharedState

  const approvalDisplayCount = computed(() => {
    return Math.max(state.pendingApprovals, state.approvalReminderBadgeCount) + state.submittedUpdates
  })

  const nonApprovalUnreadNotifications = computed(() => {
    return Math.max(0, state.unreadNotifications - state.approvalReminderNotifications - state.submittedUpdates)
  })

  const totalActionCount = computed(() => {
    return nonApprovalUnreadNotifications.value
      + approvalDisplayCount.value
      + state.payslipPending
      + state.payslipReplied
  })

  async function loadInboxCounts() {
    const { year, month } = chinaNowYearMonth()
    const tasks = await Promise.allSettled([
      get<{ count: number }>('/notifications/unread-count'),
      get<any>('/notifications', { page: 1, page_size: 50, unread_only: true }),
      canUseApproval() ? get<any>('/approval/my-pending', { skip: 0, limit: 5 }) : Promise.resolve({ total: 0, items: [] }),
      Promise.resolve([] as any[]),
    ])

    const [unreadRes, unreadListRes, pendingRes, paySlipsRes] = tasks

    state.unreadNotifications = unreadRes.status === 'fulfilled' ? Number(unreadRes.value?.count || 0) : 0
    const unreadItems = unreadListRes.status === 'fulfilled' && Array.isArray(unreadListRes.value?.items)
      ? unreadListRes.value.items
      : []
    const unreadApprovalItems = unreadItems.filter((item: any) => {
      const text = `${item.title || ''}${item.content || ''}`
      return String(item.notif_type || '').toLowerCase() === 'approval'
        && String(item.ref_type || '').toLowerCase() === 'approval_instance'
        && text.trim()
    })
    const rawApprovalReminderNotifications = unreadApprovalItems.filter((item: any) => {
      const text = `${item.title || ''}${item.content || ''}`
      return /催办|请及时处理|待审批|待处理|新的审批待办|提交了|流转到你|转交给你处理|转办提醒/.test(text)
    }).length
    const pendingApprovalTotal = pendingRes.status === 'fulfilled'
      ? Number(pendingRes.value?.total || pendingRes.value?.items?.length || 0)
      : 0
    state.pendingApprovals = pendingApprovalTotal
    state.approvalReminderNotifications = rawApprovalReminderNotifications
    state.approvalReminderBadgeCount = pendingRes.status === 'fulfilled'
      ? Math.min(rawApprovalReminderNotifications, pendingApprovalTotal)
      : rawApprovalReminderNotifications
    state.submittedUpdates = unreadApprovalItems.length - rawApprovalReminderNotifications

    const slips = paySlipsRes.status === 'fulfilled' && Array.isArray(paySlipsRes.value) ? paySlipsRes.value : []
    const actionable = slips.filter((item) => ['queried', 'replied', 'escalated', 'confirmed'].includes(String(item.query_status || '').toLowerCase()))
    state.payslipPending = actionable.filter((item) => String(item.query_status).toLowerCase() === 'queried').length
    state.payslipReplied = actionable.filter((item) => String(item.query_status).toLowerCase() === 'replied').length
  }

  async function markAllNotificationsRead() {
    await post('/notifications/read-all')
    state.unreadNotifications = 0
    state.approvalReminderNotifications = 0
    state.approvalReminderBadgeCount = 0
    state.submittedUpdates = 0
  }

  function startAutoRefresh(intervalMs = 30000) {
    if (timer) return
    timer = window.setInterval(() => {
      if (document.hidden) return
      void loadInboxCounts()
    }, intervalMs)
  }

  function stopAutoRefresh() {
    if (!timer) return
    window.clearInterval(timer)
    timer = null
  }

  return {
    state,
    approvalDisplayCount,
    totalActionCount,
    loadInboxCounts,
    markAllNotificationsRead,
    startAutoRefresh,
    stopAutoRefresh,
  }
}
