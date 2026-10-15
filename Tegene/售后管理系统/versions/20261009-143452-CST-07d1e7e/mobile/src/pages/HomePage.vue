<template>
  <ion-page>
    <ion-content fullscreen class="app-gradient-page home-enterprise-page">
      <div class="mobile-shell home-enterprise-shell">
        <header class="home-app-head">
          <h1>售后管理系统</h1>
          <button class="home-notification-button" type="button" aria-label="通知待办" @click="router.push('/app/notifications')">
            <ion-icon :icon="notificationsOutline" />
            <span v-if="notificationBadge">{{ notificationBadge }}</span>
          </button>
        </header>

        <section class="home-identity-card" aria-label="员工基本信息">
          <div class="home-identity-card__top">
            <img
              v-if="employeeAvatarUrl"
              class="home-avatar"
              :src="employeeAvatarUrl"
              :alt="`${employeeName}照片`"
            >
            <span v-else class="home-avatar" aria-hidden="true">{{ avatarText }}</span>
            <div class="home-identity-main">
              <div class="home-identity-title">
                <div class="home-identity-copy">
                  <div class="home-name-row">
                    <h1>{{ employeeName }}</h1>
                    <span class="home-id-badge">ID {{ employeeNo }}</span>
                  </div>
                  <p class="home-position-line">{{ employeePosition }}</p>
                  <p class="home-org-path">{{ employeeDepartment }} / {{ employeeBranch }}</p>
                </div>
                <button
                  class="home-card-chevron"
                  type="button"
                  aria-label="查看我的信息"
                  @click="router.push('/app/tabs/profile')"
                >
                  ›
                </button>
              </div>
              <div v-if="employeeCompany" class="home-profile-line">
                <span>{{ employeeCompany }}</span>
              </div>
            </div>
          </div>

          <div class="home-metric-strip" aria-label="员工摘要">
            <button
              v-for="item in stats"
              :key="item.label"
              type="button"
              @click="openHomeMetric(item)"
            >
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
            </button>
          </div>
        </section>

        <section class="home-attendance-card section-block" aria-label="今日打卡">
          <div class="home-card-head">
            <div>
              <span class="home-section-kicker">今日打卡</span>
              <h2>{{ attendanceSummary.status }}</h2>
            </div>
            <span class="home-status-chip" :class="{ 'is-warning': attendanceSummary.hasAbnormal }">
              {{ attendanceSummary.hasAbnormal ? '需关注' : '运行中' }}
            </span>
          </div>

          <div class="attendance-timeline">
            <div>
              <span>上班</span>
              <strong>{{ attendanceSummary.clockIn }}</strong>
              <small>
                <i class="home-rule-dot" :class="attendanceDotClass(attendanceSummary.clockInTone)" />
                应到 {{ attendanceSummary.expectedIn }}
              </small>
            </div>
            <i aria-hidden="true" />
            <div>
              <span>下班</span>
              <strong>{{ attendanceSummary.clockOut }}</strong>
              <small>
                <i class="home-rule-dot" :class="attendanceDotClass(attendanceSummary.clockOutTone)" />
                应退 {{ attendanceSummary.expectedOut }}
              </small>
            </div>
          </div>

          <button class="home-primary-action" type="button" @click="router.push('/app/attendance')">
            <ion-icon :icon="locationOutline" />
            <span>立即打卡</span>
            <ion-icon :icon="chevronForwardOutline" />
          </button>

          <p class="home-location-line">{{ attendanceSummary.location }}</p>
        </section>

        <section class="home-panel section-block" aria-label="快捷入口">
          <div class="home-card-head">
            <div>
              <span class="home-section-kicker">可自定义</span>
              <h2>快捷入口</h2>
            </div>
            <button class="home-text-button" type="button" @click="toggleShortcutEditing">
              {{ shortcutEditing ? '完成' : '管理' }}
            </button>
          </div>

          <div class="home-service-grid" :class="{ 'is-editing': shortcutEditing }">
            <div
              v-for="item in selectedShortcutActions"
              :key="item.path"
              :data-shortcut-path="item.path"
              class="home-service-item"
              :class="{ 'is-editing': shortcutEditing, 'is-dragging': draggingShortcutPath === item.path }"
              role="button"
              tabindex="0"
              :aria-grabbed="draggingShortcutPath === item.path"
              @click="openShortcut(item.path)"
              @keyup.enter="openShortcut(item.path)"
              @pointerdown="startShortcutDrag($event, item.path)"
              @pointermove="dragShortcut"
              @pointerup="finishShortcutDrag"
              @pointercancel="finishShortcutDrag"
            >
              <button
                v-if="shortcutEditing"
                class="shortcut-remove-button"
                type="button"
                aria-label="删除入口"
                @pointerdown.stop
                @click.stop="removeShortcut(item.path)"
              >
                <ion-icon :icon="closeOutline" />
              </button>
              <span class="home-service-icon" :style="{ '--service-accent': item.accent }">
                <ion-icon :icon="item.icon" />
              </span>
              <strong>{{ item.title }}</strong>
              <span v-if="shortcutEditing" class="shortcut-drag-handle" aria-hidden="true">
                <ion-icon :icon="reorderThreeOutline" />
              </span>
            </div>
          </div>

          <div v-if="shortcutEditing" class="shortcut-add-panel">
            <div class="shortcut-add-head">
              <span>添加入口</span>
              <small>与工作台目录保持一致</small>
            </div>
            <div
              v-for="group in addableShortcutGroups"
              :key="group.key"
              class="shortcut-feature-group"
            >
              <p>{{ group.title }}</p>
              <div class="shortcut-feature-list">
                <button
                  v-for="item in group.items"
                  :key="item.path"
                  type="button"
                  @click="addShortcut(item.path)"
                >
                  <span class="shortcut-feature-icon" :style="{ '--service-accent': item.accent }">
                    <ion-icon :icon="item.icon" />
                  </span>
                  <span>
                    <strong>{{ item.title }}</strong>
                    <small>{{ item.feature }}</small>
                  </span>
                  <ion-icon :icon="addCircleOutline" />
                </button>
              </div>
            </div>
            <p v-if="!addableShortcutGroups.length" class="shortcut-empty-line">所有可用功能点已添加</p>
          </div>
        </section>
      </div>

      <div v-if="abnormalSheetVisible" class="home-sheet-mask" @click.self="abnormalSheetVisible = false">
        <section class="home-abnormal-panel" role="dialog" aria-modal="true" aria-labelledby="home-abnormal-title">
          <header>
            <div>
              <span>本月异常</span>
              <h2 id="home-abnormal-title">{{ homeSummary.monthlyAbnormalCount }} 条记录</h2>
            </div>
            <button type="button" aria-label="关闭" @click="abnormalSheetVisible = false">×</button>
          </header>

          <div v-if="monthlyAbnormalRows.length" class="home-abnormal-list">
            <article v-for="record in monthlyAbnormalRows" :key="`${record.id || record.date}-${record.clock_in_time || record.clock_out_time || ''}`">
              <div class="home-abnormal-main">
                <span>{{ abnormalRecordTimeText(record) }}</span>
                <strong>{{ abnormalRecordTypeText(record) }}</strong>
                <small v-if="abnormalRecordReasonText(record)">{{ abnormalRecordReasonText(record) }}</small>
              </div>
              <div class="home-abnormal-actions">
                <button type="button" @click="openAbnormalCorrection(record)">补卡</button>
                <button type="button" @click="openAbnormalApproval(record)">其他审批</button>
              </div>
            </article>
          </div>
          <div v-else class="home-abnormal-empty">本月暂无考勤异常</div>
        </section>
      </div>

      <div v-if="compTimeSheetVisible" class="home-sheet-mask" @click.self="compTimeSheetVisible = false">
        <section class="home-abnormal-panel home-comp-time-panel" role="dialog" aria-modal="true" aria-labelledby="home-comp-time-title">
          <header>
            <div>
              <span id="home-comp-time-title">调休余额</span>
            </div>
            <button type="button" aria-label="关闭" @click="compTimeSheetVisible = false">×</button>
          </header>

          <div v-if="compTimeDetailLoading" class="home-abnormal-empty">正在同步调休明细...</div>
          <div v-else-if="compTimeDetailRows.length" class="home-comp-time-list">
            <article v-for="item in compTimeDetailRows" :key="item.id">
              <div class="home-comp-time-main">
                <span>{{ item.dateText }} · {{ item.dateType }}</span>
                <strong>{{ item.timeText }}</strong>
                <small v-if="compTimeDetailMetaText(item)">{{ compTimeDetailMetaText(item) }}</small>
              </div>
              <div class="home-comp-time-amount">
                <span>计为调休</span>
                <strong>{{ item.hoursText }}</strong>
              </div>
            </article>
          </div>
          <div v-else class="home-abnormal-empty">暂无加班转调休记录</div>
        </section>
      </div>
    </ion-content>
  </ion-page>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { onIonViewDidEnter, onIonViewDidLeave, onIonViewWillEnter, IonContent, IonIcon, IonPage } from '@ionic/vue'
import {
  addCircleOutline,
  chevronForwardOutline,
  closeOutline,
  locationOutline,
  notificationsOutline,
  reorderThreeOutline,
} from 'ionicons/icons'
import { useAuthStore } from '@/stores/auth'
import { roleLabels, roleWorkbench, type AppRole, type WorkbenchItem } from '@/config/access'
import type { ShortcutCategory } from '@/config/workbench'
import { get } from '@/utils/request'
import { chinaDateString, chinaNowYearMonth, formatDateTime, formatStatus } from '@/utils/format'
import { useMobileInbox } from '@/composables/useMobileInbox'

const auth = useAuthStore()
const router = useRouter()
const shortcutEditing = ref(false)
const shortcutPaths = ref<string[]>([])
const draggingShortcutPath = ref('')
const abnormalSheetVisible = ref(false)
const compTimeSheetVisible = ref(false)
const compTimeDetailLoading = ref(false)
const compTimeDetailLoaded = ref(false)
const monthlyAbnormalRows = ref<any[]>([])
const compTimeDetailRows = ref<CompTimeDetailRow[]>([])
const inbox = useMobileInbox(
  () => auth.displayRole !== 'employee',
  () => auth.roles.includes('hr') || auth.roles.includes('finance'),
)
let refreshTimer: number | null = null
let secondaryTimer: number | null = null

const homeSummary = reactive({
  pendingApprovals: 0,
  monthlyAbnormalCount: 0,
  annualLeaveBalanceText: '0',
  compTimeBalanceText: '0',
  payslipCount: 0,
  assignmentCount: 0,
  peopleTotal: 0,
  payrollCount: 0,
  payrollNetTotal: 0,
  assetTotal: 0,
})
const attendanceSummary = reactive({
  clockIn: '未打卡',
  clockOut: '未打卡',
  expectedIn: '--:--',
  expectedOut: '--:--',
  status: '读取中',
  location: '正在同步今日打卡规则',
  hasAbnormal: false,
  clockInTone: 'pending',
  clockOutTone: 'pending',
})

const roleLabel = computed(() => roleLabels[auth.displayRole])
const avatarText = computed(() => {
  const name = String(auth.user?.name || '').trim()
  return name ? name.slice(-2) : 'HR'
})

function valueToProfileText(value: unknown): string {
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number') return String(value)
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    for (const key of ['name', 'title', 'label', 'short_name', 'full_name', 'display_name']) {
      const text = valueToProfileText(record[key])
      if (text) return text
    }
  }
  return ''
}

function readProfileText(keys: string[], fallback = '-') {
  const profile = (auth.user || {}) as Record<string, unknown>
  for (const key of keys) {
    const text = valueToProfileText(profile[key])
    if (text) return text
  }
  return fallback
}

const employeeName = computed(() => readProfileText(['name'], '售后人员'))
const employeeAvatarUrl = computed(() => readProfileText(
  ['avatar_url', 'avatar', 'photo_url', 'photo', 'profile_photo', 'portrait_url'],
  '',
))
const employeePosition = computed(() => readProfileText(
  ['position_name', 'position', 'post_name', 'post', 'job_position', 'job_title', 'title'],
  roleLabel.value || '员工',
))
const employeeDepartment = computed(() => readProfileText(
  ['department_name', 'department', 'dept_name', 'dept', 'department_full_name'],
  '未配置部门',
))
const employeeBranch = computed(() => readProfileText(
  ['location_name', 'branch_name', 'branch', 'subsidiary_name', 'subsidiary', 'division_name', 'office_name', 'site_name'],
  '未配置总部',
))
const employeeCompany = computed(() => readProfileText(
  ['company_name', 'company', 'corp_name', 'corporation_name', 'enterprise_name', 'tenant_name', 'organization_name'],
  '',
))
const employeeNo = computed(() => readProfileText(
  ['employee_no', 'employeeNo', 'staff_no', 'staffNo', 'job_number', 'work_no', 'employee_code', 'code'],
  auth.user?.id ? String(auth.user.id) : '-',
))

interface ShortcutAction {
  title: string
  caption: string
  path: string
  icon: string
  roles: AppRole[]
  accent: string
  category: ShortcutCategory
  categoryTitle: string
  feature: string
}

const shortcutCategoryTitles: Record<ShortcutCategory, string> = {
  daily: '日常服务',
  approval: '审批事务',
  personal: '个人资产',
  team: '团队管理',
  admin: '专项管理',
}

type HomeMetricKind = 'monthly_abnormal' | 'comp_time_detail'
interface HomeMetric {
  label: string
  value: string
  path?: string
  kind?: HomeMetricKind
}

interface CompTimeDetailRow {
  id: string
  date: string
  dateText: string
  dateType: string
  hoursText: string
  sourceText: string
  timeText: string
  reasonText: string
  sortKey: string
}

const stats = computed(() => {
  const common: HomeMetric[] = [
    { label: '待办', value: `${homeSummary.pendingApprovals}`, path: '/app/tabs/approvals' },
    { label: '本月异常', value: `${homeSummary.monthlyAbnormalCount}`, kind: 'monthly_abnormal' },
    { label: '年假余额', value: homeSummary.annualLeaveBalanceText, path: '/app/leave' },
    { label: '调休余额', value: homeSummary.compTimeBalanceText, kind: 'comp_time_detail' },
    { label: '我的项目', value: `${homeSummary.assignmentCount}`, path: '/app/field' },
  ]
  return common
})

function openHomeMetric(item: HomeMetric) {
  if (item.kind === 'monthly_abnormal') {
    compTimeSheetVisible.value = false
    abnormalSheetVisible.value = true
    return
  }
  if (item.kind === 'comp_time_detail') {
    abnormalSheetVisible.value = false
    compTimeSheetVisible.value = true
    void loadCompTimeDetailRows()
    return
  }
  if (item.path) {
    router.push(item.path)
  }
}

function toShortcutAction(item: WorkbenchItem): ShortcutAction {
  const category = item.shortcutCategory
  return {
    ...item,
    category,
    categoryTitle: shortcutCategoryTitles[category],
    feature: item.caption,
  }
}

const serviceActions = computed<ShortcutAction[]>(() => {
  return roleWorkbench(auth.roles).map(toShortcutAction)
})

const shortcutStorageKey = computed(() => `aftersales_home_shortcuts_${auth.user?.id || 'guest'}`)
const defaultShortcutPaths = computed(() => {
  const preferred = [
    '/app/attendance',
    '/app/approval/start',
    '/app/tabs/approvals?segment=submitted',
    '/app/field',
    '/app/leave',
    '/app/notifications',
  ]
  const available = new Set(serviceActions.value.map((item) => item.path))
  return preferred.filter((path) => available.has(path)).slice(0, 6)
})
const selectedShortcutActions = computed(() => {
  const byPath = new Map(serviceActions.value.map((item) => [item.path, item]))
  return shortcutPaths.value
    .map((path) => byPath.get(path))
    .filter((item): item is NonNullable<typeof item> => Boolean(item))
})
const notificationBadge = computed(() => {
  const count = inbox.state.unreadNotifications
  if (count <= 0) return ''
  return count > 99 ? '99+' : `${count}`
})
const addableShortcutActions = computed(() => {
  const selected = new Set(shortcutPaths.value)
  return serviceActions.value.filter((item) => !selected.has(item.path))
})
const addableShortcutGroups = computed(() => {
  const groups = new Map<ShortcutCategory, { key: ShortcutCategory; title: string; items: ShortcutAction[] }>()
  for (const item of addableShortcutActions.value) {
    if (!groups.has(item.category)) {
      groups.set(item.category, {
        key: item.category,
        title: item.categoryTitle,
        items: [],
      })
    }
    groups.get(item.category)?.items.push(item)
  }
  return Array.from(groups.values())
})

function compactNumberText(value: number, digits = 1) {
  if (!Number.isFinite(value) || value <= 0) return '0'
  const fixed = Number(value.toFixed(digits))
  return String(fixed)
}

function balanceNumber(value: unknown) {
  const parsed = Number(value ?? 0)
  return Number.isFinite(parsed) ? parsed : 0
}

function leaveTypeForBalance(balance: any, typeMap: Map<number, any>) {
  const embedded = balance?.leave_type && typeof balance.leave_type === 'object' ? balance.leave_type : null
  return embedded || typeMap.get(Number(balance?.leave_type_id)) || null
}

function isCompTimeBalance(balance: any, type: any) {
  const code = String(type?.code || balance?.leave_type_code || '').toLowerCase()
  const name = String(type?.name || balance?.leave_type_name || '').trim()
  return ['comp_time', 'comp', 'time_off', 'lieu', 'adjust_rest'].includes(code) || name.includes('调休')
}

function isAnnualLeaveBalance(balance: any, type: any) {
  const code = String(type?.code || balance?.leave_type_code || '').toLowerCase()
  const name = String(type?.name || balance?.leave_type_name || '').trim()
  return code === 'annual' || code === 'annual_leave' || name.includes('年假')
}

function formatLeaveBalanceAmount(balance: any, type: any) {
  const remainingDays = balanceNumber(balance?.remaining_days)
  const remainingHours = balanceNumber(balance?.remaining_hours)
  const leaveUnit = String(type?.leave_unit || balance?.leave_unit || '').toLowerCase()
  if (leaveUnit === 'hour' || remainingHours > 0) {
    return `${compactNumberText(remainingHours || remainingDays * 8)}h`
  }
  return `${compactNumberText(remainingDays)}天`
}

function formatTypedLeaveBalance(
  balances: any[],
  leaveTypes: any[],
  matcher: (balance: any, type: any) => boolean,
) {
  const typeMap = new Map(leaveTypes.map((item) => [Number(item.id), item]))
  for (const balance of balances) {
    const type = leaveTypeForBalance(balance, typeMap)
    if (matcher(balance, type)) return formatLeaveBalanceAmount(balance, type)
  }
  return '0'
}

function formatCompTimeBalance(balances: any[], leaveTypes: any[]) {
  const typeMap = new Map(leaveTypes.map((item) => [Number(item.id), item]))
  let totalDays = 0
  let totalHours = 0
  let useHourUnit = false

  for (const balance of balances) {
    const type = leaveTypeForBalance(balance, typeMap)
    if (!isCompTimeBalance(balance, type)) continue
    const remainingDays = balanceNumber(balance?.remaining_days)
    const remainingHours = balanceNumber(balance?.remaining_hours)
    const leaveUnit = String(type?.leave_unit || balance?.leave_unit || '').toLowerCase()
    const hoursPerDay = balanceNumber(type?.hours_per_day || balance?.hours_per_day || 8) || 8
    if (leaveUnit === 'hour' || remainingHours > 0) {
      useHourUnit = true
      totalHours += remainingHours || remainingDays * hoursPerDay
    } else {
      totalDays += remainingDays
    }
  }

  if (useHourUnit) {
    return `${compactNumberText(totalHours + totalDays * 8)}h`
  }
  if (totalDays > 0) {
    return `${compactNumberText(totalDays)}天`
  }
  return '0'
}

function formatCompTimeHoursText(hours: any, minutes: any) {
  const numericHours = Number(hours)
  if (Number.isFinite(numericHours) && numericHours > 0) {
    return `${compactNumberText(numericHours)}小时`
  }
  const numericMinutes = Number(minutes || 0)
  if (numericMinutes > 0) return `${compactNumberText(numericMinutes / 60)}小时`
  return '0小时'
}

function normalizeOvertimeTimeText(value: any) {
  const text = String(value || '').trim()
  if (!text) return ''
  const match = text.match(/(\d{1,2}):(\d{2})/)
  if (!match) return text
  return `${match[1].padStart(2, '0')}:${match[2]}`
}

function overtimeTimeRangeText(startTime: any, endTime: any) {
  const start = normalizeOvertimeTimeText(startTime)
  const end = normalizeOvertimeTimeText(endTime)
  if (start && end) return `${start}-${end}`
  return start || end || '未记录具体时段'
}

function overtimeSourceLabel(value: any) {
  const source = String(value || '').trim()
  if (source === 'attendance_record') return '上下班打卡'
  if (source === 'overtime_request') return '加班申请'
  if (source === 'approval_instance') return '审批单'
  if (source === 'attendance_effect') return '审批生效'
  if (source === 'leave_balance_adjustment') return '余额账本'
  return source || '来源记录'
}

function isCompTimeOvertimeItem(item: any) {
  const settlement = String(item?.settlement || '').toLowerCase()
  const settlementLabel = String(item?.settlement_label || '').trim()
  return ['comp_time', 'rest', 'time_off', 'lieu', 'adjust_rest'].includes(settlement) || settlementLabel.includes('调休')
}

function normalizeCompTimeDetailRow(item: any): CompTimeDetailRow | null {
  if (!isCompTimeOvertimeItem(item)) return null
  const date = String(item?.date || '').trim()
  const minutes = Number(item?.minutes || 0)
  const hours = Number(item?.hours ?? (minutes / 60))
  if (!date || !Number.isFinite(hours) || hours <= 0) return null
  const source = String(item?.source || '').trim()
  const id = String(item?.id || `${date}-${item?.source || 'overtime'}-${item?.start_time || ''}-${item?.end_time || ''}-${hours}`)
  const sourceNote = String(item?.source_note || '').trim()
  return {
    id,
    date,
    dateText: String(item?.display_date_label || '').trim() || formatMonthDayText(date),
    dateType: String(item?.date_type_label || '工作日'),
    hoursText: formatCompTimeHoursText(hours, minutes),
    sourceText: overtimeSourceLabel(source),
    timeText: source === 'leave_balance_adjustment'
      ? (sourceNote || '未匹配到具体加班来源')
      : overtimeTimeRangeText(item?.start_time, item?.end_time),
    reasonText: source === 'leave_balance_adjustment'
      ? String(item?.reason || '历史同步或人工调整').trim()
      : String(item?.reason || '').trim(),
    sortKey: `${date} ${item?.end_time || item?.start_time || ''} ${id}`,
  }
}

function compTimeDetailMetaText(item: CompTimeDetailRow) {
  return [item.sourceText, item.reasonText].filter(Boolean).join(' · ')
}

function monthTargetDate(year: number, month: number) {
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-15`
}

async function loadCompTimeDetailRows(force = false) {
  if (compTimeDetailLoading.value) return
  if (!force && compTimeDetailLoaded.value) return
  compTimeDetailLoading.value = true
  try {
    const { year, month } = chinaNowYearMonth()
    try {
      const directRows = await get<any[]>('/attendance/mobile/comp-time-details', { year })
      if (Array.isArray(directRows) && directRows.length) {
        const byId = new Map<string, CompTimeDetailRow>()
        for (const item of directRows) {
          const row = normalizeCompTimeDetailRow(item)
          if (row) byId.set(row.id, row)
        }
        compTimeDetailRows.value = Array.from(byId.values()).sort((a, b) => b.sortKey.localeCompare(a.sortKey))
        compTimeDetailLoaded.value = true
        return
      }
    } catch {
      // 兼容尚未部署新接口的环境，继续用月度考勤明细兜底。
    }
    const monthList = Array.from({ length: month }, (_, index) => month - index)
    const results = await Promise.allSettled(
      monthList.map((monthValue) => get<any>('/attendance/mobile/dashboard', {
        range_type: 'month',
        target_date: monthTargetDate(year, monthValue),
      })),
    )
    const byId = new Map<string, CompTimeDetailRow>()
    for (const result of results) {
      if (result.status !== 'fulfilled') continue
      const items = result.value?.details?.overtime_items
      if (!Array.isArray(items)) continue
      for (const item of items) {
        const row = normalizeCompTimeDetailRow(item)
        if (row) byId.set(row.id, row)
      }
    }
    compTimeDetailRows.value = Array.from(byId.values()).sort((a, b) => b.sortKey.localeCompare(a.sortKey))
    compTimeDetailLoaded.value = true
  } finally {
    compTimeDetailLoading.value = false
  }
}

function isHomeAbnormalRecord(record: any) {
  const status = String(record?.status || '').toLowerCase()
  const display = String(record?.display_status || '')
  const anomaly = String(record?.anomaly_type || '')
  return Boolean(
    record?.is_abnormal
    || ['late', 'early_leave', 'missed_clock', 'absent', 'abnormal'].includes(status)
    || ['迟到', '早退', '缺卡', '旷工', '异常'].some((item) => display.includes(item) || anomaly.includes(item)),
  )
}

function formatMonthDayText(value: any) {
  const text = String(value || '')
  const month = Number(text.slice(5, 7))
  const day = Number(text.slice(8, 10))
  if (month && day) return `${month}月${day}日`
  return text || '-'
}

function clockOnlyText(value: any) {
  const text = formatDateTime(value)
  const match = text.match(/(\d{2}:\d{2})/)
  return match?.[1] || ''
}

function abnormalRecordTypeText(record: any) {
  const status = formatStatus(record?.display_status || record?.status || '')
  const anomaly = String(record?.anomaly_type || '').trim()
  if (status && status !== '-' && !['正常', 'normal'].includes(status)) return status
  return anomaly || '考勤异常'
}

function abnormalRecordReasonText(record: any) {
  const anomaly = String(record?.anomaly_type || '').trim()
  const typeText = abnormalRecordTypeText(record)
  if (!anomaly || anomaly === typeText) return ''
  return anomaly
}

function abnormalRecordTimeText(record: any) {
  const typeText = `${abnormalRecordTypeText(record)} ${record?.anomaly_type || ''}`
  const clockValue = typeText.includes('早退') || typeText.includes('下班')
    ? record?.clock_out_time
    : record?.clock_in_time || record?.clock_out_time
  const time = clockOnlyText(clockValue)
  return [formatMonthDayText(record?.date), time || '未打卡'].filter(Boolean).join(' ')
}

function abnormalPunchType(record: any) {
  const text = `${abnormalRecordTypeText(record)} ${record?.anomaly_type || ''}`
  return text.includes('早退') || text.includes('下班') ? 'check_out' : 'check_in'
}

function abnormalQuery(record: any, businessCode?: string) {
  const query: Record<string, string> = {
    source: 'home_monthly_abnormal',
    attendance_date: String(record?.date || ''),
    attendance_status: abnormalRecordTypeText(record),
  }
  if (businessCode) query.business_code = businessCode
  if (businessCode === 'punch_correction') query.punch_type = abnormalPunchType(record)
  return query
}

function openAbnormalCorrection(record: any) {
  abnormalSheetVisible.value = false
  router.push({ path: '/app/approval/start', query: abnormalQuery(record, 'punch_correction') })
}

function openAbnormalApproval(record: any) {
  abnormalSheetVisible.value = false
  router.push({ path: '/app/approval/start', query: abnormalQuery(record) })
}

function applyMonthlyAbnormalDashboard(dashboard: any) {
  const rows = Array.isArray(dashboard?.details?.attendance_records)
    ? dashboard.details.attendance_records.filter(isHomeAbnormalRecord)
    : []
  rows.sort((a: any, b: any) => String(b?.date || '').localeCompare(String(a?.date || '')))
  monthlyAbnormalRows.value = rows
  const count = Number(dashboard?.details?.counts?.attendance?.abnormal ?? dashboard?.monthly_abnormal?.pending_days ?? 0)
  homeSummary.monthlyAbnormalCount = Math.max(rows.length, Number.isFinite(count) ? count : 0)
}

watch(
  () => [auth.user?.id || '', auth.roles.join(','), serviceActions.value.map((item) => item.path).join('|')],
  () => {
    compTimeDetailRows.value = []
    compTimeDetailLoaded.value = false
    loadShortcutConfig()
  },
  { immediate: true },
)

async function loadCriticalHomeData() {
  const { year } = chinaNowYearMonth()
  const targetDate = chinaDateString(new Date())

  const requests = await Promise.allSettled([
    get<any[]>('/ess/leave/balance', { year }),
    Promise.resolve([] as any[]),
    get<any>('/field/directory'),
    get<any[]>('/leave/types'),
    get<any>('/attendance/mobile/dashboard', { range_type: 'month', target_date: targetDate }),
  ])

  const [balance, payslips, assets, leaveTypes, monthlyDashboard] = requests
  const balanceRows = balance.status === 'fulfilled' && Array.isArray(balance.value) ? balance.value : []
  const leaveTypeRows = leaveTypes.status === 'fulfilled' && Array.isArray(leaveTypes.value) ? leaveTypes.value : []
  homeSummary.annualLeaveBalanceText = formatTypedLeaveBalance(balanceRows, leaveTypeRows, isAnnualLeaveBalance)
  homeSummary.compTimeBalanceText = formatCompTimeBalance(balanceRows, leaveTypeRows)
  homeSummary.payslipCount = payslips.status === 'fulfilled' ? payslips.value.length : 0
  homeSummary.assignmentCount = assets.status === 'fulfilled' ? new Set(assets.value.assignments.filter((item: any) => item.employee_id === auth.user?.id && item.status === 'active' && item.end_date >= targetDate).map((item: any) => item.project_id)).size : 0
  applyMonthlyAbnormalDashboard(monthlyDashboard.status === 'fulfilled' ? monthlyDashboard.value : null)
  await inbox.loadInboxCounts()
  homeSummary.pendingApprovals = inbox.approvalDisplayCount.value
  await loadAttendanceHomeData()
}

function formatClockOnly(value?: string | null) {
  if (!value) return '未打卡'
  const text = formatDateTime(value)
  if (!text || text === '-') return '未打卡'
  return text.slice(-5)
}

function normalizeAttendanceTone(status?: string | null, fallback: 'normal' | 'pending' = 'normal') {
  const text = String(status || '').toLowerCase()
  if (text.includes('迟到') || text.includes('late')) return 'warning'
  if (text.includes('早退') || text.includes('early')) return 'warning'
  if (text.includes('异常') || text.includes('缺') || text.includes('旷') || text.includes('abnormal') || text.includes('absent')) return 'danger'
  if (text.includes('未') || text.includes('pending')) return 'pending'
  if (text.includes('正常') || text.includes('normal') || text.includes('已')) return 'normal'
  return fallback
}

function attendanceDotClass(tone: string) {
  return {
    'is-normal': tone === 'normal',
    'is-warning': tone === 'warning',
    'is-danger': tone === 'danger',
    'is-pending': tone === 'pending',
  }
}

function resolveRuleLocation(runtime: any) {
  const locations = runtime?.rule?.locations
  if (Array.isArray(locations) && locations.length) {
    const first = locations[0] || {}
    return first.name || first.address || '已配置打卡地点'
  }
  return runtime?.rule?.gps_address || runtime?.rule?.name || '点击进入打卡页查看实时定位'
}

async function loadAttendanceHomeData() {
  const targetDate = chinaDateString(new Date())
  const [runtimeRes, dashboardRes] = await Promise.allSettled([
    get<any>('/attendance/my/runtime', { target_date: targetDate }),
    get<any>('/attendance/mobile/dashboard', { range_type: 'day', target_date: targetDate }),
  ])

  const runtime = runtimeRes.status === 'fulfilled' ? runtimeRes.value : null
  const dashboard = dashboardRes.status === 'fulfilled' ? dashboardRes.value : null
  const daily = dashboard?.daily || {}
  const hasAbnormal = Boolean(daily.has_today_abnormal || daily.today_anomaly || daily.anomaly_type)

  attendanceSummary.expectedIn = runtime?.expected?.clock_in || '--:--'
  attendanceSummary.expectedOut = runtime?.expected?.clock_out || '--:--'
  attendanceSummary.clockIn = formatClockOnly(daily.clock_in_time)
  attendanceSummary.clockOut = formatClockOnly(daily.clock_out_time)
  attendanceSummary.hasAbnormal = hasAbnormal
  attendanceSummary.clockInTone = normalizeAttendanceTone(daily.clock_in_status, daily.clock_in_time ? 'normal' : 'pending')
  attendanceSummary.clockOutTone = normalizeAttendanceTone(daily.clock_out_status, daily.clock_out_time ? 'normal' : 'pending')
  if (hasAbnormal) {
    if (attendanceSummary.clockInTone === 'normal') attendanceSummary.clockInTone = 'danger'
    if (attendanceSummary.clockOutTone === 'normal') attendanceSummary.clockOutTone = 'danger'
  }
  attendanceSummary.status = hasAbnormal
    ? formatStatus(daily.today_status || daily.status || daily.display_status || '异常')
    : daily.clock_in_time
      ? (daily.clock_out_time ? '今日已完成' : '下班待打卡')
      : '上班待打卡'
  attendanceSummary.location = resolveRuleLocation(runtime)
}

function normalizeShortcutPaths(paths: string[]) {
  const available = new Set(serviceActions.value.map((item) => item.path))
  const migratedPaths = paths.map((path) => (
    path === '/app/applications'
      ? '/app/tabs/approvals?segment=submitted'
      : path === '/app/messages'
        ? '/app/notifications'
        : path
  ))
  const next = migratedPaths.filter((path, index, list) => available.has(path) && list.indexOf(path) === index)
  return next.length ? next : defaultShortcutPaths.value
}

function loadShortcutConfig() {
  try {
    const stored = JSON.parse(localStorage.getItem(shortcutStorageKey.value) || '[]')
    shortcutPaths.value = normalizeShortcutPaths(Array.isArray(stored) ? stored.map(String) : [])
  } catch {
    shortcutPaths.value = defaultShortcutPaths.value
  }
}

function saveShortcutConfig() {
  localStorage.setItem(shortcutStorageKey.value, JSON.stringify(shortcutPaths.value))
}

function toggleShortcutEditing() {
  finishShortcutDrag()
  shortcutEditing.value = !shortcutEditing.value
}

function openShortcut(path: string) {
  if (shortcutEditing.value) return
  router.push(path)
}

function reorderShortcut(from: number, to: number) {
  if (from < 0 || to < 0 || from === to) return
  const next = [...shortcutPaths.value]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  shortcutPaths.value = next
}

function startShortcutDrag(event: PointerEvent, path: string) {
  if (!shortcutEditing.value) return
  draggingShortcutPath.value = path
  const target = event.currentTarget
  if (target instanceof HTMLElement) {
    target.setPointerCapture?.(event.pointerId)
  }
}

function dragShortcut(event: PointerEvent) {
  if (!shortcutEditing.value || !draggingShortcutPath.value) return
  event.preventDefault()
  const hovered = document.elementFromPoint(event.clientX, event.clientY)
  const target = hovered instanceof HTMLElement
    ? hovered.closest<HTMLElement>('[data-shortcut-path]')
    : null
  const targetPath = target?.dataset.shortcutPath
  if (!targetPath || targetPath === draggingShortcutPath.value) return
  const from = shortcutPaths.value.indexOf(draggingShortcutPath.value)
  const to = shortcutPaths.value.indexOf(targetPath)
  reorderShortcut(from, to)
}

function finishShortcutDrag(event?: PointerEvent) {
  if (draggingShortcutPath.value) {
    saveShortcutConfig()
  }
  const target = event?.currentTarget
  if (target instanceof HTMLElement && typeof event?.pointerId === 'number') {
    if (target.hasPointerCapture?.(event.pointerId)) {
      target.releasePointerCapture?.(event.pointerId)
    }
  }
  draggingShortcutPath.value = ''
}

function removeShortcut(path: string) {
  shortcutPaths.value = shortcutPaths.value.filter((item) => item !== path)
  if (draggingShortcutPath.value === path) {
    draggingShortcutPath.value = ''
  }
  saveShortcutConfig()
}

function addShortcut(path: string) {
  if (shortcutPaths.value.includes(path)) return
  shortcutPaths.value = [...shortcutPaths.value, path]
  saveShortcutConfig()
}

async function loadHomeData() {
  await loadCriticalHomeData()
}

onIonViewWillEnter(loadHomeData)

onIonViewDidEnter(() => {
  inbox.startAutoRefresh(30000)
  refreshTimer = window.setInterval(() => {
    if (document.hidden) return
    void loadCriticalHomeData()
  }, 45000)
})

onIonViewDidLeave(() => {
  inbox.stopAutoRefresh()
  if (refreshTimer) {
    window.clearInterval(refreshTimer)
    refreshTimer = null
  }
  if (secondaryTimer) {
    window.clearTimeout(secondaryTimer)
    secondaryTimer = null
  }
})
</script>

<style scoped>
.home-enterprise-page {
  --background: #f7f9fc;
}

.home-enterprise-shell {
  padding: 16px 14px calc(24px + env(safe-area-inset-bottom));
}

.home-app-head {
  min-height: 42px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.home-app-head h1 {
  margin: 0;
  color: #071224;
  font-size: 24px;
  font-weight: 900;
  line-height: 1;
}

.home-app-head button {
  position: relative;
  width: 38px;
  height: 38px;
  border: 0;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: #ffffff;
  color: #071224;
  font-size: 22px;
  box-shadow: 0 6px 16px rgba(20, 32, 56, 0.06);
}

.home-notification-button span {
  position: absolute;
  top: -5px;
  right: -5px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border: 2px solid #ffffff;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #ef4444;
  color: #ffffff;
  font-size: 10px;
  font-weight: 900;
  line-height: 1;
}

.home-identity-card,
.home-attendance-card,
.home-panel {
  border: 1px solid rgba(214, 223, 235, 0.92);
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 8px 22px rgba(20, 32, 56, 0.075);
}

.home-identity-card {
  padding: 18px 16px 15px;
  background: #ffffff;
}

.home-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.home-identity-card__top {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: start;
  gap: 16px;
}

.home-eyebrow,
.home-section-kicker {
  display: inline-flex;
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
}

.home-identity-card h1,
.home-card-head h2 {
  margin: 0;
  color: #111827;
  font-size: 22px;
  font-weight: 800;
  line-height: 1.16;
  letter-spacing: 0;
}

.home-card-head h2 {
  font-size: 18px;
}

.home-identity-card p {
  margin: 0;
  color: #475569;
  font-size: 13px;
  line-height: 1.45;
}

.home-avatar {
  flex: 0 0 auto;
  width: 72px;
  height: 72px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: linear-gradient(180deg, #dbeafe 0%, #a9c7f7 100%);
  color: #ffffff;
  font-size: 18px;
  font-weight: 800;
  object-fit: cover;
}

.home-identity-main {
  min-width: 0;
  padding-top: 2px;
}

.home-identity-title {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 30px;
  align-items: start;
  gap: 10px;
}

.home-identity-copy {
  min-width: 0;
}

.home-name-row {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.home-name-row h1 {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-id-badge {
  flex: 0 1 auto;
  max-width: 112px;
  min-height: 24px;
  padding: 0 8px;
  border: 1px solid #dbe4ef;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  overflow: hidden;
  background: #f8fafc;
  color: #64748b;
  font-size: 11px;
  font-weight: 800;
  line-height: 1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-position-line {
  margin-top: 8px !important;
  overflow: hidden;
  color: #334155;
  font-size: 14px !important;
  font-weight: 800;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-org-path {
  margin-top: 4px !important;
  overflow: hidden;
  color: #64748b;
  font-size: 13px !important;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-card-chevron {
  flex: 0 0 auto;
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: transparent;
  color: #64748b;
  font-size: 28px;
  line-height: 1;
  cursor: pointer;
}

.home-profile-line {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 10px;
}

.home-profile-line span {
  min-width: 0;
  max-width: 100%;
  min-height: 24px;
  padding: 0 8px;
  border: 1px solid #dbe4ef;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  overflow: hidden;
  color: #475569;
  background: #f8fafc;
  font-size: 11px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-metric-strip {
  margin-top: 14px;
  border-top: 1px solid #e5edf6;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.home-metric-strip button {
  min-width: 0;
  padding: 13px 4px 0;
  border: 0;
  display: grid;
  gap: 5px;
  background: transparent;
  text-align: center;
}

.home-metric-strip span {
  color: #64748b;
  font-size: 11px;
  line-height: 1.2;
}

.home-metric-strip strong {
  min-width: 0;
  overflow: hidden;
  color: #111827;
  font-size: 15px;
  font-weight: 800;
  line-height: 1.1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-sheet-mask {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 0 12px calc(12px + env(safe-area-inset-bottom));
  background: rgba(15, 23, 42, 0.36);
}

.home-abnormal-panel {
  width: min(100%, var(--mobile-window-width));
  max-height: min(72vh, 620px);
  border-radius: 8px;
  overflow: hidden;
  background: #ffffff;
  box-shadow: 0 -16px 36px rgba(15, 23, 42, 0.2);
}

.home-abnormal-panel header {
  min-height: 62px;
  padding: 14px 16px 12px;
  border-bottom: 1px solid #e5edf6;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.home-abnormal-panel header span {
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.home-abnormal-panel header h2 {
  margin: 4px 0 0;
  color: #111827;
  font-size: 18px;
  font-weight: 900;
  line-height: 1.2;
}

.home-abnormal-panel header button {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: #f1f5f9;
  color: #475569;
  font-size: 22px;
  line-height: 1;
}

.home-abnormal-list {
  max-height: calc(min(72vh, 620px) - 62px);
  overflow-y: auto;
  padding: 6px 16px 14px;
}

.home-abnormal-list article {
  padding: 12px 0;
  border-bottom: 1px solid #eef2f7;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
}

.home-abnormal-list article:last-child {
  border-bottom: 0;
}

.home-abnormal-main {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.home-abnormal-main span {
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
}

.home-abnormal-main strong {
  overflow: hidden;
  color: #e84d5b;
  font-size: 15px;
  font-weight: 900;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-abnormal-main small {
  overflow: hidden;
  color: #94a3b8;
  font-size: 11px;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-abnormal-actions {
  display: inline-flex;
  gap: 7px;
}

.home-abnormal-actions button {
  min-height: 30px;
  border: 0;
  border-radius: 8px;
  padding: 0 10px;
  background: #edf5ff;
  color: #0b63f6;
  font-size: 12px;
  font-weight: 900;
}

.home-abnormal-actions button + button {
  background: #fff7ed;
  color: #c2410c;
}

.home-abnormal-empty {
  padding: 42px 16px 46px;
  color: #94a3b8;
  font-size: 14px;
  text-align: center;
}

.home-comp-time-list {
  max-height: calc(min(72vh, 620px) - 62px);
  overflow-y: auto;
  padding: 6px 16px 14px;
}

.home-comp-time-list article {
  padding: 12px 0;
  border-bottom: 1px solid #eef2f7;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
}

.home-comp-time-list article:last-child {
  border-bottom: 0;
}

.home-comp-time-main {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.home-comp-time-main span {
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
}

.home-comp-time-main strong {
  overflow: hidden;
  color: #111827;
  font-size: 15px;
  font-weight: 900;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-comp-time-main small {
  overflow: hidden;
  color: #94a3b8;
  font-size: 11px;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-comp-time-amount {
  min-width: 74px;
  min-height: 46px;
  border-radius: 8px;
  display: grid;
  align-content: center;
  justify-items: end;
  gap: 4px;
  padding: 7px 9px;
  background: #f0fdfa;
}

.home-comp-time-amount span {
  color: #0f766e;
  font-size: 10px;
  font-weight: 800;
  line-height: 1;
}

.home-comp-time-amount strong {
  color: #0f766e;
  font-size: 15px;
  font-weight: 900;
  line-height: 1;
}

.home-attendance-card,
.home-panel {
  padding: 16px;
}

.home-attendance-card {
  text-align: center;
}

.home-attendance-card .home-card-head {
  position: relative;
  justify-content: center;
  text-align: center;
}

.home-attendance-card .home-status-chip {
  position: absolute;
  right: 0;
  top: 0;
}

.home-status-chip {
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  background: #ecfdf5;
  color: #047857;
  font-size: 12px;
  font-weight: 800;
}

.home-status-chip.is-warning {
  background: #fff7ed;
  color: #c2410c;
}

.attendance-timeline {
  margin-top: 16px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 28px minmax(0, 1fr);
  align-items: center;
}

.attendance-timeline div {
  min-height: 76px;
  padding: 12px;
  border: 1px solid #e5edf6;
  border-radius: 8px;
  display: grid;
  gap: 6px;
  background: #f8fafc;
  text-align: center;
}

.attendance-timeline span,
.attendance-timeline small {
  color: #64748b;
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.attendance-timeline strong {
  color: #111827;
  font-size: 20px;
  font-weight: 800;
}

.attendance-timeline i {
  height: 2px;
  background: #cbd5e1;
}

.home-rule-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  display: inline-flex;
  background: #94a3b8;
}

.home-rule-dot.is-normal {
  background: #35b66f;
}

.home-rule-dot.is-warning {
  background: #e8a300;
}

.home-rule-dot.is-danger {
  background: #e84d5b;
}

.home-rule-dot.is-pending {
  background: #94a3b8;
}

.home-primary-action {
  width: 100%;
  min-height: 46px;
  margin-top: 14px;
  border: 0;
  border-radius: 8px;
  display: grid;
  grid-template-columns: 24px 1fr 24px;
  align-items: center;
  padding: 0 14px;
  background: linear-gradient(180deg, #0b63f6 0%, #0757d9 100%);
  color: #ffffff;
  font-size: 15px;
  font-weight: 800;
}

.home-primary-action ion-icon {
  font-size: 19px;
}

.home-location-line {
  margin: 10px 0 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
  text-align: center;
}

.home-text-button {
  min-height: 32px;
  border: 0;
  border-radius: 8px;
  padding: 0 10px;
  background: #eef6ff;
  color: #0b63f6;
  font-size: 13px;
  font-weight: 800;
}

.home-service-grid {
  margin-top: 14px;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px 8px;
}

.home-service-grid.is-editing {
  user-select: none;
}

.home-service-item {
  position: relative;
  min-width: 0;
  min-height: 96px;
  border: 1px solid transparent;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 8px 4px;
  background: transparent;
  color: #111827;
  text-align: center;
  cursor: pointer;
}

.home-service-item:focus-visible {
  outline: 2px solid rgba(11, 99, 246, 0.5);
  outline-offset: 3px;
}

.home-service-icon {
  width: 42px;
  height: 42px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: color-mix(in srgb, var(--service-accent) 13%, #ffffff);
  color: var(--service-accent);
  font-size: 21px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.24),
    0 5px 12px color-mix(in srgb, var(--service-accent) 12%, transparent);
}

.home-service-item strong {
  max-width: 100%;
  min-height: 16px;
  overflow: hidden;
  color: #111827;
  font-size: 13px;
  font-weight: 800;
  line-height: 1.2;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.home-service-item.is-editing {
  border-color: #dbeafe;
  background: #f8fbff;
  cursor: grab;
  touch-action: none;
}

.home-service-item.is-dragging {
  z-index: 2;
  border-color: rgba(11, 99, 246, 0.45);
  box-shadow: 0 12px 24px rgba(15, 35, 75, 0.14);
  cursor: grabbing;
  transform: scale(1.035);
}

.shortcut-remove-button {
  position: absolute;
  top: -7px;
  right: -5px;
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: 999px;
  display: grid;
  place-items: center;
  background: #fff1f2;
  color: #e11d48;
  font-size: 15px;
  box-shadow: 0 4px 12px rgba(225, 29, 72, 0.16);
}

.shortcut-drag-handle {
  width: 32px;
  height: 20px;
  border: 1px solid #dbe4ef;
  border-radius: 999px;
  display: grid;
  place-items: center;
  color: #64748b;
  background: #ffffff;
  font-size: 16px;
}

.shortcut-add-panel {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid #e5edf6;
}

.shortcut-add-head {
  display: grid;
  gap: 4px;
}

.shortcut-add-head span {
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.shortcut-add-head small,
.shortcut-feature-list small,
.shortcut-empty-line {
  color: #64748b;
  font-size: 11px;
  line-height: 1.35;
}

.shortcut-feature-group {
  margin-top: 12px;
}

.shortcut-feature-group p {
  margin: 0 0 8px;
  color: #111827;
  font-size: 13px;
  font-weight: 900;
}

.shortcut-feature-list {
  display: grid;
  gap: 8px;
}

.shortcut-feature-list button {
  min-width: 0;
  min-height: 54px;
  border: 1px solid #dbeafe;
  border-radius: 8px;
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) 22px;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  background: #f8fbff;
  color: #111827;
  text-align: left;
}

.shortcut-feature-icon {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: color-mix(in srgb, var(--service-accent) 13%, #ffffff);
  color: var(--service-accent);
  font-size: 18px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.24),
    0 5px 12px color-mix(in srgb, var(--service-accent) 10%, transparent);
}

.shortcut-feature-list button > span:nth-child(2) {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.shortcut-feature-list strong {
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  font-weight: 800;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shortcut-feature-list button > ion-icon {
  color: #0b63f6;
  font-size: 19px;
}

.shortcut-empty-line {
  margin: 12px 0 0;
}

@media (max-width: 360px) {
  .home-enterprise-shell {
    padding-inline: 10px;
  }

  .home-service-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .home-metric-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    row-gap: 10px;
  }

  .home-comp-time-list article {
    grid-template-columns: minmax(0, 1fr);
  }

  .home-comp-time-amount {
    justify-items: start;
  }
}
</style>
