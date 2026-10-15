<template>
  <ion-page>
    <ion-content fullscreen class="app-gradient-page approvals-enterprise-page">
      <div class="mobile-shell approvals-shell">
        <section class="approval-command-card" aria-label="审批中心">
          <div class="approval-command-card__head">
            <div>
              <h1>审批中心</h1>
            </div>
            <div class="approval-title-actions">
              <button type="button" aria-label="筛选" @click="loadApprovals">
                <ion-icon :icon="funnelOutline" />
              </button>
              <button type="button" aria-label="日历" @click="loadApprovals">
                <ion-icon :icon="calendarOutline" />
              </button>
            </div>
          </div>

          <label class="approval-search" aria-label="搜索审批数据">
            <ion-icon :icon="searchOutline" />
            <input
              v-model.trim="filters.applicant_keyword"
              type="search"
              placeholder="搜索审批、员工、单据编号"
              @keyup.enter="loadApprovals"
            />
          </label>

          <div class="approval-tab-row">
            <button
              v-for="item in approvalSummaryCards"
              :key="item.segment"
              type="button"
              :class="{ 'is-active': segment === item.segment }"
              @click="setSegment(item.segment)"
            >
              <span>{{ item.label }}</span>
              <em>{{ item.value }}</em>
            </button>
          </div>

          <div v-if="segment === 'submitted'" class="approval-status-filter-row" aria-label="我发起的审批状态">
            <button
              v-for="item in submittedStatusOptions"
              :key="item.value"
              type="button"
              :class="{ 'is-active': submittedStatus === item.value }"
              @click="setSubmittedStatus(item.value)"
            >
              <span>{{ item.label }}</span>
              <em>{{ item.count }}</em>
            </button>
          </div>
        </section>

        <div v-if="applicantPickerVisible" class="org-picker-backdrop" @click.self="closeApplicantPicker">
          <section class="org-picker-sheet" role="dialog" aria-modal="true" aria-label="选择申请人">
            <header class="org-picker-header">
              <div>
                <span>选择申请人</span>
                <strong>组织架构</strong>
              </div>
              <button type="button" @click="closeApplicantPicker">关闭</button>
            </header>
            <div class="org-picker-body">
              <button
                v-for="row in visibleApplicantRows"
                :key="row.key"
                type="button"
                class="org-picker-row"
                :class="{
                  'org-picker-row--employee': row.kind === 'employee',
                  'org-picker-row--selected': row.kind === 'employee' && filters.applicant_id === row.id,
                }"
                :style="{ paddingLeft: `${12 + row.level * 18}px` }"
                @click="handleApplicantRowClick(row)"
              >
                <span
                  v-if="row.kind === 'department'"
                  class="org-picker-caret"
                  @click.stop="toggleApplicantDepartment(row)"
                >
                  {{ row.expandable ? (row.expanded ? '▼' : '▶') : '' }}
                </span>
                <span v-else class="org-picker-caret" />
                <span class="org-picker-row-label">{{ row.label }}</span>
                <small v-if="row.caption">{{ row.caption }}</small>
              </button>
              <div v-if="!visibleApplicantRows.length" class="org-picker-empty">
                暂无可选员工
              </div>
            </div>
            <footer class="org-picker-footer">
              <span>{{ selectedApplicantDisplay || '未选择申请人' }}</span>
              <button type="button" @click="clearApplicantFilter">清除</button>
            </footer>
          </section>
        </div>

        <section class="section-block approval-list">
          <div class="approval-list-toolbar">
            <span>{{ activeSegmentLabel }} {{ displayedList.length }} 项</span>
            <button v-if="segment === 'pending'" type="button" @click="router.push('/app/tabs/approvals')">
              批量处理
            </button>
            <button v-else-if="segment === 'submitted'" type="button" @click="router.push('/app/approval/start')">
              发起申请
            </button>
          </div>
          <section v-if="loading" class="approval-empty-card">
            <strong>正在加载审批数据</strong>
            <span>请稍候。</span>
          </section>
          <template v-else>
            <article
              v-for="item in displayedList"
              :key="item.id"
              class="approval-card pressable-card"
              @click="openDetail(item.id)"
            >
              <div class="approval-card__main">
                <span class="approval-card__icon" :style="{ '--approval-accent': approvalItemAccent(item) }">
                  <ion-icon :icon="approvalItemIcon(item)" />
                </span>
                <div class="approval-card__content">
                  <div class="approval-card__title-row">
                    <h2>{{ approvalItemTitle(item) }}</h2>
                    <em v-if="approvalItemHighlight(item)">{{ approvalItemHighlight(item) }}</em>
                  </div>
                  <p>申请人：{{ approvalItemApplicant(item) }}</p>
                  <p>{{ approvalItemNode(item) }}</p>
                  <p v-if="approvalItemSecondaryLine(item)">{{ approvalItemSecondaryLine(item) }}</p>
                  <p>{{ approvalItemMetaLine(item) }}</p>
                </div>
                <div class="approval-card__side">
                  <strong :class="approvalStatusClass(item.status)">{{ formatStatus(item.status) }}</strong>
                  <span aria-hidden="true">›</span>
                </div>
              </div>
              <footer class="approval-card__actions">
                <template v-if="segment === 'pending'">
                  <button type="button" class="approval-card__primary" @click.stop="processApproval(item.id, 'approve')">通过</button>
                  <button type="button" class="approval-card__danger" @click.stop="processApproval(item.id, 'reject')">拒绝</button>
                </template>
                <button type="button" class="approval-card__link" @click.stop="openDetail(item.id)">查看详情</button>
              </footer>
            </article>
          </template>
          <section v-if="!loading && !displayedList.length" class="approval-empty-card">
            <strong>{{ emptyTitle }}</strong>
            <span>{{ emptyCaption }}</span>
            <button v-if="segment === 'submitted'" type="button" @click="router.push('/app/approval/start')">发起申请</button>
          </section>
        </section>
      </div>
    </ion-content>
  </ion-page>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  IonContent,
  IonIcon,
  IonPage,
  onIonViewWillEnter,
} from '@ionic/vue'
import {
  calendarOutline,
  documentTextOutline,
  funnelOutline,
  gitCompareOutline,
  personAddOutline,
  searchOutline,
  walletOutline,
} from 'ionicons/icons'
import { get, post } from '@/utils/request'
import { formatDateTime, formatStatus } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { useMobileInbox } from '@/composables/useMobileInbox'

type ApprovalSegment = 'pending' | 'approved' | 'submitted' | 'cc'
type SubmittedStatus = 'all' | 'pending' | 'approved' | 'rejected' | 'withdrawn' | 'cancelled'
type EmployeeOption = {
  id: number
  name: string
  employee_no?: string
  department_id?: number | null
  department_name?: string
  position?: string
}
type DepartmentOption = {
  id: number
  name: string
  parent_id?: number | null
  sort_order?: number
}
type ApplicantPickerRow = {
  key: string
  kind: 'department' | 'employee'
  id: number
  label: string
  caption?: string
  level: number
  expandable?: boolean
  expanded?: boolean
}

const segment = ref<ApprovalSegment>('pending')
const pendingList = ref<any[]>([])
const approvedList = ref<any[]>([])
const submittedList = ref<any[]>([])
const ccList = ref<any[]>([])
const approvalTypeOptions = ref<Array<{ label: string; value: string }>>([])
const employeeOptions = ref<EmployeeOption[]>([])
const departmentOptions = ref<DepartmentOption[]>([])
const applicantPickerVisible = ref(false)
const expandedApplicantDepartmentKeys = ref<Set<string>>(new Set())
const loading = ref(false)
const submittedStatus = ref<SubmittedStatus>('all')
const approvalCenterCounts = ref<Record<string, number>>({})
const submittedStatusCounts = ref<Record<string, number>>({})
const filters = reactive({
  start_date: '',
  end_date: '',
  business_type: '',
  applicant_id: null as number | null,
  applicant_keyword: '',
})
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const inbox = useMobileInbox(
  () => auth.displayRole !== 'employee',
  () => auth.roles.includes('hr') || auth.roles.includes('finance'),
)

const currentList = computed(() => {
  if (segment.value === 'approved') return approvedList.value
  if (segment.value === 'submitted') return submittedList.value
  if (segment.value === 'cc') return ccList.value
  return pendingList.value
})
const approvalSummaryCards = computed<Array<{ label: string; value: number; segment: ApprovalSegment }>>(() => [
  { label: '待处理', value: approvalCenterCounts.value.pending ?? pendingList.value.length, segment: 'pending' },
  { label: '已处理', value: approvalCenterCounts.value.processed ?? approvedList.value.length, segment: 'approved' },
  { label: '我发起的', value: approvalCenterCounts.value.submitted ?? submittedList.value.length, segment: 'submitted' },
  { label: '抄送我的', value: approvalCenterCounts.value.cc ?? ccList.value.length, segment: 'cc' },
])
const submittedStatusOptions = computed<Array<{ label: string; value: SubmittedStatus; count: number }>>(() => [
  { label: '全部', value: 'all', count: submittedStatusCounts.value.all ?? approvalCenterCounts.value.submitted ?? submittedList.value.length },
  { label: '审批中', value: 'pending', count: submittedStatusCounts.value.pending ?? 0 },
  { label: '已通过', value: 'approved', count: submittedStatusCounts.value.approved ?? 0 },
  { label: '未通过', value: 'rejected', count: submittedStatusCounts.value.rejected ?? 0 },
  { label: '已撤回', value: 'withdrawn', count: (submittedStatusCounts.value.withdrawn ?? 0) + (submittedStatusCounts.value.cancelled ?? 0) },
])
const activeSegmentLabel = computed(() => {
  return approvalSummaryCards.value.find((item) => item.segment === segment.value)?.label || '待处理'
})
const emptyTitle = computed(() => {
  if (segment.value === 'pending') return '当前没有待处理审批'
  if (segment.value === 'approved') return '当前没有已处理记录'
  if (segment.value === 'submitted') return submittedStatus.value === 'all' ? '还没有提交过申请' : '当前状态暂无申请'
  return '当前没有抄送记录'
})
const emptyCaption = computed(() => {
  if (segment.value === 'submitted') return '需要请假、加班或提交其他事项时，从这里发起，提交后会自动出现在我发起的列表里。'
  return '可以调整搜索词、审批类型或日期范围后重试。'
})
const displayedList = computed(() => {
  const keyword = filters.applicant_keyword.trim().toLowerCase()
  if (!keyword) return currentList.value
  return currentList.value.filter((item) => {
    return [
      item?.id,
      approvalItemType(item),
      approvalItemTitle(item),
      approvalItemApplicant(item),
      approvalItemNode(item),
      formatStatus(item?.status),
    ]
      .map((value) => String(value || '').toLowerCase())
      .some((value) => value.includes(keyword))
  })
})
const selectedApplicant = computed(() => {
  if (!filters.applicant_id) return null
  return employeeOptions.value.find((employee) => employee.id === filters.applicant_id) || null
})
const selectedApplicantDisplay = computed(() => {
  const applicant = selectedApplicant.value
  if (!applicant) return ''
  const employeeNo = applicant.employee_no ? ` · ${applicant.employee_no}` : ''
  return `${applicant.name}${employeeNo}`
})
const departmentTree = computed(() => buildDepartmentTree(departmentOptions.value))
const employeeByDepartment = computed(() => {
  const groups = new Map<number, EmployeeOption[]>()
  for (const employee of employeeOptions.value) {
    const departmentId = Number(employee.department_id || 0)
    if (!groups.has(departmentId)) groups.set(departmentId, [])
    groups.get(departmentId)!.push(employee)
  }
  for (const employees of groups.values()) {
    employees.sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN'))
  }
  return groups
})
const visibleApplicantRows = computed<ApplicantPickerRow[]>(() => {
  const rows: ApplicantPickerRow[] = []
  const visitDepartment = (department: any, level: number) => {
    const id = Number(department.id)
    const key = `department-${id}`
    const children = department.children || []
    const employees = employeeByDepartment.value.get(id) || []
    const expandable = children.length > 0 || employees.length > 0
    const expanded = expandedApplicantDepartmentKeys.value.has(key)
    rows.push({
      key,
      kind: 'department',
      id,
      label: department.name || department.department_name || `部门 ${id}`,
      caption: employees.length ? `${employees.length} 人` : '',
      level,
      expandable,
      expanded,
    })
    if (!expanded) return
    for (const child of children) visitDepartment(child, level + 1)
    for (const employee of employees) {
      rows.push({
        key: `employee-${employee.id}`,
        kind: 'employee',
        id: Number(employee.id),
        label: employee.name || employee.employee_no || `员工 ${employee.id}`,
        caption: employee.position || employee.employee_no || '',
        level: level + 1,
      })
    }
  }

  for (const department of departmentTree.value) visitDepartment(department, 0)
  if (!departmentTree.value.length) {
    for (const employee of employeeOptions.value) {
      rows.push({
        key: `employee-${employee.id}`,
        kind: 'employee',
        id: Number(employee.id),
        label: employee.name || employee.employee_no || `员工 ${employee.id}`,
        caption: employee.department_name || employee.employee_no || '',
        level: 0,
      })
    }
    return rows
  }
  const unassigned = employeeByDepartment.value.get(0) || []
  for (const employee of unassigned) {
    rows.push({
      key: `employee-${employee.id}`,
      kind: 'employee',
      id: Number(employee.id),
      label: employee.name || employee.employee_no || `员工 ${employee.id}`,
      caption: '未分配部门',
      level: 0,
    })
  }
  return rows
})

function normalizeList(payload: any) {
  if (Array.isArray(payload)) return payload
  if (Array.isArray(payload?.items)) return payload.items
  return []
}

function normalizeSegment(value: unknown): ApprovalSegment {
  const text = String(Array.isArray(value) ? value[0] : value || '').toLowerCase()
  if (text === 'approved' || text === 'processed') return 'approved'
  if (text === 'submitted') return 'submitted'
  if (text === 'cc') return 'cc'
  return 'pending'
}

function setSegment(next: ApprovalSegment) {
  segment.value = next
  void router.replace({
    query: {
      ...route.query,
      segment: next === 'pending' ? undefined : next,
    },
  })
  void loadApprovals()
}

function setSubmittedStatus(next: SubmittedStatus) {
  submittedStatus.value = next
  if (segment.value !== 'submitted') {
    setSegment('submitted')
    return
  }
  void loadApprovals()
}

function buildDepartmentTree(list: DepartmentOption[]) {
  const map = new Map<number, any>()
  for (const department of list || []) {
    map.set(Number(department.id), { ...department, children: [] })
  }

  const roots: any[] = []
  for (const department of list || []) {
    const id = Number(department.id)
    const parentId = Number(department.parent_id || 0)
    const node = map.get(id)
    if (parentId && map.has(parentId)) {
      map.get(parentId).children.push(node)
    } else {
      roots.push(node)
    }
  }

  const sortNodes = (nodes: any[]) => {
    nodes.sort((a, b) => {
      const order = Number(a.sort_order || 0) - Number(b.sort_order || 0)
      if (order !== 0) return order
      return String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN')
    })
    nodes.forEach((node) => sortNodes(node.children || []))
  }
  sortNodes(roots)
  return roots
}

function approvalItemType(item: any) {
  const raw = String(item?.approval_type_name || item?.business_type || item?.module || '').trim()
  const normalized = raw.toLowerCase()
  if (raw.includes('招聘') || normalized.includes('recruitment')) return '招聘审批'
  if (raw.includes('请假') || normalized.includes('leave')) return '请假审批'
  if (raw.includes('加班') || normalized.includes('overtime')) return '加班审批'
  if (raw.includes('调薪') || normalized.includes('salary')) return '调薪审批'
  return raw || '审批'
}

function approvalItemTitle(item: any) {
  return item?.summary || item?.title || item?.business_name || `${approvalItemType(item)} #${item?.id || '-'}`
}

function approvalItemApplicant(item: any) {
  return item?.applicant_name || item?.submitter_name || (item?.applicant_id ? `员工 #${item.applicant_id}` : '-')
}

function approvalItemNode(item: any) {
  const status = String(item?.status || '').toLowerCase()
  if (status !== 'pending') {
    return item?.current_node_status_label || formatStatus(item?.status)
  }
  const nodeName = item?.current_node_name || (item?.current_node_order ? `第 ${item.current_node_order} 节点` : '审批节点')
  const nodeStatus = item?.current_node_status_label || '待审批'
  return `${nodeName} · ${nodeStatus}`
}

function approvalItemIcon(item: any) {
  const type = approvalItemType(item)
  if (type.includes('报销') || type.includes('费用')) return walletOutline
  if (type.includes('入职') || type.includes('招聘')) return personAddOutline
  if (type.includes('调岗') || type.includes('调薪')) return gitCompareOutline
  return documentTextOutline
}

function approvalItemAccent(item: any) {
  const type = approvalItemType(item)
  if (type.includes('报销') || type.includes('费用')) return '#1688ff'
  if (type.includes('入职') || type.includes('招聘')) return '#0b63f6'
  if (type.includes('调岗') || type.includes('调薪')) return '#7c3aed'
  return '#15b66d'
}

function approvalItemHighlight(item: any) {
  const fields = item?.form_data || item?.business_data || item?.payload || {}
  const amount = fields?.amount || fields?.total_amount || fields?.expense_amount || item?.amount
  if (amount) return `￥${Number(amount).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  const duration = fields?.duration || fields?.leave_duration || fields?.days || item?.duration
  if (duration) return `${duration}时`
  return ''
}

function approvalItemSecondaryLine(item: any) {
  const no = item?.business_no || item?.instance_no || item?.code
  if (no) return `单据编号：${no}`
  return ''
}

function approvalItemMetaLine(item: any) {
  const department = approvalItemDepartment(item)
  const submittedAt = formatDateTime(item?.created_at)
  return [department ? `部门：${department}` : '', submittedAt ? `申请时间：${submittedAt}` : '']
    .filter(Boolean)
    .join(' · ') || '申请时间：-'
}

function approvalItemDepartment(item: any) {
  const fields = item?.form_data || item?.business_data || item?.payload || {}
  const displayValues = item?.form_field_display_values || {}
  return normalizeApprovalText(
    item?.department_name,
    item?.applicant_department,
    displayValues.department_name,
    displayValues.department,
    displayValues.dept_name,
    displayValues.dept,
    displayValues.department_id,
    displayValues.apply_department_id,
    fields.department_name,
    fields.department,
    fields.dept_name,
    fields.dept,
    fields.applicant_department,
    fields.apply_department,
    fields.apply_dept,
  )
}

function normalizeApprovalText(...values: unknown[]): string {
  for (const value of values) {
    if (value === undefined || value === null) continue
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (typeof value === 'number' || typeof value === 'boolean') return String(value)
    if (Array.isArray(value)) {
      const text: string = value.map((item) => normalizeApprovalText(item)).filter(Boolean).join('、')
      if (text) return text
    }
    if (typeof value === 'object') {
      const record = value as Record<string, unknown>
      const text: string = normalizeApprovalText(record.display, record.label, record.name, record.text, record.value)
      if (text) return text
    }
  }
  return ''
}

function approvalStatusClass(status?: string | null) {
  const value = String(status || '').toLowerCase()
  return {
    'is-pending': value === 'pending',
    'is-success': value === 'approved' || value === 'completed',
    'is-danger': value === 'rejected',
    'is-muted': value === 'withdrawn' || value === 'cancelled',
  }
}

function queryParams() {
  return {
    limit: 50,
    business_type: filters.business_type || undefined,
    start_date: filters.start_date || undefined,
    end_date: filters.end_date || undefined,
    keyword: filters.applicant_keyword || undefined,
    submitted_status: submittedStatus.value === 'all' ? undefined : submittedStatus.value,
  }
}

async function loadApplicantOptions() {
  const [departments, employees] = await Promise.allSettled([
    get('/departments'),
    get('/employees/selector', { limit: 1000 }),
  ])
  const departmentList = departments.status === 'fulfilled' ? normalizeList(departments.value) : []
  const employeeList = employees.status === 'fulfilled' ? normalizeList(employees.value) : []
  departmentOptions.value = departmentList.map((department: any) => ({
    id: Number(department.id),
    name: String(department.name || department.department_name || `部门 ${department.id}`),
    parent_id: department.parent_id ?? null,
    sort_order: Number(department.sort_order || 0),
  }))
  employeeOptions.value = employeeList.map((employee: any) => ({
    id: Number(employee.id),
    name: String(employee.name || `员工 ${employee.id}`),
    employee_no: employee.employee_no ? String(employee.employee_no) : '',
    department_id: employee.department_id ?? null,
    department_name: employee.department_name ? String(employee.department_name) : '',
    position: employee.position ? String(employee.position) : '',
  }))
}

async function openApplicantPicker() {
  if (!departmentOptions.value.length || !employeeOptions.value.length) {
    await loadApplicantOptions()
  }
  const next = new Set<string>()
  for (const department of departmentTree.value) {
    next.add(`department-${department.id}`)
  }
  expandedApplicantDepartmentKeys.value = next
  applicantPickerVisible.value = true
}

function closeApplicantPicker() {
  applicantPickerVisible.value = false
}

function toggleApplicantDepartment(row: ApplicantPickerRow) {
  if (row.kind !== 'department' || !row.expandable) return
  const next = new Set(expandedApplicantDepartmentKeys.value)
  if (next.has(row.key)) next.delete(row.key)
  else next.add(row.key)
  expandedApplicantDepartmentKeys.value = next
}

function handleApplicantRowClick(row: ApplicantPickerRow) {
  if (row.kind === 'department') {
    toggleApplicantDepartment(row)
    return
  }
  filters.applicant_id = row.id
  filters.applicant_keyword = ''
  closeApplicantPicker()
}

function clearApplicantFilter() {
  filters.applicant_id = null
  filters.applicant_keyword = ''
}

async function loadApprovalTypes() {
  try {
    const groups: any[] = await get('/approval/templates')
    const options = (groups || [])
      .flatMap((group) => Array.isArray(group?.templates) ? group.templates : [])
      .map((item: any) => ({
        label: String(item?.name || item?.business_code || '').trim(),
        value: String(item?.business_code || '').trim(),
      }))
      .filter((item) => item.label && item.value)
    const seen = new Set<string>()
    approvalTypeOptions.value = options.filter((item) => {
      if (seen.has(item.value)) return false
      seen.add(item.value)
      return true
    })
  } catch {
    approvalTypeOptions.value = []
  }
}

async function loadApprovals() {
  loading.value = true
  const params = queryParams()
  try {
    const center = await get<any>('/approval/mobile/center', params)
    pendingList.value = normalizeList(center?.pending)
    approvedList.value = normalizeList(center?.processed)
    submittedList.value = normalizeList(center?.submitted)
    ccList.value = normalizeList(center?.cc)
    approvalCenterCounts.value = center?.counts && typeof center.counts === 'object' ? center.counts : {}
    submittedStatusCounts.value = center?.submitted_status_counts && typeof center.submitted_status_counts === 'object'
      ? center.submitted_status_counts
      : {}
  } finally {
    loading.value = false
  }
}

async function refreshApprovalsAndInbox() {
  const [approvalsResult] = await Promise.allSettled([loadApprovals(), inbox.loadInboxCounts()])
  if (approvalsResult.status === 'rejected') throw approvalsResult.reason
}

function resetFilters() {
  filters.start_date = ''
  filters.end_date = ''
  filters.business_type = ''
  filters.applicant_id = null
  filters.applicant_keyword = ''
  void loadApprovals()
}

async function processApproval(id: number, action: 'approve' | 'reject') {
  await post(`/approval/instances/${id}/process`, { action, comment: '' })
  await refreshApprovalsAndInbox()
}

function openDetail(id: number) {
  router.push({
    path: `/app/approval/${id}`,
    query: { mode: segment.value },
  })
}

onIonViewWillEnter(async () => {
  segment.value = normalizeSegment(route.query.segment)
  await refreshApprovalsAndInbox()
})
</script>

<style scoped>
.approvals-enterprise-page {
  --background: #f7f9fc;
}

.approvals-shell {
  padding: 16px 14px calc(24px + env(safe-area-inset-bottom));
}

.approval-command-card,
.approval-card,
.approval-empty-card {
  border: 1px solid rgba(214, 223, 235, 0.92);
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 8px 22px rgba(20, 32, 56, 0.075);
}

.approval-command-card {
  padding: 18px 16px 16px;
}

.approval-command-card__head,
.approval-card__actions {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.approval-command-card h1 {
  margin: 0;
  color: #111827;
  font-size: 24px;
  font-weight: 800;
  line-height: 1.18;
}

.approval-title-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.approval-title-actions button {
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: transparent;
  color: #0f172a;
  font-size: 22px;
}

.approval-title-actions button:active {
  background: #eef6ff;
}

.approval-search {
  min-height: 44px;
  margin-top: 18px;
  padding: 0 12px;
  border: 1px solid #dbe4ef;
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 9px;
  background: #ffffff;
  color: #64748b;
  box-shadow: 0 8px 18px rgba(20, 32, 56, 0.06);
}

.approval-search ion-icon {
  font-size: 18px;
}

.approval-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: #111827;
  font: inherit;
  font-size: 14px;
}

.approval-tab-row {
  margin-top: 20px;
  border-bottom: 1px solid #e5eaf2;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.approval-tab-row button {
  min-width: 0;
  min-height: 42px;
  border: 0;
  border-bottom: 2px solid transparent;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 0 2px 8px;
  background: transparent;
  color: #25324a;
  font-size: 14px;
  font-weight: 800;
}

.approval-tab-row button.is-active {
  border-bottom-color: #0b63f6;
  color: #0b63f6;
}

.approval-tab-row em {
  min-width: 20px;
  height: 20px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #eef4ff;
  color: #0b63f6;
  font-size: 11px;
  font-style: normal;
  font-weight: 800;
}

.approval-status-filter-row {
  margin-top: 12px;
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 2px;
}

.approval-status-filter-row button {
  flex: 0 0 auto;
  min-height: 32px;
  border: 1px solid #dbe4ef;
  border-radius: 8px;
  padding: 0 9px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: #ffffff;
  color: #475569;
  font-size: 12px;
  font-weight: 800;
}

.approval-status-filter-row button.is-active {
  border-color: #bfdbfe;
  background: #eff6ff;
  color: #0b63f6;
}

.approval-status-filter-row em {
  min-width: 18px;
  height: 18px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #eef4ff;
  color: #0b63f6;
  font-size: 10px;
  font-style: normal;
}

.approval-filter-grid {
  margin-top: 14px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.approval-filter-grid label,
.approval-type-select,
.approval-applicant-select {
  display: grid;
  gap: 7px;
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.approval-type-select,
.approval-applicant-select {
  margin-top: 12px;
}

.approval-filter-grid input,
.approval-type-select select,
.approval-applicant-trigger {
  width: 100%;
  min-height: 42px;
  border: 1px solid #dbe4ef;
  border-radius: 8px;
  background: #ffffff;
  color: #111827;
  padding: 0 11px;
  font-size: 14px;
}

.approval-applicant-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  text-align: left;
}

.approval-applicant-trigger span:first-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.approval-applicant-placeholder {
  color: #94a3b8;
}

.approval-query-button {
  width: 100%;
  min-height: 44px;
  margin-top: 14px;
  border: 0;
  border-radius: 8px;
  background: #0f4c81;
  color: #ffffff;
  font-size: 15px;
  font-weight: 800;
}

.approval-list {
  display: grid;
  gap: 10px;
}

.approval-list-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 2px 2px;
}

.approval-list-toolbar span {
  color: #475569;
  font-size: 14px;
  font-weight: 700;
}

.approval-list-toolbar button {
  min-height: 30px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #0b63f6;
  font-size: 14px;
  font-weight: 800;
}

.approval-card {
  padding: 16px 14px;
}

.approval-card__main {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
}

.approval-card__icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: color-mix(in srgb, var(--approval-accent) 12%, #ffffff);
  color: var(--approval-accent);
  font-size: 22px;
}

.approval-card__content {
  min-width: 0;
}

.approval-card__title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.approval-card h2 {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: #111827;
  font-size: 16px;
  font-weight: 800;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.approval-card__title-row em {
  flex: 0 0 auto;
  min-height: 24px;
  border-radius: 5px;
  display: inline-flex;
  align-items: center;
  padding: 0 7px;
  background: #fff3e6;
  color: #f97316;
  font-size: 12px;
  font-style: normal;
  font-weight: 800;
}

.approval-card p {
  margin: 8px 0 0;
  color: #53627a;
  font-size: 13px;
  line-height: 1.25;
}

.approval-card p + p {
  margin-top: 7px;
}

.approval-card__side {
  display: flex;
  align-items: center;
  gap: 8px;
}

.approval-card__side strong {
  min-height: 28px;
  border-radius: 5px;
  display: inline-flex;
  align-items: center;
  padding: 0 8px;
  background: #eef2f7;
  color: #475569;
  font-size: 12px;
  font-weight: 800;
}

.approval-card__side strong.is-pending {
  background: #eff6ff;
  color: #0b63f6;
}

.approval-card__side strong.is-success {
  background: #e8f8ee;
  color: #15a266;
}

.approval-card__side strong.is-danger {
  background: #fff1f2;
  color: #dc2626;
}

.approval-card__side strong.is-muted {
  background: #f1f5f9;
  color: #64748b;
}

.approval-card__side span {
  color: #7b8798;
  font-size: 26px;
  line-height: 1;
}

.approval-card__actions {
  align-items: center;
  justify-content: flex-end;
  margin-top: 14px;
}

.approval-card__actions button {
  min-height: 34px;
  border-radius: 8px;
  padding: 0 12px;
  font-size: 13px;
  font-weight: 800;
}

.approval-card__primary {
  border: 0;
  background: #0f4c81;
  color: #ffffff;
}

.approval-card__danger {
  border: 1px solid #fecaca;
  background: #fff7f7;
  color: #b91c1c;
}

.approval-card__link {
  border: 1px solid #dbe4ef;
  background: #ffffff;
  color: #0f4c81;
}

.approval-empty-card {
  padding: 24px 16px;
  display: grid;
  gap: 8px;
  text-align: center;
}

.approval-empty-card strong {
  color: #111827;
  font-size: 15px;
}

.approval-empty-card span {
  color: #64748b;
  font-size: 13px;
}

.approval-empty-card button {
  justify-self: center;
  min-height: 36px;
  border: 0;
  border-radius: 8px;
  padding: 0 14px;
  background: #0b63f6;
  color: #ffffff;
  font-size: 14px;
  font-weight: 800;
}

.org-picker-backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: flex-end;
}

.org-picker-sheet {
  width: 100%;
  max-height: 78vh;
  border-radius: 8px 8px 0 0;
  background: #ffffff;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  overflow: hidden;
}

.org-picker-header {
  padding: 14px 16px;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.org-picker-header div {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.org-picker-header span {
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
}

.org-picker-header strong {
  color: #0f172a;
  font-size: 16px;
}

.org-picker-header button {
  border: 0;
  background: transparent;
  color: #0f4c81;
  font-size: 14px;
  font-weight: 800;
}

.org-picker-body {
  overflow: auto;
  padding: 8px 0 18px;
}

.org-picker-row {
  width: 100%;
  min-height: 44px;
  border: 0;
  border-bottom: 1px solid #f1f5f9;
  background: #ffffff;
  color: #0f172a;
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: left;
}

.org-picker-row--employee {
  color: #334155;
}

.org-picker-row--selected {
  background: #eef6ff;
  color: #0f4c81;
  font-weight: 800;
}

.org-picker-caret {
  width: 18px;
  flex: 0 0 18px;
  color: #64748b;
  font-size: 11px;
}

.org-picker-row-label {
  min-width: 0;
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.org-picker-row small {
  flex: 0 0 auto;
  max-width: 34%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #64748b;
  font-size: 12px;
}

.org-picker-empty {
  padding: 28px 16px;
  color: #64748b;
  text-align: center;
  font-size: 14px;
}

.org-picker-footer {
  min-height: 56px;
  padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.org-picker-footer span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #64748b;
  font-size: 13px;
}

.org-picker-footer button {
  min-width: 72px;
  height: 36px;
  border: 0;
  border-radius: 8px;
  background: #0f4c81;
  color: #ffffff;
  font-size: 14px;
  font-weight: 800;
}

@media (max-width: 360px) {
  .approvals-shell {
    padding-inline: 10px;
  }
}
</style>
